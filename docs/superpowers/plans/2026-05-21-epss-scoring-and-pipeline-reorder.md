# EPSS Scoring & Ingestion Pipeline Reorder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorder ingestion so CVE/CVSS enrichment sees body-level CVEs, and wire EPSS (exploit-prediction) scores into cluster importance scoring.

**Architecture:** EPSS populates `cve_topics` inline when a topic is first created and is refreshed by the existing `scripts/refresh_epss.py`. `rescore_cluster` derives a `max_epss` field per cluster from `cve_topics` and feeds it to an 8th scoring factor (max 15 pts, below CISA KEV). Separately, `_apply_cve_intel` moves after NER so body-discovered CVEs reach CVSS lookup.

**Tech Stack:** Python 3.12 async, FastAPI, OpenSearch (`opensearch-py`), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-05-21-epss-scoring-and-pipeline-reorder-design.md`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `app/ingestion/scorer.py` | Cluster scoring | Add EPSS factor; compute/write `max_epss` in `rescore_cluster` |
| `app/db/opensearch.py` | Index mappings | Add `max_epss` to `clusters` mapping |
| `app/ingestion/clusterer.py` | Cluster create/merge | Seed `max_epss: 0.0` in `create_cluster` |
| `app/models/cluster.py` | API response models | Add `max_epss` to summary + detail |
| `app/api/routes/clusters.py` | Cluster endpoints | Surface `max_epss` in responses |
| `app/ingestion/cve_topic_manager.py` | `cve_topics` writes | Fetch EPSS inline on topic creation |
| `app/ingestion/ingester.py` | Ingestion loop | Move `_apply_cve_intel` post-NER; merge NER CVEs |
| `scripts/rebuild_all.py` | Full rebuild orchestration | Add EPSS refresh step before clustering |
| `docker-compose.yml` | Service scheduling | Run `refresh_epss.py` in the ingestion loop |

---

## Task 1: EPSS scoring factor in `compute_cluster_score`

**Files:**
- Modify: `app/ingestion/scorer.py`
- Test: `tests/test_scorer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scorer.py`:

```python
class TestEpssFactor:
    """EPSS adds 0-15 pts scaled linearly on the raw exploitation probability."""

    def _base_kwargs(self, **overrides) -> dict:
        defaults = {
            "article_count": 1,
            "max_cvss": None,
            "cve_count": 0,
            "entity_keys": [],
            "state": "new",
            "latest_at": "2026-05-21T00:00:00+00:00",
            "max_credibility_weight": 1.0,
        }
        defaults.update(overrides)
        return defaults

    def test_epss_scaled_linearly_at_15_pts(self):
        result = compute_cluster_score(**self._base_kwargs(max_epss=0.62))
        epss = next(f for f in result["top_factors"] if f["factor"] == "epss")
        assert epss["points"] == 9.3  # round(0.62 * 15, 1)

    def test_epss_label_shows_percentage(self):
        result = compute_cluster_score(**self._base_kwargs(max_epss=0.62))
        epss = next(f for f in result["top_factors"] if f["factor"] == "epss")
        assert epss["label"] == "EPSS 62% exploit probability"

    def test_epss_none_produces_no_factor(self):
        result = compute_cluster_score(**self._base_kwargs(max_epss=None))
        assert all(f["factor"] != "epss" for f in result["top_factors"])

    def test_epss_zero_produces_no_factor(self):
        result = compute_cluster_score(**self._base_kwargs(max_epss=0.0))
        assert all(f["factor"] != "epss" for f in result["top_factors"])

    def test_epss_omitted_param_produces_no_factor(self):
        """max_epss defaults to None so existing callers are unaffected."""
        result = compute_cluster_score(**self._base_kwargs())
        assert all(f["factor"] != "epss" for f in result["top_factors"])

    def test_score_capped_at_100_with_epss(self):
        result = compute_cluster_score(
            article_count=10,
            max_cvss=10.0,
            cve_count=5,
            entity_keys=["e1", "e2", "e3", "e4", "e5"],
            state="confirmed",
            latest_at="2026-05-21T00:00:00+00:00",
            max_credibility_weight=1.5,
            cisa_kev=True,
            max_epss=1.0,
        )
        assert result["score"] <= 100.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scorer.py::TestEpssFactor -v`
Expected: FAIL — `compute_cluster_score()` got an unexpected keyword argument `max_epss`.

- [ ] **Step 3: Add the `max_epss` parameter**

In `app/ingestion/scorer.py`, change the `compute_cluster_score` signature. Current last param block:

```python
    max_credibility_weight: float = 1.0,
    unique_source_count: int = 0,
    cisa_kev: bool = False,
) -> dict:
```

becomes:

```python
    max_credibility_weight: float = 1.0,
    unique_source_count: int = 0,
    cisa_kev: bool = False,
    max_epss: Optional[float] = None,
) -> dict:
```

- [ ] **Step 4: Add the EPSS factor**

In `app/ingestion/scorer.py`, immediately after the CISA KEV block (the `if cisa_kev:` block ending with `total += 20.0`) and before the `# Finalise` section, insert:

