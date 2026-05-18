# Clustering Quality — Phase 1 Design Spec
_2026-05-18_

## Problem

Cluster state today: 1,838 clusters, 1,701 (92%) single-article. Clustering is
greedy and incremental — each article picks a cluster from a small candidate pool
retrieved through three narrow keyholes. Four structural leaks cause the singletons:

| # | Leak | Location | Effect |
|---|------|----------|--------|
| 1 | Embedding sees only `title + summary[:400]` | `clusterer.py:20-28` | The model never reads the article body where entities/context live |
| 2 | k-NN candidate window is 72h | `unified_scorer.py:29,168` | Articles >3 days apart can only match via exact CVE/alias/campaign |
| 3 | Structured retrieval keys = CVE/alias/campaign only | `unified_scorer.py:111-122` | Two APT28 articles with no shared CVE never retrieve each other |
| 4 | `vendor` excluded from scoring | `unified_scorer.py:56` | Niche vendors (Ivanti, Fortinet) give zero clustering signal |

A fifth issue is a design flaw, not a leak: embedding contributes max 0.30 against a
0.31 threshold, so a pure embedding match can **never** merge two articles on its own
(enforced by `test_score_embedding_only_cannot_exceed_threshold`). Embedding should be
a first-class merge signal for strongly-similar articles.

## Goals

- Reduce singleton rate by widening candidate retrieval and making embedding a real
  merge signal.
- No hardcoded stoplists — generic-entity noise handled by data-driven IDF.
- Every retrieval/scoring change must be order-independent of arrival time within the
  configured windows.

## Non-goals

- Global graph rebuild / community detection — deferred to Phase 2 (separate spec).
- Moving embedding generation into OpenSearch ML Commons — deferred to Phase 2.
- Nested per-chunk vector storage — Phase 1 averages chunks into one vector.
- Changing CVE/alias/actor scoring semantics beyond the weight rebalance below.

---

## Component 1 — Entity-aware, chunked embeddings

### Embedding input

Per chunk, the text fed to the embedder is:

```
<title>. <body_chunk>
Entities: <comma-joined entity normalized_keys>
```

- `body` is `strip_html(content_html)`, falling back to `summary`/`desc` when no
  body HTML exists.
- The `Entities:` line makes the vector entity-aware — two articles sharing entities
  embed closer even with different prose.
- The title and entity line are repeated on **every** chunk so each chunk vector
  stays anchored to article identity; only the body portion varies between chunks.

### Adaptive chunking

- `_CHUNK_CHARS = 1800` (≈ 512 tokens for `bge-large-en-v1.5`; the model truncates
  precisely at its own token limit regardless).
- If `len(body) <= _CHUNK_CHARS`: single chunk, single embedding (current behavior
  path, just with full body + entities instead of `summary[:400]`).
- If longer: split `body` into consecutive `_CHUNK_CHARS` slices. Build one input
  string per slice. Batch all chunk inputs to the sidecar `/embed/batch` endpoint.
  Average the returned vectors element-wise into one article vector.
- Averaging normalized vectors does not need re-normalization — the cosine in
  `_compute_score()` normalizes both operands.

### Shared builder

`_build_embed_input()` is currently duplicated in `clusterer.py:23` and
`backfill_embeddings.py:30`. Unify: a single module (`app/ingestion/embedding_input.py`)
exposes:

- `build_chunk_inputs(article: dict, entity_keys: list[str]) -> list[str]` — returns
  the list of chunk input strings (length 1 for short articles).
- `embed_article(article: dict, entity_keys: list[str]) -> list[float] | None` —
  builds chunk inputs, calls `embed_batch()`, averages, returns the article vector.
  Returns `None` if every chunk embedding failed.

Both `clusterer.py` and `backfill_embeddings.py` call `embed_article()`. The old
`_build_embed_input()` functions and `_EMBED_INPUT_MAX` are deleted.

### Files

