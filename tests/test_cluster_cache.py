"""Tests for the run-scoped in-process cluster cache (clustering perf fix #2)."""
from datetime import datetime, timezone

import pytest
from unittest.mock import AsyncMock, patch

from app.ingestion import cluster_cache


@pytest.fixture
def cache_on():
    """Enable the cache for one test, guaranteeing it is disabled afterwards."""
    cluster_cache.enable()
    yield
    cluster_cache.disable()


# ---------------------------------------------------------------------------
# Storage primitives
# ---------------------------------------------------------------------------

def test_disabled_by_default():
    assert cluster_cache.is_enabled() is False
    assert cluster_cache.get("anything") is None
    assert cluster_cache.hits() == []


def test_put_is_noop_when_disabled():
    cluster_cache.put("c1", {"state": "new"})
    assert cluster_cache.get("c1") is None


def test_put_get_when_enabled(cache_on):
    cluster_cache.put("c1", {"state": "new", "article_count": 1})
    got = cluster_cache.get("c1")
    assert got == {"state": "new", "article_count": 1}


def test_get_returns_a_shallow_copy(cache_on):
    """get() returns a fresh dict — reassigning a top-level key cannot corrupt
    the cached entry. (Nested containers are shared; callers that mutate them,
    e.g. merge_into_cluster, copy the nested list themselves first.)"""
    cluster_cache.put("c1", {"state": "new", "article_count": 1})
    got = cluster_cache.get("c1")
    got["state"] = "resolved"
    got["article_count"] = 99
    assert cluster_cache.get("c1") == {"state": "new", "article_count": 1}


def test_hits_shape(cache_on):
    cluster_cache.put("c1", {"state": "new"})
    hits = cluster_cache.hits()
    assert hits == [{"_id": "c1", "_source": {"state": "new"}}]


def test_enable_starts_empty_and_disable_clears(cache_on):
    cluster_cache.put("c1", {"state": "new"})
    cluster_cache.enable()  # re-enable should wipe
    assert cluster_cache.hits() == []
    cluster_cache.put("c2", {"state": "new"})
    cluster_cache.disable()
    assert cluster_cache.hits() == []


# ---------------------------------------------------------------------------
# _cache_candidates — mirrors structured + k-NN retrieval over the cache
# ---------------------------------------------------------------------------

_CUTOFF = "2026-04-19T00:00:00Z"


def test_cache_candidates_empty_when_disabled():
    from app.ingestion.unified_scorer import _cache_candidates
    assert _cache_candidates([{"type": "actor", "normalized_key": "apt29"}],
                             None, _CUTOFF, _CUTOFF) == []


def test_cache_candidates_structured_entity_match(cache_on):
    from app.ingestion.unified_scorer import _cache_candidates
    cluster_cache.put("c1", {
        "state": "new", "latest_at": "2026-05-19T00:00:00Z",
        "entity_keys": ["apt29"], "centroid_embedding": None,
    })
    out = _cache_candidates(
        [{"type": "actor", "normalized_key": "apt29"}], None, _CUTOFF, _CUTOFF)
    assert [h["_id"] for h in out] == ["c1"]


def test_cache_candidates_skips_resolved_and_out_of_window(cache_on):
    from app.ingestion.unified_scorer import _cache_candidates
    cluster_cache.put("resolved", {
        "state": "resolved", "latest_at": "2026-05-19T00:00:00Z",
        "entity_keys": ["apt29"],
    })
    cluster_cache.put("stale", {
        "state": "new", "latest_at": "2026-01-01T00:00:00Z",
        "entity_keys": ["apt29"],
    })
    out = _cache_candidates(
        [{"type": "actor", "normalized_key": "apt29"}], None, _CUTOFF, _CUTOFF)
    assert out == []


def test_cache_candidates_knn_embedding_match(cache_on):
    from app.ingestion.unified_scorer import _cache_candidates
    emb = [1.0] + [0.0] * 1023
    cluster_cache.put("c-embed", {
        "state": "new", "latest_at": "2026-05-19T00:00:00Z",
        "entity_keys": [], "centroid_embedding": emb,
    })
    out = _cache_candidates([], emb, _CUTOFF, _CUTOFF)
    assert [h["_id"] for h in out] == ["c-embed"]


# ---------------------------------------------------------------------------
# find_best_cluster — picks up a cached cluster invisible to OpenSearch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_best_cluster_matches_cached_cluster(cache_on):
    """A cluster present only in the cache (not yet in OpenSearch) still wins."""
    from app.ingestion.unified_scorer import find_best_cluster

    cluster_cache.put("cached-c1", {
        "state": "new",
        "latest_at": "2026-05-19T00:00:00Z",
        "entity_keys": ["apt29", "citrixbleed"],
        "founding_entity_types": [
            {"key": "apt29", "type": "actor"},
            {"key": "citrixbleed", "type": "vuln_alias"},
        ],
        "centroid_embedding": None,
    })

    os_mock = AsyncMock()
    os_mock.search.return_value = {"hits": {"hits": []}}  # OpenSearch sees nothing

    article_entities = [
        {"type": "actor", "normalized_key": "apt29"},
        {"type": "vuln_alias", "normalized_key": "citrixbleed"},
    ]

    with patch("app.ingestion.unified_scorer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.unified_scorer.ensure_idf_map", new_callable=AsyncMock):
        result = await find_best_cluster(
            article_entities, None,
            reference_time=datetime(2026, 5, 19, tzinfo=timezone.utc),
        )

    assert result == "cached-c1"