```python
    # ------------------------------------------------------------------
    # 8. EPSS exploitation-likelihood component (0-15 pts)
    # ------------------------------------------------------------------
    if max_epss is not None and max_epss > 0:
        epss_pts = round(min(max_epss, 1.0) * 15.0, 1)
        factors.append({
            "factor": "epss",
            "label": f"EPSS {max_epss:.0%} exploit probability",
            "points": epss_pts,
        })
        total += epss_pts
```

- [ ] **Step 5: Update the module docstring**

In `app/ingestion/scorer.py`, the module docstring currently says "six factors" and lists seven. Replace the docstring's factor list and totals so it reads:

```python
"""Cluster scoring and explainability.

Computes a 0-100 importance score for a cluster from eight factors:
  1. CVSS severity      — max CVSS from NVD-enriched CVE entities  (0-30 pts)
  2. Coverage           — unique source count                       (0-25 pts)
  3. Recency            — time since the cluster last updated       (0-20 pts)
  4. CVE / Entities     — number of known CVEs or entities         (0-15 pts)
  5. State bonus        — cluster maturity                         (0-10 pts)
  6. Source credibility — max credibility_weight of member articles (0-15 pts)
  7. CISA KEV           — any CVE in CISA Known Exploited Vulns    (+20 pts)
  8. EPSS               — max exploit-prediction probability        (0-15 pts)

Max raw points = 150, clamped to 100.

Confidence reflects data completeness, not just score:
  high   — has CVSS + ≥2 unique sources + named entities
  medium — has ≥2 unique sources OR (has CVSS and entities)
  low    — everything else
"""
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_scorer.py -v`
Expected: PASS — all `TestEpssFactor` tests plus the pre-existing `TestCredibilityFactor` tests.

- [ ] **Step 7: Commit**

```bash
git add app/ingestion/scorer.py tests/test_scorer.py
git commit -m "feat: add EPSS exploit-likelihood factor to cluster scoring"
```

---

## Task 2: `max_epss` cluster mapping + `create_cluster` seed

**Files:**
- Modify: `app/db/opensearch.py`
- Modify: `app/ingestion/clusterer.py:405-440` (the `create_cluster` doc literal)
- Test: `tests/test_clusterer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_clusterer.py`:

```python
@pytest.mark.asyncio
async def test_create_cluster_seeds_max_epss_zero():
    os_mock = AsyncMock()
    os_mock.index.return_value = {"_id": "cluster-epss-seed"}
    os_mock.update.return_value = {}

    article = {
        "slug": "epss-seed-001",
        "title": "Some vulnerability",
        "cve_ids": ["CVE-2026-7777"],
        "published_at": "2026-05-21T10:00:00Z",
        "content_type": "news",
    }

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer._rescore", new_callable=AsyncMock):
        from app.ingestion.clusterer import create_cluster
        await create_cluster(article, [], embedding=[0.1] * 1024)

    indexed = os_mock.index.call_args.kwargs["body"]
    assert indexed["max_epss"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_clusterer.py::test_create_cluster_seeds_max_epss_zero -v`
Expected: FAIL — `KeyError: 'max_epss'`.

- [ ] **Step 3: Add `max_epss` to the `clusters` mapping**

In `app/db/opensearch.py`, the `clusters` mapping has this line:

```python
            "max_cvss":       {"type": "half_float"},
```

Add directly below it:

```python
            "max_epss":       {"type": "half_float"},
```

(The `clusters` mapping is `dynamic: strict`, so this is mandatory. `ensure_indexes()` runs `put_mapping` on startup, so the field applies to the existing index without a rebuild.)

- [ ] **Step 4: Seed `max_epss` in `create_cluster`**

In `app/ingestion/clusterer.py`, the `create_cluster` doc literal has this line:

```python
        "max_cvss": article.get("cvss_score") or 0.0,
```

Add directly below it:

```python
        "max_epss": 0.0,
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_clusterer.py -v`
Expected: PASS — the new test plus all pre-existing `clusterer` tests.

- [ ] **Step 6: Commit**

```bash
git add app/db/opensearch.py app/ingestion/clusterer.py tests/test_clusterer.py
git commit -m "feat: add max_epss field to clusters index and create_cluster"
```

---

## Task 3: `rescore_cluster` computes and writes `max_epss`

**Files:**
- Modify: `app/ingestion/scorer.py` (`rescore_cluster` + new helper)
- Test: `tests/test_scorer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scorer.py` (add `import pytest` and `from unittest.mock import AsyncMock, patch` at the top of the file if not already present):