| File | Change |
|------|--------|
| `app/ingestion/embedding_input.py` | New — `build_chunk_inputs()`, `embed_article()` |
| `app/ingestion/clusterer.py` | Delete `_build_embed_input()`/`_EMBED_INPUT_MAX`; call `embed_article()` |
| `scripts/backfill_embeddings.py` | Delete `_build_embed_input()`; call `embed_article()` |

---

## Component 2 — Configurable retrieval windows

In `unified_scorer.py`:

- Replace `_EMBED_WINDOW_HOURS = 72` with
  `_EMBED_WINDOW_DAYS = int(os.getenv("CLUSTER_EMBED_WINDOW_DAYS", "30"))`.
- Replace `_STRUCTURED_WINDOW_DAYS = 14` with
  `_STRUCTURED_WINDOW_DAYS = int(os.getenv("CLUSTER_STRUCTURED_WINDOW_DAYS", "30"))`.
- The k-NN post-filter (`unified_scorer.py:168`) and the structured `range` filter
  use the new cutoffs.

`.env.example` gains `CLUSTER_EMBED_WINDOW_DAYS=30` and
`CLUSTER_STRUCTURED_WINDOW_DAYS=30`.

Rationale: exact-term matches (shared CVE, shared alias) are high-precision, so a 30d
structured window is safe; a shared CVE five weeks apart should cluster. The embedding
window stays bounded at 30d so k-NN never matches against long-dead clusters.

---

## Component 3 — Structured retrieval on all entity keys

`_structured_lookup()` in `unified_scorer.py` currently builds `should` clauses against
`event_signature.cve_ids`, `event_signature.vuln_aliases`,
`event_signature.campaign_names`.

Replace with: one `should` clause per article entity `normalized_key` against the
cluster's flat `entity_keys` field:

```python
should_clauses = [
    {"term": {"entity_keys": _retrieval_key(e)}}
    for e in article_entities
]
```

- `_retrieval_key(e)` uppercases CVE keys (`cve-2025-1234` → `CVE-2025-1234`) because
  `entity_keys` stores CVE IDs uppercase; all other types pass through lowercase.
- `minimum_should_match: 1`, `size: 20`, same `latest_at`/`state` filters as today.
- OpenSearch scores by number of matched `should` clauses, so clusters sharing more
  entities with the article float to the top 20 naturally — generic single-entity
  matches do not crowd out strong multi-entity matches.
- If `article_entities` is empty, `_structured_lookup()` returns `[]` (unchanged).

No schema change — `entity_keys` already holds every entity type and is already in
`_SOURCE_FIELDS`.

---

## Component 4 — IDF-weighted entity overlap

### Frequency source

A new helper `app/ingestion/entity_idf.py`:

- `build_idf_map() -> dict[str, float]` — runs a `terms` aggregation on
  `normalized_key` over the `entities` index to get document frequency `df` per
  entity, and a `count` for total articles `N`. Returns `{normalized_key: log(N / df)}`.
  Entities with `df` ≥ `N` get IDF clamped to a small floor (e.g. `0.01`) rather than 0.
- Module-level cache `_IDF_MAP: dict[str, float]` and `refresh_idf_map()` that
  populates it.
- `idf(key: str) -> float` — lookup with a default for unseen keys equal to the
  maximum observed IDF (an unseen entity is treated as rare/specific).

`refresh_idf_map()` is called:
- At ingestion-worker startup (alongside `refresh_entity_intel()` in `main.py`).
- At the start of a `cluster_articles.py` run.

Staleness is acceptable — document frequencies move slowly; a startup-time snapshot
is sufficient for Phase 1.

### Scoring change

In `_compute_score()`:

- Remove `"vendor"` from the exclusion tuple on line 56. `art_others` / `cl_others`
  now include vendor entities.
- Replace the plain Jaccard:
  ```python
  union_others = art_others | cl_others
  shared = art_others & cl_others
  num = sum(idf(k) for k in shared)
  den = sum(idf(k) for k in union_others)
  entity_jaccard = num / den if den else 0.0
  ```
- The `_W_ENTITY` term and all other components are otherwise unchanged.

