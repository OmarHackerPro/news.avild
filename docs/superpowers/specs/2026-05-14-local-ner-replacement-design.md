# Local NER replacement — design spec

**Date:** 2026-05-14
**Status:** Phase 1 design, awaiting implementation plan
**Owner:** @omar-shukurov

## Why

Per-article NER currently runs through Claude Haiku (`app/ingestion/ner_llm.py`). This is the largest recurring Anthropic API cost in the ingestion pipeline and uses a frontier LLM for what is essentially a token-classification task. We replace it with a local encoder-NER model running as a sidecar service. Daily briefs continue to use the Anthropic API (low volume, high value, different problem).

## Non-goals

- Removing Anthropic NER from the codebase. `ner_llm.py` stays as a backfill/eval tool, off the hot path.
- GPU inference. CPU-only for portability to a VPS.
- Auto-suggester for new `vuln_alias` entries (deferred to Phase 2).
- Quantization / ONNX optimization (deferred unless CPU throughput becomes a bottleneck).
- Adding new entity types beyond the existing seven.

## What's shipping

1. **`kiber-ner` sidecar service** — new Docker Compose service running a small FastAPI app that loads `attack-vector/SecureModernBERT-NER` at startup and exposes a single inference endpoint. CPU-only.
2. **Replacement of the Haiku call** in `app/ingestion/entity_extractor.py` with an HTTP call to the sidecar. New module `app/ingestion/ner_client.py` (thin httpx wrapper). `ner_llm.py` stays in tree, no longer called from the hot path.
3. **`ner_cache` schema change** — composite unique key on `(slug, model_version)`. Old Haiku rows backfilled to `model_version='haiku-4-5'`. New rows under `'securebert-v1'`. Active model selected by `NER_ACTIVE_MODEL` env var.
4. **`vuln_alias` curated list** — seeded from one-time `ner_cache` dump UNION a hand-curated canonical list of famous named vulnerabilities. Added to `data/threat_keywords.json` and wired into `entity_extractor.py` alongside existing actor/malware/tool keyword matching.
5. **Eval harness** — `scripts/eval_ner.py` computes the local-vs-Haiku diff from `ner_cache` (no fresh Haiku calls). `/admin/ner-eval` adjudication UI with per-entity verdicts. Verdicts persist to new Postgres table `ner_eval_judgments`.
6. **Cutover script** — `scripts/cutover_ner.py` performs ordered backfill, entity-link refresh, and triggers cluster `--reset` rebuild.

## Architecture

### Sidecar service

A new container `ner` in `docker-compose.yml`:

```
ner:
  build:
    context: .
    dockerfile: Dockerfile.ner
  environment:
    NER_MODEL_ID: attack-vector/SecureModernBERT-NER
    NER_MODEL_REVISION: <pinned commit sha>     # for reproducibility
    NER_MAX_TOKENS: 4096                         # input cap; see Input length below
    NER_CONFIDENCE_THRESHOLD: 0.5
    LOG_LEVEL: INFO
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:8001/health"]
    interval: 30s
    timeout: 5s
    retries: 3
  restart: unless-stopped
```

`Dockerfile.ner` installs `torch` (CPU-only wheel via `--index-url https://download.pytorch.org/whl/cpu`), `transformers`, `fastapi`, `uvicorn`. The model is **baked into the image at build time** by running a `huggingface-cli download` step pinned to a specific revision SHA — eliminates runtime download fragility. Adds ~1.2 GB to this image only; `Dockerfile.backend` is unchanged.

The sidecar app:
- Single endpoint `POST /extract` taking `{"slug": str, "title": str, "body": str}` and returning `{"entities": [{"type": str, "name": str, "normalized_key": str, "score": float, "char_offset": int}], "model_version": str}`.
- `GET /health` returns 200 once the model is loaded; 503 before. Container exits non-zero if model load fails — no silent degradation.
- Single `asyncio.Lock` around `model.forward()` to serialize inference across concurrent ingestion requests (multiple workers calling at once must not interleave through the same model object).
- Logs p50/p99 inference latency per request and a Prometheus-friendly counter for inference failures.

### Ingestion side

`app/ingestion/ner_client.py` replaces `ner_llm.py` as the called module from `entity_extractor.py`:

