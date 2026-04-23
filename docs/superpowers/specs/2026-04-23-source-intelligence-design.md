# Source Intelligence System Design

## Goal

Add per-source credibility weighting and category-level ingest filtering to the Kiber ingestion pipeline, so high-signal sources (CISA, Kaspersky) lift cluster scores and low-value content categories (weekly digests, sponsored posts) are discarded before they waste storage or pollute clustering.

## Architecture

Three changes compose to form the full system:

1. **Postgres schema additions** — `feed_sources` gains three new columns; a new `source_categories` table stores per-source, per-category ingest decisions.
2. **Normalizer refactor** — the `CisaNormalizer` / `BleepingComputerNormalizer` class hierarchy is replaced with one `normalize_article()` function that reads `extract_cves` / `extract_cvss` flags from the source profile.
3. **LLM category classifier** — a model-agnostic async interface classifies RSS `<category>` tag values once per source (bulk batch at setup, on-demand for unknowns at runtime), writing decisions back to `source_categories`.

**Data ownership:** `feed_sources` and `source_categories` live in Postgres (operational config + ACID guarantees). `credibility_weight` is denormalized onto each article in OpenSearch at ingest time to avoid cross-store joins at scoring time.

## Tech Stack

- Python 3.12, SQLAlchemy (async) + Alembic migrations
- Local LLM (Ollama or HuggingFace — selected at implementation time)
- OpenSearch for article storage; scorer reads denormalized `credibility_weight` field

---

## Section 1: Data Model

### `feed_sources` — new columns

```sql
credibility_weight  FLOAT   NOT NULL DEFAULT 1.0
extract_cves        BOOLEAN NOT NULL DEFAULT FALSE
extract_cvss        BOOLEAN NOT NULL DEFAULT FALSE
```

`credibility_weight` is a multiplier. Typical values:

- CISA, Kaspersky, Mandiant: `1.5`
- Mainstream news (BleepingComputer, SecurityWeek): `1.0` (default)
- Unknown/low-signal blogs: `0.5`

`extract_cves` and `extract_cvss` drive conditional field extraction in the normalizer. CISA Advisories and CISA ICS ship with both set to `true`. All other sources default to `false`.

### New `source_categories` table

```sql
CREATE TABLE source_categories (
    id                   SERIAL PRIMARY KEY,
    source_id            INTEGER NOT NULL REFERENCES feed_sources(id) ON DELETE CASCADE,
    category_label       VARCHAR(255) NOT NULL,
    ingest               BOOLEAN NOT NULL DEFAULT TRUE,
    priority_modifier    FLOAT NOT NULL DEFAULT 0.0,   -- range -0.5 to +0.5
    classified_by        VARCHAR(20) NOT NULL,          -- "llm" | "manual" | "heuristic"
    classification_notes TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(source_id, category_label)
);
```

`ingest=false` causes the article to be discarded at normalization time — no storage, no entity extraction, no clustering.

`priority_modifier` is added to the source's `credibility_weight` for that article only. Example: BleepingComputer (weight=1.0) + category "ransomware" (modifier=+0.2) → effective weight 1.2 for that article.

`classified_by` provides an audit trail. LLM-classified rows carry the model name or prompt version in `classification_notes`.

### Alembic migration

One migration adds the three columns to `feed_sources` and creates `source_categories`. Existing rows get default values (`credibility_weight=1.0`, `extract_cves=false`, `extract_cvss=false`). A data migration seeds known credibility weights and extraction flags for the 18 existing sources.

---

## Section 2: Scoring Integration

`compute_cluster_score()` in `app/ingestion/scorer.py` gains a sixth factor: **Source Credibility (0–15 pts)**.

The cluster document in OpenSearch stores `max_credibility_weight` (max across all member articles, maintained at cluster index time). The scoring translation:

| `max_credibility_weight` | Points |
| --- | --- |
| ≥ 1.5 | 15 |
| ≥ 1.2 | 10 |
| ≥ 1.0 (default) | 5 |
| < 1.0 | 0 |

