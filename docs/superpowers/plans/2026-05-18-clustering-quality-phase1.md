# Clustering Quality Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the 92% singleton-cluster rate by widening candidate retrieval, feeding embeddings full entity-aware article text, and making a strong embedding match able to merge articles on its own.

**Architecture:** Five changes to the existing greedy incremental clusterer — (1) entity-aware chunked embeddings, (2) configurable retrieval windows, (3) structured retrieval on all entity keys, (4) IDF-weighted entity overlap, (5) an embedding calibration curve with rebalanced weights. No new services, no schema changes.

**Tech Stack:** Python 3.12, async OpenSearch (`opensearch-py`), `numpy`, `pytest`/`pytest-asyncio`. Embeddings via the existing `embedder` sidecar (`bge-large-en-v1.5`).

**Spec:** `docs/superpowers/specs/2026-05-18-clustering-quality-phase1-design.md`

---

## File Structure

| File | Responsibility |
|------|----------------|
| `app/ingestion/entity_idf.py` | NEW — build & cache an entity document-frequency → IDF map from the `entities` index |
| `app/ingestion/embedding_input.py` | NEW — build entity-aware chunked embedding input; `embed_article()` averages chunk vectors |
| `app/ingestion/clusterer.py` | MODIFY — call `embed_article()`; delete old `_build_embed_input`/`_EMBED_INPUT_MAX` |
| `app/ingestion/unified_scorer.py` | MODIFY — configurable windows, broadened retrieval, IDF scoring, calibration curve, weights |
| `scripts/backfill_embeddings.py` | MODIFY — fetch body + entity keys, use `embed_article()`, add `--force` |
| `scripts/cluster_articles.py` | MODIFY — refresh IDF map at run start |
| `.env.example` | MODIFY — two new window env vars |
| `tests/test_entity_idf.py` | NEW |
| `tests/test_embedding_input.py` | NEW |
| `tests/test_unified_scorer.py` | MODIFY — flip embedding test, add IDF/calibration tests |

**Test command (all tasks):** tests run inside the backend container.
Run: `docker compose exec backend python -m pytest <path> -v`
If the container lacks edited files, copy them first:
`docker compose cp <file> backend:/app/<file>`

---

## Task 1: Entity IDF map module

**Files:**
- Create: `app/ingestion/entity_idf.py`
- Test: `tests/test_entity_idf.py`

Context: the `entities` OpenSearch index (`INDEX_ENTITIES`) stores one document per
entity, each with a `normalized_key` and an `article_count` (how many articles mention
it). IDF for an entity is `log(N_articles / article_count)`. Common entities
(`microsoft`) get a near-zero weight; rare ones (`ivanti`) get a high weight. The map
is built once and cached at module level; `_compute_score()` (Task 7) reads it
synchronously via `idf()`. If the map is empty (not yet built, or build failed),
`idf()` returns `_DEFAULT_IDF` for every key — so scoring degrades gracefully to plain
unweighted Jaccard.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_entity_idf.py`:

```python
"""Tests for app.ingestion.entity_idf."""
import math
import pytest


def test_idf_returns_default_when_map_empty():
    from app.ingestion import entity_idf

    entity_idf._IDF_MAP.clear()
    assert entity_idf.idf("anything") == entity_idf._DEFAULT_IDF


def test_idf_returns_mapped_value():
    from app.ingestion import entity_idf

    entity_idf._IDF_MAP.clear()
    entity_idf._IDF_MAP["ivanti"] = 5.0
    assert entity_idf.idf("ivanti") == 5.0
    assert entity_idf.idf("unseen-key") == entity_idf._DEFAULT_IDF
    entity_idf._IDF_MAP.clear()


def test_compute_idf_common_entity_is_low_rare_is_high():
    from app.ingestion.entity_idf import _compute_idf

    # 1000 articles total
    common = _compute_idf(n_articles=1000, df=900)   # in 90% of articles
    rare = _compute_idf(n_articles=1000, df=5)        # in 0.5% of articles
    assert common < 0.5
    assert rare > 4.0


def test_compute_idf_clamps_to_floor():
    from app.ingestion.entity_idf import _compute_idf, _MIN_IDF

    # entity present in every article -> log(1) = 0 -> clamped to floor
    assert _compute_idf(n_articles=1000, df=1000) == _MIN_IDF
    # df larger than N (stale count) -> still clamped, never negative
    assert _compute_idf(n_articles=1000, df=5000) == _MIN_IDF
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec backend python -m pytest tests/test_entity_idf.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ingestion.entity_idf'`

- [ ] **Step 3: Write the module**

Create `app/ingestion/entity_idf.py`:

