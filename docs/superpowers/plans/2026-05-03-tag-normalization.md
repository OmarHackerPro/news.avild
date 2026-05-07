# Tag Normalization & Entity Extraction from RSS Tags — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn raw RSS `<category>` tags into two structured signals — a controlled topic vocabulary (`normalized_topics`) and tag-sourced entities — running concurrently with text-based entity extraction at ingestion time, exposed via the feed API's `?topic=` filter.

**Architecture:** A new `tag_classifier.py` module runs three sequential passes (junk filter → entity classifier → topic mapper) as a pure synchronous function. At ingestion time, `classify_tags()` and `extract_entities()` are dispatched concurrently via `asyncio.gather()`; their results are merged by `merge_entities()` before storing. A one-time backfill script processes existing articles using the same classifier with concurrent batches.

**Tech Stack:** Python 3.12, asyncio, FastAPI, OpenSearch (opensearch-py async), SQLAlchemy + Alembic (PostgreSQL), pytest + pytest-asyncio.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `alembic/versions/<rev>_add_junk_tags.py` | Create | Migration: add `junk_tags JSONB` to `feed_sources` |
| `app/db/models/feed_source.py` | Modify | Add `junk_tags` column + update `to_source_dict()` |
| `app/ingestion/sources.py` | Modify | Add `junk_tags` field to TypedDict + per-source values in `SEED_SOURCES` |
| `scripts/seed_sources.py` | Modify | Seed `junk_tags` column |
| `app/ingestion/tag_classifier.py` | Create | Three-layer tag classifier — junk filter, entity classifier, topic mapper |
| `app/ingestion/entity_extractor.py` | Modify | Add `merge_entities()` helper |
| `app/db/opensearch.py` | Modify | Add `raw_tags` + `normalized_topics` to `NEWS_MAPPING` |
| `app/ingestion/ingester.py` | Modify | Concurrent classify + extract; write `raw_tags`/`normalized_topics` |
| `app/models/news.py` | Modify | Add `normalized_topics: List[str]` + rename `tags` → `raw_tags` in responses |
| `app/api/routes/news.py` | Modify | Add `topic: list[str]` filter param + update field references |
| `scripts/backfill_tag_normalization.py` | Create | Concurrent backfill of existing articles |
| `tests/test_tag_classifier.py` | Create | Unit tests for all classifier functions |

---

## Task 1: DB Migration — Add `junk_tags` to `feed_sources`

**Files:**
- Create: `alembic/versions/c1d2e3f4a5b6_add_junk_tags_to_feed_sources.py`
- Modify: `app/db/models/feed_source.py`

- [ ] **Step 1: Create the Alembic migration file**

```python
# alembic/versions/c1d2e3f4a5b6_add_junk_tags_to_feed_sources.py
"""Add junk_tags JSONB to feed_sources

Revision ID: c1d2e3f4a5b6
Revises: a0b1c2d3e4f5
Create Date: 2026-05-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "a0b1c2d3e4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "feed_sources",
        sa.Column(
            "junk_tags",
            JSONB,
            nullable=False,
            server_default="'[]'::jsonb",
        ),
    )
    # Seed known blog-navigation junk per source
    op.execute("""
        UPDATE feed_sources SET junk_tags = '["news & events", "product updates", "testing and validation"]'::jsonb
        WHERE name = 'Red Canary'
    """)
    op.execute("""
        UPDATE feed_sources SET junk_tags = '["a little sunshine", "the coming storm", "ne''er-do-well news", "web fraud 2.0", "breadcrumbs"]'::jsonb
        WHERE name = 'Krebs on Security'
    """)
    op.execute("""
        UPDATE feed_sources SET junk_tags = '["uncategorized", "schneier news"]'::jsonb
        WHERE name = 'Schneier on Security'
    """)
    op.execute("""
        UPDATE feed_sources SET junk_tags = '["blog", "research (insikt)"]'::jsonb
        WHERE name = 'Recorded Future'
    """)
    op.execute("""
        UPDATE feed_sources SET junk_tags = '["full", "large", "medium", "thumbnail"]'::jsonb
        WHERE name = 'Securelist'
    """)
    op.execute("""
        UPDATE feed_sources SET junk_tags = '["my software", "update", "announcement", "beta"]'::jsonb
        WHERE name = 'Didier Stevens'
    """)
    op.execute("""
        UPDATE feed_sources SET junk_tags = '["featured", "in other news", "cybersecurity funding", "funding"]'::jsonb
        WHERE name = 'SecurityWeek'
    """)


def downgrade() -> None:
    op.drop_column("feed_sources", "junk_tags")
```

- [ ] **Step 2: Update the SQLAlchemy model to add the column and expose it in `to_source_dict()`**

In `app/db/models/feed_source.py`, add after the `extract_cvss` column and update `to_source_dict()`:

```python
# Add this import at the top of the file:
from sqlalchemy import JSON

# Add column after extract_cvss:
    junk_tags: Mapped[list] = mapped_column(
        JSON, nullable=False, server_default="[]"
    )
```

Update `to_source_dict()` to include the new field:
```python
    def to_source_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "default_type": self.default_type,
            "default_category": self.default_category,
            "default_severity": self.default_severity,
            "normalizer": self.normalizer_key,
            "credibility_weight": self.credibility_weight,
            "extract_cves": self.extract_cves,
            "extract_cvss": self.extract_cvss,
            "junk_tags": self.junk_tags or [],
        }
```

- [ ] **Step 3: Run the migration**

```bash
docker compose exec ingestion alembic upgrade head
```

