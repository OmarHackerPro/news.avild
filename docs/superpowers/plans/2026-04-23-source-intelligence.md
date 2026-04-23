# Source Intelligence System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-source credibility weighting and category-level ingest filtering so high-signal sources lift cluster scores and low-value content categories are discarded before polluting the pipeline.

**Architecture:** Three pillars: (1) Postgres schema additions (`feed_sources` + new `source_categories` table) with an Alembic migration; (2) normalizer refactor — replace the `cisa_advisory`/`generic` callable registry with a flag-driven `normalize_article()` function; (3) `compute_cluster_score()` gains a 6th factor reading `max_credibility_weight` from the cluster document, which the clusterer maintains as the max across all member articles.

**Tech Stack:** Python 3.12, SQLAlchemy async + Alembic, OpenSearch, feedparser, local LLM (Ollama — selected at script runtime via env var `OLLAMA_MODEL`)

---

## File Map

| Action | Path | Purpose |
|---|---|---|
| Create | `app/db/models/source_category.py` | ORM model for `source_categories` table |
| Modify | `app/db/models/feed_source.py` | Add 3 new columns + extend `to_source_dict()` |
| Create | `alembic/versions/f6a7b8c9d0e1_add_source_intelligence.py` | Schema migration + data seed |
| Modify | `app/db/opensearch.py` | Add `credibility_weight` to NEWS_MAPPING, `max_credibility_weight` to CLUSTERS_MAPPING |
| Modify | `app/ingestion/sources.py` | Add `NotRequired` fields to `FeedSource` TypedDict |
| Modify | `app/ingestion/normalizer.py` | Add `normalize_article()`, refactor registry to flag dicts |
| Modify | `app/ingestion/scorer.py` | Add `max_credibility_weight` param + credibility factor (0–15 pts) |
| Modify | `app/ingestion/clusterer.py` | Track `max_credibility_weight` in create + merge |
| Modify | `app/ingestion/ingester.py` | Load categories from DB, filter articles, inject `credibility_weight` |
| Create | `app/ingestion/category_classifier.py` | `CategoryDecision` + `classify_categories()` + Ollama impl |
| Create | `scripts/classify_source_categories.py` | Bulk classification script |
| Modify | `tests/test_normalizer.py` | Add tests for `normalize_article()`, update registry tests |
| Create | `tests/test_scorer.py` | Tests for the new credibility factor |
| Create | `tests/test_category_classifier.py` | Tests for classifier interface |

---

## Task 1: Postgres Schema — ORM Model + Migration

**Files:**
- Create: `app/db/models/source_category.py`
- Modify: `app/db/models/feed_source.py`
- Create: `alembic/versions/f6a7b8c9d0e1_add_source_intelligence.py`

- [ ] **Step 1: Write the ORM model for source_categories**

Create `app/db/models/source_category.py`:

```python
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SourceCategory(Base):
    __tablename__ = "source_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("feed_sources.id", ondelete="CASCADE"), nullable=False
    )
    category_label: Mapped[str] = mapped_column(String(255), nullable=False)
    ingest: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    priority_modifier: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0.0"
    )
    classified_by: Mapped[str] = mapped_column(String(20), nullable=False)
    classification_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        sa.UniqueConstraint("source_id", "category_label", name="uq_source_categories"),
    )
```

- [ ] **Step 2: Add the three new columns to feed_source.py**

In `app/db/models/feed_source.py`, add after `normalizer_key`:

```python
    credibility_weight: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="1.0"
    )
    extract_cves: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    extract_cvss: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
```

Also update `to_source_dict()` to return the new fields:

```python
    def to_source_dict(self) -> dict:
        return {
            "name": self.name,
            "url": self.url,
            "default_type": self.default_type,
            "default_category": self.default_category,
            "default_severity": self.default_severity,
            "normalizer": self.normalizer_key,
            "credibility_weight": self.credibility_weight,
            "extract_cves": self.extract_cves,
            "extract_cvss": self.extract_cvss,
        }
```

- [ ] **Step 3: Write a failing test that the model imports correctly**

In `tests/test_normalizer.py`, add at the top temporarily (just to verify import works later):

```python
# This will be replaced by proper tests in Task 4
def test_source_category_model_imports():
    from app.db.models.source_category import SourceCategory
    assert SourceCategory.__tablename__ == "source_categories"
```

Run: `pytest tests/test_normalizer.py::test_source_category_model_imports -v`

Expected: FAIL with `ModuleNotFoundError` (file not created yet) — but we already wrote the file, so it should PASS after creation.

- [ ] **Step 4: Create the Alembic migration**

Create `alembic/versions/f6a7b8c9d0e1_add_source_intelligence.py`:

```python
"""Add source intelligence: credibility fields + source_categories table

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-23

Changes:
  - feed_sources: add credibility_weight (FLOAT DEFAULT 1.0)
  - feed_sources: add extract_cves (BOOL DEFAULT FALSE)
  - feed_sources: add extract_cvss (BOOL DEFAULT FALSE)
  - Create source_categories table
  - Data: seed credibility weights for known high-signal sources
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. New columns on feed_sources
    # ------------------------------------------------------------------
    op.add_column(
        "feed_sources",
        sa.Column("credibility_weight", sa.Float(), nullable=False, server_default="1.0"),
    )
    op.add_column(
        "feed_sources",
        sa.Column("extract_cves", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "feed_sources",
        sa.Column("extract_cvss", sa.Boolean(), nullable=False, server_default="false"),
    )

    # ------------------------------------------------------------------
    # 2. source_categories table
    # ------------------------------------------------------------------
    op.create_table(
        "source_categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("feed_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category_label", sa.String(255), nullable=False),
        sa.Column("ingest", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("priority_modifier", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("classified_by", sa.String(20), nullable=False),
        sa.Column("classification_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("source_id", "category_label", name="uq_source_categories"),
    )

    # ------------------------------------------------------------------
    # 3. Seed credibility weights for known high-signal sources
    # ------------------------------------------------------------------
    op.execute(
        "UPDATE feed_sources SET credibility_weight = 1.5 "
        "WHERE name IN ('CISA Advisories', 'CISA News')"
    )
    op.execute(
        "UPDATE feed_sources SET credibility_weight = 1.2 "
        "WHERE name IN ('Krebs on Security', 'Schneier on Security')"
    )
    op.execute(
        "UPDATE feed_sources SET extract_cves = true, extract_cvss = true "
        "WHERE name = 'CISA Advisories'"
    )


def downgrade() -> None:
    op.drop_table("source_categories")
    op.drop_column("feed_sources", "extract_cvss")
    op.drop_column("feed_sources", "extract_cves")
    op.drop_column("feed_sources", "credibility_weight")
```