```python
"""Entity document-frequency IDF map.

Built from the `entities` index. Common entities get a near-zero weight,
rare entities a high weight. Read synchronously by unified_scorer._compute_score
via idf(); when the map is empty, idf() returns _DEFAULT_IDF for every key so
scoring degrades to plain unweighted Jaccard.
"""
import logging
import math

from app.db.opensearch import INDEX_ENTITIES, INDEX_NEWS, get_os_client

logger = logging.getLogger(__name__)

_MIN_IDF = 0.01
_DEFAULT_IDF = 1.0

_IDF_MAP: dict[str, float] = {}


def _compute_idf(n_articles: int, df: int) -> float:
    n = max(n_articles, 1)
    d = max(df, 1)
    return max(_MIN_IDF, math.log(n / d))


def idf(key: str) -> float:
    """Synchronous IDF lookup. Returns _DEFAULT_IDF for unseen / unbuilt keys."""
    return _IDF_MAP.get(key, _DEFAULT_IDF)


async def build_idf_map() -> dict[str, float]:
    """Scan the entities index, return {normalized_key: idf}. Does not mutate cache."""
    client = get_os_client()
    count_resp = await client.count(index=INDEX_NEWS)
    n_articles = count_resp.get("count", 0)

    result: dict[str, float] = {}
    resp = await client.search(
        index=INDEX_ENTITIES,
        body={"query": {"match_all": {}}, "_source": ["normalized_key", "article_count"]},
        scroll="2m",
        size=1000,
    )
    scroll_id = resp.get("_scroll_id")
    hits = resp["hits"]["hits"]
    while hits:
        for hit in hits:
            src = hit["_source"]
            key = src.get("normalized_key")
            if not key:
                continue
            df = src.get("article_count") or 1
            result[key] = _compute_idf(n_articles, df)
        resp = await client.scroll(scroll_id=scroll_id, scroll="2m")
        scroll_id = resp.get("_scroll_id")
        hits = resp["hits"]["hits"]
    if scroll_id:
        try:
            await client.clear_scroll(scroll_id=scroll_id)
        except Exception:
            pass
    return result


async def refresh_idf_map() -> int:
    """Rebuild the cached IDF map. Returns entity count. Swallows errors (leaves
    the map as-is so scoring still degrades gracefully)."""
    try:
        new_map = await build_idf_map()
    except Exception:
        logger.warning("IDF map build failed — entity scoring falls back to plain Jaccard", exc_info=True)
        return len(_IDF_MAP)
    _IDF_MAP.clear()
    _IDF_MAP.update(new_map)
    logger.info("IDF map built: %d entities", len(_IDF_MAP))
    return len(_IDF_MAP)


async def ensure_idf_map() -> None:
    """Build the map on first use if it has not been built yet."""
    if not _IDF_MAP:
        await refresh_idf_map()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose cp app/ingestion/entity_idf.py backend:/app/app/ingestion/entity_idf.py && docker compose cp tests/test_entity_idf.py backend:/app/tests/test_entity_idf.py && docker compose exec backend python -m pytest tests/test_entity_idf.py -v`
Expected: PASS — 4 passed

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/entity_idf.py tests/test_entity_idf.py
git commit -m "feat(clustering): add entity IDF map"
```

---

## Task 2: Entity-aware chunked embedding input

**Files:**
- Create: `app/ingestion/embedding_input.py`
- Test: `tests/test_embedding_input.py`

Context: today the embedding sees only `title + summary[:400]`. New input per chunk is
`"<title>. <body_chunk>\nEntities: <comma-joined keys>"`. Body is `strip_html(content_html)`
(falling back to `summary`/`desc`). Articles whose body is ≤ `_CHUNK_CHARS` produce one
chunk; longer articles split into consecutive `_CHUNK_CHARS` slices, each embedded and
the vectors averaged into one article vector. `strip_html` is an existing function in
`app/ingestion/normalizer.py`. `embed_batch` is in `app/ingestion/embedding_client.py`
and returns a list the same length as its input with `None` for failed entries.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_embedding_input.py`:

```python
"""Tests for app.ingestion.embedding_input."""
from unittest.mock import AsyncMock, patch

import pytest


def test_short_article_produces_one_chunk():
    from app.ingestion.embedding_input import build_chunk_inputs

    article = {"title": "Ivanti VPN flaw", "content_html": "<p>Short body.</p>"}
    chunks = build_chunk_inputs(article, ["ivanti", "cve-2025-1"])
    assert len(chunks) == 1
    assert chunks[0].startswith("Ivanti VPN flaw. Short body.")
    assert "Entities: ivanti, cve-2025-1" in chunks[0]


def test_long_article_splits_into_multiple_chunks():
    from app.ingestion.embedding_input import build_chunk_inputs, _CHUNK_CHARS

    body = "<p>" + ("word " * 2000) + "</p>"  # well over _CHUNK_CHARS
    article = {"title": "Big report", "content_html": body}
    chunks = build_chunk_inputs(article, ["apt28"])
    assert len(chunks) >= 2
    # title + entity line repeat on every chunk
    for c in chunks:
        assert c.startswith("Big report. ")
        assert "Entities: apt28" in c


def test_falls_back_to_summary_when_no_body():
    from app.ingestion.embedding_input import build_chunk_inputs

    article = {"title": "T", "content_html": "", "summary": "Summary text here."}
    chunks = build_chunk_inputs(article, [])
    assert len(chunks) == 1
    assert "Summary text here." in chunks[0]
    assert "Entities:" not in chunks[0]  # empty entity list -> no line


@pytest.mark.asyncio
async def test_embed_article_averages_chunk_vectors():
    from app.ingestion import embedding_input

    article = {"title": "T", "content_html": "<p>" + ("x" * 4000) + "</p>"}
    fake_vecs = [[2.0, 0.0], [4.0, 0.0]]  # 2 chunks
    with patch.object(embedding_input, "embed_batch", new=AsyncMock(return_value=fake_vecs)):
        result = await embedding_input.embed_article(article, ["e1"])
    assert result == [3.0, 0.0]  # element-wise mean


@pytest.mark.asyncio
async def test_embed_article_returns_none_when_all_chunks_fail():
    from app.ingestion import embedding_input

    article = {"title": "T", "content_html": "<p>body</p>"}
    with patch.object(embedding_input, "embed_batch", new=AsyncMock(return_value=[None])):
        result = await embedding_input.embed_article(article, [])
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec backend python -m pytest tests/test_embedding_input.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ingestion.embedding_input'`