Expected output ends with: `Running upgrade a0b1c2d3e4f5 -> c1d2e3f4a5b6, Add junk_tags JSONB to feed_sources`

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/c1d2e3f4a5b6_add_junk_tags_to_feed_sources.py app/db/models/feed_source.py
git commit -m "feat(db): add junk_tags JSONB column to feed_sources"
```

---

## Task 2: Update `sources.py` and `seed_sources.py`

**Files:**
- Modify: `app/ingestion/sources.py`
- Modify: `scripts/seed_sources.py`

- [ ] **Step 1: Add `junk_tags` to the `FeedSource` TypedDict in `sources.py`**

In `app/ingestion/sources.py`, add to the `FeedSource` TypedDict after `extract_cvss`:

```python
    junk_tags: NotRequired[list[str]]          # blog-nav labels to discard; default []
```

Then add `junk_tags` values to the relevant entries in `SEED_SOURCES`. Add to each entry that needs it (entries not listed here get no `junk_tags` key, defaulting to `[]`):

```python
    FeedSource(
        name="Krebs on Security",
        url="https://krebsonsecurity.com/feed/",
        default_type="analysis",
        default_category="deep-dives",
        default_severity=None,
        normalizer="krebs",
        junk_tags=["a little sunshine", "the coming storm", "ne'er-do-well news", "web fraud 2.0", "breadcrumbs"],
    ),
    FeedSource(
        name="Schneier on Security",
        url="https://www.schneier.com/feed/atom/",
        default_type="analysis",
        default_category="deep-dives",
        default_severity=None,
        normalizer="generic",
        junk_tags=["uncategorized", "schneier news"],
    ),
    FeedSource(
        name="Recorded Future",
        url="https://www.recordedfuture.com/feed",
        default_type="report",
        default_category="research",
        default_severity=None,
        normalizer="generic",
        junk_tags=["blog", "research (insikt)"],
    ),
    FeedSource(
        name="Red Canary",
        url="https://redcanary.com/blog/feed/",
        default_type="analysis",
        default_category="research",
        default_severity=None,
        normalizer="generic",
        junk_tags=["news & events", "product updates", "testing and validation"],
    ),
    FeedSource(
        name="Securelist",
        url="https://securelist.com/feed/",
        default_type="report",
        default_category="research",
        default_severity=None,
        normalizer="securelist",
        credibility_weight=1.2,
        extract_cves=True,
        junk_tags=["full", "large", "medium", "thumbnail"],
    ),
    FeedSource(
        name="Didier Stevens",
        url="https://blog.didierstevens.com/feed/atom/",
        default_type="analysis",
        default_category="research",
        default_severity=None,
        normalizer="generic",
        junk_tags=["my software", "update", "announcement", "beta"],
    ),
    FeedSource(
        name="SecurityWeek",
        url="https://www.securityweek.com/feed/",
        default_type="news",
        default_category="breaking",
        default_severity=None,
        normalizer="securityweek",
        junk_tags=["featured", "in other news"],
    ),
```

- [ ] **Step 2: Update `seed_sources.py` to include `junk_tags` in upsert rows**

In `scripts/seed_sources.py`, update the `rows` list comprehension to include `junk_tags`:

```python
    rows = [
        {
            "name": s["name"],
            "url": s["url"],
            "default_type": s["default_type"],
            "default_category": s["default_category"],
            "default_severity": s["default_severity"],
            "normalizer_key": s["normalizer"],
            "credibility_weight": s.get("credibility_weight", 1.0),
            "extract_cves": s.get("extract_cves", False),
            "extract_cvss": s.get("extract_cvss", False),
            "junk_tags": s.get("junk_tags", []),
        }
        for s in SEED_SOURCES
    ]
```

Also update the `on_conflict_do_update` `set_` dict to include `"junk_tags": row["junk_tags"]`.

- [ ] **Step 3: Rebuild and run the seeder**

```bash
docker compose build ingestion
docker compose up -d ingestion
docker compose exec ingestion python scripts/seed_sources.py
```

Expected: seeder logs upsert for all 29 sources.

- [ ] **Step 4: Commit**

```bash
git add app/ingestion/sources.py scripts/seed_sources.py
git commit -m "feat(sources): add junk_tags per source to seed data"
```

---

## Task 3: Create `tag_classifier.py`

**Files:**
- Create: `app/ingestion/tag_classifier.py`
- Create: `tests/test_tag_classifier.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tag_classifier.py
import pytest
from app.ingestion.tag_classifier import classify_tags, TOPIC_MAP


# ---------------------------------------------------------------------------
# Junk filter
# ---------------------------------------------------------------------------

def test_junk_filter_strips_image_sizes():
    result = classify_tags(["full", "large", "medium", "thumbnail", "Malware"], [])
    assert "full" not in result["clean_tags"]
    assert "large" not in result["clean_tags"]
    assert "Malware" in result["clean_tags"]


def test_junk_filter_strips_emails():
    result = classify_tags(["user@example.com", "Ransomware"], [])
    assert not any("@" in t for t in result["clean_tags"])
    assert "Ransomware" in result["clean_tags"]


def test_junk_filter_strips_numeric():
    result = classify_tags(["12345", "CVE-2026-1234"], [])
    assert "12345" not in result["clean_tags"]
    assert "CVE-2026-1234" in result["clean_tags"]


def test_junk_filter_strips_source_specific():
    result = classify_tags(["uncategorized", "schneier news", "AI"], ["uncategorized", "schneier news"])
    assert "uncategorized" not in result["clean_tags"]
    assert "schneier news" not in result["clean_tags"]
    assert "AI" in result["clean_tags"]


def test_junk_filter_case_insensitive_source_specific():
    result = classify_tags(["Uncategorized", "AI"], ["uncategorized"])
    assert "Uncategorized" not in result["clean_tags"]
    assert "AI" in result["clean_tags"]


# ---------------------------------------------------------------------------
# Entity classifier
# ---------------------------------------------------------------------------

