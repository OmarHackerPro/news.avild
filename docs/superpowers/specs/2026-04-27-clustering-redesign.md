# Clustering Redesign: NER + Embeddings + Unified Scoring

## Context

The current 3-tier clustering system (CVE → entity → MLT) has the right architecture but is broken in practice: ~94% of articles have no extracted entities because the regex/gazetteer NER misses almost everything. Tiers 1 and 2 rarely fire, so the MLT text-similarity fallback handles nearly all clustering. MLT generates false merges because generic security vocabulary (CISA advisories, threat intel roundups) matches on boilerplate rather than actual event identity. All the MLT band-aids (stop words, thresholds, size limits, naked-series guard) treat symptoms rather than root cause.

The fix: AI-based NER at ingest (Claude Haiku) to push entity coverage from ~6% to ~75%+, combined with replacing MLT with local GPU-accelerated vector embeddings and a unified composite scoring model. A cluster merge detection job catches the cases where the same event spawned two separate clusters before they could be linked.

All work happens on a `feat/clustering-redesign` branch. Production on `main` is untouched until deliberate merge.

---

## Architecture

**Same online per-article assignment model** — articles are assigned to clusters as they arrive. The 3-tier waterfall is replaced by a unified scorer that combines all signals into a single score.

```
Article arrives
     │
     ▼
[Existing normalizer]              ← unchanged
     │
     ▼
[LLM NER — app/ingestion/ner_llm.py]    ← NEW (Claude Haiku, cached in Postgres)
     │  extracts: cve, product+version, malware, actor, tool, vuln_alias, campaign
     ▼
[Embedding — app/ingestion/embedding_client.py]  ← NEW (calls kiber-embedder container)
     │  model: BAAI/bge-large-en-v1.5, GPU, 1024-dim
     ▼
[Unified Scorer — app/ingestion/unified_scorer.py]  ← REPLACES 3-tier waterfall
     │
     ├── candidate retrieval (parallel):
     │     1. Structured terms query on event_signature fields (14-day window)
     │     2. ANN k-NN search on centroid_embedding (72-hour window, top-10)
     │
     ├── score each candidate:
     │     0.45·cve_overlap + 0.25·alias_overlap + 0.15·entity_jaccard + 0.15·cosine_sim
     │
     └── best above 0.30 → merge | none above → new cluster
          │
          ▼
     [merge_into_cluster()]  ← updated to also update centroid_embedding + event_signature
```

**Separate background job** (`scripts/detect_cluster_merges.py`, runs every 4 hours) catches duplicate clusters that formed before they could be linked.

---

## New Components

### 1. LLM NER — `app/ingestion/ner_llm.py`

Calls Claude Haiku with structured output (tool_use) to extract entities from article title + summary. Returns a validated Pydantic model. Results cached in Postgres `ner_cache` table by slug — no re-extraction on cluster rebuilds.

**Entity types extracted:**

| Type | Examples | New? |
|---|---|---|
| `cve` | `CVE-2024-1234` | No (better coverage than regex) |
| `product` | `fortigate-7.4.2`, `windows-11-23h2` | No (now includes version) |
| `malware` | `lockbit-3.0`, `blackcat` | No |
| `actor` | `apt29`, `volt-typhoon` | No |
| `tool` | `cobalt-strike`, `mimikatz` | No |
| `vuln_alias` | `log4shell`, `heartbleed`, `citrixbleed` | **YES** |
| `campaign` | `moveit-campaign`, `solarwinds-breach` | **YES** |

Integration with `entity_extractor.py`: LLM NER runs first and produces the authoritative list. Existing regex extractor runs as supplement — results merged and deduplicated by `normalized_key`. Existing tests still pass.

Fallback: if LLM call fails or times out → regex extractor only, failure logged, article not dropped.

4–5 few-shot examples in system prompt covering all entity types including `vuln_alias` and `campaign`.

### 2. Embedding Service — `services/embedder/`

Docker container (`kiber-embedder`) running `BAAI/bge-large-en-v1.5` (1024-dim) on the RTX 3050 via CUDA. Defined in `docker-compose.override.yml` (already exists as untracked file).

- Input per article: `"Represent this cybersecurity article for finding related articles: {title}. {summary or desc[:400]}"`
- FastAPI endpoints: `POST /embed` (single), `POST /embed/batch` (up to 256)
- GPU inference: ~15ms/article → 2s timeout in client has ample headroom
- Model weights cached in a named Docker volume (downloaded once on first start)

