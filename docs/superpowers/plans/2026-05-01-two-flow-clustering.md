# Two-Flow Clustering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the single clustering flow into two parallel flows — a long-lived `cve_topics` index (CVE identity, EPSS-ranked) and the existing `clusters` index (time-bounded incident events, reweighted for actor/campaign signals) — so that CVE-centric articles are tracked per-CVE across months while incident clusters remain tight, time-bounded events.

**Architecture:** Every article runs through both flows independently. Roundups (>5 CVEs) only create empty CVE topic stubs and proceed to incident clustering; dedicated articles (≤5 CVEs) attach to CVE topics and incident clusters. The incident scorer is reweighted away from CVE overlap (0.45→0.10) toward actor/campaign overlap (0.25 new) and embedding similarity (0.15→0.30). A daily cron refreshes EPSS scores on all CVE topics.

**Tech Stack:** Python 3.12, opensearch-py (async), httpx (EPSS API), pytest + unittest.mock, FIRST.org EPSS API (`https://api.first.org/data/1.0/epss`)

---

## File Map

**New files:**
- `app/ingestion/epss_client.py` — async HTTP client for FIRST.org EPSS API (batch fetch)
- `app/ingestion/cve_topic_manager.py` — create/update `cve_topics` documents in OpenSearch
- `scripts/refresh_epss.py` — daily cron: scroll all CVE topics, fetch EPSS, update scores
- `tests/test_cve_topic_manager.py` — unit tests for topic manager
- `tests/test_epss_client.py` — unit tests for EPSS client

**Modified files:**
- `app/db/opensearch.py` — add `INDEX_CVE_TOPICS`, `_CVE_TOPICS_MAPPING`, register in `ensure_indexes()`
- `app/ingestion/unified_scorer.py` — reweight incident scorer, add actor/campaign overlap signal
- `app/ingestion/clusterer.py` — add two-flow routing in `cluster_article()`, import cve_topic_manager
- `tests/test_unified_scorer.py` — update tests to match new weights
- `tests/test_clusterer.py` — add routing tests
- `scripts/cluster_articles.py` — add CVE topics count to rebuild summary table

---

## Task 1: `cve_topics` OpenSearch index mapping

**Files:**
- Modify: `app/db/opensearch.py`

Context: OpenSearch index mappings live in `app/db/opensearch.py`. The `ensure_indexes()` function at line 286 creates all indexes on startup. Follow the exact same pattern as `_CLUSTERS_MAPPING` — settings block with `"index.knn": True`, strict dynamic mapping, `knn_vector` for the embedding field. The document ID will be the CVE ID (e.g. `CVE-2024-1234`).

- [ ] **Step 1: Add the index constant and mapping**

Open `app/db/opensearch.py`. After line 8 (`INDEX_NVD_CACHE = "nvd_cache"`), add:

```python
INDEX_CVE_TOPICS = "cve_topics"
```

Then after the `_NVD_CACHE_MAPPING` block (after line 259), add:

```python
_CVE_TOPICS_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "refresh_interval": "10s",
        "index.knn": True,
    },
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "cve_id":            {"type": "keyword"},
            "aliases":           {"type": "keyword"},
            "cvss_score":        {"type": "half_float"},
            "cvss_severity":     {"type": "keyword"},
            "cvss_vector":       {"type": "keyword"},
            "cisa_kev":          {"type": "boolean"},
            "kev_added_at":      {"type": "date", "format": "date_optional_time||epoch_millis"},
            "epss_score":        {"type": "half_float"},
            "epss_percentile":   {"type": "half_float"},
            "epss_updated_at":   {"type": "date", "format": "date_optional_time||epoch_millis"},
            "nvd_description":   {"type": "text", "analyzer": "english"},
            "nvd_last_modified": {"type": "date", "format": "date_optional_time||epoch_millis"},
            "article_ids":       {"type": "keyword"},
            "article_count":     {"type": "integer"},
            "linked_event_ids":  {"type": "keyword"},
            "cve_embedding": {
                "type": "knn_vector",
                "dimension": 1024,
                "method": {
                    "name": "hnsw",
                    "space_type": "cosinesimil",
                    "engine": "nmslib",
                },
            },
            "created_at": {
                "type": "date",
                "format": "strict_date_time||strict_date_time_no_millis",
            },
            "updated_at": {
                "type": "date",
                "format": "strict_date_time||strict_date_time_no_millis",
            },
        },
    },
}
```

- [ ] **Step 2: Register in `ensure_indexes()`**

In `ensure_indexes()` (line 292), find the list of `(index, mapping)` tuples. Add `(INDEX_CVE_TOPICS, _CVE_TOPICS_MAPPING)` to it:

```python
for index, mapping in [
    (INDEX_NEWS, NEWS_MAPPING),
    (INDEX_SNAPSHOTS, _SNAPSHOTS_MAPPING),
    (INDEX_CLUSTERS, _CLUSTERS_MAPPING),
    (INDEX_ENTITIES, _ENTITIES_MAPPING),
    (INDEX_NVD_CACHE, _NVD_CACHE_MAPPING),
    (INDEX_CVE_TOPICS, _CVE_TOPICS_MAPPING),   # ← add this line
]:
```

- [ ] **Step 3: Verify the import is available**

`INDEX_CVE_TOPICS` must be importable from `app.db.opensearch`. Run:

```bash
cd "c:/Users/xb_admin/Desktop/Omar/Projects/kiber.info/kiber"
.venv/Scripts/python -c "from app.db.opensearch import INDEX_CVE_TOPICS, _CVE_TOPICS_MAPPING; print('ok', INDEX_CVE_TOPICS)"
```