- [ ] **Step 5: Run the migration**

```bash
docker compose exec ingestion alembic upgrade head
```

Expected output ends with: `Running upgrade e5f6a7b8c9d0 -> f6a7b8c9d0e1, Add source intelligence: ...`

- [ ] **Step 6: Verify columns exist**

```bash
docker compose exec postgres psql -U kiber -d kiber -c "\d feed_sources"
```

Expected: `credibility_weight`, `extract_cves`, `extract_cvss` columns present.

```bash
docker compose exec postgres psql -U kiber -d kiber -c "\d source_categories"
```

Expected: table with all 8 columns and unique constraint.

- [ ] **Step 7: Run import test**

```bash
docker compose exec ingestion pytest tests/test_normalizer.py::test_source_category_model_imports -v
```

Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add app/db/models/source_category.py app/db/models/feed_source.py alembic/versions/f6a7b8c9d0e1_add_source_intelligence.py tests/test_normalizer.py
git commit -m "feat(schema): add source credibility fields and source_categories table"
```

---

## Task 2: OpenSearch Mapping Additions

**Files:**
- Modify: `app/db/opensearch.py`

- [ ] **Step 1: Add `credibility_weight` to NEWS_MAPPING**

In `app/db/opensearch.py`, inside `NEWS_MAPPING["mappings"]["properties"]`, add after `"cve_ids"`:

```python
            "credibility_weight": {"type": "half_float"},
```

- [ ] **Step 2: Add `max_credibility_weight` to CLUSTERS_MAPPING**

In `_CLUSTERS_MAPPING["mappings"]["properties"]`, add after `"max_cvss"`:

```python
            "max_credibility_weight": {"type": "half_float"},
```

- [ ] **Step 3: Apply the mapping update**

```bash
docker compose exec ingestion python -c "
import asyncio
from app.db.opensearch import ensure_indexes
asyncio.run(ensure_indexes())
print('Done')
"
```

Expected: `Updated mapping for index: news_articles` and `Updated mapping for index: clusters`

- [ ] **Step 4: Commit**

```bash
git add app/db/opensearch.py
git commit -m "feat(opensearch): add credibility_weight and max_credibility_weight to mappings"
```

---

## Task 3: FeedSource TypedDict Update

**Files:**
- Modify: `app/ingestion/sources.py`

- [ ] **Step 1: Add NotRequired fields to FeedSource TypedDict**

In `app/ingestion/sources.py`, update the imports and TypedDict:

```python
from typing import NotRequired, Optional, TypedDict


class FeedSource(TypedDict):
    name: str
    url: str
    default_type: str       # news | analysis | report | advisory | alert
    default_category: str   # research | deep-dives | beginner | dark-web | breaking
    default_severity: Optional[str]
    normalizer: str         # key into NORMALIZER_REGISTRY in normalizer.py
    credibility_weight: NotRequired[float]   # score multiplier; default 1.0
    extract_cves: NotRequired[bool]          # extract CVE IDs from advisory HTML
    extract_cvss: NotRequired[bool]          # extract CVSS score from advisory HTML
```

No changes needed to `SEED_SOURCES` — the new fields are `NotRequired` and will use defaults from `normalize_article()` when absent.

- [ ] **Step 2: Verify existing tests still pass**

```bash
docker compose exec ingestion pytest tests/test_normalizer.py -v
```

Expected: all existing tests PASS (TypedDict changes are purely annotations).

- [ ] **Step 3: Commit**

```bash
git add app/ingestion/sources.py
git commit -m "feat(sources): add credibility_weight, extract_cves, extract_cvss to FeedSource TypedDict"
```

---

## Task 4: Normalizer Refactor

**Files:**
- Modify: `app/ingestion/normalizer.py`
- Modify: `tests/test_normalizer.py`

- [ ] **Step 1: Write the failing tests first**

Add this class to `tests/test_normalizer.py`:

```python
from app.ingestion.normalizer import normalize_article