```python
@pytest.mark.asyncio
async def test_rescore_cluster_writes_max_epss_from_cve_topics():
    from app.ingestion.scorer import rescore_cluster

    os_mock = AsyncMock()
    os_mock.get.return_value = {
        "_source": {
            "article_count": 1,
            "max_cvss": 7.5,
            "cve_ids": ["CVE-2026-1111", "CVE-2026-2222"],
            "entity_keys": ["fortios"],
            "state": "new",
            "latest_at": "2026-05-21T00:00:00Z",
            "created_at": "2026-05-21T00:00:00Z",
            "max_credibility_weight": 1.0,
            "timeline": [],
            "cisa_kev": False,
        }
    }
    os_mock.update.return_value = {}

    async def fake_lookup(cve_ids):
        return {
            "CVE-2026-1111": {"epss_score": 0.20},
            "CVE-2026-2222": {"epss_score": 0.55},
        }

    with patch("app.ingestion.scorer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.scorer.lookup_cve_intel", fake_lookup):
        await rescore_cluster("cluster-xyz")

    written = os_mock.update.call_args.kwargs["body"]["doc"]
    assert written["max_epss"] == 0.55  # max of the two member CVEs


@pytest.mark.asyncio
async def test_rescore_cluster_max_epss_zero_when_no_epss_data():
    from app.ingestion.scorer import rescore_cluster

    os_mock = AsyncMock()
    os_mock.get.return_value = {
        "_source": {
            "article_count": 1,
            "max_cvss": None,
            "cve_ids": ["CVE-2026-9999"],
            "entity_keys": [],
            "state": "new",
            "latest_at": "2026-05-21T00:00:00Z",
            "created_at": "2026-05-21T00:00:00Z",
            "max_credibility_weight": 1.0,
            "timeline": [],
            "cisa_kev": False,
        }
    }
    os_mock.update.return_value = {}

    async def fake_lookup(cve_ids):
        return {}  # CVE not enriched yet

    with patch("app.ingestion.scorer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.scorer.lookup_cve_intel", fake_lookup):
        await rescore_cluster("cluster-noepss")

    written = os_mock.update.call_args.kwargs["body"]["doc"]
    assert written["max_epss"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scorer.py::test_rescore_cluster_writes_max_epss_from_cve_topics -v`
Expected: FAIL — `AttributeError`/`ImportError` for `lookup_cve_intel` in `app.ingestion.scorer`, or `KeyError: 'max_epss'`.

- [ ] **Step 3: Import `lookup_cve_intel` and add the helper**

In `app/ingestion/scorer.py`, add to the imports near the top:

```python
from app.ingestion.cve_intel import lookup_cve_intel
```

Then add this helper above `rescore_cluster`:

```python
async def _max_epss_for_cves(cve_ids: list[str]) -> float:
    """Max EPSS score across a cluster's CVEs, read from cve_topics.

    Returns 0.0 when the cluster has no CVEs or none are EPSS-enriched yet.
    """
    if not cve_ids:
        return 0.0
    intel = await lookup_cve_intel(cve_ids)
    scores = [
        v["epss_score"]
        for v in intel.values()
        if v.get("epss_score") is not None
    ]
    return max(scores) if scores else 0.0
```

- [ ] **Step 4: Wire `max_epss` into `rescore_cluster`**

In `app/ingestion/scorer.py`, `rescore_cluster` currently reads `src`, computes `unique_source_count`, calls `compute_cluster_score`, then writes the update. Replace the body from `score_data = compute_cluster_score(` through the end of the `client.update(...)` call with:

```python
    cve_ids = src.get("cve_ids") or []
    max_epss = await _max_epss_for_cves(cve_ids)

    score_data = compute_cluster_score(
        article_count=src.get("article_count", 1),
        max_cvss=src.get("max_cvss"),
        cve_count=len(cve_ids),
        entity_keys=src.get("entity_keys") or [],
        state=src.get("state", "new"),
        latest_at=src.get("latest_at") or src.get("created_at", ""),
        max_credibility_weight=float(src.get("max_credibility_weight") or 1.0),
        unique_source_count=unique_source_count,
        cisa_kev=bool(src.get("cisa_kev", False)),
        max_epss=max_epss,
    )

    await client.update(
        index=INDEX_CLUSTERS,
        id=cluster_id,
        body={"doc": {
            "score": score_data["score"],
            "confidence": score_data["confidence"],
            "top_factors": score_data["top_factors"],
            "max_epss": max_epss,
        }},
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_scorer.py -v`
Expected: PASS — both new `rescore_cluster` tests plus all earlier `test_scorer.py` tests.

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/scorer.py tests/test_scorer.py
git commit -m "feat: compute and persist max_epss during cluster rescore"
```

---

## Task 4: Expose `max_epss` in the cluster API

**Files:**
- Modify: `app/models/cluster.py` (`ClusterSummary`, `ClusterDetail`)
- Modify: `app/api/routes/clusters.py` (`list_clusters`, `get_cluster`)
- Test: `tests/test_clusters_api.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_clusters_api.py`:

```python
@pytest.mark.asyncio
async def test_get_cluster_exposes_max_epss():
    from app.api.routes.clusters import get_cluster

    os_mock = AsyncMock()
    os_mock.get.return_value = {
        "_id": "cluster-epss-api",
        "_source": {
            "label": "Test cluster",
            "state": "new",
            "article_ids": [],
            "article_count": 0,
            "score": 12.0,
            "max_epss": 0.47,
            "timeline": [],
        },
    }

    with patch("app.api.routes.clusters.get_os_client", return_value=os_mock):
        result = await get_cluster("cluster-epss-api")

    assert result.max_epss == 0.47
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_clusters_api.py::test_get_cluster_exposes_max_epss -v`
Expected: FAIL — `ClusterDetail` has no attribute `max_epss` (Pydantic ignores the unknown field, so `result.max_epss` raises `AttributeError`).

- [ ] **Step 3: Add `max_epss` to the response models**

In `app/models/cluster.py`, the `ClusterSummary` class has:

```python
    max_cvss: Optional[float] = Field(None, json_schema_extra={"example": 9.8})
