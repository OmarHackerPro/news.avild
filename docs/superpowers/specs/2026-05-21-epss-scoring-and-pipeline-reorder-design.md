# EPSS Scoring & Ingestion Pipeline Reorder — Design

- **Date:** 2026-05-21
- **Status:** Approved (pending spec review)
- **Topic:** Wire EPSS into cluster importance scoring; reorder ingestion so CVE enrichment sees body-level CVEs.

---

## Problem

Two related gaps in the ingestion pipeline:

1. **CVE extraction misses body-level CVEs.** `normalize_article` extracts CVE IDs
   only from the RSS snippet (title + short description). The full article body —
   fetched later by body extraction — frequently mentions additional CVEs. NER
   *does* find these as CVE entities, but they never flow back into the article's
   `cve_ids` field, so `_apply_cve_intel` (CVSS/severity lookup) never sees them.
   This is part of why recent clusters show `severity: none`.

2. **EPSS data is unused.** `app/ingestion/epss_client.py` is fully implemented and
   `cve_topics` docs already carry `epss_score` / `epss_percentile` fields, but
   nothing ever calls `fetch_epss`. EPSS — the probability a CVE will be exploited
   in the next 30 days — is a strong real-world urgency signal that currently
   contributes nothing to cluster importance scoring.

## Goals

- Reorder ingestion so `_apply_cve_intel` runs *after* NER, with body-level CVEs
  merged into `cve_ids`.
- Fetch EPSS at CVE-topic creation time, and keep it fresh via a daily sync job.
- Add EPSS as the 8th cluster importance-scoring factor (max 15 pts).
- Store `max_epss` as a first-class, queryable cluster field — not only a hidden
  score input.
- Update `scripts/rebuild_all.py` to populate EPSS during a full rebuild.

## Non-Goals

- NVD/NIST CVSS enrichment (separately deferred — see `reference_nist_api`).
- Per-user / asset-based personalized relevance scoring (future feature; this
  design is deliberately compatible with it — see Architecture Note).
- Having the daily EPSS sync rescore affected clusters (noted limitation; future
  follow-up).

## Architecture Note — compatibility with future personalization

Two scoring axes must remain separate:

| Axis | Question it answers | Per-user? |
|---|---|---|
| Global importance (`cluster.score`) | How big a deal is this objectively? | No |
| Personal relevance (future) | Does this hit *my* stack? | Yes |

EPSS is an objective, global property of a CVE — it belongs in `cluster.score`.
The future personalization feature adds a *separate* per-user relevance score;
the final user-facing rank becomes `f(global_importance, personal_relevance)`.
Two rules keep this clean and uncomplicated by the present work:

1. `max_epss` is stored as an explicit, queryable cluster field so a future
   per-user ranker can read EPSS directly and weight it its own way.
2. `cluster.score` stays global — it is never personalized in place.
   Personalization gets its own computed field, layered on top.

EPSS also *benefits* the future feature: when a user marks an asset ("I run
FortiOS") and a matching cluster surfaces, exploitation probability is exactly
the signal they need. The data plumbed into `cve_topics` here is a prerequisite
that feature would need regardless.

---

## Section 1 — Pipeline reorder

**File:** `app/ingestion/ingester.py`, function `ingest_source`.

`_apply_cve_intel` moves out of the pre-upsert path and into the post-insert
block, after NER. It currently runs before `upsert_article`, seeing only
RSS-snippet CVEs.

**New order inside the `if inserted:` block:**

```
maybe_extract_body          → content_html merged into article (existing)
extract_entities (NER)      → text_entities, runs on full body (existing)
merge_entities              → all_entities (existing)
─ NEW: ner_cves = uppercased CVE-type entity keys from all_entities
─ NEW: article["cve_ids"] = dedup(article["cve_ids"] + ner_cves)
_apply_cve_intel(article)   → now sees body-level CVEs too (MOVED here)
store_article_entities      → (existing)
─ One OpenSearch update: { keywords, cve_ids, cvss_score, severity }
cluster_article             → (existing)
```

**Details:**

- `_apply_cve_intel` is removed entirely from the pre-upsert path. Because it ran
  *before* dedup previously, duplicate articles already discarded its result —
  there is **no behavior change for duplicates**.
- Enrichment now happens *after* the doc is indexed, so `cvss_score` /
  `severity` / `cve_ids` must be written back via an explicit
  `os_client.update()`. **This write-back runs unconditionally** — it must NOT be
  folded into the existing post-NER `keywords` update, because that update is
  gated on `if all_entities:` (`ingester.py:463`). An article with body-level
  CVEs but zero NER entities would otherwise skip the CVE/severity write-back
  entirely. The write-back update carries `cve_ids` / `cvss_score` / `severity`
  always; `keywords` is included in the same call only when `all_entities` is
  non-empty. Body extraction keeps its own separate `update` (independent failure
  domain, already wrapped in its own try/except).
- **CVE casing:** NER entity `normalized_key` for CVEs is lowercase; the `cve_ids`
  field convention is uppercase (`CVE-2024-1234`). NER CVE keys are uppercased
  before merging into `cve_ids`.
- `--update` reparse mode (`overwrite_article`) always enters the post-insert
  block, so reparse now also picks up body-level CVEs — no extra work needed.

## Section 2 — EPSS fetch & storage (inline-on-create + daily cron)

EPSS populates `cve_topics` at two moments.

**a) Inline on topic creation.** Both creation paths in
`app/ingestion/cve_topic_manager.py` — `upsert_cve_topics` and
`create_cve_topic_stubs` — get a pre-step: determine which CVE IDs are genuinely
new (no existing topic doc), batch-call `fetch_epss(new_cve_ids)`, and merge
`epss_score` / `epss_percentile` / `epss_updated_at` into the new doc. Existing
topics are **not** refetched on the ingest path — the cron owns refresh.
Fetch once per call (batched), never per-CVE.