class TestNormalizeArticle:
    def _make_source(self, **overrides) -> dict:
        defaults = {
            "name": "TestFeed",
            "url": "https://example.com/feed",
            "default_type": "news",
            "default_category": "breaking",
            "default_severity": None,
            "normalizer": "generic",
            "credibility_weight": 1.0,
            "extract_cves": False,
            "extract_cvss": False,
        }
        defaults.update(overrides)
        return defaults

    def test_returns_article_with_credibility_weight(self):
        entry = {
            "title": "Test Article",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "text",
        }
        result = normalize_article(entry, self._make_source(credibility_weight=1.5))
        assert result is not None
        assert result["credibility_weight"] == 1.5

    def test_credibility_weight_defaults_to_1(self):
        source = self._make_source()
        source.pop("credibility_weight")
        entry = {
            "title": "Test Article",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "text",
        }
        result = normalize_article(entry, source)
        assert result is not None
        assert result["credibility_weight"] == 1.0

    def test_does_not_extract_cves_when_flag_false(self):
        entry = {
            "title": "Patch for CVE-2026-1234",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "<p>Fixes CVE-2026-1234</p>",
        }
        result = normalize_article(entry, self._make_source(extract_cves=False))
        assert result is not None
        assert result.get("cve_ids") in ([], None)

    def test_extracts_cves_when_flag_true(self):
        entry = {
            "title": "Advisory",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "<p>Fixes CVE-2026-1234 and CVE-2026-5678</p>",
        }
        result = normalize_article(entry, self._make_source(extract_cves=True))
        assert result is not None
        assert "CVE-2026-1234" in result["cve_ids"]
        assert "CVE-2026-5678" in result["cve_ids"]

    def test_does_not_extract_cvss_when_flag_false(self):
        entry = {
            "title": "Advisory",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "Base Score: 9.8",
        }
        result = normalize_article(entry, self._make_source(extract_cvss=False))
        assert result is not None
        assert result.get("cvss_score") is None

    def test_extracts_cvss_when_flag_true(self):
        entry = {
            "title": "Advisory",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "CVSS v3.1 Base Score: 9.8",
        }
        result = normalize_article(entry, self._make_source(extract_cvss=True))
        assert result is not None
        assert float(result["cvss_score"]) == 9.8

    def test_extracts_advisory_id_into_raw_metadata_when_flag_true(self):
        entry = {
            "title": "Advisory",
            "link": "https://www.cisa.gov/advisories/aa25-099A",
            "id": "https://www.cisa.gov/advisories/aa25-099A",
            "summary": "content",
        }
        result = normalize_article(entry, self._make_source(extract_cvss=True))
        assert result is not None
        assert result.get("raw_metadata", {}).get("advisory_id") == "AA25-099A"

    def test_returns_none_for_missing_title(self):
        entry = {"link": "https://example.com/article"}
        assert normalize_article(entry, self._make_source()) is None

    def test_returns_none_for_missing_link(self):
        entry = {"title": "Test"}
        assert normalize_article(entry, self._make_source()) is None
```

Also update `TestNormalizerRegistry`:

```python
class TestNormalizerRegistry:
    def test_all_keys_present(self):
        expected = {
            "generic", "thn", "bleepingcomputer", "securityweek",
            "krebs", "cisa_news", "cisa_advisory",
        }
        assert set(NORMALIZER_REGISTRY.keys()) == expected

    def test_flag_entries_are_dicts(self):
        for key in ("generic", "thn", "bleepingcomputer", "securityweek", "krebs", "cisa_advisory"):
            assert isinstance(NORMALIZER_REGISTRY[key], dict), f"{key} must be a dict"

    def test_cisa_advisory_has_extraction_flags(self):
        flags = NORMALIZER_REGISTRY["cisa_advisory"]
        assert flags.get("extract_cves") is True
        assert flags.get("extract_cvss") is True

    def test_cisa_news_has_handler(self):
        assert "_handler" in NORMALIZER_REGISTRY["cisa_news"]
        assert callable(NORMALIZER_REGISTRY["cisa_news"]["_handler"])

    def test_generic_flags_are_empty(self):
        for key in ("generic", "thn", "bleepingcomputer", "securityweek", "krebs"):
            assert NORMALIZER_REGISTRY[key] == {}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec ingestion pytest tests/test_normalizer.py::TestNormalizeArticle tests/test_normalizer.py::TestNormalizerRegistry -v
```

Expected: FAIL with `ImportError: cannot import name 'normalize_article'` for the first class, and assertion failures for the second.

- [ ] **Step 3: Add normalize_article() to normalizer.py**

In `app/ingestion/normalizer.py`, add this function after `normalize_cisa_advisory` and before the registry:

```python
def normalize_article(
    entry: feedparser.FeedParserDict,
    source: dict,
) -> Optional[NormalizedArticle]:
    """Config-driven normalizer — reads extract_cves/extract_cvss flags from source dict.

    Replaces the per-source class hierarchy for all sources except cisa_news
    (which uses its own minimal function due to that feed's empty content).
    """
    title = (entry.get("title") or "").strip()
    link = (entry.get("link") or "").strip()

    if not title or not link:
        return None

    guid = (entry.get("id") or link).strip()

    # Content body: prefer content:encoded / Atom <content>, fall back to summary
    content_list = entry.get("content") or []
    content_value = (content_list[0].get("value") if content_list else "") or ""
    raw_desc = entry.get("summary") or entry.get("description") or ""

    if content_value:
        content_html = content_value or None
        desc_text = (
            _strip_wp_footer(strip_html(raw_desc).strip())
            or strip_html(content_value).strip()
            or title
        )
        summary_text = _strip_wp_footer(strip_html(content_value).strip())[:2000] or None
    elif raw_desc:
        content_html = raw_desc or None
        desc_text = _strip_wp_footer(strip_html(raw_desc).strip()) or title
        summary_text = _strip_wp_footer(strip_html(raw_desc).strip())[:2000] or None
    else:
        content_html = None
        desc_text = title
        summary_text = None

    tags = _extract_tags(entry)
    image_url = _extract_image_url(entry, content_html)

    article = NormalizedArticle(
        slug=build_slug(title, guid),
        guid=guid,
        source_name=source["name"],
        title=title[:500],
        author=(entry.get("author") or "").strip() or None,
        desc=desc_text,
        content_html=content_html,
        summary=summary_text,
        content_source="rss" if content_html else None,
        image_url=image_url[:2048] if image_url else None,
        tags=tags,
        keywords=[],
        published_at=_parse_date(entry),
        severity=source["default_severity"],
        type=source["default_type"],
        category=source["default_category"],
        source_url=link[:2048],
        cve_ids=[],
        credibility_weight=source.get("credibility_weight", 1.0),
    )

    # Conditional: extract CVE IDs from advisory content
    if source.get("extract_cves"):
        tag_text = " ".join(tags)
        cve_source = f"{title} {content_html or ''} {tag_text}"
        article["cve_ids"] = _extract_cve_ids(cve_source)

    # Conditional: extract CVSS score + advisory metadata
    if source.get("extract_cvss"):
        cvss = _extract_cvss_score(content_html or "")
        if cvss is not None:
            article["cvss_score"] = cvss
        raw_metadata: dict = {}
        advisory_id = _extract_advisory_id(link)
        if advisory_id:
            raw_metadata["advisory_id"] = advisory_id
        cvss_vector = _extract_cvss_vector(content_html or "")
        if cvss_vector:
            raw_metadata["cvss_vector"] = cvss_vector
        if raw_metadata:
            article["raw_metadata"] = raw_metadata

    return article
