# Tag Normalization & Entity Extraction from RSS Tags

**Date:** 2026-05-03  
**Status:** Approved  

---

## Problem

RSS feeds emit `<category>` elements that are currently stored as raw strings in OpenSearch (`tags` field) and used only as a text blob for CVE regex matching. The data contains 1,192 distinct tag values across 1,468 articles — a mix of genuine topic signals, named entities (vendors, threat actors, malware families, CVE IDs), blog navigation noise, and outright junk (image size variants, email addresses).

Nothing reads the tags for filtering, ranking, or display. The goal is to turn this raw signal into two structured outputs: a small controlled topic vocabulary and a source of additional entity extraction.

---

## Decisions

- **Storage:** Add `normalized_topics` field alongside renamed `raw_tags` (was `tags`). Raw values are preserved for audit and re-derivation.
- **Entity wiring:** Tag-based entity extraction runs as a separate concurrent pass — not merged into the text extractor. Single responsibility at each layer.
- **Concurrency:** `classify_tags()` and `extract_entities()` (text-based) run concurrently via `asyncio.gather()`. Both only need the normalized article, neither depends on the other.
- **Nation-state/APT:** Resolved via the `threat-actor` entity type in the existing seed lists — no hardcoded APT alias list in the topic mapper. New actor names get created as entities automatically.
- **API:** `normalized_topics` exposed in article responses; multi-topic `?topic=` filter (`terms` OR semantics) added to `GET /feed`.
- **Backfill:** Standalone script, concurrent within scroll batches (semaphore 20), idempotent, dry-run flag.

---

## Controlled Topic Vocabulary

12 values. An article can hold multiple.

| Value | Canonical raw signals |
|---|---|
| `vulnerability` | CVE, Vulnerabilities, vulnerability, exploited, zero-day, CISA KEV, exploit, n-day, RCE, patch, Patch Tuesday |
| `malware` | Malware, Ransomware, Trojan, spyware, backdoor, wiper, botnet, infostealer, RAT, keylogger, malware family names via entity seed list |
| `data-breach` | Data Breaches, data breach, data exfiltration, Credential Harvesting, stolen credentials |
| `nation-state` | Resolved via `threat-actor` entity type — not a keyword list |
| `phishing` | phishing, Phishing, Spear Phishing, social engineering, BEC, scam |
| `supply-chain` | supply chain, Supply Chain Security, supply chain attack, dependency confusion, open source software |
| `ics-ot` | ICS/OT, ICS, OT, industrial control systems, SCADA, Operational Technology |
| `privacy` | privacy, Privacy, surveillance, data protection, GDPR, tracking |
| `ai-security` | prompt injection, LLM, GenAI, Agentic AI, AI agents, jailbreak, large language models |
| `funding` | funding, Cybersecurity Funding, M&A, Acquisition, seed funding |
| `policy-law` | Policy, legislation, Government, Congress, sanctions, regulation |
| `cryptography` | encryption, post quantum, cryptanalysis, PKI, public key infrastructure |

---

## Architecture

### New module: `app/ingestion/tag_classifier.py`

Pure synchronous module, no I/O. Public interface:

```python
class TagClassification(TypedDict):
    normalized_topics: list[str]   # deduplicated controlled vocabulary values
    tag_entities: list[dict]       # entity dicts with source="tag", sources=["tag"]
    clean_tags: list[str]          # junk stripped; stored as raw_tags

def classify_tags(raw_tags: list[str], source_junk_tags: list[str]) -> TagClassification:
    ...
```

Three internal passes, each single-responsibility:

**1. `_filter_junk(tags, source_junk_tags)`**

Drops tags matching either list (case-insensitive):

- *Global blocklist* — truly unambiguous garbage regardless of source: image size variants (`full`, `large`, `medium`, `thumbnail`), anything containing `@`, purely numeric strings
- *Source-specific blocklist* — blog navigation labels stored in `feed_sources.junk_tags` (JSONB column). Examples:
  - Red Canary: `["news & events", "product updates", "testing and validation"]`
  - Krebs: `["a little sunshine", "the coming storm", "ne'er-do-well news", "web fraud 2.0"]`
  - Schneier: `["uncategorized", "schneier news"]`
  - Recorded Future: `["blog", "research (insikt)"]`
  - Securelist: `["full", "large", "medium", "thumbnail"]`

Articles with no resulting `normalized_topics` are still fully ingested, clustered, and ranked — they just appear in the unfiltered feed only.

**2. `_classify_entities(tags)`**

Checks each cleaned tag in order:

1. CVE pattern (`CVE-\d{4}-\d+`) → entity type `cve`, infers topic `vulnerability`
2. Lowercased tag in `VENDOR_KEYWORDS` (from `entity_extractor.py`) → entity type `vendor`, no topic inferred
3. Lowercased tag in `_BASELINE_KEYWORDS` (malware/actor seed list from `entity_extractor.py`) → entity type from dict; `malware` type → topic `malware`, `threat-actor` type → topic `nation-state`

Returns entity dicts with `source: "tag"` and `sources: ["tag"]`.