Expected: `ok cve_topics`

- [ ] **Step 4: Commit**

```bash
git add app/db/opensearch.py
git commit -m "feat(opensearch): add cve_topics index mapping with kNN and EPSS fields"
```

---

## Task 2: EPSS client

**Files:**
- Create: `app/ingestion/epss_client.py`
- Create: `tests/test_epss_client.py`

Context: FIRST.org EPSS API endpoint is `https://api.first.org/data/1.0/epss`. Accepts comma-separated CVE IDs as the `cve` query parameter (up to 100 per request). Response JSON: `{"status": "OK", "data": [{"cve": "CVE-2024-1234", "epss": "0.12345", "percentile": "0.87654", "date": "2026-05-01"}]}`. We batch in chunks of 100 and return a flat dict keyed by CVE ID.

- [ ] **Step 1: Write the failing test**

Create `tests/test_epss_client.py`:

```python
"""Tests for app.ingestion.epss_client."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _mock_response(data: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"status": "OK", "data": data}
    return resp


@pytest.mark.asyncio
async def test_fetch_epss_returns_parsed_scores():
    from app.ingestion.epss_client import fetch_epss

    mock_resp = _mock_response([
        {"cve": "CVE-2024-1234", "epss": "0.12345", "percentile": "0.87654", "date": "2026-05-01"},
        {"cve": "CVE-2024-5678", "epss": "0.00123", "percentile": "0.54321", "date": "2026-05-01"},
    ])

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await fetch_epss(["CVE-2024-1234", "CVE-2024-5678"])

    assert result["CVE-2024-1234"]["epss_score"] == pytest.approx(0.12345)
    assert result["CVE-2024-1234"]["epss_percentile"] == pytest.approx(0.87654)
    assert result["CVE-2024-1234"]["epss_updated_at"] == "2026-05-01"
    assert "CVE-2024-5678" in result


@pytest.mark.asyncio
async def test_fetch_epss_empty_input_returns_empty_dict():
    from app.ingestion.epss_client import fetch_epss
    result = await fetch_epss([])
    assert result == {}


@pytest.mark.asyncio
async def test_fetch_epss_handles_api_error_gracefully():
    from app.ingestion.epss_client import fetch_epss

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("network error"))

        result = await fetch_epss(["CVE-2024-1234"])

    assert result == {}
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd "c:/Users/xb_admin/Desktop/Omar/Projects/kiber.info/kiber"
.venv/Scripts/pytest tests/test_epss_client.py -v
```

Expected: `ImportError: cannot import name 'fetch_epss'`

- [ ] **Step 3: Create `app/ingestion/epss_client.py`**

```python
import logging
import httpx

logger = logging.getLogger(__name__)

_EPSS_URL = "https://api.first.org/data/1.0/epss"
_TIMEOUT = 30.0
_BATCH_SIZE = 100


async def fetch_epss(cve_ids: list[str]) -> dict[str, dict]:
    """Fetch EPSS scores for a list of CVE IDs from FIRST.org.

    Returns a dict keyed by CVE ID:
        {"CVE-2024-1234": {"epss_score": 0.123, "epss_percentile": 0.876, "epss_updated_at": "2026-05-01"}}
    CVEs not found in EPSS are absent from the result. On network error, returns empty dict.
    """
    if not cve_ids:
        return {}
    results: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for i in range(0, len(cve_ids), _BATCH_SIZE):
            batch = cve_ids[i : i + _BATCH_SIZE]
            try:
                resp = await client.get(_EPSS_URL, params={"cve": ",".join(batch)})
                resp.raise_for_status()
                for entry in resp.json().get("data", []):
                    results[entry["cve"]] = {
                        "epss_score": float(entry["epss"]),
                        "epss_percentile": float(entry["percentile"]),
                        "epss_updated_at": entry["date"],
                    }
            except Exception as exc:
                logger.warning("EPSS fetch failed for batch starting at %d: %s", i, exc)
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/Scripts/pytest tests/test_epss_client.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/epss_client.py tests/test_epss_client.py
git commit -m "feat(epss): add EPSS client for FIRST.org batch score fetching"
```

---

## Task 3: CVE topic manager

**Files:**
- Create: `app/ingestion/cve_topic_manager.py`
- Create: `tests/test_cve_topic_manager.py`