```

- [ ] **Step 4: Update NORMALIZER_REGISTRY to use flag dicts**

Replace the existing `NORMALIZER_REGISTRY` at the bottom of `normalizer.py`:

```python
# Maps normalizer key → flag overrides passed to normalize_article().
# "_handler" key means: use this callable directly instead of normalize_article().
# Ingester dispatch: if "_handler" present → call handler(entry, source);
#                    otherwise → normalize_article(entry, {**source, **flags})
NORMALIZER_REGISTRY: dict[str, dict] = {
    "generic":          {},
    "thn":              {},
    "bleepingcomputer": {},
    "securityweek":     {},
    "krebs":            {},
    "cisa_advisory":    {"extract_cves": True, "extract_cvss": True},
    "cisa_news":        {"_handler": normalize_cisa_news},
}
```

- [ ] **Step 5: Run tests**

```bash
docker compose exec ingestion pytest tests/test_normalizer.py::TestNormalizeArticle tests/test_normalizer.py::TestNormalizerRegistry -v
```

Expected: all PASS

- [ ] **Step 6: Run the full normalizer test suite**

```bash
docker compose exec ingestion pytest tests/test_normalizer.py -v
```

Expected: all existing tests still PASS (we didn't remove `normalize_generic`, `normalize_cisa_advisory`, or `normalize_cisa_news`).

- [ ] **Step 7: Commit**

```bash
git add app/ingestion/normalizer.py tests/test_normalizer.py
git commit -m "feat(normalizer): add config-driven normalize_article() and refactor registry to flag dicts"
```

---

## Task 5: Scorer Credibility Factor

**Files:**
- Modify: `app/ingestion/scorer.py`
- Create: `tests/test_scorer.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scorer.py`:

```python
from app.ingestion.scorer import compute_cluster_score


class TestCredibilityFactor:
    """Source credibility adds 0-15 pts based on max_credibility_weight."""

    def _base_kwargs(self, **overrides) -> dict:
        defaults = {
            "article_count": 1,
            "max_cvss": None,
            "cve_count": 0,
            "entity_keys": [],
            "state": "new",
            "latest_at": "2026-04-23T00:00:00+00:00",
            "max_credibility_weight": 1.0,
        }
        defaults.update(overrides)
        return defaults

    def test_high_credibility_source_scores_15(self):
        result = compute_cluster_score(**self._base_kwargs(max_credibility_weight=1.5))
        cred = next(f for f in result["top_factors"] if f["factor"] == "source_credibility")
        assert cred["points"] == 15.0

    def test_medium_credibility_scores_10(self):
        result = compute_cluster_score(**self._base_kwargs(max_credibility_weight=1.2))
        cred = next(f for f in result["top_factors"] if f["factor"] == "source_credibility")
        assert cred["points"] == 10.0

    def test_default_credibility_scores_5(self):
        result = compute_cluster_score(**self._base_kwargs(max_credibility_weight=1.0))
        cred = next(f for f in result["top_factors"] if f["factor"] == "source_credibility")
        assert cred["points"] == 5.0

    def test_low_credibility_scores_0(self):
        result = compute_cluster_score(**self._base_kwargs(max_credibility_weight=0.5))
        cred = next(f for f in result["top_factors"] if f["factor"] == "source_credibility")
        assert cred["points"] == 0.0

    def test_defaults_to_5_when_param_omitted(self):
        """max_credibility_weight has a default of 1.0 so existing callers are unaffected."""
        result = compute_cluster_score(
            article_count=1,
            max_cvss=None,
            cve_count=0,
            entity_keys=[],
            state="new",
            latest_at="2026-04-23T00:00:00+00:00",
        )
        cred = next(f for f in result["top_factors"] if f["factor"] == "source_credibility")
        assert cred["points"] == 5.0

    def test_score_capped_at_100(self):
        """Max possible score (all factors maxed) must not exceed 100."""
        result = compute_cluster_score(
            article_count=10,
            max_cvss=10.0,
            cve_count=5,
            entity_keys=["e1", "e2", "e3", "e4", "e5"],
            state="confirmed",
            latest_at="2026-04-23T00:00:00+00:00",
            max_credibility_weight=1.5,
        )
        assert result["score"] <= 100.0

    def test_credibility_factor_in_top_factors(self):
        result = compute_cluster_score(**self._base_kwargs(max_credibility_weight=1.5))
        factor_names = [f["factor"] for f in result["top_factors"]]
        assert "source_credibility" in factor_names
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec ingestion pytest tests/test_scorer.py -v
```

Expected: FAIL with `TypeError: compute_cluster_score() got an unexpected keyword argument 'max_credibility_weight'`

- [ ] **Step 3: Add the credibility factor to compute_cluster_score()**

In `app/ingestion/scorer.py`, update the function signature and add the factor. 

Change the module docstring header line 8 to:
```
  6. Source credibility — max credibility_weight of member articles (0-15 pts)