- [ ] **Step 3: Write the module**

Create `app/ingestion/embedding_input.py`:

```python
"""Entity-aware chunked embedding input.

Builds the text fed to the embedder: title + body + an Entities: line, split
into ~512-token chunks for long articles. embed_article() averages the chunk
vectors into one article vector.
"""
import numpy as np

from app.ingestion.embedding_client import embed_batch
from app.ingestion.normalizer import strip_html

_CHUNK_CHARS = 1800  # ~512 tokens for bge-large-en-v1.5


def _body_text(article: dict) -> str:
    html = article.get("content_html") or ""
    if html:
        return strip_html(html)
    return article.get("summary") or article.get("desc") or ""


def build_chunk_inputs(article: dict, entity_keys: list[str]) -> list[str]:
    """Return one embedding-input string per chunk (length 1 for short articles)."""
    title = article.get("title") or ""
    body = _body_text(article)
    entity_line = ("\nEntities: " + ", ".join(entity_keys)) if entity_keys else ""

    if len(body) <= _CHUNK_CHARS:
        return [f"{title}. {body}{entity_line}"]

    slices = [body[i:i + _CHUNK_CHARS] for i in range(0, len(body), _CHUNK_CHARS)]
    return [f"{title}. {s}{entity_line}" for s in slices]


async def embed_article(article: dict, entity_keys: list[str]) -> list[float] | None:
    """Embed an article (chunked, entity-aware) and average chunk vectors.

    Returns None if every chunk embedding failed.
    """
    inputs = build_chunk_inputs(article, entity_keys)
    vectors = await embed_batch(inputs)
    good = [v for v in vectors if v is not None]
    if not good:
        return None
    return np.mean(np.array(good, dtype=np.float32), axis=0).tolist()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose cp app/ingestion/embedding_input.py backend:/app/app/ingestion/embedding_input.py && docker compose cp tests/test_embedding_input.py backend:/app/tests/test_embedding_input.py && docker compose exec backend python -m pytest tests/test_embedding_input.py -v`
Expected: PASS — 5 passed

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/embedding_input.py tests/test_embedding_input.py
git commit -m "feat(clustering): add entity-aware chunked embedding input"
```

---

## Task 3: Wire clusterer.py to embed_article()

**Files:**
- Modify: `app/ingestion/clusterer.py:14,20,23-28,137`

Context: `cluster_article(article, slug, entities)` receives the article's extracted
`entities` (a list of dicts each with `type` and `normalized_key`). Line 137 currently
does `embedding = await embed_text(_build_embed_input(article))`. Replace with the
chunked, entity-aware path. The old `_build_embed_input()` and `_EMBED_INPUT_MAX` are
deleted. `embed_text` is no longer used in this file (grep confirms only line 14 import
+ line 137).

- [ ] **Step 1: Replace the import**

In `app/ingestion/clusterer.py`, change line 14 from:

```python
from app.ingestion.embedding_client import embed_text
```

to:

```python
from app.ingestion.embedding_input import embed_article
```

- [ ] **Step 2: Delete the old builder**

Delete line 20 (`_EMBED_INPUT_MAX = 400  # ...`) and lines 23-28 (the entire
`_build_embed_input` function):

```python
def _build_embed_input(article: dict) -> str:
    text = article.get("title", "")
    snippet = article.get("summary") or article.get("desc") or ""
    if snippet:
        text += ". " + snippet[:_EMBED_INPUT_MAX]
    return text
```

- [ ] **Step 3: Use embed_article at the call site**

In `cluster_article()`, change line 137 from:

```python
    embedding = await embed_text(_build_embed_input(article))
```

to:

```python
    embedding = await embed_article(article, [e["normalized_key"] for e in entities])
```

- [ ] **Step 4: Run the clusterer test suite**