Client: `app/ingestion/embedding_client.py` — async HTTP wrapper around the service.

### 3. Unified Scorer — `app/ingestion/unified_scorer.py`

Replaces `find_cluster_by_cve()`, `find_cluster_by_entities()`, `find_cluster_by_mlt()` entirely.

**Candidate retrieval** (two parallel async OpenSearch queries):
1. `terms` query on `event_signature.cve_ids`, `event_signature.vuln_aliases`, `event_signature.campaign_names` — 14-day window
2. k-NN search on `centroid_embedding` (cosine similarity) — 72-hour window, top-10 results

Union of both result sets (deduplicated by cluster ID) → typically 5–20 candidates.

**Score formula** (weights tunable via env vars `CLUSTER_WEIGHT_CVE`, etc.):
```
score(A, C) = 0.45 · cve_overlap(A, C)        # binary: any CVE in both
            + 0.25 · alias_overlap(A, C)       # binary: any vuln_alias or campaign in both
            + 0.15 · entity_jaccard(A, C)      # |shared entities| / |union entities|
            + 0.15 · cosine_sim(A.embedding, C.centroid_embedding)
```

Assignment threshold: `0.30` (tunable via `CLUSTER_SCORE_THRESHOLD` env var). A pure embedding-only match (max 0.15) can never trigger assignment alone — requires at least some structured signal.

Best scoring cluster above threshold wins. If none qualify → new cluster created.

### 4. Event Signature

New `event_signature` object field on cluster documents. Built from union of member article entities, updated on every merge via scripted update.

```json
{
  "event_signature": {
    "cve_ids": ["CVE-2024-1234"],
    "vuln_aliases": ["citrixbleed"],
    "campaign_names": ["moveit-campaign"],
    "affected_products": ["netscaler-adc-13.1", "netscaler-gateway"],
    "primary_actors": ["fin7"],
    "confidence": "high"
  }
}
```

`confidence` values:
- `high`: 2+ CVEs OR (1 CVE + at least 1 vuln_alias)
- `medium`: entity overlap only (no CVE, no alias)
- `low`: embedding-only assignment

### 5. Cluster Centroid Embedding

`centroid_embedding` (1024-dim dense vector) stored on each cluster document. Updated in `merge_into_cluster()` via scripted update:

```
new_centroid = (old_centroid × (n-1) + article_embedding) / n
```

New clusters: centroid initialized to the seeding article's embedding.

### 6. Merge Detection — `scripts/detect_cluster_merges.py`

Runs every 4 hours (cron in ingestion container or standalone Docker Compose service).

1. **Candidate pairs**: for each cluster updated in last 24h, find clusters whose `event_signature` overlaps on ≥1 field (`cve_ids`, `vuln_aliases`, `campaign_names`)
2. **Merge score**: same formula as unified scorer. Threshold: **0.55** (higher because merging established clusters is a larger commitment than assigning one article)
3. **Merge execution**: smaller cluster (by `article_count`) dissolved into larger — all `article_ids`, `entity_keys`, `event_signature`, timeline entries, `centroid_embedding` (weighted average by article count) merged in
4. **Tombstone**: dissolved cluster gets `state: resolved`, `merged_into: <surviving_cluster_id>` — API links redirect to surviving cluster

---

## Data Model Changes

### Postgres — new Alembic migration