**b) No `cve_topics` mapping change needed.** `epss_score`, `epss_percentile`,
and `epss_updated_at` are **already present** in the `cve_topics` mapping
(`opensearch.py:302-304`). The inline-on-create path just needs to populate
them; no schema work.

`fetch_epss` already exists, returns exactly the
`{cve_id: {epss_score, epss_percentile, epss_updated_at}}` shape, and batches
100 CVEs per request — **no changes to `epss_client.py`.**

## Section 3 — Cluster `max_epss` + scoring

**`max_epss` becomes a first-class cluster field.**

- Add `max_epss` (type `float`) to the `clusters` mapping in
  `app/db/opensearch.py`. The `clusters` mapping is `dynamic: strict`, so this is
  **required** — without it, `create_cluster`'s index call and `rescore_cluster`'s
  update would be rejected. `ensure_indexes()` runs `put_mapping` on existing
  indexes at startup, so the additive field applies without an index rebuild.
- `create_cluster` (`app/ingestion/clusterer.py`) seeds `"max_epss": 0.0`,
  mirroring `max_cvss`.
- `rescore_cluster` (`app/ingestion/scorer.py`) already fetches the cluster doc.
  New step: call `lookup_cve_intel(cluster.cve_ids)` (which already reads
  `epss_score` from `cve_topics`), take `max(epss_score)` across the cluster's
  CVEs, write `max_epss` back onto the cluster alongside
  `score` / `confidence` / `top_factors`, and feed it into the scorer.

**New scoring factor** in `compute_cluster_score`:

```python
if max_epss is not None and max_epss > 0:
    epss_pts = round(max_epss * 15.0, 1)
    factors.append({
        "factor": "epss",
        "label": f"EPSS {max_epss:.0%} exploit probability",
        "points": epss_pts,
    })
    total += epss_pts
```

- **Weighting rationale:** linear on the raw EPSS probability, capped at 15 pts.
  Raw EPSS is directly interpretable ("EPSS 0.62 → 62% → 9.3 pts"). 15 pts —
  not 20 — places predicted exploitation deliberately *below* CISA KEV (+20),
  which is *confirmed* exploitation. Ordering: CVSS 30 (how bad if exploited) >
  KEV +20 (confirmed exploited) > EPSS 15 (predicted likelihood).
- Raw point ceiling rises 135 → 150, still clamped to 100. KEV + EPSS stacking
  is intentional — a KEV CVE with high EPSS genuinely *is* the most urgent
  cluster.
- Update the `scorer.py` module docstring (already stale — says "six factors"
  but lists seven; will list eight).

**API exposure.** Add `max_epss: Optional[float]` to `ClusterSummary` and
`ClusterDetail` in `app/models/cluster.py`, and include it in the `_source` list
and response building in `app/api/routes/clusters.py`. The EPSS factor also
rides along in `top_factors` for the "Why it matters" UI for free.

## Section 4 — daily EPSS refresh (reuse existing `scripts/refresh_epss.py`)