**3. `_map_topics(remaining_tags)`**

Tags not consumed by entity classifier pass through here. A `dict[str, str]` at module level maps lowercase tag → normalized topic string. Tags matching nothing end up in `clean_tags` only — stored as `raw_tags`, ignored for topics.

---

### Ingestion wiring (`app/ingestion/ingester.py`)

Replaces the sequential normalize → extract_entities → cluster flow:

```python
# After normalize_article() produces `article`:

source_junk_tags = source.get("junk_tags", [])

# Concurrent: tag classification + text entity extraction
tag_result, text_entities = await asyncio.gather(
    asyncio.to_thread(classify_tags, article["tags"], source_junk_tags),
    extract_entities(article, slug=slug),
)

# Merge + deduplicate by normalized_key; merge sources lists
all_entities = merge_entities(text_entities, tag_result["tag_entities"])

# Write derived fields
article["raw_tags"]          = tag_result["clean_tags"]
article["normalized_topics"] = tag_result["normalized_topics"]
article.pop("tags", None)  # safe if source emits no tags (e.g. CISA News)

# Store + cluster unchanged
await store_article_entities(slug, all_entities)
await cluster_article(article, slug, all_entities)
```

`merge_entities()` added to `entity_extractor.py` — groups by `normalized_key`, merges `sources` lists (`["text"]` + `["tag"]` → `["text", "tag"]`), deduplicates.

---

## Data Model

### OpenSearch `news_articles` index (`app/db/opensearch.py`)

```python
"raw_tags":          {"type": "keyword"},   # renamed from "tags"
"normalized_topics": {"type": "keyword"},   # new; multi-value controlled vocabulary
```

`ensure_indexes()` adds the new field on startup. Existing documents keep the old `tags` field until backfill runs — both field names coexist during the transition window.

### PostgreSQL `feed_sources` table

New column added via Alembic migration:

```sql
ALTER TABLE feed_sources ADD COLUMN junk_tags JSONB NOT NULL DEFAULT '[]';
```

`seed_sources.py` updated to seed `junk_tags` per source. `ingester.py` DB query updated to fetch the new column.

---

## API Changes

### Article response model (`app/models/`)

Add field:
```python
normalized_topics: list[str] = []
```

Existing articles return `[]` until backfill runs. Non-breaking.

### `GET /feed` (`app/api/routes/news.py`)

New optional query param using FastAPI's `Query`:
```python
topic: list[str] = Query(default=[])
```

Filter applied in `_build_filters()`:
```python
if topic:
    filters.append({"terms": {"normalized_topics": topic}})
```

OR semantics — `?topic=malware&topic=nation-state` returns articles tagged with either value.

---

## Backfill Script: `scripts/backfill_tag_normalization.py`

Runs once after deployment. Concurrent within scroll batches.

```
Usage:
  python scripts/backfill_tag_normalization.py           # process all articles
  python scripts/backfill_tag_normalization.py --dry-run # log changes, no writes
```

**Flow:**
1. Fetch all sources and their `junk_tags` from Postgres upfront → dict keyed by source name
2. Scroll `news_articles` in batches of 100
3. Per batch: `asyncio.gather()` with semaphore(20) — each article runs `classify_tags()` via `asyncio.to_thread()` then OpenSearch `update` (writes `raw_tags`, `normalized_topics`, removes old `tags` field)
4. Progress logged every batch

**Properties:** Idempotent (safe to re-run), non-destructive (field updates only, no document replacement), no re-clustering required.

**Run after deployment:**
```powershell
docker compose exec ingestion python scripts/backfill_tag_normalization.py --dry-run
docker compose exec ingestion python scripts/backfill_tag_normalization.py
```

---

## Files Changed

| File | Change |
|---|---|
| `app/ingestion/tag_classifier.py` | **New** — full classifier module |
| `app/ingestion/ingester.py` | Wire concurrent classify + extract; write `raw_tags`/`normalized_topics` |
| `app/ingestion/entity_extractor.py` | Add `merge_entities()` helper |
| `app/db/opensearch.py` | Add `raw_tags`, `normalized_topics` to `NEWS_MAPPING`; remove `tags` |
| `app/models/` | Add `normalized_topics: list[str]` to article response model |
| `app/api/routes/news.py` | Add `topic: list[str]` filter param; update `_build_filters()` |
| `app/ingestion/sources.py` | Add `junk_tags` per source in `SEED_SOURCES` |
| `alembic/versions/` | **New migration** — add `junk_tags JSONB` to `feed_sources` |
| `scripts/seed_sources.py` | Seed `junk_tags` column |
| `scripts/backfill_tag_normalization.py` | **New** — one-time backfill script |

---

## Out of Scope

- Frontend topic filter chips (UI consumes `?topic=` when ready)
- LLM-based tag classification (correct long-term direction, needs explicit go-ahead)
- Entity backfill from tags on existing articles (separate enrichment task)
- Topics on cluster documents (follow-up)
- AND semantics for multi-topic filter (follow-up if needed)