Run: `docker compose cp app/ingestion/clusterer.py backend:/app/app/ingestion/clusterer.py && docker compose exec backend python -m pytest tests/test_clusterer.py -v`
Expected: PASS — existing clusterer tests still pass (any test that patched
`embed_text` on `clusterer` must now patch `embed_article`; fix such tests if they fail
by replacing the patch target with `app.ingestion.clusterer.embed_article` and making
the mock return a 1024-float list).

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/clusterer.py tests/test_clusterer.py
git commit -m "feat(clustering): clusterer uses entity-aware chunked embeddings"
```

---

## Task 4: Wire backfill_embeddings.py to embed_article() + add --force

**Files:**
- Modify: `scripts/backfill_embeddings.py`

Context: the backfill must regenerate every article vector with the new input. Two
changes: (1) it must fetch `content_html` (for the body) and each article's entity keys
(from the `entities` index), and (2) it needs a `--force` flag because today
`_scroll_unembedded` only picks up articles with no `article_embedding`. Entity keys
per slug come from the `entities` index via a `terms` query on `article_ids` — the
exact pattern already used in `scripts/cluster_articles.py:92-113` (`_get_entities_batch`).

- [ ] **Step 1: Replace the import and delete the old builder**

In `scripts/backfill_embeddings.py`, change line 23 from:

```python
from app.ingestion.embedding_client import embed_batch
```

to:

```python
from app.db.opensearch import INDEX_ENTITIES
from app.ingestion.embedding_input import embed_article
```

Delete line 27 (`_EMBED_INPUT_MAX = 400`) and lines 30-35 (the `_build_embed_input`
function).

- [ ] **Step 2: Add --force and fetch body in the scroll**

Change `_scroll_unembedded` to accept a `force` flag and fetch `content_html`:

```python
async def _scroll_articles(source: str | None, limit: int | None, force: bool) -> list[dict]:
    client = get_os_client()

    query: dict = {"bool": {}}
    if not force:
        query["bool"]["must_not"] = {"exists": {"field": "article_embedding"}}
    if source:
        query["bool"]["filter"] = [{"term": {"source_name": source}}]
    if not query["bool"]:
        query = {"match_all": {}}

    results = []
    page_size = 100
    from_offset = 0

    while True:
        remaining = (limit - len(results)) if limit is not None else page_size
        fetch_size = min(page_size, remaining) if limit is not None else page_size
        if fetch_size <= 0:
            break

        resp = await client.search(
            index=INDEX_NEWS,
            body={
                "query": query,
                "sort": [{"published_at": {"order": "asc"}}],
                "size": fetch_size,
                "from": from_offset,
                "_source": ["slug", "title", "summary", "desc", "content_html", "source_name"],
            },
        )
        hits = resp["hits"]["hits"]
        if not hits:
            break
        results.extend(hits)
        from_offset += len(hits)
        if len(hits) < fetch_size:
            break
        if limit is not None and len(results) >= limit:
            break

    return results
```

- [ ] **Step 3: Add an entity-keys batch fetch**

Add this helper after `_scroll_articles`:

```python
async def _entity_keys_for(slugs: list[str]) -> dict[str, list[str]]:
    """Return {slug: [normalized_key, ...]} for the given article slugs."""
    from collections import defaultdict

    keys: dict[str, list[str]] = defaultdict(list)
    if not slugs:
        return keys
    client = get_os_client()
    resp = await client.search(
        index=INDEX_ENTITIES,
        body={
            "query": {"terms": {"article_ids": slugs}},
            "size": 5000,
            "_source": ["normalized_key", "article_ids"],
        },
    )
    slug_set = set(slugs)
    for hit in resp["hits"]["hits"]:
        nk = hit["_source"].get("normalized_key")
        if not nk:
            continue
        for sid in hit["_source"].get("article_ids") or []:
            if sid in slug_set:
                keys[sid].append(nk)
    return keys
```

- [ ] **Step 4: Use embed_article in main()**

Replace the per-batch body of `main()` (the `texts = [...]` line and the
`embed_batch(texts)` call) so each article is embedded via `embed_article`. The new
batch loop body:

```python
    for batch_start in range(0, len(articles), batch_size):
        batch = articles[batch_start : batch_start + batch_size]
        slugs = [hit["_id"] for hit in batch]
        entity_keys = await _entity_keys_for(slugs)

        if args.dry_run:
            for i, hit in enumerate(batch):
                logger.info(
                    "[DRY RUN] %d/%d %s — entities=%d",
                    batch_start + i + 1, len(articles), hit["_id"],
                    len(entity_keys.get(hit["_id"], [])),
                )
            totals["embedded"] += len(batch)
            continue

        async def _embed_one(hit: dict) -> tuple[str, list[float] | None]:
            slug = hit["_id"]
            vec = await embed_article(hit["_source"], entity_keys.get(slug, []))
            return slug, vec

        embedded_pairs = await asyncio.gather(*[_embed_one(h) for h in batch])

        async def _update_one(slug: str, embedding: list[float] | None) -> str:
            if embedding is None:
                return "skipped"
            try:
                await _update_embedding(slug, embedding)
                return "ok"
            except Exception:
                logger.exception("Update failed for %s", slug)
                return "error"

        results = await asyncio.gather(
            *[_update_one(slug, emb) for slug, emb in embedded_pairs]
        )

        for outcome in results:
            if outcome == "ok":
                totals["embedded"] += 1
            elif outcome == "skipped":
                totals["skipped"] += 1
            else:
                totals["errors"] += 1

        processed_so_far = batch_start + len(batch)
        logger.info(
            "Progress: %d/%d — embedded=%d skipped=%d errors=%d",
            processed_so_far, len(articles),
            totals["embedded"], totals["skipped"], totals["errors"],
        )