Total possible score becomes 115 before clamping; `min(total, 100.0)` is unchanged.

`rescore_cluster()` already reads all cluster fields from OpenSearch, so it picks up `max_credibility_weight` automatically once it's present in the document.

`max_credibility_weight` is written to the cluster document by the clustering code whenever an article is added to a cluster — it tracks the maximum `credibility_weight` seen across all member articles. The clustering code in `scripts/cluster_articles.py` is responsible for maintaining this field during cluster creation and updates.

---

## Section 3: Normalizer Refactor

The class hierarchy (`CisaNormalizer`, `CisaIcsNormalizer`, `BleepingComputerNormalizer`, etc.) is removed. A single `normalize_article(entry, source)` function replaces all normalizer classes.

```python
def normalize_article(entry: dict, source: dict) -> dict:
    article = _base_fields(entry, source)
    if source.get("extract_cves"):
        article["cve_ids"] = _extract_cve_ids(article["full_text"])
    if source.get("extract_cvss"):
        article["max_cvss"] = _extract_cvss_score(article["full_text"])
    article["advisory_id"] = _extract_advisory_id(article["full_text"])
    article["credibility_weight"] = source.get("credibility_weight", 1.0)
    return article
```

The `NORMALIZER_REGISTRY` dict (currently mapping normalizer keys → classes) becomes a mapping of normalizer keys → flag override dicts:

```python
NORMALIZER_REGISTRY: dict[str, dict] = {
    "cisa_advisory":  {"extract_cves": True, "extract_cvss": True},
    "cisa_ics":       {"extract_cves": True, "extract_cvss": True},
    "generic":        {},
    "thn":            {},
    "bleepingcomputer": {},
    # ... etc.
}
```

At normalization time, the source dict from `FeedSource.to_source_dict()` is merged with the registry override before being passed to `normalize_article()`. Existing `normalizer_key` values in the DB continue to work without a data migration.

`FeedSource.to_source_dict()` is extended to include the new fields:

```python
def to_source_dict(self) -> dict:
    return {
        ...existing fields...,
        "credibility_weight": self.credibility_weight,
        "extract_cves": self.extract_cves,
        "extract_cvss": self.extract_cvss,
    }
```

---

## Section 4: LLM Category Classifier

### Interface

```python
from dataclasses import dataclass

@dataclass
class CategoryDecision:
    label: str
    ingest: bool
    priority_modifier: float  # -0.5 to +0.5
    notes: str

async def classify_categories(
    source_name: str,
    category_labels: list[str],
) -> list[CategoryDecision]:
    ...
```

The concrete implementation (Ollama or HuggingFace) is injected at runtime; the interface is fixed. The prompt instructs the LLM to evaluate each category label in the context of a cybersecurity news platform and decide whether articles in that category should be ingested, and whether they deserve a priority boost or suppression.

### Bulk classification script

`scripts/classify_source_categories.py`:

1. Fetch all active sources from Postgres
2. For each source, collect all distinct `<category>` values seen in the last N days of raw feed snapshots
3. Call `classify_categories(source_name, labels)` for each source
4. Write results to `source_categories` (upsert on `UNIQUE(source_id, category_label)`)

Estimated LLM calls: ~10–20 (one per source). Run once at setup, re-run when sources are added.

### Runtime path (unknown categories)

At normalization time, if an article's category labels are not found in `source_categories`:

1. Call `classify_categories()` for just those labels
2. Write results back to `source_categories`
3. Apply the decision to the current article

This ensures the table self-populates over time for sources with dynamic category taxonomies.

---

## Out of Scope

- Per-article LLM quality scoring (too expensive; category-level coverage is sufficient for MVP)
- Source reputation fetching from external APIs (Moz DA, etc.) — manual `credibility_weight` seeding is sufficient
- Full normalizer source-specific parsing beyond CVE/CVSS extraction (e.g. structured advisory field parsing beyond what `_extract_advisory_id` already does)