**`scripts/refresh_epss.py` already exists and is complete.** It scrolls all
`cve_topics`, batch-calls `fetch_epss` (100/request), and bulk-updates
`epss_score` / `epss_percentile` / `epss_updated_at` — non-write-once, with
`--dry-run` and `--limit`, and an async `main()`. **Do not create a new
`sync_epss.py`.** The earlier draft of this spec overlooked the existing script.

Remaining work for this section is purely operational: ensure `refresh_epss.py`
is **scheduled to run daily** (check docker-compose / crontab — confirm whether
it is already wired; if not, add it alongside `sync_cisa_kev`). No code change to
`refresh_epss.py` itself is anticipated.

**Known limitation (documented, accepted):** the cron updates `cve_topics` but
clusters only recompute `max_epss` / `score` on their next rescore (next article
merge). An EPSS shift lags until a cluster sees activity — the same lag
`max_cvss` already has. "Cron also rescores affected clusters" is a noted future
follow-up, out of scope here.

## Section 5 — `scripts/rebuild_all.py` update

`rebuild_all.py` currently runs NER → embeddings → clustering. EPSS sync must be
inserted **before** clustering, because `rescore_cluster` (invoked inside
`cluster_article`) reads EPSS from `cve_topics` to compute `max_epss`. If EPSS is
stale/absent when clustering runs, every cluster scores with `max_epss = 0`.

**New sequence:** NER → embeddings → **EPSS refresh** → clustering.

- Add `run_epss_sync(force, dry_run)` that invokes `scripts/refresh_epss.py`'s
  async `main`. `refresh_epss.main` takes a `Namespace(dry_run, limit)`; pass
  `limit=0`. EPSS always overwrites, so `force` has no meaning here and is
  ignored by the wrapper.
- Add a `--skip-epss` flag, consistent with the existing
  `--skip-ner` / `--skip-embed` / `--skip-cluster` flags.
- Inline-on-create (Section 2a) still covers any *new* CVE topics created during
  the clustering step; the explicit EPSS refresh step refreshes all
  *pre-existing* topics. Together they cover the full corpus.

## Section 6 — Testing (TDD)

- `compute_cluster_score` is a pure function — unit tests for the EPSS factor:
  `max_epss=0.62 → 9.3 pts`; `None → no factor`; `0.0 → no factor`; 100-point
  clamp still holds with the factor added.
- **Pipeline reorder:** a CVE present only in the body (not the RSS snippet) ends
  up in `cve_ids` and reaches `_apply_cve_intel`; the CVE/severity write-back
  fires even when the article has **zero NER entities**.
- **EPSS-on-creation:** a newly created topic gets EPSS populated; an existing
  topic is left untouched by the ingest path.
- **`rescore_cluster`:** `max_epss` correctly computed as the max across member
  CVEs.
- **`rebuild_all.py`:** the EPSS refresh step runs before clustering and
  `--skip-epss` skips it.

## Rollout

Code changes only affect *new* articles and *newly rescored* clusters. Picking up
EPSS across the existing corpus is done by running `scripts/rebuild_all.py`
(updated per Section 5). Per the standing "no unilateral actions" rule, the
rebuild run is a **separate, explicitly-authorized step** — executed only after
the code lands and tests pass, with explicit go-ahead.

## Files touched

| File | Change |
|---|---|
| `app/ingestion/ingester.py` | Move `_apply_cve_intel` post-NER; merge NER CVEs into `cve_ids`; single combined OS update |
| `app/ingestion/cve_topic_manager.py` | Inline EPSS fetch on topic creation (both paths) |
| `app/db/opensearch.py` | Add `max_epss` to `clusters` mapping (`cve_topics` EPSS fields already exist) |
| `app/ingestion/clusterer.py` | Seed `max_epss: 0.0` in `create_cluster` |
| `app/ingestion/scorer.py` | EPSS factor in `compute_cluster_score`; `max_epss` lookup + write in `rescore_cluster`; docstring fix |
| `app/models/cluster.py` | `max_epss` on `ClusterSummary` / `ClusterDetail` |
| `app/api/routes/clusters.py` | Include `max_epss` in `_source` + response |
| `scripts/refresh_epss.py` | Reuse existing script — schedule daily; no code change expected |
| `scripts/rebuild_all.py` | Add EPSS refresh step before clustering; `--skip-epss` flag |
| `tests/` | New tests per Section 6 |