```

Update the function signature:

```python
def compute_cluster_score(
    *,
    article_count: int,
    max_cvss: Optional[float],
    cve_count: int,
    entity_keys: list[str],
    state: str,
    latest_at: str,
    max_credibility_weight: float = 1.0,
) -> dict:
```

Add the new factor block after the State bonus block (before the `# Finalise` section):

```python
    # ------------------------------------------------------------------
    # 6. Source credibility component (0-15 pts)
    # ------------------------------------------------------------------
    if max_credibility_weight >= 1.5:
        cred_pts = 15.0
    elif max_credibility_weight >= 1.2:
        cred_pts = 10.0
    elif max_credibility_weight >= 1.0:
        cred_pts = 5.0
    else:
        cred_pts = 0.0
    factors.append({
        "factor": "source_credibility",
        "label": f"Source weight {max_credibility_weight:.1f}",
        "points": cred_pts,
    })
    total += cred_pts
```

Also update `rescore_cluster()` to pass the new param. Find the `compute_cluster_score(` call inside `rescore_cluster` and add:

```python
        max_credibility_weight=float(src.get("max_credibility_weight") or 1.0),
```

- [ ] **Step 4: Run tests**

```bash
docker compose exec ingestion pytest tests/test_scorer.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/scorer.py tests/test_scorer.py
git commit -m "feat(scorer): add source credibility factor (0-15 pts) to compute_cluster_score"
```

---

## Task 6: Clusterer — Track max_credibility_weight

**Files:**
- Modify: `app/ingestion/clusterer.py`

- [ ] **Step 1: Update create_cluster() to accept and store credibility_weight**

In `app/ingestion/clusterer.py`, update the `create_cluster()` signature:

```python
async def create_cluster(
    article: NormalizedArticle,
    entity_keys: list[str],
    credibility_weight: float = 1.0,
) -> str:
```

Inside `create_cluster()`, add `max_credibility_weight` to the `score_data` call and the `doc`:

```python
    score_data = compute_cluster_score(
        article_count=1,
        max_cvss=max_cvss,
        cve_count=len(cve_ids),
        entity_keys=entity_keys,
        state="new",
        latest_at=now,
        max_credibility_weight=credibility_weight,
    )

    doc = {
        ...  # existing fields
        "max_credibility_weight": credibility_weight,
        ...
    }
```

The full updated `doc` dict in `create_cluster()` should be (replace the existing one entirely):

```python
    doc = {
        "label": article.get("title", ""),
        "state": "new",
        "summary": article.get("summary") or article.get("desc"),
        "why_it_matters": None,
        "score": score_data["score"],
        "confidence": score_data["confidence"],
        "top_factors": score_data["top_factors"],
        "max_cvss": max_cvss,
        "max_credibility_weight": credibility_weight,
        "article_ids": [slug],
        "article_count": 1,
        "cve_ids": cve_ids,
        "seed_cve_ids": cve_ids,
        "entity_keys": entity_keys,
        "categories": [article["category"]] if article.get("category") else [],
        "tags": article.get("tags") or [],
        "timeline": [{
            "article_slug": slug,
            "source_name": article.get("source_name", ""),
            "title": article.get("title", ""),
            "published_at": article.get("published_at", now),
            "added_at": now,
        }],
        "latest_at": now,
        "created_at": now,
        "updated_at": now,
    }
```

- [ ] **Step 2: Update merge_into_cluster() to track max credibility_weight**

Update the `merge_into_cluster()` signature:

```python
async def merge_into_cluster(
    cluster_id: str,
    article_slug: str,
    entity_keys: list[str],
    cve_ids: list[str],
    *,
    source_name: str = "",
    title: str = "",
    published_at: str = "",
    cvss_score: Optional[float] = None,
    credibility_weight: float = 1.0,
) -> None:
```

Inside the Painless script (the big `script = """..."""` string), add this after the `max_cvss` tracking block and before `ctx._source.latest_at = params.now;`:

```javascript
        // Track max credibility_weight seen across all member articles
        if (ctx._source.max_credibility_weight == null || params.credibility_weight > ctx._source.max_credibility_weight) {
            ctx._source.max_credibility_weight = params.credibility_weight;
        }
```

Add `credibility_weight` to the script params dict:

```python
                "params": {
                    "slug": article_slug,
                    "source_name": source_name,
                    "title": title,
                    "published_at": published_at,
                    "entity_keys": entity_keys,
                    "cve_ids": cve_ids,
                    "cvss_score": cvss_score,
                    "credibility_weight": credibility_weight,
                    "now": now,
                },
```

- [ ] **Step 3: Update cluster_article() to pass credibility_weight**

In `cluster_article()`, extract credibility_weight from the article before the if/else:

```python
    credibility_weight = float(article.get("credibility_weight") or 1.0)
```

Update the `merge_into_cluster` call (inside the `if cluster_id:` block):

```python
        await merge_into_cluster(
            cluster_id, slug, entity_keys, cve_ids,
            source_name=article.get("source_name", ""),
            title=article.get("title", ""),
            published_at=article.get("published_at", ""),
            cvss_score=float(raw_cvss) if raw_cvss is not None else None,
            credibility_weight=credibility_weight,
        )
```

Update the `create_cluster` call (the `else` branch):

```python
        await create_cluster(article, entity_keys, credibility_weight)
```

- [ ] **Step 4: Verify existing clusterer tests pass**

```bash
docker compose exec ingestion pytest tests/test_clusterer.py -v
```