```

Add directly below it:

```python
    max_epss: Optional[float] = Field(None, json_schema_extra={"example": 0.62})
```

In `ClusterDetail`, find:

```python
    score: Optional[Decimal] = Field(None, json_schema_extra={"example": 87.5})
    confidence: Optional[str] = Field(None, json_schema_extra={"example": "high"})
```

Insert between those two lines:

```python
    max_epss: Optional[float] = Field(None, json_schema_extra={"example": 0.62})
```

- [ ] **Step 4: Populate `max_epss` in both routes**

In `app/api/routes/clusters.py`, `list_clusters` builds `ClusterSummary(...)`. The call has:

```python
                max_cvss=src.get("max_cvss"),
```

Add directly below it:

```python
                max_epss=src.get("max_epss"),
```

In `get_cluster`, the `ClusterDetail(...)` call has:

```python
        score=Decimal(str(src["score"])) if src.get("score") is not None else None,
        confidence=src.get("confidence"),
```

Insert between those two lines:

```python
        max_epss=src.get("max_epss"),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_clusters_api.py -v`
Expected: PASS — the new test plus `test_list_clusters_excludes_advisory_clusters`.

- [ ] **Step 6: Commit**

```bash
git add app/models/cluster.py app/api/routes/clusters.py tests/test_clusters_api.py
git commit -m "feat: expose max_epss in cluster API responses"
```

---

## Task 5: EPSS inline-on-create in `cve_topic_manager`

**Files:**
- Modify: `app/ingestion/cve_topic_manager.py`
- Test: `tests/test_cve_topic_manager.py`

- [ ] **Step 1: Update the test helper and existing tests**

In `tests/test_cve_topic_manager.py`, the `_make_os_client` helper does not stub `search`. The new EPSS code calls `os_client.search`. Replace the helper with:

```python
def _make_os_client(exists: bool = False, existing_ids: list[str] | None = None) -> AsyncMock:
    client = AsyncMock()
    client.exists = AsyncMock(return_value=exists)
    client.update = AsyncMock()
    client.index = AsyncMock()
    hits = [{"_id": cid} for cid in (existing_ids or [])]
    client.search = AsyncMock(return_value={"hits": {"hits": hits}})
    return client
```

The two existing tests `test_upsert_cve_topics_calls_update_for_each_cve` and `test_upsert_cve_topics_omits_embedding_when_none` now reach the EPSS fetch path. Patch `fetch_epss` in both so no real HTTP call happens. Replace those two tests with:

```python
@pytest.mark.asyncio
async def test_upsert_cve_topics_calls_update_for_each_cve():
    from app.ingestion.cve_topic_manager import upsert_cve_topics

    mock_client = _make_os_client()
    with patch("app.ingestion.cve_topic_manager.get_os_client", return_value=mock_client), \
         patch("app.ingestion.cve_topic_manager.fetch_epss", new_callable=AsyncMock, return_value={}):
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
async def test_upsert_cve_topics_omits_embedding_when_none():
    from app.ingestion.cve_topic_manager import upsert_cve_topics

    mock_client = _make_os_client()
    with patch("app.ingestion.cve_topic_manager.get_os_client", return_value=mock_client), \
         patch("app.ingestion.cve_topic_manager.fetch_epss", new_callable=AsyncMock, return_value={}):
        await upsert_cve_topics(["CVE-2024-1234"], "test-article", [], embedding=None)

    body = mock_client.update.call_args.kwargs["body"]
    assert "cve_embedding" not in body["upsert"]
```

- [ ] **Step 2: Write the new failing tests**

Append to `tests/test_cve_topic_manager.py`:

```python
@pytest.mark.asyncio
async def test_upsert_cve_topics_populates_epss_on_new_topic():
    """A CVE with no existing topic doc gets EPSS in its on-create doc."""
    from app.ingestion.cve_topic_manager import upsert_cve_topics

    mock_client = _make_os_client(existing_ids=[])  # nothing exists yet
    epss_payload = {
        "CVE-2024-1234": {
            "epss_score": 0.42,
            "epss_percentile": 0.91,
            "epss_updated_at": "2026-05-21",
        }
    }
    with patch("app.ingestion.cve_topic_manager.get_os_client", return_value=mock_client), \
         patch("app.ingestion.cve_topic_manager.fetch_epss", new_callable=AsyncMock, return_value=epss_payload) as mock_fetch:
        await upsert_cve_topics(["CVE-2024-1234"], "test-article", [], embedding=None)

    mock_fetch.assert_awaited_once()
    upsert_doc = mock_client.update.call_args.kwargs["body"]["upsert"]
    assert upsert_doc["epss_score"] == 0.42
    assert upsert_doc["epss_percentile"] == 0.91
    assert upsert_doc["epss_updated_at"] == "2026-05-21"