```

Update the `_scroll_unembedded(...)` call near the top of `main()` to:

```python
    articles = await _scroll_articles(source=args.source, limit=args.limit, force=args.force)
```

- [ ] **Step 5: Register the --force argument**

In the `argparse` block at the bottom, add:

```python
    parser.add_argument("--force", action="store_true", help="Re-embed articles even if they already have an embedding")
```

- [ ] **Step 6: Smoke-test the script (dry run)**

Run: `docker compose cp scripts/backfill_embeddings.py ingestion:/app/scripts/backfill_embeddings.py && docker compose exec ingestion python scripts/backfill_embeddings.py --limit 5 --force --dry-run`
Expected: prints 5 `[DRY RUN]` lines with `entities=N` counts, no errors.

- [ ] **Step 7: Commit**

```bash
git add scripts/backfill_embeddings.py
git commit -m "feat(clustering): backfill embeddings with entity-aware input and --force"
```

---

## Task 5: Configurable retrieval windows

**Files:**
- Modify: `app/ingestion/unified_scorer.py:28-29,105-106,168`
- Modify: `.env.example`
- Test: `tests/test_unified_scorer.py`

Context: `_EMBED_WINDOW_HOURS = 72` and `_STRUCTURED_WINDOW_DAYS = 14` are hardcoded.
Both move to env vars defaulting to 30 days.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_unified_scorer.py`:

```python
def test_window_constants_default_to_30_days():
    import importlib
    from app.ingestion import unified_scorer
    importlib.reload(unified_scorer)
    assert unified_scorer._EMBED_WINDOW_DAYS == 30
    assert unified_scorer._STRUCTURED_WINDOW_DAYS == 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec backend python -m pytest tests/test_unified_scorer.py::test_window_constants_default_to_30_days -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_EMBED_WINDOW_DAYS'`

- [ ] **Step 3: Change the constants**

In `app/ingestion/unified_scorer.py`, replace lines 28-29:

```python
_KNN_K = 10
_STRUCTURED_WINDOW_DAYS = 14
_EMBED_WINDOW_HOURS = 72
```

with:

```python
_KNN_K = 10
_STRUCTURED_WINDOW_DAYS = int(os.getenv("CLUSTER_STRUCTURED_WINDOW_DAYS", "30"))
_EMBED_WINDOW_DAYS = int(os.getenv("CLUSTER_EMBED_WINDOW_DAYS", "30"))
```

In `_get_candidates()`, replace lines 105-106:

```python
    cutoff_14d = (ref - timedelta(days=_STRUCTURED_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff_72h = (ref - timedelta(hours=_EMBED_WINDOW_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
```

with:

```python
    cutoff_structured = (ref - timedelta(days=_STRUCTURED_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff_embed = (ref - timedelta(days=_EMBED_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
```

Update the two references: in `_structured_lookup()` change
`{"range": {"latest_at": {"gte": cutoff_14d}}}` to use `cutoff_structured`; in
`_knn_lookup()` (line ~168) change `>= cutoff_72h` to `>= cutoff_embed`.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose cp app/ingestion/unified_scorer.py backend:/app/app/ingestion/unified_scorer.py && docker compose exec backend python -m pytest tests/test_unified_scorer.py -v`
Expected: PASS — all tests pass.

- [ ] **Step 5: Update .env.example**

Add to `.env.example` (near other clustering vars if present, else at the end):

```
CLUSTER_EMBED_WINDOW_DAYS=30
CLUSTER_STRUCTURED_WINDOW_DAYS=30
```

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/unified_scorer.py tests/test_unified_scorer.py .env.example
git commit -m "feat(clustering): make retrieval windows env-configurable, default 30d"
```

---

## Task 6: Structured retrieval on all entity keys

**Files:**
- Modify: `app/ingestion/unified_scorer.py:111-138`
- Test: `tests/test_unified_scorer.py`

Context: `_structured_lookup()` currently builds `should` clauses only for CVE IDs,
vuln_aliases, and campaign_names against `event_signature.*` subfields. Replace with
one `should` clause per article entity against the cluster's flat `entity_keys` field.
CVE keys must be uppercased — entity `normalized_key` stores `cve-2025-1234` lowercase,
but `entity_keys` on clusters stores CVE IDs uppercase.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_unified_scorer.py`:

```python
def test_retrieval_key_uppercases_cve_only():
    from app.ingestion.unified_scorer import _retrieval_key

    assert _retrieval_key({"type": "cve", "normalized_key": "cve-2025-1234"}) == "CVE-2025-1234"
    assert _retrieval_key({"type": "actor", "normalized_key": "apt28"}) == "apt28"
    assert _retrieval_key({"type": "malware", "normalized_key": "lockbit"}) == "lockbit"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec backend python -m pytest tests/test_unified_scorer.py::test_retrieval_key_uppercases_cve_only -v`
Expected: FAIL — `ImportError: cannot import name '_retrieval_key'`

- [ ] **Step 3: Add _retrieval_key and rewrite _structured_lookup**

In `app/ingestion/unified_scorer.py`, add a module-level helper (place it just above
`_get_candidates`):

```python
def _retrieval_key(entity: dict) -> str:
    """entity_keys stores CVE IDs uppercase; all other types lowercase."""
    key = entity["normalized_key"]
    return key.upper() if entity["type"] == "cve" else key