```sql
CREATE TABLE ner_cache (
  slug       TEXT        PRIMARY KEY,
  entities_json JSONB    NOT NULL,
  extracted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### OpenSearch — `news_articles` index (`app/db/opensearch.py`)

Add field:
- `article_embedding`: `knn_vector`, dim=1024, engine=`nmslib`, space=`cosinesimil`

### OpenSearch — `clusters` index (`app/db/opensearch.py`)

Add fields:
- `centroid_embedding`: `knn_vector`, dim=1024, engine=`nmslib`, space=`cosinesimil`
- `event_signature`: object with sub-fields `cve_ids` (keyword[]), `vuln_aliases` (keyword[]), `campaign_names` (keyword[]), `affected_products` (keyword[]), `primary_actors` (keyword[]), `confidence` (keyword)
- `merged_into`: keyword (tombstone, null on live clusters)

---

## Critical Files — Modified

| File | Change |
|---|---|
| `app/ingestion/clusterer.py` | Full rewrite — replace 3-tier waterfall with calls to unified_scorer; update `merge_into_cluster()` to update centroid + event_signature |
| `app/ingestion/entity_extractor.py` | Add LLM NER as primary extractor, existing regex as supplement; merge results |
| `app/db/opensearch.py` | Add `knn_vector` fields, `event_signature`, `merged_into` to index mappings in `ensure_indexes()` |
| `docker-compose.override.yml` | Add `kiber-embedder` service with `nvidia` runtime, GPU passthrough, volume for model weights |
| `scripts/cluster_articles.py` | Minor: pass embedding to `cluster_article()`; handle new cluster doc structure |

## New Files

| File | Purpose |
|---|---|
| `app/ingestion/ner_llm.py` | Claude Haiku NER client — system prompt, few-shot examples, tool_use, Pydantic model, cache read/write |
| `app/ingestion/embedding_client.py` | Async HTTP client for kiber-embedder service |
| `app/ingestion/unified_scorer.py` | Score formula, candidate retrieval (structured + ANN), threshold logic, new cluster creation |
| `services/embedder/main.py` | FastAPI embedding service (bge-large-en-v1.5, CUDA) |
| `services/embedder/Dockerfile` | CUDA-based image (pytorch + sentence-transformers) |
| `services/embedder/requirements.txt` | Dependencies for embedder service |
| `scripts/backfill_ner.py` | Scroll all articles → LLM NER → write to ner_cache + update article entity_keys in OpenSearch |
| `scripts/backfill_embeddings.py` | Scroll all articles → batch embed → update article_embedding in OpenSearch |
| `scripts/detect_cluster_merges.py` | Merge detection job (candidate pairs → score → merge execution) |
| `alembic/versions/<hash>_add_ner_cache.py` | Migration for ner_cache Postgres table |

## Retired (delete or mark as deprecated)

- `find_cluster_by_mlt()` and all MLT constants: `_MLT_STOP_WORDS`, `_MLT_SCORE_THRESHOLD`, `_MLT_MAX_CLUSTER_SIZE`
- Naked-series guard in `find_cluster_by_mlt()`
- `_SIGNAL_TYPES` constant (unified scorer handles entity type filtering internally)

---

## Implementation Order

1. `git checkout -b feat/clustering-redesign`
2. Write spec to `docs/superpowers/specs/2026-04-27-clustering-redesign.md` and commit
3. Alembic migration: `ner_cache` table
4. OpenSearch mapping updates in `app/db/opensearch.py` (`ensure_indexes()`)
5. Embedding service: `services/embedder/` (Dockerfile, main.py, requirements.txt) + `docker-compose.override.yml` entry
6. `app/ingestion/embedding_client.py`
7. `app/ingestion/ner_llm.py` (Claude Haiku NER, cache, fallback)
8. Update `app/ingestion/entity_extractor.py` to call LLM NER first, merge with regex results
9. `app/ingestion/unified_scorer.py`
10. Rewrite `app/ingestion/clusterer.py` (replace 3-tier waterfall; update `merge_into_cluster()`)
11. Update `scripts/cluster_articles.py` for new signatures
12. `scripts/backfill_ner.py`
13. `scripts/backfill_embeddings.py`
14. `scripts/detect_cluster_merges.py`
15. Run migration → backfill NER → backfill embeddings → `--reset` rebuild → verify

---

## Verification

1. **Embedding service health**: `curl -X POST http://localhost:8001/embed -d '{"text":"test"}' -H 'Content-Type: application/json'` → returns 1024-element array
2. **NER quality check**: `python scripts/backfill_ner.py --dry-run --limit 10` → inspect extracted entities; confirm `vuln_alias` and `campaign` types fire on known articles
3. **NER backfill**: `python scripts/backfill_ner.py` → `ner_cache` row count matches article count in OpenSearch
4. **Embedding backfill**: `python scripts/backfill_embeddings.py` → spot-check that `article_embedding` field exists on articles in OpenSearch
5. **Cluster rebuild**: `docker compose exec ingestion python scripts/cluster_articles.py --reset`
6. **Spot-check known problem cases**:
   - CISA advisories: should NOT all merge into one giant cluster
   - Log4Shell articles: should cluster together via `vuln_alias: log4shell`
   - Newsletter roundups: should NOT merge with single-CVE articles
   - Same CVE, different days: should cluster correctly via `event_signature.cve_ids`
7. **Merge detection**: `python scripts/detect_cluster_merges.py --dry-run` → inspect candidate pairs for reasonableness
8. **Confidence distribution**: query clusters index, check `event_signature.confidence` distribution — expect a mix of high/medium/low, not predominantly low
9. **Rebuild time**: should be faster than the previous ~13 min baseline