@pytest.mark.asyncio
async def test_upsert_cve_topics_skips_epss_for_existing_topic():
    """An already-existing CVE topic is not EPSS-refetched on the ingest path."""
    from app.ingestion.cve_topic_manager import upsert_cve_topics

    mock_client = _make_os_client(existing_ids=["CVE-2024-1234"])  # already exists
    with patch("app.ingestion.cve_topic_manager.get_os_client", return_value=mock_client), \
         patch("app.ingestion.cve_topic_manager.fetch_epss", new_callable=AsyncMock, return_value={}) as mock_fetch:
        await upsert_cve_topics(["CVE-2024-1234"], "test-article", [], embedding=None)

    mock_fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_cve_topic_stubs_populates_epss():
    """Stub creation also fetches EPSS for the new CVE topics."""
    from app.ingestion.cve_topic_manager import create_cve_topic_stubs

    mock_client = _make_os_client(exists=False)
    epss_payload = {
        "CVE-2024-1111": {
            "epss_score": 0.07,
            "epss_percentile": 0.50,
            "epss_updated_at": "2026-05-21",
        }
    }
    with patch("app.ingestion.cve_topic_manager.get_os_client", return_value=mock_client), \
         patch("app.ingestion.cve_topic_manager.fetch_epss", new_callable=AsyncMock, return_value=epss_payload):
        await create_cve_topic_stubs(["CVE-2024-1111"])

    indexed_doc = mock_client.index.call_args.kwargs["body"]
    assert indexed_doc["epss_score"] == 0.07
    assert indexed_doc["epss_percentile"] == 0.50
    assert indexed_doc["epss_updated_at"] == "2026-05-21"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_cve_topic_manager.py -v`
Expected: FAIL — `fetch_epss` is not an attribute of `app.ingestion.cve_topic_manager` (patch target missing), and EPSS keys absent from docs.

- [ ] **Step 4: Implement inline EPSS fetch**

Replace the entire contents of `app/ingestion/cve_topic_manager.py` with:

```python
"""Manages cve_topics index documents.

Two public functions:
  upsert_cve_topics()       — attach an article to CVE topics (creates if missing)
  create_cve_topic_stubs()  — create empty CVE topic docs for roundup articles

When a CVE topic is created for the first time, EPSS scores are fetched inline
from FIRST.org so the topic is never born without exploit-prediction data.
Existing topics are left untouched here — scripts/refresh_epss.py owns refresh.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from app.db.opensearch import INDEX_CVE_TOPICS, get_os_client
from app.ingestion.epss_client import fetch_epss

logger = logging.getLogger(__name__)


async def _epss_for_new_cves(os_client, cve_ids: list[str]) -> dict[str, dict]:
    """Return EPSS data keyed by uppercase CVE ID, only for CVEs with no topic doc.

    Existing topics are excluded — refresh_epss.py keeps those current. On any
    failure returns an empty dict so topic creation still proceeds.
    """
    ids = list({c.upper() for c in cve_ids if c})
    if not ids:
        return {}
    try:
        resp = await os_client.search(
            index=INDEX_CVE_TOPICS,
            body={"query": {"ids": {"values": ids}}, "size": len(ids), "_source": False},
        )
        existing = {hit["_id"] for hit in resp["hits"]["hits"]}
    except Exception as exc:
        logger.warning("EPSS existence check failed: %s", exc)
        return {}
    new_ids = [i for i in ids if i not in existing]
    if not new_ids:
        return {}
    try:
        return await fetch_epss(new_ids)
    except Exception as exc:
        logger.warning("EPSS fetch failed for %d new CVEs: %s", len(new_ids), exc)
        return {}


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
    epss_map = await _epss_for_new_cves(os_client, cve_ids)

    for cve_id in cve_ids:
        try:
            await _upsert_one(
                os_client, cve_id, article_slug, aliases, embedding, now,
                epss=epss_map.get(cve_id.upper()),
            )
        except Exception as exc:
            logger.warning("cve_topic upsert failed for %s: %s", cve_id, exc)


async def create_cve_topic_stubs(cve_ids: list[str]) -> None:
    """Create empty cve_topic documents for roundup articles.

    Does not attach the roundup article — just ensures the CVE topic exists
    so it is discoverable. Skips CVEs that already have a topic document.
    """
    if not cve_ids:
        return
    os_client = get_os_client()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    missing: list[str] = []
    for cve_id in cve_ids:
        try:
            if not await os_client.exists(index=INDEX_CVE_TOPICS, id=cve_id):
                missing.append(cve_id)
        except Exception as exc:
            logger.warning("cve_topic existence check failed for %s: %s", cve_id, exc)
    if not missing:
        return

    epss_map: dict[str, dict] = {}
    try:
        epss_map = await fetch_epss([c.upper() for c in missing])
    except Exception as exc:
        logger.warning("EPSS fetch failed for %d stub CVEs: %s", len(missing), exc)

    for cve_id in missing:
        try:
            epss = epss_map.get(cve_id.upper())
            doc = {
                "cve_id": cve_id,
                "aliases": [],
                "cvss_score": None,
                "cvss_severity": None,
                "cvss_vector": None,
                "cisa_kev": False,
                "epss_score": epss["epss_score"] if epss else None,
                "epss_percentile": epss["epss_percentile"] if epss else None,
                "epss_updated_at": epss["epss_updated_at"] if epss else None,
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
    epss: Optional[dict] = None,
) -> None:
    doc_on_create: dict = {
        "cve_id": cve_id,
        "aliases": aliases,
        "cvss_score": None,
        "cvss_severity": None,
        "cvss_vector": None,
        "cisa_kev": False,
        "epss_score": epss["epss_score"] if epss else None,
        "epss_percentile": epss["epss_percentile"] if epss else None,
        "epss_updated_at": epss["epss_updated_at"] if epss else None,
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

Note: `epss_updated_at` is added to `doc_on_create` only on create — and the
`cve_topics` mapping already maps `epss_updated_at` (`opensearch.py:304`). The
painless `script` (run when the doc already exists) does not touch EPSS, so an
existing topic keeps whatever `refresh_epss.py` last wrote.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_cve_topic_manager.py -v`
Expected: PASS — all five tests (two rewritten, three new).

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/cve_topic_manager.py tests/test_cve_topic_manager.py
git commit -m "feat: fetch EPSS scores inline when creating cve_topics"
```

---

## Task 6: Pipeline reorder — `_apply_cve_intel` after NER

**Files:**
- Modify: `app/ingestion/ingester.py` (`ingest_source`; new `_merge_entity_cves` helper)
- Test: `tests/test_ingester.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ingester.py`:

```python
class TestMergeEntityCves:
    def test_adds_body_only_cve_from_ner(self):
        from app.ingestion.ingester import _merge_entity_cves
        result = _merge_entity_cves(
            ["CVE-2026-1111"],
            [
                {"type": "cve", "normalized_key": "cve-2026-2222"},
                {"type": "product", "normalized_key": "fortios"},
            ],
        )
        assert result == ["CVE-2026-1111", "CVE-2026-2222"]

    def test_dedups_case_insensitively(self):
        from app.ingestion.ingester import _merge_entity_cves
        result = _merge_entity_cves(
            ["CVE-2026-1111"],
            [{"type": "cve", "normalized_key": "cve-2026-1111"}],
        )
        assert result == ["CVE-2026-1111"]

    def test_empty_inputs_return_empty_list(self):
        from app.ingestion.ingester import _merge_entity_cves
        assert _merge_entity_cves([], []) == []

    def test_no_cve_entities_leaves_cve_ids_unchanged(self):
        from app.ingestion.ingester import _merge_entity_cves
        result = _merge_entity_cves(
            ["CVE-2026-1111"],
            [{"type": "actor", "normalized_key": "lazarus-group"}],
        )
        assert result == ["CVE-2026-1111"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ingester.py::TestMergeEntityCves -v`
Expected: FAIL — `cannot import name '_merge_entity_cves'`.

- [ ] **Step 3: Add the `_merge_entity_cves` helper**

In `app/ingestion/ingester.py`, add this function just above `async def ingest_source(`:

```python
def _merge_entity_cves(cve_ids: list[str], entities: list[dict]) -> list[str]:
    """Union RSS-extracted cve_ids with CVE-type NER entity keys.

    NER runs on the full article body, so it finds CVEs the RSS snippet omits.
    Existing IDs keep their original casing; appended NER CVEs are uppercased.
    Dedup is case-insensitive.
    """
    merged = list(cve_ids or [])
    seen = {c.upper() for c in merged}
    for e in entities or []:
        if e.get("type") == "cve":
            key = (e.get("normalized_key") or "").upper()
            if key and key not in seen:
                merged.append(key)
                seen.add(key)
    return merged
```

- [ ] **Step 4: Remove the pre-upsert `_apply_cve_intel` call**

In `app/ingestion/ingester.py`, inside `ingest_source`, delete this line (it currently sits just before `inserted = await (overwrite_article if update else upsert_article)(article)`):

```python
            await _apply_cve_intel(article)
```

- [ ] **Step 5: Rewrite the post-insert block**

In `app/ingestion/ingester.py`, replace the entire `if inserted:` block (from `if inserted:` down to and including the `else:` / `stats["skipped"] += 1` lines) with:

```python
            if inserted:
                stats["inserted"] += 1

                # Body extraction runs first so NER sees the full article body,
                # not just the short RSS desc. content_html is merged back into
                # article in-memory so NER picks it up without an extra fetch.
                from app.ingestion.body_pipeline import maybe_extract_body
                try:
                    body_updates = await maybe_extract_body(
                        article_doc=dict(article),
                        source_dict={"min_body_chars": source.get("min_body_chars")},
                    )
                    if body_updates:
                        if body_updates.get("content_html"):
                            article["content_html"] = body_updates["content_html"]
                        await get_os_client().update(
                            index=INDEX_NEWS,
                            id=article["slug"],
                            body={"doc": body_updates},
                        )
                except Exception as exc:
                    logger.warning(
                        "[%s] Body extraction failed for '%s': %s",
                        name, article.get("slug"), exc,
                    )

                # NER entity extraction on the full body.
                entities: list[dict] = []
                keyword_list: list[str] = []
                try:
                    if not article_slug:
                        logger.warning("[%s] Article missing slug — entity store skipped", name)
                    # LLM/sidecar NER only runs on new articles, not duplicates.
                    # Use a real session so results are cached in ner_cache.
                    async with AsyncSessionLocal() as ner_session:
                        text_entities = await extract_entities(article, slug=article_slug, db_session=ner_session)
                    all_entities = merge_entities(text_entities, tag_result["tag_entities"])
                    entities = all_entities
                    if all_entities:
                        await store_article_entities(article["slug"], all_entities)
                        keyword_list = list(dict.fromkeys(e["name"] for e in all_entities))
                except Exception:
                    logger.exception(
                        "[%s] Entity extraction failed for '%s'",
                        name, article.get("slug"),
                    )

                # Merge NER-discovered CVEs into cve_ids, then enrich CVSS/severity
                # from cve_topics. Runs unconditionally so body-level CVEs reach
                # the lookup even when the article produced no NER entities.
                article["cve_ids"] = _merge_entity_cves(article.get("cve_ids") or [], entities)
                await _apply_cve_intel(article)

                # One write-back to the indexed doc: cve_ids/cvss/severity always,
                # keywords only when NER produced entities.
                enrichment_doc: dict = {
                    "cve_ids": article.get("cve_ids") or [],
                    "cvss_score": article.get("cvss_score"),
                    "severity": article.get("severity"),
                }
                if keyword_list:
                    enrichment_doc["keywords"] = keyword_list
                try:
                    await get_os_client().update(
                        index=INDEX_NEWS,
                        id=article["slug"],
                        body={"doc": enrichment_doc},
                    )
                except Exception:
                    logger.exception(
                        "[%s] Enrichment write-back failed for '%s'",
                        name, article.get("slug"),
                    )

                # Clustering — always attempt, even without entities.
                try:
                    await cluster_article(article, article["slug"], entities)
                except Exception:
                    logger.exception(
                        "[%s] Clustering failed for '%s'",
                        name, article.get("slug"),
                    )
            else:
                stats["skipped"] += 1
```

- [ ] **Step 6: Run the full ingester test file**

Run: `pytest tests/test_ingester.py -v`
Expected: PASS — `TestMergeEntityCves` (4 new tests) plus every pre-existing test (`TestPrepareArticleDoc`, `TestUpsertArticleSponsored`, `TestContentTypeIsSet`, the `_apply_cve_intel` tests, `test_ingest_all_feeds_calls_refresh_entity_intel`).

- [ ] **Step 7: Commit**

```bash
git add app/ingestion/ingester.py tests/test_ingester.py
git commit -m "feat: run CVE enrichment after NER so body-level CVEs are scored"
```

---

## Task 7: `rebuild_all.py` EPSS refresh step

**Files:**
- Modify: `scripts/rebuild_all.py`
- Test: `tests/test_rebuild_all.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_rebuild_all.py`:

```python
"""Tests for scripts/rebuild_all.py step orchestration."""
from argparse import Namespace
from unittest.mock import patch

import pytest


def _args(**overrides) -> Namespace:
    defaults = {
        "skip_ner": False,
        "skip_embed": False,
        "skip_epss": False,
        "skip_cluster": False,
        "force": False,
        "dry_run": True,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


@pytest.mark.asyncio
async def test_epss_step_runs_between_embeddings_and_clustering():
    import scripts.rebuild_all as rebuild_all

    calls: list[str] = []

    async def fake_ner(**kw):
        calls.append("ner")

    async def fake_embed(**kw):
        calls.append("embed")

    async def fake_epss(**kw):
        calls.append("epss")

    async def fake_cluster(**kw):
        calls.append("cluster")

    with patch.object(rebuild_all, "run_ner", fake_ner), \
         patch.object(rebuild_all, "run_embeddings", fake_embed), \
         patch.object(rebuild_all, "run_epss_sync", fake_epss), \
         patch.object(rebuild_all, "run_clustering", fake_cluster):
        await rebuild_all.main(_args())

    assert calls == ["ner", "embed", "epss", "cluster"]


@pytest.mark.asyncio
async def test_skip_epss_excludes_the_step():
    import scripts.rebuild_all as rebuild_all

    calls: list[str] = []

    async def fake_ner(**kw):
        calls.append("ner")

    async def fake_embed(**kw):
        calls.append("embed")

    async def fake_epss(**kw):
        calls.append("epss")

    async def fake_cluster(**kw):
        calls.append("cluster")

    with patch.object(rebuild_all, "run_ner", fake_ner), \
         patch.object(rebuild_all, "run_embeddings", fake_embed), \
         patch.object(rebuild_all, "run_epss_sync", fake_epss), \
         patch.object(rebuild_all, "run_clustering", fake_cluster):
        await rebuild_all.main(_args(skip_epss=True))

    assert "epss" not in calls
    assert calls == ["ner", "embed", "cluster"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_rebuild_all.py -v`
Expected: FAIL — `scripts.rebuild_all` has no attribute `run_epss_sync`.

- [ ] **Step 3: Add the `run_epss_sync` step function**

In `scripts/rebuild_all.py`, add this function directly after `run_embeddings`:

```python
async def run_epss_sync(force: bool, dry_run: bool) -> None:
    # EPSS always overwrites (FIRST.org recomputes daily) — `force` is unused.
    from scripts.refresh_epss import main as epss_main
    args = Namespace(dry_run=dry_run, limit=0)
    await epss_main(args)
```

- [ ] **Step 4: Register the step in `main`**

In `scripts/rebuild_all.py`, `main` builds `steps`. It currently has:

```python
    if not args.skip_embed:
        steps.append(("Embeddings", run_embeddings))
    if not args.skip_cluster:
        steps.append(("Clustering", run_clustering))
```

Replace with:

```python
    if not args.skip_embed:
        steps.append(("Embeddings", run_embeddings))
    if not args.skip_epss:
        steps.append(("EPSS refresh", run_epss_sync))
    if not args.skip_cluster:
        steps.append(("Clustering", run_clustering))
```

- [ ] **Step 5: Add the `--skip-epss` CLI flag**

In `scripts/rebuild_all.py`, the argument parser has:

```python
    parser.add_argument("--skip-embed", action="store_true", help="Skip embedding step")
    parser.add_argument("--skip-cluster", action="store_true", help="Skip clustering step")
```

Replace with:

```python
    parser.add_argument("--skip-embed", action="store_true", help="Skip embedding step")
    parser.add_argument("--skip-epss", action="store_true", help="Skip EPSS refresh step")
    parser.add_argument("--skip-cluster", action="store_true", help="Skip clustering step")
```

Also update the module docstring's usage block: change the header line to
`"""Full rebuild pipeline: NER → embeddings → EPSS refresh → clustering.` and add
the line `    python scripts/rebuild_all.py --skip-epss        # skip EPSS step`
alongside the other `--skip-*` usage lines.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_rebuild_all.py -v`
Expected: PASS — both tests.

- [ ] **Step 7: Commit**

```bash
git add scripts/rebuild_all.py tests/test_rebuild_all.py
git commit -m "feat: add EPSS refresh step to rebuild_all before clustering"
```

---

## Task 8: Schedule `refresh_epss.py` in the ingestion loop

**Files:**
- Modify: `docker-compose.yml` (the `ingestion` service `command`)

There is no crontab in this project — recurring jobs run inside the `ingestion`
service's shell loop. `refresh_epss.py` joins that loop right after the existing
NVD CVE enrichment step, so EPSS data refreshes every ingestion cycle.

- [ ] **Step 1: Add `refresh_epss.py` to the ingestion loop**

In `docker-compose.yml`, the `ingestion` service `command` currently is:

```yaml
    command: >
      sh -c 'while true; do
        echo "=== Starting feed ingestion: $$(date -Iseconds) ===";
        python scripts/ingest_feeds.py;
        echo "=== NVD CVE enrichment: $$(date -Iseconds) ===";
        python scripts/enrich_cve_nvd.py;
        echo "=== Sleeping 1 hour ===";
        sleep 3600;
      done'
