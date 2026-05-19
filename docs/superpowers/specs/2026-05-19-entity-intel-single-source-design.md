# Entity Intel: Single Source of Truth

**Date:** 2026-05-19
**Status:** Approved

## Problem

`entity_extractor.py` has three hardcoded Python dicts (`VENDOR_KEYWORDS`, `PRODUCT_KEYWORDS`, `_BASELINE_KEYWORDS`) and a JSON file dependency (`data/threat_keywords.json`). The `entity_intel` Postgres table exists and is 95% the right answer — it has 1625 rows from MITRE ATT&CK, CISA KEV, and Ransomware.live — but `refresh_entity_intel()` is never called at startup so it has zero effect at runtime. Two alias systems exist but operate from different sources, and Stage 4 alias resolution is permanently dead.

## Goal

Make `entity_intel` the single source of truth for all entity patterns. Remove all hardcoded data from Python source. Wire startup. Unify both alias systems under DB-backed loading.

## Current State (verified live)

| Data source | Entries | Status |
|---|---|---|
| `entity_intel` DB (attack) | 1050 | loaded never |
| `entity_intel` DB (ransomware.live) | 316 | loaded never |
| `entity_intel` DB (cisa_kev vendors) | 259 | loaded never |
| `threat_keywords.json` | 1040 | 100% covered by DB — redundant |
| `VENDOR_KEYWORDS` hardcoded | 42 | 29 in DB, 13 missing |
| `PRODUCT_KEYWORDS` hardcoded | 71 | 4 in DB, 67 missing — no product type in DB |
| `_BASELINE_KEYWORDS` hardcoded | 37 | 100% covered by DB — redundant |

Missing vendors (13): AWS, Cloudflare, GitHub, Zoom, Signal, OpenAI, NVIDIA, Huawei, HP, Lenovo, AMD, WhatsApp, Telegram

Missing products (67): all of `PRODUCT_KEYWORDS` except 4 already present.

`entity_intel` has no CHECK constraint on `entity_type` — adding `product` is pure data.

## Approach: Two-phase

Phase 1 and phase 2 are separate commits. Hardcoded dicts and JSON stay in the codebase during phase 1 as a cold fallback.

---

## Phase 1

### 1. Alembic data migration — seed curated entities

File: `alembic/versions/<hash>_seed_curated_entities.py`

- Insert 13 missing vendors into `entity_intel` with `entity_type='vendor'`, `source='curated'`
- Insert 67 missing products into `entity_intel` with `entity_type='product'`, `source='curated'`
- Use `INSERT ... ON CONFLICT (normalized_key) DO NOTHING` — idempotent
- No schema changes. `entity_type` is varchar with no CHECK constraint.

### 2. `_rebuild_patterns_from_db()` — add product support

Currently rebuilds `_VENDOR_PATTERNS` and `_THREAT_PATTERNS` but ignores products entirely. Add:

```python
_PRODUCT_PATTERNS.clear()
for key, (name, etype) in _DB_ENTITY_MAP.items():
    if etype == "product":
        flags = 0 if len(name) <= 3 else re.IGNORECASE
        _PRODUCT_PATTERNS.append(
            (key, name, re.compile(r"\b" + re.escape(name) + r"\b", flags))
        )
```

Add fallback guard: after rebuild, if any pattern list is empty, log a warning. Don't crash — hardcoded fallbacks still present during phase 1.

### 3. Startup wiring — `ingest_all_feeds()`

Add at the top of `ingest_all_feeds()` in `app/ingestion/ingester.py`, before the source loop:

```python
async with AsyncSessionLocal() as db:
    count = await refresh_entity_intel(db)
    logger.info("Entity intel loaded: %d entries", count)
```

One DB round-trip at startup. No per-article latency impact.

The two alias systems unify automatically at this point:
- `_ALIAS_PATTERNS` (regex text matching) — rebuilt from `_DB_ALIAS_DISPLAY` by `_rebuild_patterns_from_db()`
- Stage 4 `_resolve_aliases` (NER output normalization) — uses `_DB_ALIAS_INDEX`, now populated

Both driven from `entity_intel.aliases` jsonb column. No extra code.

---

## Phase 2 (after verification)

**Trigger:** Confirm logs show `"Entity intel loaded: N entries"` where N ≥ 1800.

Delete from `entity_extractor.py`:
- `VENDOR_KEYWORDS` dict
- `PRODUCT_KEYWORDS` dict
- `_BASELINE_KEYWORDS` dict
- `_BASELINE_ALIASES` dict
- `_load_threat_data()` function and its call site
- `THREAT_KEYWORDS`, `_THREAT_ALIASES` module-level assignments

Delete from repo:
- `data/threat_keywords.json` (verified: all 1040 keys present in DB)

The fallback guard added in phase 1 (`_rebuild_patterns_from_db()`) remains — it's the safety net if `_DB_ENTITY_MAP` is ever empty.

---

## Files changed

| File | Change |
|---|---|
| `alembic/versions/<hash>_seed_curated_entities.py` | new — data migration |
| `app/ingestion/entity_extractor.py` | add product rebuild + fallback guard |
| `app/ingestion/ingester.py` | add `refresh_entity_intel()` startup call |
| `app/ingestion/entity_extractor.py` (phase 2) | delete all hardcoded dicts + JSON loader |
| `data/threat_keywords.json` (phase 2) | delete |

## Non-goals

- No new tables or columns
- No changes to clustering logic
- No changes to NER sidecar
- No changes to how entities are stored in OpenSearch