```

In `_get_candidates()`, delete the now-unused per-type extraction lines (the
`cve_ids = [...]`, `vuln_aliases = [...]`, `campaign_names = [...]` block at lines
~111-113). Replace the body of `_structured_lookup()` with:

```python
    async def _structured_lookup() -> list[dict]:
        should_clauses = [
            {"term": {"entity_keys": _retrieval_key(e)}}
            for e in article_entities
        ]
        if not should_clauses:
            return []
        query = {
            "query": {
                "bool": {
                    "should": should_clauses,
                    "minimum_should_match": 1,
                    "filter": [
                        {"range": {"latest_at": {"gte": cutoff_structured}}},
                        {"bool": {"must_not": [{"term": {"state": "resolved"}}]}},
                    ],
                }
            },
            "_source": _SOURCE_FIELDS,
            "size": 20,
        }
        try:
            resp = await os_client.search(index=INDEX_CLUSTERS, body=query)
            return resp["hits"]["hits"]
        except Exception as exc:
            logger.warning("Structured candidate lookup failed: %s", exc)
            return []
```

- [ ] **Step 4: Run the suite to verify it passes**

Run: `docker compose cp app/ingestion/unified_scorer.py backend:/app/app/ingestion/unified_scorer.py && docker compose exec backend python -m pytest tests/test_unified_scorer.py -v`
Expected: PASS — all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/unified_scorer.py tests/test_unified_scorer.py
git commit -m "feat(clustering): retrieve candidates on all entity keys"
```

---

## Task 7: IDF-weighted entity overlap, vendor un-excluded

**Files:**
- Modify: `app/ingestion/unified_scorer.py:53-57,75-78,186-209`
- Test: `tests/test_unified_scorer.py`

Context: `_compute_score()` excludes `vendor` from `art_others`/`cl_others` and uses a
plain Jaccard. Remove the `vendor` exclusion and replace plain Jaccard with
IDF-weighted overlap: `sum(idf(k) for shared) / sum(idf(k) for union)`. When the IDF
map is empty (unit tests, build failure) `idf()` returns `_DEFAULT_IDF` for every key,
so the ratio reduces to plain Jaccard — existing tests stay valid. `find_best_cluster()`
calls `await ensure_idf_map()` before scoring so the live path has a populated map.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_unified_scorer.py`:

```python
def test_vendor_now_counts_in_entity_score():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("vendor", "ivanti")])
    cluster = _make_cluster("c1", entity_keys=["ivanti"])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert score > 0.0  # vendor overlap is no longer ignored


def test_idf_weighted_overlap_favors_rare_entity():
    from app.ingestion import unified_scorer, entity_idf

    entity_idf._IDF_MAP.clear()
    entity_idf._IDF_MAP.update({"common-tool": 0.01, "rare-tool": 6.0, "noise": 0.01})

    # article shares the RARE entity with the cluster, plus an unshared noise entity
    article = _make_article_entities([("tool", "rare-tool"), ("tool", "noise")])
    cluster = _make_cluster("c1", entity_keys=["rare-tool", "common-tool"])
    rare_score = unified_scorer._compute_score(article, cluster["_source"], None)

    # article shares only the COMMON entity instead
    article2 = _make_article_entities([("tool", "common-tool"), ("tool", "noise")])
    cluster2 = _make_cluster("c2", entity_keys=["common-tool", "rare-tool"])
    common_score = unified_scorer._compute_score(article2, cluster2["_source"], None)

    assert rare_score > common_score
    entity_idf._IDF_MAP.clear()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec backend python -m pytest tests/test_unified_scorer.py::test_vendor_now_counts_in_entity_score tests/test_unified_scorer.py::test_idf_weighted_overlap_favors_rare_entity -v`
Expected: FAIL — `test_vendor_now_counts_in_entity_score` fails (score is 0.0 because
vendor is excluded).

- [ ] **Step 3: Add the import**

In `app/ingestion/unified_scorer.py`, add near the top imports:

```python
from app.ingestion.entity_idf import ensure_idf_map, idf
```

- [ ] **Step 4: Remove the vendor exclusion and IDF-weight the overlap**

In `_compute_score()`, change the `art_others` set comprehension (line ~53-57) from:

```python
    art_others = {
        e["normalized_key"]
        for e in article_entities
        if e["type"] not in ("cve", "vuln_alias", "actor", "campaign", "vendor")
    }