```
async def extract_entities_local(
    slug: str, title: str, body: str, db_session: AsyncSession | None
) -> list[dict]
```

Same signature shape as `extract_entities_llm` to minimize merge-logic churn. Internally:
- Reads `NER_ACTIVE_MODEL` (default `'securebert-v1'`).
- Cache check: `SELECT entities_json FROM ner_cache WHERE slug=:slug AND model_version=:version`.
- On miss: HTTP `POST http://ner:8001/extract` with `httpx.AsyncClient` (shared at module scope), timeout 30s.
- Slug-normalizes the returned `name` via the existing `_normalize_key()` helper from `entity_extractor.py` before producing the `normalized_key`.
- Writes result to `ner_cache` with the model version on success. On HTTP failure, logs and returns `[]` (matching current `ner_llm.py` failure behavior for *inference* errors). The container-level health check handles *load* failure separately.

`extract_entities()` in `entity_extractor.py` is updated to call `extract_entities_local` instead of `extract_entities_llm`. The merge logic (model entities first, then regex fills gaps with prefix-suppression for more-specific variants like `lockbit-3.0` suppressing `lockbit`) is unchanged.

### Schema mapping

| SecureModernBERT-NER label | Internal `type` | Source |
|---|---|---|
| PRODUCT | product | model |
| MALWARE | malware | model |
| THREAT-ACTOR | actor | model |
| TOOL | tool | model |
| CAMPAIGN | campaign | model |
| CVE | cve | model (regex also catches; merge dedupes) |
| _(no equivalent)_ | vuln_alias | curated regex list |

Labels not in the table (SecureModernBERT-NER also emits INDICATOR, ORGANIZATION, SYSTEM, etc.) are filtered out at the sidecar before returning. We don't store entity types we don't use.

### Input length

Title + body, capped at **4096 tokens** (~16K chars). Below ModernBERT's 8K context limit, conservatively chosen to bound worst-case CPU latency to ~1s per article. Most cyber news articles are well under this cap. If eval reveals systematic entity misses in deep deepdive content, paragraph-chunking is a Phase 1.5 follow-up — not built speculatively now.

Sidecar returns `char_offset` (start position in the input body) for each entity. This is what the eval UI uses to distinguish "model-quality" disagreements from "Haiku-couldn't-see-it" disagreements (Haiku only ever saw first 500 chars).

### Cache schema

Alembic migration:
```
ALTER TABLE ner_cache ADD COLUMN model_version TEXT;
UPDATE ner_cache SET model_version = 'haiku-4-5' WHERE model_version IS NULL;
ALTER TABLE ner_cache ALTER COLUMN model_version SET NOT NULL;
ALTER TABLE ner_cache DROP CONSTRAINT ner_cache_pkey;     -- old unique-on-slug
ALTER TABLE ner_cache ADD PRIMARY KEY (slug, model_version);
```

One migration, run before deploying the new ingestion code. Old code writing slug-only rows during deploy is acceptable risk for a single-developer project — ordering is: migration → rebuild ingestion → restart. ~30 seconds of overlap.

### vuln_alias seed list

Two sources, unioned:

1. **`ner_cache` dump** — `scripts/seed_vuln_aliases.py` queries every `vuln_alias` entity Haiku has ever stored, deduplicates, normalizes keys. Expected yield: 20–30 entries (Haiku only saw 500 chars where "dubbed X" is rare).
2. **Hand-curated canonical list** — written into the same script as a Python literal. Covers famous named vulnerabilities Haiku might never have surfaced because they predate the cache or weren't mentioned in summaries:

```
CANONICAL_VULN_ALIASES = {
    "log4shell": "Log4Shell",
    "printnightmare": "PrintNightmare",
    "heartbleed": "Heartbleed",
    "citrixbleed": "CitrixBleed",
    "citrixbleed-2": "CitrixBleed 2",
    "spectre": "Spectre",
    "meltdown": "Meltdown",
    "bluekeep": "BlueKeep",
    "eternalblue": "EternalBlue",
    "zerologon": "ZeroLogon",
    "proxylogon": "ProxyLogon",
    "proxyshell": "ProxyShell",
    "follina": "Follina",
    "moveit": "MOVEit",
    "shellshock": "Shellshock",
    "poodle": "POODLE",
    "krack": "KRACK",
    "freak": "FREAK",
    "logjam": "Logjam",
    "drown": "DROWN",
    "rowhammer": "Rowhammer",
    "downfall": "Downfall",
    "kernelcare": "KernelCare",
    "specterleak": "SpecterLeak",
    "regresshion": "regreSSHion",
    "looney-tunables": "Looney Tunables",
    "dirty-pipe": "Dirty Pipe",
    "dirty-cow": "Dirty COW",
}
```

