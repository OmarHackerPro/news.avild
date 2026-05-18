# Trusted Entity Tier — Design Spec

2026-05-18

## Problem

Entity extraction now covers 71.8% of articles (NER sidecar, securebert-v1). The remaining gaps are not about raw coverage — they are about precision and consistency:

1. **Vendor is structurally broken.** SecureBERT does not emit a `vendor` type. Vendor extraction is 100% regex against a hardcoded 60-entry list → only 42 articles have a vendor entity. Vendors are the most stable, distinctive entity type and the easiest to get right — yet they're almost entirely absent.

2. **NER output is not canonicalized.** `Lazarus`, `Lazarus Group`, `HIDDEN COBRA`, `Guardians of Peace` each become different `normalized_key`s, different entity docs, different cluster signals. Missed merges result. There is no alias resolution — just a hardcoded stub dict (`_SIDECAR_SYNONYMS`) with one entry.

3. **All entity lists are hardcoded.** `VENDOR_KEYWORDS` (~60), `PRODUCT_KEYWORDS` (~80), `_BASELINE_KEYWORDS` (~40), `data/threat_keywords.json` (684 aliases). No update path, no provenance tracking, no way to refresh without touching code.

---

## Goals

- Move all entity reference data to PostgreSQL tables, synced from authoritative external sources
- Expand vendor coverage from ~60 hardcoded entries to a DB-backed list sourced from CISA KEV (~200–300 vendors)
- Replace the hand-rolled synonym stub with a data-driven alias resolution layer backed by MITRE ATT&CK aliases
- Add deterministic CVE→vendor/product enrichment path via CISA KEV join
- Add CWE and ATT&CK TTP ID regex (zero collision risk, near-free)

## Non-goals

- NIST CPE full product dictionary — products are already NER-covered (3,169 sidecar product entities); CPE product keyword matching causes English-word collisions (`Word`, `Access`, `Edge`). Deferred.
- IOC enrichment (IP/hash/domain) — out of scope for entity extraction
- Fine-tuning or replacing the SecureBERT model
- Replacing the NER sidecar — trusted tier feeds it, doesn't replace it

---

## Architecture

### What the trusted tier does

Two jobs:

**Job A — keyword extraction source (especially vendor).** DB-backed lists replace hardcoded dicts in `entity_extractor.py`. Lists are loaded into memory at startup, matching logic unchanged. Vendor expands from ~60 → ~200–300 entries.

**Job B — canonical alias resolution for NER output.** After the sidecar returns, each NER entity is looked up against an in-memory alias index. On match, `normalized_key` and `display_name` are rewritten to the canonical entry. `HIDDEN COBRA` → look up → rewrites to `lazarus-group` / `Lazarus Group`. This replaces the hardcoded `_SIDECAR_SYNONYMS` stub from the NER quality spec — the same concept, now data-driven from ATT&CK.

### Pipeline position

```text
TRUSTED TIER (startup-loaded, always kept)
  article text → CVE/CWE/TTP regex
               → vendor keyword match  (expanded, from entity_intel)
               → product keyword match (unchanged, still ~80 hardcoded for now)
               → threat keyword match  (actor/malware/tool, from entity_intel)
               → KEV join on CVE IDs   (deterministic vendor+product enrichment)

NER SIDECAR OUTPUT
  → Stage 1: synonym map          (NER quality spec)
  → Stage 2: edit-distance dedup  (NER quality spec)
  → Stage 3: mentions filter      (NER quality spec)
  → Stage 4: alias resolution     [NEW — this spec] resolve NER keys to canonical via entity_intel
  → Stage 5: trusted/discovery split + per-type policy  [NEW — this spec]
  → merge with trusted tier output
  → store
```

**Stage numbering note:** The NER quality spec (`2026-05-17-ner-quality-design.md`) defines stages 1–3 and refers to a "trusted/discovery split" as an unnamed next step. This spec adds Stage 4 (alias resolution) and formalises Stage 5 (trusted/discovery split). The NER quality spec's `_SIDECAR_SYNONYMS` stub is an interim placeholder that Stage 4 supersedes — it can be removed once `entity_intel` is populated.