```

Replace it with:

```yaml
    command: >
      sh -c 'while true; do
        echo "=== Starting feed ingestion: $$(date -Iseconds) ===";
        python scripts/ingest_feeds.py;
        echo "=== NVD CVE enrichment: $$(date -Iseconds) ===";
        python scripts/enrich_cve_nvd.py;
        echo "=== EPSS refresh: $$(date -Iseconds) ===";
        python scripts/refresh_epss.py;
        echo "=== Sleeping 1 hour ===";
        sleep 3600;
      done'
```

- [ ] **Step 2: Verify the compose file still parses**

Run: `docker compose config --quiet`
Expected: no output, exit code 0 (YAML is valid). If `docker compose` is
unavailable in the environment, instead re-read the file and confirm the
`command` block is well-formed and the new two lines sit between
`enrich_cve_nvd.py` and the `sleep 3600` line.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "chore: run refresh_epss.py in the hourly ingestion loop"
```

---

## Final verification

- [ ] **Step 1: Run the full affected test suite**

Run: `pytest tests/test_scorer.py tests/test_clusterer.py tests/test_clusters_api.py tests/test_cve_topic_manager.py tests/test_ingester.py tests/test_rebuild_all.py tests/test_epss_client.py tests/test_cve_intel.py -v`
Expected: PASS — all tests green.

- [ ] **Step 2: Confirm no other callers of `compute_cluster_score` broke**

Run: `pytest tests/ -q`
Expected: PASS — full suite green; the new `max_epss` parameter is optional so no other caller is affected.

---

## Notes for the rollout (out of plan scope)

After this plan lands and all tests pass, applying EPSS across the existing
corpus requires running `python scripts/rebuild_all.py` (now including the EPSS
step). That run executes NER and is a **separate, explicitly-authorized action** —
do not run it as part of plan execution. Confirm with the user first.