(List finalized during implementation; this is the starting set.)

The script writes the union into `data/threat_keywords.json` under the `keywords` object with type `vuln_alias`. The existing `_load_threat_data()` loader in `entity_extractor.py` picks it up automatically — no new code path needed for matching, since `_THREAT_PATTERNS` already iterates by type.

Phase 2 (separate spec) adds the auto-suggester that watches incoming articles for "dubbed X" / "tracked as X" patterns near CVE IDs and proposes new aliases for review.

### Eval harness

**Script:** `scripts/eval_ner.py`

For every article that has a `ner_cache` row under `model_version='haiku-4-5'`:
1. Look up the article body from OpenSearch.
2. Call the sidecar to produce local entities (this also fills the `'securebert-v1'` cache row as a side effect — backfill IS the eval).
3. Compute diff against the cached Haiku entities, classifying each entity as `agree | only-haiku | only-local`.
4. For each `only-local` entity, mark `input_zone = 'shared'` if its `char_offset < 500` (Haiku could have seen it), else `'new-input'` (Haiku could not have seen it).
5. Write the diff to `ner_eval_judgments` with verdict NULL (awaiting human adjudication).

Aggregate report at the end:
- Per-type counts of `agree | only-haiku | only-local`.
- Within `only-local`, the `shared` vs `new-input` split.
- Stopping criterion check: cutover-ready if the `only-haiku` rate (relative to total Haiku entities) is **<10% for product/malware/actor/tool** and **<20% for campaign**.

**Admin UI:** new route `app/api/routes/admin_ner_eval.py` mounting two pages:

- `/admin/ner-eval` — list view of articles with pending disagreements, sortable by article date or disagreement count.
- `/admin/ner-eval/{slug}` — single-article adjudication view. Shows full article body with detected entity spans highlighted (color per type). Two columns: Haiku entities and Local entities. For each entity row, three buttons: `✓ correct`, `✗ wrong`, `? skip`. Each click updates `ner_eval_judgments` and advances to the next disagreement on the same article or auto-jumps to the next article when done.

**Database table:**
```
CREATE TABLE ner_eval_judgments (
    id BIGSERIAL PRIMARY KEY,
    slug TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_normalized_key TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('haiku', 'local', 'both')),
    input_zone TEXT CHECK (input_zone IN ('shared', 'new-input')),
    verdict TEXT CHECK (verdict IN ('correct', 'wrong', 'skip', NULL)),
    judged_at TIMESTAMPTZ,
    UNIQUE (slug, entity_type, entity_normalized_key, source)
);
```

A small `/admin/ner-eval/metrics` endpoint returns running precision and recall per source per type, computed live from `ner_eval_judgments` rows where verdict is not NULL.

### Cutover

`scripts/cutover_ner.py` — single script that performs cutover with explicit confirmation prompts:

1. Snapshot current `ner_cache` to `data/ner_cache_snapshot_<timestamp>.json` (rollback insurance).
2. Run `scripts/eval_ner.py` over the full backlog. This populates both the new `securebert-v1` cache rows AND the eval table. Prints the stopping-criterion check at the end as informational output — does NOT exit non-zero. The script is a data-gathering tool, not a gatekeeper.
3. (Human checkpoint) Open `/admin/ner-eval` and adjudicate disagreements until satisfied. Re-check the live `/admin/ner-eval/metrics` endpoint — proceed only if the stopping criterion is met for product/malware/actor/tool/campaign per the thresholds in the Eval section.
4. Set `NER_ACTIVE_MODEL=securebert-v1` in `.env` and restart the ingestion container.
5. Run `scripts/cluster_articles.py --reset` (13 min full cluster rebuild — per CLAUDE.md, mandatory when entity set changes).
6. Hard-refresh the site, spot-check cluster pages.