def test_entity_classifier_cve_pattern():
    result = classify_tags(["CVE-2026-12345"], [])
    entity_keys = [e["normalized_key"] for e in result["tag_entities"]]
    assert "cve-2026-12345" in entity_keys
    entity = next(e for e in result["tag_entities"] if e["normalized_key"] == "cve-2026-12345")
    assert entity["type"] == "cve"
    assert entity["source"] == "tag"
    assert entity["sources"] == ["tag"]


def test_entity_classifier_cve_infers_vulnerability_topic():
    result = classify_tags(["CVE-2026-12345"], [])
    assert "vulnerability" in result["normalized_topics"]


def test_entity_classifier_vendor_tag():
    result = classify_tags(["Ivanti"], [])
    entity_keys = [e["normalized_key"] for e in result["tag_entities"]]
    assert "ivanti" in entity_keys
    entity = next(e for e in result["tag_entities"] if e["normalized_key"] == "ivanti")
    assert entity["type"] == "vendor"


def test_entity_classifier_vendor_does_not_infer_topic():
    result = classify_tags(["Microsoft"], [])
    assert result["normalized_topics"] == []


def test_entity_classifier_malware_family():
    result = classify_tags(["LockBit"], [])
    entity_keys = [e["normalized_key"] for e in result["tag_entities"]]
    assert "lockbit" in entity_keys
    entity = next(e for e in result["tag_entities"] if e["normalized_key"] == "lockbit")
    assert entity["type"] == "malware"
    assert "malware" in result["normalized_topics"]


def test_entity_classifier_threat_actor_infers_nation_state():
    result = classify_tags(["Volt Typhoon"], [])
    entity_keys = [e["normalized_key"] for e in result["tag_entities"]]
    assert "volt-typhoon" in entity_keys
    assert "nation-state" in result["normalized_topics"]


# ---------------------------------------------------------------------------
# Topic mapper
# ---------------------------------------------------------------------------

def test_topic_mapper_vulnerability():
    result = classify_tags(["exploited", "zero-day"], [])
    assert "vulnerability" in result["normalized_topics"]


def test_topic_mapper_ransomware_maps_to_malware():
    result = classify_tags(["Ransomware"], [])
    assert "malware" in result["normalized_topics"]


def test_topic_mapper_phishing():
    result = classify_tags(["phishing"], [])
    assert "phishing" in result["normalized_topics"]


def test_topic_mapper_supply_chain():
    result = classify_tags(["supply chain"], [])
    assert "supply-chain" in result["normalized_topics"]


def test_topic_mapper_ai_security():
    result = classify_tags(["prompt injection"], [])
    assert "ai-security" in result["normalized_topics"]


def test_topic_mapper_unknown_tag_kept_in_clean_tags():
    result = classify_tags(["some-unknown-niche-tag"], [])
    assert "some-unknown-niche-tag" in result["clean_tags"]
    assert result["normalized_topics"] == []
    assert result["tag_entities"] == []


def test_topic_mapper_deduplicates_topics():
    # Both "Ransomware" (entity→malware) and "malware" (topic map) → only one "malware"
    result = classify_tags(["Ransomware", "malware"], [])
    assert result["normalized_topics"].count("malware") == 1


def test_empty_tags_returns_empty_result():
    result = classify_tags([], [])
    assert result["normalized_topics"] == []
    assert result["tag_entities"] == []
    assert result["clean_tags"] == []
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd c:\Users\xb_admin\Desktop\Omar\Projects\kiber.info\kiber
.venv\Scripts\pytest tests/test_tag_classifier.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'app.ingestion.tag_classifier'`

- [ ] **Step 3: Implement `tag_classifier.py`**

```python
# app/ingestion/tag_classifier.py
"""Three-layer RSS tag classifier.

Runs three sequential passes over raw RSS <category> values:
  1. _filter_junk   — drops image sizes, emails, numerics, source-specific noise
  2. _classify_entities — matches vendors, malware, actors, CVEs → entity dicts
  3. _map_topics    — maps remaining tags to controlled topic vocabulary

Public API:
  classify_tags(raw_tags, source_junk_tags) -> TagClassification
"""
import re
from typing import TypedDict

from app.ingestion.entity_extractor import VENDOR_KEYWORDS, THREAT_KEYWORDS, _normalize_key

# ---------------------------------------------------------------------------
# Controlled topic vocabulary — 12 values
# ---------------------------------------------------------------------------

VALID_TOPICS = frozenset([
    "vulnerability", "malware", "data-breach", "nation-state",
    "phishing", "supply-chain", "ics-ot", "privacy",
    "ai-security", "funding", "policy-law", "cryptography",
])

# ---------------------------------------------------------------------------
# Topic mapper — lowercase tag → normalized topic
# ---------------------------------------------------------------------------