```

to (drop `"vendor"`):

```python
    art_others = {
        e["normalized_key"]
        for e in article_entities
        if e["type"] not in ("cve", "vuln_alias", "actor", "campaign")
    }
```

Replace the plain-Jaccard block (lines ~75-78):

```python
    union_others = art_others | cl_others
    entity_jaccard = (
        len(art_others & cl_others) / len(union_others) if union_others else 0.0
    )
```

with IDF-weighted overlap:

```python
    union_others = art_others | cl_others
    shared_others = art_others & cl_others
    if union_others:
        num = sum(idf(k) for k in shared_others)
        den = sum(idf(k) for k in union_others)
        entity_jaccard = num / den if den else 0.0
    else:
        entity_jaccard = 0.0
```

- [ ] **Step 5: Ensure the IDF map is loaded before scoring**

In `find_best_cluster()`, add as the first line of the function body (before the
`candidates = await _get_candidates(...)` call):

```python
    await ensure_idf_map()
```

- [ ] **Step 6: Run the full suite to verify it passes**

Run: `docker compose cp app/ingestion/unified_scorer.py backend:/app/app/ingestion/unified_scorer.py && docker compose exec backend python -m pytest tests/test_unified_scorer.py -v`
Expected: PASS — all tests pass, including `test_score_perfect_match_is_one` (with an
empty IDF map, the single shared entity gives `num/den = _DEFAULT_IDF/_DEFAULT_IDF = 1.0`).

- [ ] **Step 7: Commit**

```bash
git add app/ingestion/unified_scorer.py tests/test_unified_scorer.py
git commit -m "feat(clustering): IDF-weighted entity overlap, vendor un-excluded"
```

---

## Task 8: Embedding calibration curve and weight rebalance

**Files:**
- Modify: `app/ingestion/unified_scorer.py:21-25,80-95`
- Test: `tests/test_unified_scorer.py`

Context: embedding cosine currently contributes `_W_EMBED * cosine` (max 0.30) and can
never alone clear the 0.31 threshold. New: a calibration curve maps raw cosine to
`embed_signal` (0 below 0.70, 1.0 at/above 0.90, linear between), and weights are
rebalanced so a saturated embedding alone scores 0.35 ≥ 0.31.

New weights (sum 1.00): CVE 0.10, alias 0.15, actor 0.22, entity 0.18, embed 0.35.

- [ ] **Step 1: Replace the embedding-only test and add calibration tests**

In `tests/test_unified_scorer.py`, DELETE `test_score_embedding_only_cannot_exceed_threshold`
(lines ~68-76) and ADD:

```python
def _emb_with_cosine(target: float) -> tuple[list[float], list[float]]:
    """Two unit vectors whose cosine similarity equals `target`."""
    import math
    a = [1.0] + [0.0] * 1023
    c = [target, math.sqrt(max(0.0, 1.0 - target * target))] + [0.0] * 1022
    return a, c


def test_score_high_embedding_alone_merges():
    from app.ingestion.unified_scorer import _compute_score, ASSIGN_THRESHOLD

    a, c = _emb_with_cosine(0.95)
    cluster = _make_cluster("c1", centroid=c)
    score = _compute_score([], cluster["_source"], a)
    assert score >= ASSIGN_THRESHOLD


def test_score_moderate_embedding_alone_does_not_merge():
    from app.ingestion.unified_scorer import _compute_score, ASSIGN_THRESHOLD

    a, c = _emb_with_cosine(0.80)
    cluster = _make_cluster("c1", centroid=c)
    score = _compute_score([], cluster["_source"], a)
    assert score < ASSIGN_THRESHOLD


def test_calibration_curve_zero_below_floor():
    from app.ingestion.unified_scorer import _embed_signal

    assert _embed_signal(0.70) == 0.0
    assert _embed_signal(0.50) == 0.0
    assert _embed_signal(0.90) == 1.0
    assert _embed_signal(0.95) == 1.0
    assert abs(_embed_signal(0.80) - 0.5) < 0.001
