# CVSS Wiring + Write-Once API Cache

**Date:** 2026-05-21
**Status:** Draft

## Problem

CVSS data exists in our database but is never joined back to articles or clusters:

- `entities` index has CVSS for ~most CVEs (NVD-enriched: `CVE-2026-20223`=10.0, `CVE-2026-20206`=6.3, etc.)
- `article.cvss_score` is **null on every article** (only the CISA-advisory regex path sets it, and even there it's empty)
- `article.severity` is **null on every news article** (it's `source["default_severity"]`, which most sources leave unset)
- `cluster.max_cvss` is **0.0 on every one of 922 clusters** (initialized to 0, never updated because article cvss_score is null)
- Frontend severity sort shows "CVSS 0.0" on every card. Severity badges (critical/high/medium/low) never appear.

The join from `entities.cvss_score` → `article.cvss_score` → `cluster.max_cvss` does not exist anywhere in the code.

Architecturally there is a second, broader problem: CVE-specific intelligence currently lives in **two** places (`entities.cvss_score/cvss_severity/cvss_vector/cwe_ids/cisa_kev/nvd_last_modified` and `cve_topics.cvss_score/cvss_severity/...`). The `cve_topics` index was designed for this, but `enrich_cve_nvd.py` writes to `entities` instead — `cve_topics` CVSS fields are always null. Two incoherent sources of truth.

And finally: API-fetched data across the system is treated as freely overwritable. Only `ner_cache` is protected (DB trigger + `ON CONFLICT DO NOTHING`). NVD, EPSS, KEV, MITRE all overwrite each run, which makes runs non-deterministic and risks re-billing or stomping curated data.

## Goals

1. **Wire CVSS end-to-end** so severity sort and severity badges work.
2. **Consolidate CVE intelligence in `cve_topics`** — single source of truth.
3. **Apply the `ner_cache` write-once pattern to all API-fetched data** as the project-wide principle.

## Non-goals

- Re-running NVD enrichment (already done by user).
- Article-body CVSS regex extraction (rejected — API is authoritative; if we have the CVE, NVD has the score).
- EPSS write-once (EPSS is legitimately time-varying — explicit exception).

---

## Phase 1 — Consolidate CVE intel in `cve_topics`

### 1.1 New `cve_topics` fields

Add to `_CVE_TOPICS_MAPPING` in [opensearch.py](app/db/opensearch.py):

```python
"cwe_ids":          {"type": "keyword"},
"vuln_status":      {"type": "keyword"},
"nvd_raw":          {"type": "object", "enabled": False},  # full NVD blob (replaces nvd_cache)
"enriched_at":      {"type": "date", "format": "date_optional_time||epoch_millis"},
```

`cve_topics` now holds: `cvss_score`, `cvss_severity`, `cvss_vector`, `cwe_ids`, `cisa_kev`, `kev_added_at`, `epss_score`, `epss_percentile`, `nvd_description`, `nvd_last_modified`, `vuln_status`, `nvd_raw`, `aliases`, `article_ids`, `article_count`, `cve_embedding`. Everything CVE-related, one place.

Decision: deprecate the `nvd_cache` index entirely. `nvd_raw` lives in `cve_topics` (same doc-per-CVE, same write-once semantics). One fewer index to keep coherent.

### 1.2 Update `enrich_cve_nvd.py`

- Write to `INDEX_CVE_TOPICS` instead of `INDEX_ENTITIES`.
- `_upsert_one`-style upsert: create-if-missing with all NVD fields, update only `article_ids` linkage if the doc already exists. **Never overwrite an NVD-sourced field.**
- Remove `--force` (or gate it behind a separate explicit flag — see Phase 3).
- Keep `_rescore_clusters_for_cves` but read CVSS from `cve_topics` instead of `entities`.

### 1.3 Strip CVE-specific fields from `entities` mapping

Remove from `_ENTITIES_MAPPING`: `cvss_score`, `cvss_severity`, `cvss_vector`, `cwe_ids`, `vuln_status`, `cisa_kev`, `nvd_last_modified`.

Keep on entities: `type`, `name`, `normalized_key`, `aliases`, `description`, `article_ids`, `article_count`, `first_seen`, `last_seen`. Pure generic entity registry.

Mapping changes are additive; field removals don't break OpenSearch — old fields just stop being read. The reindex isn't required.

### 1.4 Migrate existing CVE data from `entities` to `cve_topics`

One-shot script `scripts/migrate_cve_intel_to_topics.py`:

- Scroll `entities` where `type=cve`.
- For each, build doc with NVD fields, upsert into `cve_topics` (create-if-missing, never overwrite). Pull `nvd_raw` from `nvd_cache` and embed.
- Verify count, log delta.
- Idempotent — safe to re-run.

After successful migration: `nvd_cache` can be deleted (kept for one release as fallback).

### 1.5 Update read paths

- `app/api/routes/entities.py` — for `type=cve` entities, JOIN to `cve_topics` to populate `cvss_score` in the response. One extra OS lookup per CVE entity hit.
- `app/api/routes/exports.py`, `app/briefing/selector.py`, `app/briefing/formatter.py` — same join pattern if they consume entity CVSS.
- `app/ingestion/scorer.py` doesn't read entity CVSS directly — it reads `cluster.max_cvss`. No change.

---

## Phase 2 — Wire CVSS into articles and clusters

### 2.1 New module `app/ingestion/cve_intel.py`

```python
SEVERITY_THRESHOLDS = [
    (9.0, "critical"),
    (7.0, "high"),
    (4.0, "medium"),
    (0.0, "low"),
]

def severity_from_cvss(score: float | None) -> str | None:
    """Map CVSS base score → severity label. Returns None for None/<=0."""
    if score is None or score <= 0:
        return None
    for threshold, label in SEVERITY_THRESHOLDS:
        if score >= threshold:
            return label
    return None


async def lookup_cve_intel(cve_ids: list[str]) -> dict[str, dict]:
    """Look up CVE intelligence in cve_topics. Read-only. No API calls.

    Returns {cve_id_upper: {cvss_score, cvss_severity, cisa_kev, epss_score, ...}}.
    Missing CVEs are absent from the result.
    """
```

Reads only from `cve_topics`. Never calls NVD/EPSS.

### 2.2 Ingest-time enrichment

In [ingester.py](app/ingestion/ingester.py) `ingest_source()`, after entity extraction and before `cluster_article`:

```python
if article.get("cve_ids"):
    intel = await lookup_cve_intel(article["cve_ids"])
    if intel:
        scores = [v["cvss_score"] for v in intel.values() if v.get("cvss_score")]
        if scores:
            article["cvss_score"] = max(scores)
            article["severity"] = severity_from_cvss(article["cvss_score"])
```

Write-once: only set if currently null. Future ingests skip the lookup if already set.

If the CVE isn't yet in `cve_topics` (new CVE, NVD hasn't been enriched for it), `severity` stays null. The nightly enrich + backfill pair re-derives once data lands. **Option A — first-ingest may miss, backfill catches up.** Documented behavior.

### 2.3 Drop the `extract_cvss` regex path

In [normalizer.py](app/ingestion/normalizer.py): remove `_extract_cvss_score` calls. Remove `extract_cvss` flag from `NORMALIZER_REGISTRY["cisa_advisory"]`. DB lookup supersedes it. Keep `_extract_cvss_vector` and `advisory_id` (those are not in NVD).

### 2.4 Cluster `max_cvss` propagation

Already correctly wired in [clusterer.py](app/ingestion/clusterer.py) — `create_cluster` and `merge_into_cluster` consume `article.get("cvss_score")`. Becomes effective the moment articles get `cvss_score` populated.

### 2.5 Backfill script `scripts/backfill_cve_intel.py`

One-shot, idempotent:

1. **Articles** — scroll `news_articles` where `cve_ids` is non-empty AND `cvss_score` is null. For each, look up CVE intel in `cve_topics`, compute max score, update `cvss_score` + `severity`. Write-once (skip if already set).
2. **Clusters** — scroll `clusters` where `cve_ids` is non-empty AND (`max_cvss` is null OR `max_cvss == 0`). For each, look up CVE intel, set `max_cvss`. Call `rescore_cluster` after.

Pure DB-internal join, no API calls. Runs in minutes.

---

## Phase 3 — Write-once enforcement for API-fetched data

### 3.1 Principle

**API-fetched fields are immutable.** Once a CVSS score is in `cve_topics`, it stays. To re-enrich, delete the doc first. To update a specific field, you do so explicitly with a separate admin tool — not via the same script that fills in missing data.

Rationale: the user has already paid the API cost (or accepted the rate-limit budget). Overwriting is at best wasteful, at worst destructive (if the new fetch returns less data than the old one, or if a manual correction is in place).

### 3.2 OpenSearch helper

New `app/db/os_write_once.py`:

```python
async def upsert_immutable(
    index: str,
    doc_id: str,
    immutable_fields: dict,
    mutable_fields: dict | None = None,
) -> None:
    """Set immutable_fields only if currently null. Always set mutable_fields.

    Implemented as a Painless update with upsert. Painless ensures atomicity
    so concurrent writers can't race.
    """
```

Used by `enrich_cve_nvd.py`, future enrichers. The script source clause is:
```
for (entry in params.immutable.entrySet()) {
  if (!ctx._source.containsKey(entry.getKey()) || ctx._source[entry.getKey()] == null) {
    ctx._source[entry.getKey()] = entry.getValue();
  }
}
for (entry in params.mutable.entrySet()) {
  ctx._source[entry.getKey()] = entry.getValue();
}
```

### 3.3 Apply to existing enrichers

| Script | Index | Immutable fields | Mutable fields |
|---|---|---|---|
| `enrich_cve_nvd.py` | `cve_topics` | `cvss_score`, `cvss_severity`, `cvss_vector`, `cwe_ids`, `nvd_description`, `nvd_last_modified`, `nvd_raw`, `vuln_status` (when terminal) | `vuln_status` (when transient: Reserved, Pending NVD), `updated_at`, `article_ids`, `article_count` |
| `refresh_epss.py` | `cve_topics` | — | `epss_score`, `epss_percentile`, `epss_updated_at` (EPSS is time-varying — explicit exception) |
| `sync_cisa_kev.py` | `cve_topics` | `kev_added_at` | `cisa_kev` (can flip false→true) |
| `sync_mitre_attack.py` | Postgres `entity_intel` | `display_name`, `entity_type` once set | `aliases`, `last_synced` |

### 3.4 Postgres triggers for API data

Apply the `ner_cache_protect_api_rows` pattern to:

- `cisa_kev` table — `vulnerability_name`, `date_added`, `cwes`: write-once after first sync.
- `entity_intel` table — when `source='mitre_attack'`: `display_name`, `entity_type` write-once.

Alembic migration `<hash>_protect_api_sourced_rows.py`. Same trigger shape as `c4d5e6f7a8b9_protect_ner_cache_api_rows.py`. EPSS doesn't apply (no Postgres EPSS table).

### 3.5 Removing `--force`

`enrich_cve_nvd.py --force` and similar destructive flags are removed. To re-enrich a CVE, the explicit operator path is:

```
python scripts/admin_invalidate_cve.py CVE-2026-20223
python scripts/enrich_cve_nvd.py
```

The invalidate script deletes the doc from `cve_topics`; the next enrichment refills it. Two-step, audit-traceable.

---

## Phase 4 — Frontend severity badge

Already partially shipped: `cluster.max_cvss > 0` check in `news-grid.js` so empty CVSS doesn't render "CVSS 0.0".

Remaining: nothing. Once the rebuild runs (Phase 5), articles have `severity` set, the existing badge logic in `buildCard()` lights up automatically.

---

## Phase 5 — Pipeline execution

Run after all code is committed. Total runtime ~15-20 minutes.

### 5.1 Data migration (entities → cve_topics)

```bash
docker compose exec ingestion python scripts/migrate_cve_intel_to_topics.py
```

Copies `entities.cvss_*`, `cwe_ids`, `cisa_kev`, `nvd_last_modified`, `vuln_status` for all `type=cve` entities into `cve_topics`. Pulls `nvd_raw` from `nvd_cache` and embeds. Write-once: never overwrites existing `cve_topics` fields. Idempotent.

Expected: ~600-1000 CVE entities migrated (matches NVD-enriched count).

### 5.2 Full cluster rebuild

```bash
docker compose build ingestion && docker compose up -d ingestion
docker cp scripts/cluster_articles.py kiber-ingestion-1:/app/scripts/cluster_articles.py
docker compose exec ingestion python scripts/cluster_articles.py --reset
```

`--reset` drops all 922 clusters and re-clusters 2,146 articles from scratch. Critically, this run uses the new ingest-time wiring: each article gets `cvss_score` and `severity` populated from `cve_topics` before clustering, and `max_cvss` propagates into the cluster doc via the existing Painless script.

Expected runtime: ~13 minutes (per CLAUDE.md baseline).

### 5.3 NER backfill — explicitly out of scope

The 35 articles currently missing NER entities (~1.6%) are not relevant to this PR. NER is upstream of CVE intel and runs through a separate cache. If those 35 need fixing later, it's a separate `scripts/backfill_ner_sidecar.py` run, not blocking on this work.

### 5.4 Verification

After the rebuild, hard-refresh the browser at `http://localhost/` and confirm:

1. Sort by **Severity** — top cards now show a meaningful CVSS spread (10.0, 9.8, 9.1, etc.) instead of "CVSS 0.0".
2. **Severity badges** (critical/high/medium/low colored tags) appear on cards whose top article has a known CVE.
3. `/api/feed?sort=severity&limit=5` returns clusters with `max_cvss > 0`.
4. `/api/search/?q=CVE` returns articles with non-null `cvss_score` and `severity`.

If any of those fail, stop and diagnose before merging.

---

## Migration order (single PR)

1. **Schema** — add new fields to `_CVE_TOPICS_MAPPING`. Strip CVE fields from `_ENTITIES_MAPPING`. Run `ensure_indexes()` — additive only.
2. **Code** — `cve_intel.py`, `os_write_once.py`, helpers.
3. **Update enrichers** — `enrich_cve_nvd.py` writes to `cve_topics`, uses `upsert_immutable`.
4. **Update reads** — `entities.py` API route joins to `cve_topics` for CVE entries.
5. **Ingester wiring** — Phase 2.2.
6. **Drop regex** — Phase 2.3.
7. **Alembic triggers** — Phase 3.4.
8. **One-shot migration** — Phase 5.1.
9. **Full cluster rebuild** — Phase 5.2.
10. **Browser verification** — Phase 5.4.

Total: ~1 day of work + ~15-20 min pipeline runtime.

## Risks

- **Migration data loss** — if `migrate_cve_intel_to_topics` has a bug, CVE data is in two places temporarily. Mitigation: idempotent, never deletes from `entities` until verified. `nvd_cache` retained one release.
- **Backfill churn** — touching every article + cluster with CVEs (~600+ articles, ~200+ clusters from spot-check). OpenSearch update load is fine, but rescore triggers a write per cluster. Run during low-traffic window.
- **Article severity drift** — if NVD changes a score post-ingest, our article severity is frozen (write-once). Acceptable: NVD score updates are rare and the cluster `max_cvss` still re-derives from `cve_topics` on rescore.
- **`nvd_raw` enabled:false** — full blob isn't indexed/queryable, only stored. That's intentional (no need to query into NVD JSON), but if someone later wants to search it they need to change the mapping.

## Open questions

1. Should `aliases` on `cve_topics` be write-once or mutable? Currently mutable (new alias discovered → append). I'd keep mutable but flag it for review.
2. Do we want a `cve_topics_audit` trail (who/when set each field)? Out of scope for this spec.