TOPIC_MAP: dict[str, str] = {
    # vulnerability
    "cve": "vulnerability",
    "vulnerabilities": "vulnerability",
    "vulnerability": "vulnerability",
    "exploited": "vulnerability",
    "zero-day": "vulnerability",
    "zero day": "vulnerability",
    "cisa kev": "vulnerability",
    "exploit": "vulnerability",
    "exploits": "vulnerability",
    "n-day": "vulnerability",
    "rce": "vulnerability",
    "remote code execution": "vulnerability",
    "patch": "vulnerability",
    "patch tuesday": "vulnerability",
    "patches": "vulnerability",
    "patching": "vulnerability",
    "unpatched": "vulnerability",
    "vulnerability disclosure": "vulnerability",
    "vulnerability management": "vulnerability",
    "vulnerability reports": "vulnerability",
    "software vulnerabilities": "vulnerability",
    "known exploited vulnerabilities (kev)": "vulnerability",
    "ics patch tuesday": "vulnerability",
    # malware
    "malware": "malware",
    "ransomware": "malware",
    "trojan": "malware",
    "spyware": "malware",
    "backdoor": "malware",
    "wiper": "malware",
    "botnet": "malware",
    "infostealer": "malware",
    "infostealers": "malware",
    "rat": "malware",
    "keyloggers": "malware",
    "malware & threats": "malware",
    "malware descriptions": "malware",
    "malware technologies": "malware",
    "windows malware": "malware",
    "mobile malware": "malware",
    "financial malware": "malware",
    "unix and macos malware": "malware",
    "macros malware": "malware",
    "worm": "malware",
    "adware": "malware",
    "crimeware": "malware",
    "malware-as-a-service": "malware",
    # data-breach
    "data breaches": "data-breach",
    "data breach": "data-breach",
    "data exfiltration": "data-breach",
    "credential harvesting": "data-breach",
    "stolen credentials": "data-breach",
    "credential theft": "data-breach",
    "stolen data": "data-breach",
    "data theft": "data-breach",
    "exfiltration": "data-breach",
    # phishing
    "phishing": "phishing",
    "spear phishing": "phishing",
    "social engineering": "phishing",
    "bec": "phishing",
    "scam": "phishing",
    "scams": "phishing",
    "phishing kit": "phishing",
    "phishing websites": "phishing",
    "spam and phishing": "phishing",
    # supply-chain
    "supply chain": "supply-chain",
    "supply chain security": "supply-chain",
    "supply chain attack": "supply-chain",
    "supply chain attacks": "supply-chain",
    "supply-chain attack": "supply-chain",
    "open source software": "supply-chain",
    "dependency confusion": "supply-chain",
    "sbom": "supply-chain",
    # ics-ot
    "ics/ot": "ics-ot",
    "ics": "ics-ot",
    "ot": "ics-ot",
    "industrial control systems": "ics-ot",
    "industrial control systems (ics)": "ics-ot",
    "scada": "ics-ot",
    "operational technology": "ics-ot",
    # privacy
    "privacy": "privacy",
    "surveillance": "privacy",
    "data protection": "privacy",
    "gdpr": "privacy",
    "tracking": "privacy",
    "data brokers": "privacy",
    "de-anonymization": "privacy",
    # ai-security
    "prompt injection": "ai-security",
    "indirect prompt injection": "ai-security",
    "llm": "ai-security",
    "llms": "ai-security",
    "genai": "ai-security",
    "agentic ai": "ai-security",
    "ai agents": "ai-security",
    "jailbreak": "ai-security",
    "large language models": "ai-security",
    "shadow ai": "ai-security",
    "ai security": "ai-security",
    "model provenance kit": "ai-security",
    # funding
    "funding": "funding",
    "cybersecurity funding": "funding",
    "m&a": "funding",
    "acquisition": "funding",
    "seed funding": "funding",
    "acquires": "funding",
    "funding/m&a": "funding",
    "emerge from stealth": "funding",
    # policy-law
    "policy": "policy-law",
    "legislation": "policy-law",
    "government": "policy-law",
    "congress": "policy-law",
    "sanctions": "policy-law",
    "regulation": "policy-law",
    "law enforcement": "policy-law",
    "executive order": "policy-law",
    "privacy law": "policy-law",
    "cybersecurity compliance": "policy-law",
    "cybersecurity strategy": "policy-law",
    "cyber strategy": "policy-law",
    "export control": "policy-law",
    "guilty": "policy-law",
    "sentenced": "policy-law",
    "extradited": "policy-law",
    "plead guilty": "policy-law",
    "prison": "policy-law",
    "takedown": "policy-law",
    # cryptography
    "encryption": "cryptography",
    "post quantum": "cryptography",
    "post quantum cryptography": "cryptography",
    "cryptanalysis": "cryptography",
    "pki": "cryptography",
    "public key infrastructure": "cryptography",
    "q-day": "cryptography",
    "quantum computing": "cryptography",
    "history of cryptography": "cryptography",
}

# ---------------------------------------------------------------------------
# Global junk blocklist — unambiguous garbage regardless of source
# ---------------------------------------------------------------------------

_GLOBAL_JUNK: frozenset[str] = frozenset([
    "full", "large", "medium", "thumbnail",  # Securelist image size variants
    "security",  # too generic to be useful (177 articles, all BleepingComputer)
])

_EMAIL_RE = re.compile(r"@")
_NUMERIC_RE = re.compile(r"^\d+$")

# ---------------------------------------------------------------------------
# Entity type → topic inference
# ---------------------------------------------------------------------------

_ENTITY_TYPE_TO_TOPIC: dict[str, str] = {
    "malware": "malware",
    "actor": "nation-state",
    "tool": "malware",
    "cve": "vulnerability",
}


# ---------------------------------------------------------------------------
# TypedDict for return value
# ---------------------------------------------------------------------------

class TagClassification(TypedDict):
    normalized_topics: list[str]
    tag_entities: list[dict]
    clean_tags: list[str]


# ---------------------------------------------------------------------------
# Internal passes
# ---------------------------------------------------------------------------

def _filter_junk(tags: list[str], source_junk_tags: list[str]) -> list[str]:
    """Remove global junk + source-specific navigation labels."""
    source_lower = {t.lower() for t in source_junk_tags}
    result = []
    for tag in tags:
        lower = tag.lower().strip()
        if not lower:
            continue
        if lower in _GLOBAL_JUNK:
            continue
        if _EMAIL_RE.search(lower):
            continue
        if _NUMERIC_RE.match(lower):
            continue
        if lower in source_lower:
            continue
        result.append(tag)
    return result