Expected: all PASS (clusterer tests mock OpenSearch calls, signature changes are backward-compatible with defaults).

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/clusterer.py
git commit -m "feat(clusterer): track max_credibility_weight in cluster documents"
```

---

## Task 7: Ingester Wiring

**Files:**
- Modify: `app/ingestion/ingester.py`

- [ ] **Step 1: Add the category-loading helper**

In `app/ingestion/ingester.py`, add these imports at the top:

```python
from app.db.models.source_category import SourceCategory
```

After the `mark_source_failure` function, add:

```python
async def get_source_category_map(
    session: AsyncSession, source_id: int
) -> dict[str, dict]:
    """Return {category_label: {ingest, priority_modifier}} for one source.

    Returns empty dict if no categories are classified yet (allow-all default).
    """
    from sqlalchemy import select as sa_select
    result = await session.execute(
        sa_select(SourceCategory).where(SourceCategory.source_id == source_id)
    )
    rows = result.scalars().all()
    return {
        row.category_label: {
            "ingest": row.ingest,
            "priority_modifier": row.priority_modifier,
        }
        for row in rows
    }
```

- [ ] **Step 2: Add article-level category filter helper**

After `get_source_category_map`, add:

```python
def _effective_credibility(
    article_tags: list[str],
    base_weight: float,
    category_map: dict[str, dict],
) -> tuple[float, bool]:
    """Return (effective_weight, should_ingest) based on article category tags.

    - If any tag maps to ingest=False, returns (base_weight, False).
    - Otherwise returns (base_weight + sum(priority_modifiers), True).
    Unknown tags (not in category_map) default to ingest=True, modifier=0.
    """
    total_modifier = 0.0
    for tag in article_tags:
        decision = category_map.get(tag)
        if decision is None:
            continue
        if not decision["ingest"]:
            return base_weight, False
        total_modifier += decision["priority_modifier"]
    return base_weight + total_modifier, True
```

- [ ] **Step 3: Update ingest_source() to accept category_map and wire it**

Update the `ingest_source` signature to accept `category_map`:

```python
async def ingest_source(
    source: FeedSource,
    client: httpx.AsyncClient,
    *,
    update: bool = False,
    category_map: dict[str, dict] | None = None,
) -> dict:
```

Replace the normalizer dispatch block inside the entry-processing loop:

```python
    # Old code (remove):
    normalizer_fn = NORMALIZER_REGISTRY.get(source["normalizer"])
    if normalizer_fn is None:
        logger.error("[%s] Unknown normalizer '%s' — skipping.", name, source["normalizer"])
        return stats

    for entry in entries:
        try:
            article = normalizer_fn(entry, source)
```

New code:

```python
    from app.ingestion.normalizer import normalize_article

    flags = NORMALIZER_REGISTRY.get(source["normalizer"])
    if flags is None:
        logger.error("[%s] Unknown normalizer '%s' — skipping.", name, source["normalizer"])
        return stats

    _cat_map = category_map or {}

    for entry in entries:
        try:
            handler = flags.get("_handler")
            if handler:
                article = handler(entry, source)
            else:
                merged_source = {**source, **{k: v for k, v in flags.items() if not k.startswith("_")}}
                article = normalize_article(entry, merged_source)
```

After `if article is None:` check and before `inserted = ...`, add category filtering:

```python
            # Category filter: check article tags against source_categories
            if _cat_map:
                article_tags = article.get("tags") or []
                effective_weight, should_ingest = _effective_credibility(
                    article_tags,
                    source.get("credibility_weight", 1.0),
                    _cat_map,
                )
                if not should_ingest:
                    logger.debug(
                        "[%s] Skipped entry (category filtered): %s",
                        name, article.get("title", "<no title>"),
                    )
                    stats["skipped"] += 1
                    continue
                article["credibility_weight"] = effective_weight
            else:
                article["credibility_weight"] = source.get("credibility_weight", 1.0)
```

- [ ] **Step 4: Update _ingest_one() to load categories from DB**

In `_ingest_one()`, update to load categories before calling `ingest_source`:

```python
async def _ingest_one(src, client: httpx.AsyncClient, *, update: bool = False) -> None:
    """Ingest a single source and update its operational state."""
    source_dict = src.to_source_dict()

    # Load category decisions for this source
    category_map: dict[str, dict] = {}
    try:
        async with AsyncSessionLocal() as session:
            category_map = await get_source_category_map(session, src.id)
        if category_map:
            logger.debug("[%s] Loaded %d category rules", src.name, len(category_map))
    except Exception:
        logger.exception("[%s] Failed to load category map — proceeding without filtering", src.name)

    try:
        stats = await ingest_source(source_dict, client, update=update, category_map=category_map)
        logger.info(
            "[%s] Done - fetched=%d inserted=%d skipped=%d errors=%d",
            src.name,
            stats["fetched"], stats["inserted"],
            stats["skipped"], stats["errors"],
        )
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await mark_source_success(session, src.id)
    except Exception:
        logger.exception("Fatal error ingesting '%s'", src.name)
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    await mark_source_failure(session, src.id)
        except Exception:
            logger.exception("Failed to record failure for '%s'", src.name)
```

- [ ] **Step 5: Verify ingester tests pass**

```bash
docker compose exec ingestion pytest tests/test_ingester.py -v
```

Expected: all PASS (the `_prepare_article_doc` tests don't touch the new category path).

- [ ] **Step 6: Run a full ingestion dry-run to verify no errors**

```bash
docker compose exec ingestion python scripts/ingest_feeds.py 2>&1 | head -60
```

Expected: ingestion completes without `KeyError` or `TypeError`. Some sources may fail due to network, that's fine.

- [ ] **Step 7: Commit**

```bash
git add app/ingestion/ingester.py
git commit -m "feat(ingester): wire category filtering and credibility_weight injection"
```

---

## Task 8: Category Classifier Module

**Files:**
- Create: `app/ingestion/category_classifier.py`
- Create: `scripts/classify_source_categories.py`
- Create: `tests/test_category_classifier.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_category_classifier.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch
from app.ingestion.category_classifier import CategoryDecision, classify_categories