Effect: `microsoft` (in hundreds of articles) gets IDF ≈ floor and barely moves the
score; `ivanti` (in a handful) gets a high IDF and is a strong signal. One mechanism
covers noisy vendors, products, and tools — no hardcoded stoplist.

---

## Component 5 — Embedding as a first-class merge signal

### Calibration curve

In `_compute_score()`, after computing raw `cosine`:

```python
_EMBED_LO = 0.70
_EMBED_HI = 0.90
embed_signal = max(0.0, min(1.0, (cosine - _EMBED_LO) / (_EMBED_HI - _EMBED_LO)))
```

- `cosine <= 0.70` → 0 (unrelated / loosely related contributes nothing).
- `cosine >= 0.90` → 1.0 (same-event coverage range — full signal).
- Linear between.

The `_W_EMBED` term uses `embed_signal` instead of raw `cosine`.

### Weight rebalance

| Component | Old `_W_*` | New `_W_*` |
|-----------|-----------|-----------|
| CVE       | 0.10 | 0.10 |
| alias     | 0.15 | 0.15 |
| actor     | 0.25 | 0.22 |
| entity    | 0.20 | 0.18 |
| embed     | 0.30 | 0.35 |

Sum = 1.00. `ASSIGN_THRESHOLD` stays 0.31.

Consequence: a saturated embedding signal alone scores `0.35 ≥ 0.31` → merges. A
cosine of 0.85 → signal 0.75 → `0.26` → needs a small entity/CVE nudge. A cosine of
0.80 → signal 0.50 → `0.175`. All weights stay env-overridable via the existing
`CLUSTER_WEIGHT_*` vars.

### Test change

`tests/test_unified_scorer.py`:

- `test_score_embedding_only_cannot_exceed_threshold` is replaced by two tests:
  - `test_score_high_embedding_alone_merges` — `cosine = 0.95`, no shared entities →
    score `≥ ASSIGN_THRESHOLD`.
  - `test_score_moderate_embedding_alone_does_not_merge` — `cosine = 0.80`, no shared
    entities → score `< ASSIGN_THRESHOLD`.
- `test_score_actor_plus_embed_exceeds_threshold` is updated for the new weights /
  calibration curve.
- New tests: IDF-weighted overlap (common entity contributes ~0, rare entity strong);
  vendor now counted; calibration curve boundary values.

---

## Files touched (summary)

| File | Change |
|------|--------|
| `app/ingestion/embedding_input.py` | New — chunk builder + `embed_article()` |
| `app/ingestion/entity_idf.py` | New — IDF map builder + cache |
| `app/ingestion/clusterer.py` | Use `embed_article()`; drop old builder |
| `app/ingestion/unified_scorer.py` | Windows, broadened retrieval, IDF scoring, calibration curve, weights |
| `app/main.py` | Call `refresh_idf_map()` at startup |
| `scripts/backfill_embeddings.py` | Use `embed_article()`; drop old builder; add a `--force` flag to re-embed already-embedded articles (current `_scroll_unembedded` only picks up unembedded ones) |
| `scripts/cluster_articles.py` | Call `refresh_idf_map()` at run start |
| `.env.example` | `CLUSTER_EMBED_WINDOW_DAYS`, `CLUSTER_STRUCTURED_WINDOW_DAYS` |
| `tests/test_unified_scorer.py` | Flip embedding test, add IDF/calibration tests |
| `tests/test_embedding_input.py` | New — chunking, entity line, averaging |

---

## Rollout

Changes 2, 3, 5 affect newly-ingested articles immediately. Changes 1 and 4 only
fully apply once historical data is regenerated. Required closing sequence:

1. Rebuild + restart the ingestion container with the new code.
2. `backfill_embeddings.py` with `--force`-equivalent to regenerate every article
   vector with the chunked, entity-aware input.
3. `cluster_articles.py --reset` (~13 min) to re-cluster from scratch with the new
   retrieval, IDF scoring, and calibration curve.
4. Re-check the singleton rate against the 92% baseline.

This rollout is operator-run (per project rule: no unilateral rebuilds/resets).