def _classify_entities(tags: list[str]) -> tuple[list[dict], list[str], set[str]]:
    """Match tags against CVE pattern, vendor list, and threat keyword list.

    Returns:
        entities   — entity dicts with source="tag", sources=["tag"]
        remaining  — tags not consumed by entity classification
        topics     — topics inferred from entity types
    """
    entities: list[dict] = []
    remaining: list[str] = []
    topics: set[str] = set()
    seen_keys: set[str] = set()

    for tag in tags:
        # CVE pattern
        upper = tag.upper().strip()
        if re.match(r"CVE-\d{4}-\d+", upper):
            key = upper.lower()
            if key not in seen_keys:
                entities.append({
                    "type": "cve",
                    "name": upper,
                    "normalized_key": key,
                    "source": "tag",
                    "sources": ["tag"],
                })
                seen_keys.add(key)
                topics.add("vulnerability")
            continue

        # Vendor / threat keyword via normalized key
        norm_key = _normalize_key(tag)

        if norm_key in VENDOR_KEYWORDS and norm_key not in seen_keys:
            entities.append({
                "type": "vendor",
                "name": VENDOR_KEYWORDS[norm_key],
                "normalized_key": norm_key,
                "source": "tag",
                "sources": ["tag"],
            })
            seen_keys.add(norm_key)
            # vendors don't infer a topic on their own
            continue

        if norm_key in THREAT_KEYWORDS and norm_key not in seen_keys:
            name, etype = THREAT_KEYWORDS[norm_key]
            entities.append({
                "type": etype,
                "name": name,
                "normalized_key": norm_key,
                "source": "tag",
                "sources": ["tag"],
            })
            seen_keys.add(norm_key)
            inferred = _ENTITY_TYPE_TO_TOPIC.get(etype)
            if inferred:
                topics.add(inferred)
            continue

        remaining.append(tag)

    return entities, remaining, topics


def _map_topics(tags: list[str]) -> set[str]:
    """Map remaining tags to controlled topic vocabulary via TOPIC_MAP."""
    topics: set[str] = set()
    for tag in tags:
        topic = TOPIC_MAP.get(tag.lower().strip())
        if topic:
            topics.add(topic)
    return topics


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_tags(raw_tags: list[str], source_junk_tags: list[str]) -> TagClassification:
    """Classify raw RSS tags into topics, entities, and clean tags.

    Args:
        raw_tags:         Raw tag strings from the RSS feed entry.
        source_junk_tags: Source-specific navigation labels to discard (from feed_sources.junk_tags).

    Returns:
        TagClassification with normalized_topics, tag_entities, clean_tags.
    """
    clean = _filter_junk(raw_tags, source_junk_tags)
    tag_entities, remaining, entity_topics = _classify_entities(clean)
    mapper_topics = _map_topics(remaining)
    all_topics = sorted(entity_topics | mapper_topics)

    return TagClassification(
        normalized_topics=all_topics,
        tag_entities=tag_entities,
        clean_tags=clean,
    )
```

- [ ] **Step 4: Run the tests**

```bash
.venv\Scripts\pytest tests/test_tag_classifier.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/tag_classifier.py tests/test_tag_classifier.py
git commit -m "feat(ingestion): add tag_classifier module with junk filter, entity classifier, topic mapper"
```

---

## Task 4: Add `merge_entities()` to `entity_extractor.py`

**Files:**
- Modify: `app/ingestion/entity_extractor.py`
- Modify: `tests/test_entity_extractor.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_entity_extractor.py`:

```python
from app.ingestion.entity_extractor import merge_entities


def test_merge_entities_no_overlap():
    text = [{"type": "vendor", "name": "Ivanti", "normalized_key": "ivanti"}]
    tag = [{"type": "malware", "name": "LockBit", "normalized_key": "lockbit", "source": "tag", "sources": ["tag"]}]
    result = merge_entities(text, tag)
    keys = [e["normalized_key"] for e in result]
    assert "ivanti" in keys
    assert "lockbit" in keys
    assert len(result) == 2


def test_merge_entities_overlap_merges_sources():
    text = [{"type": "vendor", "name": "Ivanti", "normalized_key": "ivanti"}]
    tag = [{"type": "vendor", "name": "Ivanti", "normalized_key": "ivanti", "source": "tag", "sources": ["tag"]}]
    result = merge_entities(text, tag)
    assert len(result) == 1
    ivanti = result[0]
    assert set(ivanti["sources"]) == {"text", "tag"}


def test_merge_entities_text_entity_gets_sources_field():
    text = [{"type": "vendor", "name": "Cisco", "normalized_key": "cisco"}]
    result = merge_entities(text, [])
    assert result[0]["sources"] == ["text"]


def test_merge_entities_empty_inputs():
    assert merge_entities([], []) == []
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv\Scripts\pytest tests/test_entity_extractor.py::test_merge_entities_no_overlap -v
```

Expected: `ImportError: cannot import name 'merge_entities'`

- [ ] **Step 3: Add `merge_entities()` to the bottom of `entity_extractor.py`**

```python
def merge_entities(text_entities: list[dict], tag_entities: list[dict]) -> list[dict]:
    """Merge text-derived and tag-derived entity lists, deduplicating by normalized_key.

    Text entities get sources=["text"]. Tag entities already carry sources=["tag"].
    Overlapping keys have their sources lists merged.
    """
    merged: dict[str, dict] = {}
    for e in text_entities:
        key = e["normalized_key"]
        merged[key] = {**e, "sources": ["text"]}
    for e in tag_entities:
        key = e["normalized_key"]
        if key in merged:
            existing_sources = merged[key].get("sources", ["text"])
            new_sources = e.get("sources", ["tag"])
            merged[key]["sources"] = sorted(set(existing_sources) | set(new_sources))
        else:
            merged[key] = {**e}
    return list(merged.values())
```

- [ ] **Step 4: Run the tests**

```bash
.venv\Scripts\pytest tests/test_entity_extractor.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/entity_extractor.py tests/test_entity_extractor.py
git commit -m "feat(entities): add merge_entities() helper for combining text and tag entity sources"
```

---

## Task 5: OpenSearch Schema Changes

**Files:**
- Modify: `app/db/opensearch.py`

- [ ] **Step 1: Add `raw_tags` and `normalized_topics` to `NEWS_MAPPING` in `opensearch.py`**

In `app/db/opensearch.py`, find the `"tags": {"type": "keyword"}` line (line 46) and replace it with:

```python
            "raw_tags":          {"type": "keyword"},
            "normalized_topics": {"type": "keyword"},
            "tags":              {"type": "keyword"},   # legacy — present on pre-backfill docs