The alias resolution step is new to this spec. The synonym map, edit-distance dedup, and mentions filter are from the NER quality spec (`docs/superpowers/specs/2026-05-17-ner-quality-design.md`) and are unchanged here.

---

## Data sources

### MITRE ATT&CK Enterprise + ICS

- **URL:** `https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master/enterprise-attack/enterprise-attack.json` (and `ics-attack/ics-attack.json`)
- **Format:** STIX 2.1 JSON bundle
- **What we extract:**
  - `intrusion-set` objects → `actor` type, `x_mitre_aliases` → aliases array
  - `malware` objects → `malware` type, `x_mitre_aliases` → aliases array
  - `tool` objects → `tool` type, `x_mitre_aliases` → aliases array
- **Update frequency:** quarterly; fetch latest on demand
- **Auth:** none

### CISA Known Exploited Vulnerabilities (KEV)

- **URL:** `https://raw.githubusercontent.com/cisagov/kev-data/develop/known_exploited_vulnerabilities.json`
- **Format:** JSON, `vulnerabilities[]` array
- **What we extract:** `cveID`, `vendorProject`, `product`, `vulnerabilityName`, `dateAdded`, `dueDate`, `knownRansomwareCampaignUse`, `cwes`
- **Dual use:** (1) `vendorProject` field populates the vendor keyword list in `entity_intel`; (2) the full KEV row lives in `cisa_kev` for CVE-keyed enrichment at article ingestion
- **Update frequency:** weekdays; ~600–900 entries and growing
- **Auth:** none

### Ransomware.live

- **URL:** `https://api.ransomware.live/v2/groups`
- **Format:** JSON array
- **What we extract:** `name`, `aliases`, `status` (active/inactive)
- **Entity type:** `actor` (ransomware groups are threat actors; distinguished by `source='ransomware.live'`)
- **Update frequency:** real-time; daily sync adequate
- **Auth:** free Pro API key (500K calls/month, no rate limit)

---

## Database schema

Both tables belong in PostgreSQL (reference/lookup data, not content). SQLAlchemy models + Alembic migration required.

### `entity_intel`

```sql
CREATE TABLE entity_intel (
    id              SERIAL PRIMARY KEY,
    normalized_key  VARCHAR NOT NULL UNIQUE,   -- canonical slug (e.g. "lazarus-group")
    display_name    VARCHAR NOT NULL,           -- canonical display (e.g. "Lazarus Group")
    entity_type     VARCHAR NOT NULL,           -- vendor | actor | malware | tool | vuln_alias | campaign
    aliases         JSONB   NOT NULL DEFAULT '[]',  -- list of strings to match (incl. display_name)
    source          VARCHAR NOT NULL,           -- attack | ransomware.live | cisa_kev | manual
    source_id       VARCHAR,                    -- ATT&CK external ID (e.g. G0032, S0002)
    active          BOOLEAN NOT NULL DEFAULT TRUE,  -- false for defunct ransomware groups
    last_synced     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX entity_intel_type_idx ON entity_intel (entity_type);
CREATE INDEX entity_intel_source_idx ON entity_intel (source);
```

**Alias matching note:** the `aliases` column stores raw match strings (e.g. `["Lazarus Group", "HIDDEN COBRA", "Labyrinth Chollima", "Guardians of Peace", "ZINC"]`). At startup, `entity_extractor.py` builds a flat dict from all rows: each alias string is passed through `_normalize_key()` (lowercase + non-alphanumeric → hyphen) to produce the lookup key, mapping to the row's `normalized_key`. The row's `display_name` is also indexed under its own normalized form.

### `cisa_kev`

```sql
CREATE TABLE cisa_kev (
    cve_id                VARCHAR PRIMARY KEY,  -- e.g. "CVE-2024-12345"
    vendor                VARCHAR NOT NULL,
    product               VARCHAR NOT NULL,
    vulnerability_name    VARCHAR NOT NULL,
    date_added            DATE    NOT NULL,
    due_date              DATE,
    known_ransomware_use  BOOLEAN NOT NULL DEFAULT FALSE,
    cwes                  JSONB   NOT NULL DEFAULT '[]',  -- e.g. ["CWE-79", "CWE-89"]
    last_synced           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX cisa_kev_vendor_idx ON cisa_kev (vendor);
```