Rollback: set `NER_ACTIVE_MODEL=haiku-4-5`, restart ingestion, re-run `scripts/cluster_articles.py --reset`. Old Haiku rows are still in `ner_cache` and the snapshot is on disk if anything got corrupted.

## Observability

- Sidecar logs structured JSON: `request_id, slug, latency_ms, entity_count, model_version, status`.
- Sidecar `/metrics` endpoint (Prometheus-format) exposing p50/p99 inference latency and inference-error counter.
- Ingestion logs at WARNING when the sidecar HTTP call fails with the slug; INFO on cache hits.
- Container exits non-zero on model-load failure (loud failure; no silent zero-entity ingestion).

## Out of scope (deferred)

- **Phase 2 — auto-suggester for new vuln_aliases.** Watches ingester for "dubbed X" / "tracked as X" patterns near CVE IDs, writes candidates to a `pending_vuln_aliases` table for human promotion.
- **GPU inference.** Phase 1 is CPU-only. If throughput becomes a bottleneck (e.g., post-VPS-move on a slow CPU), consider FP16 ONNX or quantization in a separate spec.
- **Sidecar horizontal scaling.** Single container is sufficient at current ingestion volume. If volume grows, the sidecar is stateless and trivially replicatable behind a load balancer — but that work belongs to the day we need it.
- **GLiNER zero-shot fallback** for low-confidence entities or experimental new types. Not part of Phase 1.

## Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| SecureModernBERT-NER F1 on Kiber's RSS corpus is much lower than the 0.85 it reports on benchmarks | Medium | Eval harness measures actual quality before cutover; stopping criterion blocks rollout |
| Sidecar HTTP latency dominates per-article ingestion cost | Low | ~1s p99 inference + ~5ms HTTP overhead; ingestion is already async and batched |
| Eval asymmetry (Haiku saw 500 chars, local sees 16K) masks real model regressions | Medium | `input_zone` classification separates "shared" disagreements (real model diff) from "new-input" (input asymmetry) |
| Hand-curated `vuln_alias` list misses contemporary names | Medium | Phase 2 auto-suggester closes this gap; meanwhile starting list is better than 0 entries |
| Sidecar OOM under unexpected long input | Low | Hard cap at 4096 tokens before model.forward |
| Container restart loses request in flight | Low | Inference is short (<1s); ingestion retries failed slug on next cycle |
| Re-clustering after cutover takes 13 min of stale-cluster time | Acceptable | Scheduled during low-traffic window; documented in cutover script |

## File list

**New:**
- `Dockerfile.ner`
- `app/services/ner_sidecar/main.py` (FastAPI app)
- `app/services/ner_sidecar/model.py` (model wrapper with asyncio.Lock)
- `app/ingestion/ner_client.py`
- `scripts/eval_ner.py`
- `scripts/seed_vuln_aliases.py`
- `scripts/cutover_ner.py`
- `app/api/routes/admin_ner_eval.py`
- `templates/admin_ner_eval_list.html`
- `templates/admin_ner_eval_article.html`
- `app/db/models/ner_eval_judgment.py`
- `alembic/versions/<rev>_ner_cache_model_version.py`
- `alembic/versions/<rev>_ner_eval_judgments.py`

**Edited:**
- `docker-compose.yml` (add `ner` service)
- `app/ingestion/entity_extractor.py` (swap LLM call for sidecar client; merge logic unchanged)
- `app/ingestion/ner_llm.py` (add module-level docstring marking it backfill-only)
- `data/threat_keywords.json` (vuln_alias entries added by seed script)
- `app/core/config.py` (add `NER_ACTIVE_MODEL`, `NER_SIDECAR_URL` settings)
- `app/main.py` (register admin_ner_eval router)
- Tests: `tests/test_entity_extractor.py` (mock `ner_client.extract_entities_local` instead of `ner_llm.extract_entities_llm`), `tests/test_ner_local.py` (new), `tests/test_ner_eval_admin.py` (new)

## Open questions for implementation

- Specific HF commit SHA to pin for `attack-vector/SecureModernBERT-NER`. Choose at implementation time, after smoke-testing the latest revision works on Python 3.12 + current `transformers` version.
- Exact admin auth pattern for `/admin/ner-eval` — match whatever the other `/admin/*` routes already use.
- Whether to expose `/metrics` from the sidecar publicly or only inside the Docker network. Default to internal-only.