```

> Note: `tags` stays in the mapping because OpenSearch cannot remove fields from existing indexes. New documents written after this change will use `raw_tags`; existing documents keep `tags` until the backfill script runs.

- [ ] **Step 2: Verify `ensure_indexes()` will push the new fields on startup**

`ensure_indexes()` already calls `put_mapping` for all existing indexes (confirmed in `opensearch.py:388`). No code change needed — just restarting the backend will push the new fields.

- [ ] **Step 3: Commit**

```bash
git add app/db/opensearch.py
git commit -m "feat(opensearch): add raw_tags and normalized_topics fields to news_articles mapping"
```

---

## Task 6: Wire Tag Classification into the Ingestion Pipeline

**Files:**
- Modify: `app/ingestion/ingester.py`

- [ ] **Step 1: Update imports at the top of `ingester.py`**

Find the existing imports block and add:

```python
from app.ingestion.tag_classifier import classify_tags
from app.ingestion.entity_extractor import merge_entities
```

- [ ] **Step 2: Replace the sequential entity extraction + clustering block with the concurrent version**

Find the block starting at line ~351 (after the category filter check and before `inserted = await upsert_article(article)`). Replace from the `inserted = await ...` line through the end of the entity/clustering try blocks with:

```python
            # Concurrent: tag classification + text entity extraction
            source_junk_tags = source.get("junk_tags", []) or []
            tag_result, text_entities = await asyncio.gather(
                asyncio.to_thread(
                    classify_tags,
                    article.get("tags") or [],
                    source_junk_tags,
                ),
                extract_entities(article, slug=article.get("slug"), db_session=None),
            )

            # Rename fields before storing
            article["raw_tags"] = tag_result["clean_tags"]
            article["normalized_topics"] = tag_result["normalized_topics"]
            article.pop("tags", None)

            inserted = await (overwrite_article if update else upsert_article)(article)
            if inserted:
                stats["inserted"] += 1
                entities = []
                try:
                    all_entities = merge_entities(text_entities, tag_result["tag_entities"])
                    entities = all_entities
                    if all_entities:
                        await store_article_entities(article["slug"], all_entities)
                        keyword_list = list(dict.fromkeys(
                            e["name"] for e in all_entities
                        ))
                        try:
                            await get_os_client().update(
                                index=INDEX_NEWS,
                                id=article["slug"],
                                body={"doc": {"keywords": keyword_list}},
                            )
                        except Exception:
                            logger.exception(
                                "[%s] Failed to update keywords for '%s'",
                                name, article.get("slug"),
                            )
                except Exception:
                    logger.exception(
                        "[%s] Entity extraction failed for '%s'",
                        name, article.get("slug"),
                    )

                # Clustering — always attempt, even without entities
                try:
                    await cluster_article(article, article["slug"], entities)
                except Exception:
                    logger.exception(
                        "[%s] Clustering failed for '%s'",
                        name, article.get("slug"),
                    )
            else:
                stats["skipped"] += 1
```

- [ ] **Step 3: Rebuild the ingestion container and run a smoke test**

```bash
docker compose build ingestion
docker compose up -d ingestion
docker compose logs ingestion --tail=50
```

Expected: no import errors, ingestion starts normally.

- [ ] **Step 4: Trigger a manual ingest and verify new fields appear in OpenSearch**

```bash
docker compose exec ingestion python scripts/ingest_feeds.py
```

Then query OpenSearch for a recent article:
```bash
curl -sk -u "kiber_app:REDACTED" \
  "https://81.17.98.185:9200/news_articles/_search?size=1&sort=created_at:desc" \
  -H "Content-Type: application/json" \
  -d '{"_source": ["raw_tags", "normalized_topics", "tags"]}' | python -m json.tool
```

Expected: new articles have `raw_tags` and `normalized_topics` fields; no `tags` field.

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/ingester.py
git commit -m "feat(ingestion): wire concurrent tag classification and entity extraction"
```

---

## Task 7: API and Response Model Changes

**Files:**
- Modify: `app/models/news.py`
- Modify: `app/api/routes/news.py`

- [ ] **Step 1: Update `NewsItem` in `app/models/news.py`**

Replace the `tags` field with `raw_tags` and add `normalized_topics`:

```python
class NewsItem(BaseModel):
    id: str = Field(json_schema_extra={"example": "cisa-warns-fortinet-rce-cve-2026-12345"})
    slug: str = Field(json_schema_extra={"example": "cisa-warns-fortinet-rce-cve-2026-12345"})
    raw_tags: List[str] = Field(default=[], json_schema_extra={"example": ["Malware", "Ransomware"]})
    normalized_topics: List[str] = Field(default=[], json_schema_extra={"example": ["malware", "vulnerability"]})
    title: str = Field(json_schema_extra={"example": "CISA Warns of Critical Fortinet FortiOS RCE Vulnerability"})
    desc: Optional[str] = Field(None, json_schema_extra={"example": "CISA has added CVE-2026-12345 to its Known Exploited Vulnerabilities catalog after active exploitation was confirmed in the wild."})
    summary: Optional[str] = Field(None, json_schema_extra={"example": "CISA has added CVE-2026-12345 to its Known Exploited Vulnerabilities catalog after active exploitation was confirmed in the wild. The vulnerability affects FortiOS versions prior to 7.4.3 and allows remote code execution without authentication."})
    keywords: List[str] = Field(json_schema_extra={"example": ["fortinet", "cve-2026-12345", "rce"]})
    time: str = Field(json_schema_extra={"example": "3h"})
    severity: Optional[str] = Field(None, json_schema_extra={"example": "critical"})
    type: str = Field(json_schema_extra={"example": "advisory"})
    category: str = Field(json_schema_extra={"example": "breaking"})
    author: Optional[str] = Field(None, json_schema_extra={"example": "CISA"})
    source_name: Optional[str] = Field(None, json_schema_extra={"example": "CISA Advisories"})
    source_url: Optional[str] = Field(None, json_schema_extra={"example": "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"})
    image_url: Optional[str] = Field(None, json_schema_extra={"example": "https://news.avild.com/static/images/cisa-logo.png"})
    cvss_score: Optional[Decimal] = Field(None, json_schema_extra={"example": 9.8})
    cve_ids: List[str] = Field(default=[], json_schema_extra={"example": ["CVE-2026-12345"]})
    published_at: str = Field(json_schema_extra={"example": "2026-03-15T08:30:00Z"})
    body_quality: Optional[str] = Field(None, json_schema_extra={"example": "teaser"})
    body_source: Optional[str] = Field(None, json_schema_extra={"example": "summary"})
    is_teaser: bool = Field(False, json_schema_extra={"example": True})
```