```

Also update `test_score_perfect_match_is_one`: its inline comment is now
`# 0.10 + 0.15 + 0.22 + 0.18*(1/1) + 0.35*1.0 = 1.0` — the assertion `abs(score-1.0)<0.01`
still holds. Update `test_score_actor_overlap_only` (expects 0.25 → now **0.22**),
`test_score_campaign_overlap_uses_actor_weight` (0.25 → **0.22**), and
`test_score_actor_plus_embed_exceeds_threshold` (comment → `0.22 + 0.35 = 0.57`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec backend python -m pytest tests/test_unified_scorer.py -v`
Expected: FAIL — `_embed_signal` import error; actor-weight tests fail (0.25 ≠ 0.22).

- [ ] **Step 3: Rebalance weights**

In `app/ingestion/unified_scorer.py`, replace lines 21-25:

```python
_W_CVE = float(os.getenv("CLUSTER_WEIGHT_CVE", "0.10"))
_W_ALIAS = float(os.getenv("CLUSTER_WEIGHT_ALIAS", "0.15"))
_W_ACTOR = float(os.getenv("CLUSTER_WEIGHT_ACTOR", "0.25"))
_W_ENTITY = float(os.getenv("CLUSTER_WEIGHT_ENTITY", "0.20"))
_W_EMBED = float(os.getenv("CLUSTER_WEIGHT_EMBED", "0.30"))
```

with:

```python
_W_CVE = float(os.getenv("CLUSTER_WEIGHT_CVE", "0.10"))
_W_ALIAS = float(os.getenv("CLUSTER_WEIGHT_ALIAS", "0.15"))
_W_ACTOR = float(os.getenv("CLUSTER_WEIGHT_ACTOR", "0.22"))
_W_ENTITY = float(os.getenv("CLUSTER_WEIGHT_ENTITY", "0.18"))
_W_EMBED = float(os.getenv("CLUSTER_WEIGHT_EMBED", "0.35"))

_EMBED_LO = float(os.getenv("CLUSTER_EMBED_LO", "0.70"))
_EMBED_HI = float(os.getenv("CLUSTER_EMBED_HI", "0.90"))
```

- [ ] **Step 4: Add the calibration function**

Add a module-level helper just above `_compute_score`:

```python
def _embed_signal(cosine: float) -> float:
    """Calibration curve: 0 below _EMBED_LO, 1.0 at/above _EMBED_HI, linear between."""
    if _EMBED_HI <= _EMBED_LO:
        return 1.0 if cosine >= _EMBED_HI else 0.0
    return max(0.0, min(1.0, (cosine - _EMBED_LO) / (_EMBED_HI - _EMBED_LO)))
```

- [ ] **Step 5: Apply the curve in _compute_score**

In `_compute_score()`, the final `return` (lines ~89-95) currently ends with
`_W_EMBED * cosine`. Change the cosine handling: after the `cosine` value is computed
(line ~87), the return becomes:

```python
    return (
        _W_CVE * cve_overlap
        + _W_ALIAS * alias_overlap
        + _W_ACTOR * actor_campaign_overlap
        + _W_ENTITY * entity_jaccard
        + _W_EMBED * _embed_signal(cosine)
    )
```

- [ ] **Step 6: Run the full suite to verify it passes**

Run: `docker compose cp app/ingestion/unified_scorer.py backend:/app/app/ingestion/unified_scorer.py && docker compose exec backend python -m pytest tests/test_unified_scorer.py -v`
Expected: PASS — all tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/ingestion/unified_scorer.py tests/test_unified_scorer.py
git commit -m "feat(clustering): embedding calibration curve, rebalanced weights"
```

---

## Task 9: Refresh IDF map at the start of a clustering run

**Files:**
- Modify: `scripts/cluster_articles.py:206-218`

Context: `cluster_articles.py main()` re-clusters the whole corpus. The IDF map must be
built once at run start so `_compute_score()` uses real document frequencies (without
this, the first call lazily builds it via `ensure_idf_map()`, which also works — this
task makes it explicit and logs the count).

- [ ] **Step 1: Add the import**

In `scripts/cluster_articles.py`, add to the imports (near
`from app.ingestion.clusterer import cluster_article`):

```python
from app.ingestion.entity_idf import refresh_idf_map
```

- [ ] **Step 2: Call refresh_idf_map at run start**

In `main()`, immediately after the OpenSearch client is obtained and before the
`--reset` branch (around line 209), add:

```python
    idf_count = await refresh_idf_map()
    console.print(f"[dim]IDF map: {idf_count} entities[/dim]")
```

- [ ] **Step 3: Smoke-test (dry run, tiny limit)**

Run: `docker compose cp scripts/cluster_articles.py ingestion:/app/scripts/cluster_articles.py && docker compose exec ingestion python scripts/cluster_articles.py --limit 5 --dry-run`
Expected: prints `IDF map: <N> entities` with N > 0, then the dry-run output, no errors.

- [ ] **Step 4: Commit**

```bash
git add scripts/cluster_articles.py
git commit -m "feat(clustering): build IDF map at clustering run start"
```

---

## Final step: rollout (operator-run)

After all 9 tasks are merged and the ingestion image is rebuilt, the operator (Omar)
runs the regeneration sequence. **Do not run these automatically** — per project rule,
no unilateral rebuilds/resets.

```bash
# 1. Rebuild + restart ingestion with the new code
docker compose build ingestion && docker compose up -d ingestion

# 2. Regenerate every article embedding with the entity-aware chunked input
docker compose exec ingestion python scripts/backfill_embeddings.py --force

# 3. Re-cluster from scratch (~13 min)
docker compose exec ingestion python scripts/cluster_articles.py --reset

# 4. Check the singleton rate against the 92% baseline
```

---

## Notes for the implementer

- Tests run **inside the backend container** — copy edited files in with
  `docker compose cp` before running pytest (each task's run command shows this).
- `tests/test_unified_scorer.py` is modified by Tasks 5, 6, 7, and 8 — each task adds
  its own tests; do not delete another task's additions.
- The IDF map degrading to plain Jaccard when empty is intentional — it keeps unit
  tests hermetic (no OpenSearch needed) and keeps scoring safe if the entities index
  is unreachable.
- No OpenSearch index mappings change in this plan.