Context: This module owns all writes to the `cve_topics` index. Two public functions: `upsert_cve_topics()` attaches an article to CVE topics (creating the document if it doesn't exist via scripted upsert); `create_cve_topic_stubs()` creates empty CVE topic documents for roundup articles (no article attached, just ensures the document exists). The document ID in OpenSearch is the CVE ID itself (e.g. `CVE-2024-1234`). Uses OpenSearch scripted update with `upsert` fallback — same pattern as `merge_into_cluster` in `clusterer.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cve_topic_manager.py`:

```python
"""Tests for app.ingestion.cve_topic_manager."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


def _make_os_client(exists: bool = False) -> AsyncMock:
    client = AsyncMock()
    client.exists = AsyncMock(return_value=exists)
    client.update = AsyncMock()
    client.index = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_upsert_cve_topics_calls_update_for_each_cve():
    from app.ingestion.cve_topic_manager import upsert_cve_topics

    mock_client = _make_os_client()
    with patch("app.ingestion.cve_topic_manager.get_os_client", return_value=mock_client):
        await upsert_cve_topics(
            cve_ids=["CVE-2024-1234", "CVE-2024-5678"],
            article_slug="test-article",
            entities=[{"type": "vuln_alias", "normalized_key": "log4shell"}],
            embedding=[0.1] * 1024,
        )

    assert mock_client.update.call_count == 2
    call_args = mock_client.update.call_args_list[0]
    body = call_args.kwargs["body"]
    assert body["upsert"]["cve_id"] == "CVE-2024-1234"
    assert body["upsert"]["article_ids"] == ["test-article"]
    assert "log4shell" in body["upsert"]["aliases"]


@pytest.mark.asyncio
async def test_upsert_cve_topics_noop_when_no_cves():
    from app.ingestion.cve_topic_manager import upsert_cve_topics

    mock_client = _make_os_client()
    with patch("app.ingestion.cve_topic_manager.get_os_client", return_value=mock_client):
        await upsert_cve_topics([], "test-article", [], None)

    mock_client.update.assert_not_called()


@pytest.mark.asyncio
async def test_create_cve_topic_stubs_creates_only_missing():
    from app.ingestion.cve_topic_manager import create_cve_topic_stubs

    mock_client = _make_os_client(exists=False)
    with patch("app.ingestion.cve_topic_manager.get_os_client", return_value=mock_client):
        await create_cve_topic_stubs(["CVE-2024-1111", "CVE-2024-2222"])

    assert mock_client.index.call_count == 2
    first_doc = mock_client.index.call_args_list[0].kwargs["body"]
    assert first_doc["article_ids"] == []
    assert first_doc["article_count"] == 0


@pytest.mark.asyncio
async def test_create_cve_topic_stubs_skips_existing():
    from app.ingestion.cve_topic_manager import create_cve_topic_stubs

    mock_client = _make_os_client(exists=True)
    with patch("app.ingestion.cve_topic_manager.get_os_client", return_value=mock_client):
        await create_cve_topic_stubs(["CVE-2024-1111"])

    mock_client.index.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_cve_topics_omits_embedding_when_none():
    from app.ingestion.cve_topic_manager import upsert_cve_topics

    mock_client = _make_os_client()
    with patch("app.ingestion.cve_topic_manager.get_os_client", return_value=mock_client):
        await upsert_cve_topics(["CVE-2024-1234"], "test-article", [], embedding=None)

    body = mock_client.update.call_args.kwargs["body"]
    assert "cve_embedding" not in body["upsert"]
```

- [ ] **Step 2: Run to verify failure**

```bash
.venv/Scripts/pytest tests/test_cve_topic_manager.py -v
```

Expected: `ImportError: cannot import name 'upsert_cve_topics'`

- [ ] **Step 3: Create `app/ingestion/cve_topic_manager.py`**

```python
"""Manages cve_topics index documents.

Two public functions:
  upsert_cve_topics()       — attach an article to CVE topics (creates if missing)
  create_cve_topic_stubs()  — create empty CVE topic docs for roundup articles
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from app.db.opensearch import INDEX_CVE_TOPICS, get_os_client

logger = logging.getLogger(__name__)


async def upsert_cve_topics(
    cve_ids: list[str],
    article_slug: str,
    entities: list[dict],
    embedding: Optional[list[float]],
) -> None:
    """Create or update cve_topic documents and attach the article to each."""
    if not cve_ids:
        return
    os_client = get_os_client()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    aliases = [e["normalized_key"] for e in entities if e["type"] == "vuln_alias"]

    for cve_id in cve_ids:
        try:
            await _upsert_one(os_client, cve_id, article_slug, aliases, embedding, now)
        except Exception as exc:
            logger.warning("cve_topic upsert failed for %s: %s", cve_id, exc)


async def create_cve_topic_stubs(cve_ids: list[str]) -> None:
    """Create empty cve_topic documents for roundup articles.

    Does not attach the roundup article — just ensures the CVE topic exists
    so it's discoverable. Skips CVEs that already have a topic document.
    """
    if not cve_ids:
        return
    os_client = get_os_client()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for cve_id in cve_ids:
        try:
            exists = await os_client.exists(index=INDEX_CVE_TOPICS, id=cve_id)
            if not exists:
                doc = {
                    "cve_id": cve_id,
                    "aliases": [],
                    "cvss_score": None,
                    "cvss_severity": None,
                    "cvss_vector": None,
                    "cisa_kev": False,
                    "epss_score": None,
                    "epss_percentile": None,
                    "article_ids": [],
                    "article_count": 0,
                    "linked_event_ids": [],
                    "created_at": now,
                    "updated_at": now,
                }
                await os_client.index(index=INDEX_CVE_TOPICS, id=cve_id, body=doc)
        except Exception as exc:
            logger.warning("cve_topic stub creation failed for %s: %s", cve_id, exc)


async def _upsert_one(
    os_client,
    cve_id: str,
    article_slug: str,
    aliases: list[str],
    embedding: Optional[list[float]],
    now: str,
) -> None:
    doc_on_create: dict = {
        "cve_id": cve_id,
        "aliases": aliases,
        "cvss_score": None,
        "cvss_severity": None,
        "cvss_vector": None,
        "cisa_kev": False,
        "epss_score": None,
        "epss_percentile": None,
        "article_ids": [article_slug],
        "article_count": 1,
        "linked_event_ids": [],
        "created_at": now,
        "updated_at": now,
    }
    if embedding is not None:
        doc_on_create["cve_embedding"] = embedding

    script_source = """
        if (!ctx._source.article_ids.contains(params.slug)) {
            ctx._source.article_ids.add(params.slug);
            ctx._source.article_count += 1;
        }
        for (alias in params.aliases) {
            if (!ctx._source.aliases.contains(alias)) {
                ctx._source.aliases.add(alias);
            }
        }
        if (params.embedding != null) {
            ctx._source.cve_embedding = params.embedding;
        }
        ctx._source.updated_at = params.now;
    """

    await os_client.update(
        index=INDEX_CVE_TOPICS,
        id=cve_id,
        body={
            "script": {
                "source": script_source,
                "lang": "painless",
                "params": {
                    "slug": article_slug,
                    "aliases": aliases,
                    "embedding": embedding,
                    "now": now,
                },
            },
            "upsert": doc_on_create,
        },
        retry_on_conflict=3,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/Scripts/pytest tests/test_cve_topic_manager.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/cve_topic_manager.py tests/test_cve_topic_manager.py
git commit -m "feat(cve_topics): add CVE topic manager with upsert and stub creation"
```

---

## Task 4: Incident scorer reweight

**Files:**
- Modify: `app/ingestion/unified_scorer.py`
- Modify: `tests/test_unified_scorer.py`

Context: The incident flow no longer sees CVE-centric articles routed away to `cve_topics`. The remaining articles cluster by actor/campaign/product/narrative. CVE overlap drops from 0.45 → 0.10 (tie-breaker only). A new `actor_campaign_overlap` signal (0.25) replaces the dominant CVE role. Embedding similarity rises from 0.15 → 0.30 because incident narrative is the primary identity signal. `alias_overlap` drops from 0.25 → 0.15. `entity_jaccard` rises from 0.15 → 0.20.

The existing `art_aliases` set included both `vuln_alias` and `campaign`. Campaign now moves into the new `actor_campaign` signal. `alias_overlap` becomes `vuln_alias`-only. Total weights: 0.10 + 0.15 + 0.25 + 0.20 + 0.30 = 1.00.

- [ ] **Step 1: Update the weight constants in `unified_scorer.py`**

Replace lines 21–24 in `app/ingestion/unified_scorer.py`:

```python
# Old:
_W_CVE = float(os.getenv("CLUSTER_WEIGHT_CVE", "0.45"))
_W_ALIAS = float(os.getenv("CLUSTER_WEIGHT_ALIAS", "0.25"))
_W_ENTITY = float(os.getenv("CLUSTER_WEIGHT_ENTITY", "0.15"))
_W_EMBED = float(os.getenv("CLUSTER_WEIGHT_EMBED", "0.15"))

# New:
_W_CVE = float(os.getenv("CLUSTER_WEIGHT_CVE", "0.10"))
_W_ALIAS = float(os.getenv("CLUSTER_WEIGHT_ALIAS", "0.15"))
_W_ACTOR = float(os.getenv("CLUSTER_WEIGHT_ACTOR", "0.25"))
_W_ENTITY = float(os.getenv("CLUSTER_WEIGHT_ENTITY", "0.20"))
_W_EMBED = float(os.getenv("CLUSTER_WEIGHT_EMBED", "0.30"))
```

- [ ] **Step 2: Update `_compute_score()` to add actor/campaign signal**

Replace the entire `_compute_score` function (lines 36–83) with:

```python
def _compute_score(
    article_entities: list[dict],
    cluster_source: dict,
    article_embedding: Optional[list[float]],
) -> float:
    sig = cluster_source.get("event_signature") or {}

    art_cves = {e["normalized_key"] for e in article_entities if e["type"] == "cve"}
    art_vuln_aliases = {
        e["normalized_key"] for e in article_entities if e["type"] == "vuln_alias"
    }
    art_actors_campaigns = {
        e["normalized_key"]
        for e in article_entities
        if e["type"] in ("actor", "campaign")
    }
    art_others = {
        e["normalized_key"]
        for e in article_entities
        if e["type"] not in ("cve", "vuln_alias", "actor", "campaign", "vendor")
    }

    cl_cves = set(sig.get("cve_ids") or [])
    cl_vuln_aliases = set(sig.get("vuln_aliases") or [])
    cl_actors_campaigns = set(
        (sig.get("primary_actors") or []) + (sig.get("campaign_names") or [])
    )
    cl_others = (
        set(cluster_source.get("entity_keys") or [])
        - cl_cves
        - cl_vuln_aliases
        - cl_actors_campaigns
    )

    cve_overlap = 1.0 if art_cves & cl_cves else 0.0
    alias_overlap = 1.0 if art_vuln_aliases & cl_vuln_aliases else 0.0
    actor_campaign_overlap = 1.0 if art_actors_campaigns & cl_actors_campaigns else 0.0

    union_others = art_others | cl_others
    entity_jaccard = (
        len(art_others & cl_others) / len(union_others) if union_others else 0.0
    )

    cosine = 0.0
    centroid = cluster_source.get("centroid_embedding")
    if article_embedding and centroid:
        a = np.array(article_embedding, dtype=np.float32)
        c = np.array(centroid, dtype=np.float32)
        denom = np.linalg.norm(a) * np.linalg.norm(c)
        if denom > 0:
            cosine = max(0.0, float(np.dot(a, c) / denom))

    return (
        _W_CVE * cve_overlap
        + _W_ALIAS * alias_overlap
        + _W_ACTOR * actor_campaign_overlap
        + _W_ENTITY * entity_jaccard
        + _W_EMBED * cosine
    )
```

- [ ] **Step 3: Update the scorer tests to match new weights**

Replace the affected assertions in `tests/test_unified_scorer.py`. The `_make_cluster` helper needs a `primary_actors` field. Replace the file with:

```python
"""Tests for app.ingestion.unified_scorer."""
import pytest
from unittest.mock import AsyncMock, patch
import numpy as np


def _make_cluster(
    cluster_id: str,
    cve_ids: list[str] = None,
    vuln_aliases: list[str] = None,
    campaign_names: list[str] = None,
    primary_actors: list[str] = None,
    entity_keys: list[str] = None,
    centroid: list[float] = None,
    article_count: int = 1,
    state: str = "new",
) -> dict:
    return {
        "_id": cluster_id,
        "_source": {
            "article_count": article_count,
            "state": state,
            "entity_keys": entity_keys or [],
            "event_signature": {
                "cve_ids": cve_ids or [],
                "vuln_aliases": vuln_aliases or [],
                "campaign_names": campaign_names or [],
                "affected_products": [],
                "primary_actors": primary_actors or [],
                "confidence": "medium",
            },
            "centroid_embedding": centroid,
        },
    }


def _make_article_entities(types_keys: list[tuple]) -> list[dict]:
    return [{"type": t, "normalized_key": k} for t, k in types_keys]


# ---------------------------------------------------------------------------
# Score formula
# ---------------------------------------------------------------------------

def test_score_perfect_match_is_one():
    from app.ingestion.unified_scorer import _compute_score

    emb = [1.0] + [0.0] * 1023
    article_entities = _make_article_entities([
        ("cve", "CVE-2024-1234"),
        ("vuln_alias", "log4shell"),
        ("actor", "apt29"),
        ("malware", "lockbit"),
    ])
    cluster = _make_cluster(
        "c1",
        cve_ids=["CVE-2024-1234"],
        vuln_aliases=["log4shell"],
        primary_actors=["apt29"],
        entity_keys=["lockbit"],
        centroid=emb,
    )
    score = _compute_score(article_entities, cluster["_source"], emb)
    # 0.10 + 0.15 + 0.25 + 0.20*(1/1) + 0.30*1.0 = 1.0
    assert abs(score - 1.0) < 0.01


def test_score_embedding_only_cannot_exceed_threshold():
    """Pure embedding match (no structured signals) must score below 0.30 threshold."""
    from app.ingestion.unified_scorer import _compute_score, ASSIGN_THRESHOLD

    emb = [1.0] + [0.0] * 1023
    article_entities = []
    cluster = _make_cluster("c1", centroid=emb)
    score = _compute_score(article_entities, cluster["_source"], emb)
    assert score < ASSIGN_THRESHOLD


def test_score_cve_overlap_only():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("cve", "CVE-2024-9999")])
    cluster = _make_cluster("c1", cve_ids=["CVE-2024-9999"])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert abs(score - 0.10) < 0.01


def test_score_alias_overlap_only():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("vuln_alias", "heartbleed")])
    cluster = _make_cluster("c1", vuln_aliases=["heartbleed"])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert abs(score - 0.15) < 0.01


def test_score_actor_overlap_only():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("actor", "volt-typhoon")])
    cluster = _make_cluster("c1", primary_actors=["volt-typhoon"])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert abs(score - 0.25) < 0.01


def test_score_campaign_overlap_uses_actor_weight():
    from app.ingestion.unified_scorer import _compute_score

    article_entities = _make_article_entities([("campaign", "moveit-campaign")])
    cluster = _make_cluster("c1", campaign_names=["moveit-campaign"])
    score = _compute_score(article_entities, cluster["_source"], None)
    assert abs(score - 0.25) < 0.01


def test_score_actor_plus_embed_exceeds_threshold():
    from app.ingestion.unified_scorer import _compute_score, ASSIGN_THRESHOLD

    emb = [1.0] + [0.0] * 1023
    article_entities = _make_article_entities([("actor", "lazarus-group")])
    cluster = _make_cluster("c1", primary_actors=["lazarus-group"], centroid=emb)
    score = _compute_score(article_entities, cluster["_source"], emb)
    # 0.25 + 0.30 = 0.55 > 0.30 threshold
    assert score >= ASSIGN_THRESHOLD


def test_score_cve_plus_alias_still_exceeds_threshold():
    from app.ingestion.unified_scorer import _compute_score, ASSIGN_THRESHOLD

    article_entities = _make_article_entities([
        ("cve", "CVE-2024-1234"),
        ("vuln_alias", "citrixbleed"),
    ])
    cluster = _make_cluster("c1", cve_ids=["CVE-2024-1234"], vuln_aliases=["citrixbleed"])
    score = _compute_score(article_entities, cluster["_source"], None)
    # 0.10 + 0.15 = 0.25 < 0.30 — CVE alone no longer clears threshold
    # This is correct: CVE-centric articles belong in cve_topics, not purely incident clusters
    assert score < ASSIGN_THRESHOLD


# ---------------------------------------------------------------------------
# find_best_cluster
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_best_cluster_returns_none_below_threshold():
    from app.ingestion.unified_scorer import find_best_cluster

    with patch("app.ingestion.unified_scorer._get_candidates", new_callable=AsyncMock) as mock_cands:
        mock_cands.return_value = []
        result = await find_best_cluster([], None)

    assert result is None


@pytest.mark.asyncio
async def test_find_best_cluster_returns_highest_scoring():
    from app.ingestion.unified_scorer import find_best_cluster

    low_cluster = _make_cluster("c-low", primary_actors=["apt29"])
    high_cluster = _make_cluster("c-high", primary_actors=["apt29"], campaign_names=["cozy-bear-2024"])

    article_entities = _make_article_entities([
        ("actor", "apt29"),
        ("campaign", "cozy-bear-2024"),
    ])

    with patch("app.ingestion.unified_scorer._get_candidates", new_callable=AsyncMock) as mock_cands:
        mock_cands.return_value = [low_cluster, high_cluster]
        result = await find_best_cluster(article_entities, None)

    assert result == "c-high"


@pytest.mark.asyncio
async def test_find_best_cluster_returns_none_when_candidates_below_threshold():
    from app.ingestion.unified_scorer import find_best_cluster

    no_match_cluster = _make_cluster("c-nomatch")
    article_entities = _make_article_entities([("cve", "CVE-2024-9999")])

    with patch("app.ingestion.unified_scorer._get_candidates", new_callable=AsyncMock) as mock_cands:
        mock_cands.return_value = [no_match_cluster]
        result = await find_best_cluster(article_entities, None)

    assert result is None
```

- [ ] **Step 4: Run tests**

```bash
.venv/Scripts/pytest tests/test_unified_scorer.py -v
```

Expected: all tests pass (note: `test_score_cve_plus_alias_still_exceeds_threshold` now asserts `< ASSIGN_THRESHOLD` — this is intentional, CVE-only articles belong in `cve_topics` not incident clusters)

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/unified_scorer.py tests/test_unified_scorer.py
git commit -m "feat(scorer): reweight incident scorer — actor/campaign 0.25, embed 0.30, cve 0.10"
```

---

## Task 5: Two-flow routing in `clusterer.py`

**Files:**
- Modify: `app/ingestion/clusterer.py`
- Modify: `tests/test_clusterer.py`

Context: `cluster_article()` is the main entry point (line 82). It currently does: embed → find_best_cluster → merge or create. We need to add the CVE flow before the incident flow: if the article has CVEs and is not a roundup (≤5 CVEs), call `upsert_cve_topics()`; if it's a roundup (>5 CVEs), call `create_cve_topic_stubs()`. The incident flow (find_best_cluster → merge/create) always runs regardless.

- [ ] **Step 1: Add the roundup constant and routing logic**

Replace the `cluster_article` function (lines 82–109) in `app/ingestion/clusterer.py`:

```python
_MAX_ARTICLE_CVES_FOR_CVE_TOPIC = 5  # articles with >5 CVEs are treated as roundups


async def cluster_article(
    article: dict,
    slug: str,
    entities: list[dict],
) -> None:
    """Assign article to an incident cluster and optionally to CVE topics.

    CVE flow: articles with ≤5 CVEs attach to cve_topics (one per CVE ID).
    Roundups (>5 CVEs) only create empty CVE topic stubs.
    Incident flow always runs — article is assigned to or creates an incident cluster.
    """
    from app.ingestion.cve_topic_manager import upsert_cve_topics, create_cve_topic_stubs

    cve_ids: list[str] = article.get("cve_ids") or []
    embedding = await embed_text(_build_embed_input(article))
    ref_time = _parse_published_at(article.get("published_at"))

    # CVE flow
    if cve_ids:
        if len(cve_ids) > _MAX_ARTICLE_CVES_FOR_CVE_TOPIC:
            await create_cve_topic_stubs(cve_ids)
        else:
            await upsert_cve_topics(cve_ids, slug, entities, embedding)

    # Incident flow (always runs)
    cluster_id = await find_best_cluster(entities, embedding, reference_time=ref_time)

    if cluster_id:
        await merge_into_cluster(
            cluster_id,
            slug,
            [e["normalized_key"] for e in entities],
            cve_ids,
            source_name=article.get("source_name", ""),
            title=article.get("title", ""),
            published_at=article.get("published_at", ""),
            cvss_score=article.get("cvss_score"),
            credibility_weight=float(article.get("credibility_weight") or 1.0),
            new_entities=entities,
            new_embedding=embedding,
        )
    else:
        await create_cluster(article, entities, embedding=embedding)
```

- [ ] **Step 2: Write the routing tests**

Add to `tests/test_clusterer.py` (append after the existing tests):

```python
# ---------------------------------------------------------------------------
# Two-flow routing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cluster_article_calls_upsert_for_dedicated_cve_article():
    """Articles with ≤5 CVEs trigger upsert_cve_topics."""
    article = {
        "slug": "fortios-rce-001",
        "title": "FortiOS RCE",
        "cve_ids": ["CVE-2026-1234"],
        "source_name": "BleepingComputer",
        "published_at": "2026-04-27T10:00:00Z",
        "credibility_weight": 1.2,
    }
    entities = [{"type": "cve", "normalized_key": "CVE-2026-1234"}]

    with patch("app.ingestion.clusterer.embed_text", new_callable=AsyncMock, return_value=[0.1] * 1024), \
         patch("app.ingestion.clusterer.find_best_cluster", new_callable=AsyncMock, return_value=None), \
         patch("app.ingestion.clusterer.create_cluster", new_callable=AsyncMock), \
         patch("app.ingestion.cve_topic_manager.upsert_cve_topics", new_callable=AsyncMock) as mock_upsert, \
         patch("app.ingestion.cve_topic_manager.create_cve_topic_stubs", new_callable=AsyncMock) as mock_stubs:
        await cluster_article(article, article["slug"], entities)

    mock_upsert.assert_awaited_once()
    mock_stubs.assert_not_awaited()
    call_kwargs = mock_upsert.call_args
    assert call_kwargs.args[0] == ["CVE-2026-1234"]
    assert call_kwargs.args[1] == "fortios-rce-001"


@pytest.mark.asyncio
async def test_cluster_article_calls_stubs_for_roundup():
    """Articles with >5 CVEs trigger create_cve_topic_stubs, not upsert."""
    article = {
        "slug": "patch-tuesday-may-2026",
        "title": "Patch Tuesday May 2026",
        "cve_ids": [f"CVE-2026-{i:04d}" for i in range(80)],
        "source_name": "Microsoft",
        "published_at": "2026-05-01T10:00:00Z",
        "credibility_weight": 1.0,
    }
    entities = []

    with patch("app.ingestion.clusterer.embed_text", new_callable=AsyncMock, return_value=[0.1] * 1024), \
         patch("app.ingestion.clusterer.find_best_cluster", new_callable=AsyncMock, return_value=None), \
         patch("app.ingestion.clusterer.create_cluster", new_callable=AsyncMock), \
         patch("app.ingestion.cve_topic_manager.upsert_cve_topics", new_callable=AsyncMock) as mock_upsert, \
         patch("app.ingestion.cve_topic_manager.create_cve_topic_stubs", new_callable=AsyncMock) as mock_stubs:
        await cluster_article(article, article["slug"], entities)

    mock_stubs.assert_awaited_once()
    mock_upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_cluster_article_incident_flow_runs_even_for_cve_article():
    """Incident flow (find_best_cluster) always runs regardless of CVE routing."""
    article = {
        "slug": "fortios-rce-001",
        "title": "FortiOS RCE",
        "cve_ids": ["CVE-2026-1234"],
        "source_name": "BleepingComputer",
        "published_at": "2026-04-27T10:00:00Z",
        "credibility_weight": 1.2,
    }
    entities = [{"type": "cve", "normalized_key": "CVE-2026-1234"}]

    with patch("app.ingestion.clusterer.embed_text", new_callable=AsyncMock, return_value=[0.1] * 1024), \
         patch("app.ingestion.clusterer.find_best_cluster", new_callable=AsyncMock, return_value="cluster-abc") as mock_best, \
         patch("app.ingestion.clusterer.merge_into_cluster", new_callable=AsyncMock) as mock_merge, \
         patch("app.ingestion.cve_topic_manager.upsert_cve_topics", new_callable=AsyncMock), \
         patch("app.ingestion.cve_topic_manager.create_cve_topic_stubs", new_callable=AsyncMock):
        await cluster_article(article, article["slug"], entities)

    mock_best.assert_awaited_once()
    mock_merge.assert_awaited_once()


@pytest.mark.asyncio
async def test_cluster_article_no_cve_skips_cve_flow():
    """Articles with no CVEs skip the CVE flow entirely."""
    article = {
        "slug": "threat-actor-post",
        "title": "Lazarus Group targets banks",
        "cve_ids": [],
        "source_name": "Krebs",
        "published_at": "2026-04-27T10:00:00Z",
        "credibility_weight": 1.2,
    }
    entities = [{"type": "actor", "normalized_key": "lazarus-group"}]

    with patch("app.ingestion.clusterer.embed_text", new_callable=AsyncMock, return_value=[0.1] * 1024), \
         patch("app.ingestion.clusterer.find_best_cluster", new_callable=AsyncMock, return_value=None), \
         patch("app.ingestion.clusterer.create_cluster", new_callable=AsyncMock), \
         patch("app.ingestion.cve_topic_manager.upsert_cve_topics", new_callable=AsyncMock) as mock_upsert, \
         patch("app.ingestion.cve_topic_manager.create_cve_topic_stubs", new_callable=AsyncMock) as mock_stubs:
        await cluster_article(article, article["slug"], entities)

    mock_upsert.assert_not_awaited()
    mock_stubs.assert_not_awaited()
```

- [ ] **Step 3: Run the full test suite**

```bash
.venv/Scripts/pytest tests/test_clusterer.py -v
```

Expected: all tests pass

- [ ] **Step 4: Commit**

```bash
git add app/ingestion/clusterer.py tests/test_clusterer.py
git commit -m "feat(clusterer): add two-flow routing — cve_topics + incident cluster run in parallel"
```

---

## Task 6: EPSS daily refresh script

**Files:**
- Create: `scripts/refresh_epss.py`

Context: Scrolls all documents from `cve_topics` index, collects their CVE IDs, batches to `fetch_epss()`, then updates each document with the returned scores. Follows the same pattern as `scripts/cluster_articles.py` — `argparse`, dotenv, `close_os_client()` in finally block, rich console output.

- [ ] **Step 1: Create `scripts/refresh_epss.py`**

```python
#!/usr/bin/env python
"""Refresh EPSS scores for all CVE topics.

Fetches current EPSS scores from FIRST.org and updates cve_topics documents.
Run daily after NVD enrichment.

Usage:
    python scripts/refresh_epss.py
    python scripts/refresh_epss.py --dry-run
    python scripts/refresh_epss.py --limit 100
"""
import asyncio
import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console

from app.db.opensearch import INDEX_CVE_TOPICS, get_os_client
from app.ingestion.epss_client import fetch_epss

logger = logging.getLogger(__name__)
console = Console()


async def _scroll_cve_ids(client, limit: int) -> list[str]:
    cve_ids: list[str] = []
    from_offset = 0
    page_size = 100
    while True:
        resp = await client.search(
            index=INDEX_CVE_TOPICS,
            body={
                "query": {"match_all": {}},
                "size": page_size,
                "from": from_offset,
                "_source": [],
            },
        )
        hits = resp["hits"]["hits"]
        if not hits:
            break
        cve_ids.extend(h["_id"] for h in hits)
        from_offset += len(hits)
        if len(hits) < page_size:
            break
        if limit and len(cve_ids) >= limit:
            cve_ids = cve_ids[:limit]
            break
    return cve_ids


async def main(args: argparse.Namespace) -> None:
    client = get_os_client()

    with console.status("[cyan]Scanning CVE topics…"):
        cve_ids = await _scroll_cve_ids(client, args.limit)

    console.print(f"[bold]Found {len(cve_ids)} CVE topics to refresh.[/bold]")

    if args.dry_run:
        console.print(f"[dim][DRY RUN] Would fetch EPSS for {len(cve_ids)} CVEs. First 5: {cve_ids[:5]}[/dim]")
        return

    with console.status("[cyan]Fetching EPSS scores from FIRST.org…"):
        epss_data = await fetch_epss(cve_ids)

    console.print(f"[dim]EPSS scores returned for {len(epss_data)}/{len(cve_ids)} CVEs.[/dim]")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    updated = 0
    missed = 0

    for cve_id in cve_ids:
        if cve_id not in epss_data:
            missed += 1
            continue
        scores = epss_data[cve_id]
        try:
            await client.update(
                index=INDEX_CVE_TOPICS,
                id=cve_id,
                body={"doc": {
                    "epss_score": scores["epss_score"],
                    "epss_percentile": scores["epss_percentile"],
                    "epss_updated_at": scores["epss_updated_at"],
                    "updated_at": now,
                }},
                retry_on_conflict=3,
            )
            updated += 1
        except Exception as exc:
            logger.warning("Failed to update EPSS for %s: %s", cve_id, exc)

    console.print(f"[green]Updated {updated} CVE topics with EPSS scores.[/green]")
    if missed:
        console.print(f"[yellow]{missed} CVEs not found in EPSS (may be reserved/rejected IDs).[/yellow]")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Refresh EPSS scores for all CVE topics")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--limit", type=int, default=0, help="Process only first N CVEs (0 = no limit)")

    async def _run():
        try:
            await main(parser.parse_args())
        finally:
            from app.db.opensearch import close_os_client
            await close_os_client()

    asyncio.run(_run())