---

## Sync scripts

Three new standalone scripts under `scripts/`, following the existing `seed_*.py` pattern — idempotent, safe to re-run, cron-able.

### `scripts/sync_attack.py`

1. Fetch enterprise-attack + ics-attack STIX JSON from GitHub
2. Extract `intrusion-set`, `malware`, `tool` objects
3. For each: build `normalized_key` from `name`, collect all `x_mitre_aliases` into `aliases[]`
4. Upsert into `entity_intel` on `normalized_key` conflict (update display_name, aliases, last_synced)
5. Print summary: N inserted, N updated, source=attack

### `scripts/sync_cisa_kev.py`

1. Fetch KEV JSON from GitHub
2. For each vulnerability: upsert into `cisa_kev`
3. For each unique `vendorProject`: **normalize** the string before upserting into `entity_intel` — strip whitespace, remove legal suffixes (`Corp.`, `LLC`, `Inc.`, parenthetical notes like `(formerly ...)`), then apply `_normalize_key()` to produce the canonical slug. Raw KEV examples requiring this: `"Google LLC"`, `"Microsoft Corp."`, `"Ivanti (formerly Pulse Secure)"`. Two KEV rows with different raw vendor strings that normalize to the same slug are merged into one `entity_intel` row.
4. Print summary

### `scripts/sync_ransomware.py`

1. Fetch `/v2/groups` from ransomware.live API (requires `RANSOMWARE_LIVE_API_KEY` env var)
2. For each group: upsert into `entity_intel` (type=actor, source=ransomware.live, active from status field)
3. Print summary

**Conflict resolution across sources:** `normalized_key` is the merge key. Conflicts only occur when two sources emit the same entity type with the same key (e.g. ATT&CK and ransomware.live both have `lockbit` as an actor). In that case ATT&CK wins — it has richer aliases and an authoritative `source_id`. Source priority for same-type conflicts: `attack` > `ransomware.live` > `cisa_kev` > `manual`. Enforced by checking `source` before update: a lower-priority source only updates `last_synced`, never `aliases` or `display_name`. Cross-type conflicts (e.g. ATT&CK actor vs KEV vendor) cannot happen by definition since `entity_type` differs.

**API key:** `RANSOMWARE_LIVE_API_KEY` added to `.env` and Docker Compose env. Script prints a clear error and exits if key is absent.

---

## `entity_extractor.py` changes

### 1. Startup loader

New function `refresh_entity_intel(db_session)`:

```python
async def refresh_entity_intel(db_session) -> None:
    """Load entity_intel from DB into module-level dicts. Called once at app startup."""
    # Populates _VENDOR_PATTERNS, _PRODUCT_PATTERNS, _THREAT_PATTERNS, _ALIAS_INDEX
```

Called from `main.py` startup event, after DB is available. If `entity_intel` table is empty (e.g. sync scripts have not been run), falls back to current hardcoded dicts + `threat_keywords.json` — no breaking change.

### 2. In-memory structures (module-level, loaded from DB)

```python
# normalized_key → (display_name, entity_type)   — replaces VENDOR_KEYWORDS / PRODUCT_KEYWORDS / THREAT_KEYWORDS
_ENTITY_MAP: dict[str, tuple[str, str]] = {}

# alias text (lowercased) → canonical normalized_key  — replaces _THREAT_ALIASES + threat_keywords.json aliases
_ALIAS_INDEX: dict[str, str] = {}
```

`_VENDOR_PATTERNS`, `_PRODUCT_PATTERNS`, `_THREAT_PATTERNS` are rebuilt from `_ENTITY_MAP` after each `refresh_entity_intel()` call. Pattern compilation is identical to today.

### 3. Alias resolution step (new)

Applied to sidecar output inside `extract_entities()`, after the existing NER quality stages (synonym map → dedup → mentions filter), before the trusted-tier merge:

```python
def _resolve_aliases(entities: list[dict]) -> list[dict]:
    """Rewrite NER entities to canonical keys using _ALIAS_INDEX."""
    resolved = []
    for e in entities:
        alias_key = e["normalized_key"]
        canonical = _ALIAS_INDEX.get(alias_key) or _ALIAS_INDEX.get(e["name"].lower())
        if canonical and canonical in _ENTITY_MAP:
            display, etype = _ENTITY_MAP[canonical]
            e = {**e, "normalized_key": canonical, "name": display, "type": etype}
        resolved.append(e)
    return resolved
```

If two NER entities in the same article resolve to the same canonical key, the one with higher `mentions` wins; the other is dropped (same dedup logic used elsewhere).

### 4. CVE → KEV enrichment path (new)

Inside `extract_entities()`, after regex extraction:

```python
async def _enrich_from_kev(cve_ids: list[str], db_session) -> list[dict]:
    """For each CVE, look up cisa_kev and emit vendor + product entities."""
```

Queries `cisa_kev` for all CVE IDs found in the article (batch query). For each hit, emits two entities: `type=vendor, name=vendorProject` and `type=product, name=product`. These are trusted-tier entities — they are not subject to the NER quality filter stages.

**Only runs when `db_session` is provided** (same guard as the NER sidecar call today). No change to tests or scripts that call `extract_entities()` without a session.

### 5. CWE and ATT&CK TTP regex (new, in `_extract_regex()`)

```python
# CWE IDs
CWE_RE = re.compile(r"\bCWE-\d+\b")

# ATT&CK Technique IDs (T1059 or T1059.003) — bounded to T1xxx range to avoid serial/model number FPs
TTP_RE = re.compile(r"\bT1[0-6]\d{2}(?:\.\d{3})?\b")
```

Entity types: `cwe` and `ttp`. These join CVE on the regex path — regex-extracted, always kept, no mentions filter.

---

## Migration strategy

1. Add Alembic migration for `entity_intel` and `cisa_kev` tables
2. Run `scripts/sync_attack.py && scripts/sync_cisa_kev.py && scripts/sync_ransomware.py` — populates DB
3. `entity_extractor.py` detects DB is populated → uses DB-loaded dicts
4. `data/threat_keywords.json` and hardcoded dicts remain as fallback — not deleted until the next cleanup pass, after DB sync is confirmed stable in production

The hardcoded dicts and JSON file are **not removed in this spec**. They are the fallback. A future cleanup spec removes them once sync scripts are running reliably.

---

## Implementation sequence

1. Alembic migration — `entity_intel` + `cisa_kev` tables
2. `scripts/sync_attack.py`
3. `scripts/sync_cisa_kev.py`
4. `scripts/sync_ransomware.py`
5. `entity_extractor.py` — `refresh_entity_intel()` + startup hook in `main.py`
6. `entity_extractor.py` — alias resolution step
7. `entity_extractor.py` — KEV enrichment path
8. `entity_extractor.py` — CWE + TTP regex
9. Update `scripts/backfill_ner_sidecar.py` to open a DB session and pass it to `extract_entities()` so KEV enrichment runs during backfill (currently the backfill script calls `extract_entities()` without a session). Then run `--force` on all articles.
10. Verify: spot-check entity docs in OpenSearch — confirm `HIDDEN COBRA` → `lazarus-group`, vendor counts up from 42

---

## Files touched

| File | Change |
|---|---|
| `alembic/versions/<hash>_add_entity_intel_tables.py` | New migration — entity_intel + cisa_kev |
| `app/db/models.py` | New — SQLAlchemy ORM models for entity_intel + cisa_kev (Base imported from app/db/base.py) |
| `scripts/sync_attack.py` | New — MITRE ATT&CK sync |
| `scripts/sync_cisa_kev.py` | New — CISA KEV sync (also populates vendor entries with normalization) |
| `scripts/backfill_ner_sidecar.py` | Add DB session pass-through to `extract_entities()` for KEV enrichment |
| `scripts/sync_ransomware.py` | New — ransomware.live sync |
| `app/ingestion/entity_extractor.py` | refresh_entity_intel(), alias resolution, KEV enrichment, CWE/TTP regex |
| `app/main.py` | Call refresh_entity_intel() in startup event |
| `.env.example` | Add RANSOMWARE_LIVE_API_KEY |