class TestCategoryDecision:
    def test_dataclass_fields(self):
        d = CategoryDecision(
            label="ransomware",
            ingest=True,
            priority_modifier=0.2,
            notes="High-signal cybersecurity category",
        )
        assert d.label == "ransomware"
        assert d.ingest is True
        assert d.priority_modifier == 0.2
        assert d.notes == "High-signal cybersecurity category"


class TestClassifyCategories:
    @pytest.mark.asyncio
    async def test_returns_list_of_decisions(self):
        mock_response = [
            {"label": "ransomware", "ingest": True, "priority_modifier": 0.2, "notes": "high signal"},
            {"label": "sponsored", "ingest": False, "priority_modifier": 0.0, "notes": "paid content"},
        ]
        with patch(
            "app.ingestion.category_classifier._call_llm",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await classify_categories("TestFeed", ["ransomware", "sponsored"])

        assert len(result) == 2
        assert result[0].label == "ransomware"
        assert result[0].ingest is True
        assert result[1].label == "sponsored"
        assert result[1].ingest is False

    @pytest.mark.asyncio
    async def test_returns_allow_all_on_llm_failure(self):
        """If LLM call fails, every label defaults to ingest=True, modifier=0."""
        with patch(
            "app.ingestion.category_classifier._call_llm",
            new_callable=AsyncMock,
            side_effect=Exception("LLM unavailable"),
        ):
            result = await classify_categories("TestFeed", ["ransomware", "news"])

        assert all(d.ingest is True for d in result)
        assert all(d.priority_modifier == 0.0 for d in result)

    @pytest.mark.asyncio
    async def test_empty_labels_returns_empty_list(self):
        result = await classify_categories("TestFeed", [])
        assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec ingestion pytest tests/test_category_classifier.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create category_classifier.py**

Create `app/ingestion/category_classifier.py`:

```python
"""LLM-backed category classification for RSS feed categories.

Classifies RSS <category> labels into ingest decisions (allow/block) and
priority modifiers (-0.5 to +0.5) for a cybersecurity news platform.

LLM backend: Ollama (default model: llama3). Override with OLLAMA_MODEL env var.
Requires Ollama running at OLLAMA_URL (default: http://localhost:11434).
"""
import json
import logging
import os
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")

_SYSTEM_PROMPT = """You are a content classifier for a cybersecurity news intelligence platform.
Given a source name and a list of RSS category labels, decide for each label:
1. ingest (true/false): should articles with this category label be ingested?
   - true: label is relevant to cybersecurity news, analysis, or research
   - false: label is irrelevant (sponsored content, lifestyle, job postings, weekly digests not specific to security)
2. priority_modifier (float, -0.5 to +0.5): scoring adjustment for this category
   - +0.2 to +0.5: high-signal categories (ransomware, zero-day, critical vulnerability, threat actor)
   - 0.0: neutral/standard categories (news, updates, security)
   - -0.2 to -0.5: low-signal categories (opinion, generic tech, marketing)
3. notes: one-sentence explanation

Respond ONLY with a JSON array matching this schema:
[{"label": "...", "ingest": true/false, "priority_modifier": 0.0, "notes": "..."}]
"""


@dataclass
class CategoryDecision:
    label: str
    ingest: bool
    priority_modifier: float
    notes: str


async def _call_llm(source_name: str, labels: list[str]) -> list[dict]:
    """Call Ollama and return parsed JSON list."""
    user_msg = f"Source: {source_name}\nCategories: {json.dumps(labels)}"
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
        "format": "json",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
        raw = data["message"]["content"]
        return json.loads(raw)


async def classify_categories(
    source_name: str,
    category_labels: list[str],
) -> list[CategoryDecision]:
    """Classify RSS category labels via LLM. Returns one decision per label.

    Falls back to ingest=True, modifier=0.0 for all labels if LLM fails.
    """
    if not category_labels:
        return []

    try:
        raw_decisions = await _call_llm(source_name, category_labels)
        decisions = []
        label_set = set(category_labels)
        for item in raw_decisions:
            label = item.get("label", "")
            if label not in label_set:
                continue
            decisions.append(CategoryDecision(
                label=label,
                ingest=bool(item.get("ingest", True)),
                priority_modifier=float(item.get("priority_modifier", 0.0)),
                notes=str(item.get("notes", "")),
            ))
        # For any label the LLM didn't return, default to allow
        returned_labels = {d.label for d in decisions}
        for label in category_labels:
            if label not in returned_labels:
                logger.warning("LLM did not return decision for label '%s' — defaulting to allow", label)
                decisions.append(CategoryDecision(label=label, ingest=True, priority_modifier=0.0, notes="default"))
        return decisions
    except Exception:
        logger.exception("LLM classification failed for source '%s' — defaulting all to allow", source_name)
        return [
            CategoryDecision(label=lbl, ingest=True, priority_modifier=0.0, notes="llm_error")
            for lbl in category_labels
        ]
```

- [ ] **Step 4: Run tests**

```bash
docker compose exec ingestion pytest tests/test_category_classifier.py -v
```

Expected: all PASS

- [ ] **Step 5: Create the bulk classification script**

Create `scripts/classify_source_categories.py`:

```python
#!/usr/bin/env python
"""Classify RSS category labels for all active sources via local LLM.

For each source, collects distinct category labels seen in raw_feed_snapshots
(last 30 days), classifies them via the LLM, and upserts into source_categories.

Usage:
    python scripts/classify_source_categories.py
    python scripts/classify_source_categories.py --source "CISA Advisories"
    python scripts/classify_source_categories.py --dry-run

Requires Ollama running locally. Set OLLAMA_URL and OLLAMA_MODEL env vars
to override defaults (http://localhost:11434, llama3).
"""
import asyncio
import argparse
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models.feed_source import FeedSource as FeedSourceModel
from app.db.models.source_category import SourceCategory
from app.db.opensearch import INDEX_SNAPSHOTS, get_os_client
from app.db.session import AsyncSessionLocal
from app.ingestion.category_classifier import classify_categories

logger = logging.getLogger(__name__)


async def _collect_categories_from_snapshots(source_name: str, days: int = 30) -> list[str]:
    """Collect distinct RSS category labels from stored raw snapshots.

    Parses raw feed XML from OpenSearch snapshots to extract <category> tags.
    Falls back to empty list if no snapshots exist for the source.
    """
    import feedparser

    client = get_os_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    resp = await client.search(
        index=INDEX_SNAPSHOTS,
        body={
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"source_name": source_name}},
                        {"range": {"fetched_at": {"gte": cutoff}}},
                    ]
                }
            },
            "sort": [{"fetched_at": {"order": "desc"}}],
            "size": 5,
            "_source": ["raw_content"],
        },
    )

    category_set: set[str] = set()
    for hit in resp["hits"]["hits"]:
        raw = hit["_source"].get("raw_content", "")
        if not raw:
            continue
        feed = feedparser.parse(raw)
        for entry in feed.get("entries", []):
            for tag in entry.get("tags", []):
                term = (tag.get("term") or "").strip()
                if term:
                    category_set.add(term)

    return sorted(category_set)


async def classify_source(
    source: FeedSourceModel,
    dry_run: bool,
    session,
) -> int:
    """Classify categories for one source. Returns number of decisions written."""
    labels = await _collect_categories_from_snapshots(source.name)

    if not labels:
        logger.info("[%s] No category labels found in snapshots — skipping", source.name)
        return 0

    logger.info("[%s] Classifying %d labels: %s", source.name, len(labels), labels[:10])

    decisions = await classify_categories(source.name, labels)

    if dry_run:
        for d in decisions:
            logger.info(
                "[%s] DRY-RUN: label=%r ingest=%s modifier=%.1f notes=%s",
                source.name, d.label, d.ingest, d.priority_modifier, d.notes,
            )
        return len(decisions)

    # Upsert into source_categories
    for decision in decisions:
        stmt = pg_insert(SourceCategory).values(
            source_id=source.id,
            category_label=decision.label,
            ingest=decision.ingest,
            priority_modifier=decision.priority_modifier,
            classified_by="llm",
            classification_notes=decision.notes,
        ).on_conflict_do_update(
            constraint="uq_source_categories",
            set_={
                "ingest": decision.ingest,
                "priority_modifier": decision.priority_modifier,
                "classified_by": "llm",
                "classification_notes": decision.notes,
            },
        )
        await session.execute(stmt)

    await session.commit()
    logger.info("[%s] Wrote %d category decisions", source.name, len(decisions))
    return len(decisions)


async def run(source_filter: str | None, dry_run: bool) -> None:
    async with AsyncSessionLocal() as session:
        query = select(FeedSourceModel).where(FeedSourceModel.is_active.is_(True))
        if source_filter:
            query = query.where(FeedSourceModel.name == source_filter)
        result = await session.execute(query)
        sources = list(result.scalars().all())

    if not sources:
        logger.warning("No matching active sources found.")
        return

    logger.info("Classifying categories for %d source(s)...", len(sources))
    total = 0
    for source in sources:
        async with AsyncSessionLocal() as session:
            count = await classify_source(source, dry_run=dry_run, session=session)
            total += count

    logger.info("Done. Total decisions: %d", total)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Classify source category labels via LLM")
    parser.add_argument("--source", help="Process only this source name")
    parser.add_argument("--dry-run", action="store_true", help="Print decisions without writing")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
    )

    asyncio.run(run(args.source, args.dry_run))
```

- [ ] **Step 6: Run the full test suite**

```bash
docker compose exec ingestion pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 7: Verify the classifier script runs (dry-run)**

```bash
docker compose exec ingestion python scripts/classify_source_categories.py --dry-run 2>&1 | head -20
```

Expected: script starts, loads sources from DB, attempts to collect categories from snapshots. May print "No category labels found" if no snapshots have been stored for that source yet — this is normal.

- [ ] **Step 8: Commit**

```bash
git add app/ingestion/category_classifier.py scripts/classify_source_categories.py tests/test_category_classifier.py
git commit -m "feat(classifier): add LLM category classifier and bulk classification script"
```

---

## Final Verification

- [ ] **Rebuild and reset clusters to see credibility scores in action**

```bash
docker compose build ingestion && docker compose up -d ingestion
docker compose exec ingestion python scripts/cluster_articles.py --reset
```

Expected: script runs ~13 min. After completion, clusters in OpenSearch have `max_credibility_weight` field and `top_factors` includes `source_credibility`.

- [ ] **Spot-check a CISA cluster**

```bash
docker compose exec ingestion python -c "
import asyncio
from app.db.opensearch import INDEX_CLUSTERS, get_os_client

async def check():
    client = get_os_client()
    resp = await client.search(
        index=INDEX_CLUSTERS,
        body={
            'query': {'match': {'label': 'advisory'}},
            'size': 1,
            '_source': ['label', 'max_credibility_weight', 'score', 'top_factors'],
        }
    )
    hits = resp['hits']['hits']
    if hits:
        import json
        print(json.dumps(hits[0]['_source'], indent=2))
    else:
        print('No advisory clusters found')

asyncio.run(check())
"
```

Expected: cluster shows `max_credibility_weight: 1.5` and `top_factors` includes entry with `factor: source_credibility`.

- [ ] **Commit docs + spec together**

```bash
git add docs/superpowers/specs/2026-04-23-source-intelligence-design.md docs/superpowers/plans/2026-04-23-source-intelligence.md
git commit -m "docs: add source intelligence spec and implementation plan"
```