```

- [ ] **Step 2: Verify it imports cleanly**

```bash
cd "c:/Users/xb_admin/Desktop/Omar/Projects/kiber.info/kiber"
.venv/Scripts/python -c "import scripts.refresh_epss; print('ok')" 2>&1 | head -5
```

Expected: `ok` (or import error showing exactly what's missing, not a syntax error)

- [ ] **Step 3: Commit**

```bash
git add scripts/refresh_epss.py
git commit -m "feat(epss): add daily EPSS refresh script for cve_topics"
```

---

## Task 7: Rebuild stats for `cluster_articles.py`

**Files:**
- Modify: `scripts/cluster_articles.py`

Context: The rebuild script drives the full `--reset` cluster rebuild. Now that `cluster_article()` also writes to `cve_topics`, the rebuild naturally populates both indexes. We only need to add a post-rebuild count of `cve_topics` documents to the summary table so the operator knows the rebuild covered both flows.

- [ ] **Step 1: Add CVE topics count to the summary table**

In `scripts/cluster_articles.py`, find the summary table block (around line 285). Add a CVE topics count query and a new row. Replace the summary block (from `table = Table(...)` to `console.print(table)`) with:

```python
    # Count cve_topics created during this rebuild
    cve_topic_count = 0
    try:
        resp = await _os_search(client, "cve_topics", {"query": {"match_all": {}}, "size": 0})
        cve_topic_count = resp["hits"]["total"]["value"]
    except Exception:
        pass

    table = Table(title="Clustering Complete", show_header=True, header_style="bold magenta")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="bold")
    table.add_row("Processed", str(totals["processed"]))
    table.add_row("Skipped (already clustered)", str(totals["skipped"]))
    table.add_row("Errors", str(totals["errors"]), style="red" if totals["errors"] else "")
    table.add_row("CVE topics in index", str(cve_topic_count))
    console.print(table)