- [ ] **Step 2: Update `app/api/routes/news.py` — field list, hit mappers, filter, and param**

**2a.** Replace `_LIST_SOURCE_FIELDS` to include the new fields (remove `"tags"`, add `"raw_tags"` and `"normalized_topics"`):

```python
_LIST_SOURCE_FIELDS = [
    "slug", "title", "desc", "summary", "raw_tags", "normalized_topics",
    "keywords", "published_at", "severity", "type", "category", "author",
    "source_name", "source_url", "image_url", "cvss_score", "cve_ids",
    "body_quality", "body_source", "is_teaser",
]
```

**2b.** Update `_hit_to_item()` — replace `tags=src.get("tags") or []` with:

```python
        raw_tags=src.get("raw_tags") or src.get("tags") or [],   # fallback for pre-backfill docs
        normalized_topics=src.get("normalized_topics") or [],
```

**2c.** Update `_hit_to_detail()` with the same change as 2b.

**2d.** Update `_build_filters()` — add `topics` parameter and rename `tag` → keep for backward compat but add topic filter:

```python
def _build_filters(
    *,
    category: Optional[str] = None,
    type: Optional[str] = None,
    severity: Optional[str] = None,
    source_name: Optional[str] = None,
    tag: Optional[str] = None,
    cve: Optional[str] = None,
    min_cvss: Optional[float] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    topics: list[str] | None = None,
) -> List[dict]:
    filters: List[dict] = []
    if category:
        filters.append({"term": {"category": category}})
    if type:
        filters.append({"term": {"type": type}})
    if severity:
        filters.append({"term": {"severity": severity}})
    if source_name:
        filters.append({"term": {"source_name": source_name}})
    if tag:
        filters.append({"term": {"raw_tags": tag}})
    if cve:
        filters.append({"term": {"cve_ids": cve}})
    if min_cvss is not None:
        filters.append({"range": {"cvss_score": {"gte": min_cvss}}})
    if topics:
        filters.append({"terms": {"normalized_topics": topics}})
    date_range: dict = {}
    if date_from:
        date_range["gte"] = date_from
    if date_to:
        date_range["lte"] = date_to
    if date_range:
        filters.append({"range": {"published_at": date_range}})
    return filters
```

**2e.** Add the `topic` query param to `get_news()` and pass it to `_build_filters()`:

```python
async def get_news(
    category: Optional[str] = Query(None, description="Filter by category"),
    type: Optional[str] = Query(None, description="Filter by type (news|analysis|report|advisory)"),
    severity: Optional[str] = Query(None, description="Filter by severity"),
    source_name: Optional[str] = Query(None, description="Filter by source name"),
    tag: Optional[str] = Query(None, description="Filter by raw tag"),
    topic: list[str] = Query(default=[], description="Filter by normalized topic (e.g. malware, vulnerability). Multiple values use OR logic."),
    cve: Optional[str] = Query(None, description="Filter by CVE ID"),
    min_cvss: Optional[float] = Query(None, ge=0, le=10, description="Minimum CVSS score"),
    date_from: Optional[str] = Query(None, description="Start date (ISO-8601)"),
    date_to: Optional[str] = Query(None, description="End date (ISO-8601)"),
    sort: str = Query("newest", description="Sort order: newest|oldest|cvss"),
    q: Optional[str] = Query(None, description="Full-text search across title, desc, tags"),
    limit: int = Query(12, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    filters = _build_filters(
        category=category, type=type, severity=severity,
        source_name=source_name, tag=tag, cve=cve,
        min_cvss=min_cvss, date_from=date_from, date_to=date_to,
        topics=topic or None,
    )
```

- [ ] **Step 3: Restart the backend and verify Swagger shows the new fields**

```bash
docker compose restart backend
```

Open `http://localhost:8000/api/docs` — confirm `GET /news/` shows the `topic` query param and response model shows `raw_tags` + `normalized_topics`.

- [ ] **Step 4: Commit**

```bash
git add app/models/news.py app/api/routes/news.py
git commit -m "feat(api): add normalized_topics to article responses and topic filter to GET /news/"
```

---

## Task 8: Backfill Script

**Files:**
- Create: `scripts/backfill_tag_normalization.py`

- [ ] **Step 1: Create the backfill script**