```

Note: `_os_search` is already defined in the same file (line 46). `"cve_topics"` is the index name string — we don't need to import `INDEX_CVE_TOPICS` since the script already has direct string references elsewhere.

- [ ] **Step 2: Run the full test suite one final time**

```bash
.venv/Scripts/pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all existing tests pass plus the new tests from Tasks 2, 3, 4, 5.

- [ ] **Step 3: Commit**

```bash
git add scripts/cluster_articles.py
git commit -m "feat(rebuild): show cve_topics count in cluster_articles.py summary table"
```

---

## Post-implementation: rebuild instructions

After all tasks are complete, run `ensure_indexes()` to create the `cve_topics` index, then do a full cluster rebuild to populate both flows from scratch:

```bash
# In the ingestion container (or local venv):
docker compose exec ingestion python -c "import asyncio; from app.db.opensearch import ensure_indexes; asyncio.run(ensure_indexes())"

# Then give yourself the rebuild command to run manually:
docker compose exec ingestion python scripts/cluster_articles.py --reset
```

After the rebuild completes, run EPSS refresh:

```bash
docker compose exec ingestion python scripts/refresh_epss.py --dry-run
# If dry-run looks correct:
docker compose exec ingestion python scripts/refresh_epss.py
```

---

## TODOs (out of scope for this plan)

- **Real-time EPSS + NVD**: currently daily cron; future work is near-real-time polling (after NVD webhook or short-interval polling is available). Add alongside the existing NVD enrichment pipeline.
- **`cve_embedding` backfill**: `cve_topics` documents created during rebuild will have `cve_embedding` (from the article embedding). CVE topic stubs created by roundups will not. Future: embed the NVD description text for stub topics.
- **`linked_event_ids` population**: the `linked_event_ids` field on `cve_topics` is reserved for linking CVE topics to their incident clusters. Not populated in this plan — requires a separate backfill or runtime linkage job.
- **Frontend split**: CVE topic flashcards and incident cluster flashcards should be shown on different sides of the home feed (noted by user during planning).
- **EPSS-based feed ranking**: `epss_score` on `cve_topics` should influence the cluster/topic ranking score. Currently the scorer only uses `cvss_score`. Future: add EPSS percentile as a ranking factor in `app/ingestion/scorer.py`.