```python
#!/usr/bin/env python
"""Backfill raw_tags and normalized_topics for all existing news_articles.

Reads existing articles from OpenSearch, runs classify_tags() on the current
tags field, writes raw_tags + normalized_topics back, and removes the old tags field.

Usage:
    python scripts/backfill_tag_normalization.py           # process all articles
    python scripts/backfill_tag_normalization.py --dry-run # log changes, no writes
"""
import asyncio
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select

from app.db.models.feed_source import FeedSource as FeedSourceModel
from app.db.opensearch import INDEX_NEWS, get_os_client
from app.db.session import AsyncSessionLocal
from app.ingestion.tag_classifier import classify_tags

logger = logging.getLogger(__name__)

BATCH_SIZE = 100
CONCURRENCY = 20


async def _load_junk_map() -> dict[str, list[str]]:
    """Return source_name → junk_tags from Postgres."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(FeedSourceModel))
        sources = result.scalars().all()
    return {s.name: s.junk_tags or [] for s in sources}


async def _process_article(
    doc: dict,
    junk_map: dict[str, list[str]],
    client,
    sem: asyncio.Semaphore,
    *,
    dry_run: bool,
) -> None:
    async with sem:
        src = doc["_source"]
        raw_tags = src.get("tags") or []
        source_name = src.get("source_name", "")
        source_junk = junk_map.get(source_name, [])

        result = await asyncio.to_thread(classify_tags, raw_tags, source_junk)

        if dry_run:
            logger.info(
                "[DRY RUN] %s | topics=%s entities=%d clean_tags=%d",
                doc["_id"],
                result["normalized_topics"],
                len(result["tag_entities"]),
                len(result["clean_tags"]),
            )
            return

        update_body: dict = {
            "doc": {
                "raw_tags": result["clean_tags"],
                "normalized_topics": result["normalized_topics"],
            }
        }
        await client.update(index=INDEX_NEWS, id=doc["_id"], body=update_body)


async def backfill(*, dry_run: bool = False) -> None:
    client = get_os_client()
    junk_map = await _load_junk_map()
    sem = asyncio.Semaphore(CONCURRENCY)

    scroll_resp = await client.search(
        index=INDEX_NEWS,
        scroll="5m",
        body={
            "query": {"match_all": {}},
            "size": BATCH_SIZE,
            "_source": ["tags", "source_name"],
        },
    )

    scroll_id = scroll_resp["_scroll_id"]
    total = scroll_resp["hits"]["total"]["value"]
    processed = 0
    batch_num = 0

    logger.info("Total articles to backfill: %d", total)

    try:
        hits = scroll_resp["hits"]["hits"]
        while hits:
            batch_num += 1
            await asyncio.gather(*[
                _process_article(doc, junk_map, client, sem, dry_run=dry_run)
                for doc in hits
            ])
            processed += len(hits)
            logger.info("Batch %d complete — %d/%d articles processed", batch_num, processed, total)

            scroll_resp = await client.scroll(scroll_id=scroll_id, scroll="5m")
            scroll_id = scroll_resp["_scroll_id"]
            hits = scroll_resp["hits"]["hits"]
    finally:
        await client.clear_scroll(scroll_id=scroll_id)

    logger.info("Backfill complete. %d articles processed. dry_run=%s", processed, dry_run)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Backfill tag normalization for existing articles")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    asyncio.run(backfill(dry_run=args.dry_run))
```

- [ ] **Step 2: Rebuild and run dry-run first**

```bash
docker compose build ingestion
docker compose exec ingestion python scripts/backfill_tag_normalization.py --dry-run 2>&1 | tail -30
```

Expected: logs showing article IDs with inferred topics, no writes.

- [ ] **Step 3: Run the real backfill**

```bash
docker compose exec ingestion python scripts/backfill_tag_normalization.py
```

Expected: `Backfill complete. 1468 articles processed. dry_run=False`

- [ ] **Step 4: Verify a sample article in OpenSearch has the new fields**

```bash
curl -sk -u "kiber_app:REDACTED" \
  -X POST "https://81.17.98.185:9200/news_articles/_search" \
  -H "Content-Type: application/json" \
  -d '{
    "size": 3,
    "query": {"terms": {"normalized_topics": ["malware"]}},
    "_source": ["title", "raw_tags", "normalized_topics", "source_name"]
  }' | python -m json.tool
```

Expected: articles with `normalized_topics: ["malware"]` and `raw_tags` populated.

- [ ] **Step 5: Verify topic aggregation counts look reasonable**

```bash
curl -sk -u "kiber_app:REDACTED" \
  -X POST "https://81.17.98.185:9200/news_articles/_search" \
  -H "Content-Type: application/json" \
  -d '{"size": 0, "aggs": {"topics": {"terms": {"field": "normalized_topics", "size": 20}}}}' \
  | python -m json.tool
```

Expected: buckets for `vulnerability`, `malware`, `nation-state`, `phishing`, etc. with reasonable counts.

- [ ] **Step 6: Commit**

```bash
git add scripts/backfill_tag_normalization.py
git commit -m "feat(scripts): add concurrent backfill script for tag normalization"
```

---

## Self-Review Checklist

- [x] **Spec coverage:**
  - `tag_classifier.py` three-layer pipeline → Task 3 ✓
  - `merge_entities()` → Task 4 ✓
  - OpenSearch schema (`raw_tags`, `normalized_topics`) → Task 5 ✓
  - Alembic migration (`junk_tags JSONB`) → Task 1 ✓
  - `sources.py` + seeder update → Task 2 ✓
  - Ingestion wiring (concurrent `asyncio.gather`) → Task 6 ✓
  - API (`normalized_topics` in response, `topic` filter) → Task 7 ✓
  - Backfill (concurrent, idempotent, dry-run) → Task 8 ✓
- [x] **No placeholders** — all code blocks are complete
- [x] **Type consistency** — `TagClassification`, `classify_tags`, `merge_entities` defined in Tasks 3–4 and imported consistently in Tasks 6 and 8
- [x] **`to_source_dict()` updated** — Task 1 includes `junk_tags` in the return dict so ingester can read it
- [x] **`asyncio.to_thread` used for synchronous `classify_tags`** — Tasks 6 and 8 both use it
- [x] **Transition safety** — `_hit_to_item` falls back to `src.get("tags")` for pre-backfill docs; `article.pop("tags", None)` is safe for CISA News which emits no tags
