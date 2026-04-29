"""Tests for the rewritten app.ingestion.clusterer (unified scorer)."""
import pytest
from unittest.mock import AsyncMock, patch

from app.ingestion.clusterer import _build_event_signature, _updated_centroid


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------

def test_build_event_signature_high_confidence_cve_and_alias():
    entities = [
        {"type": "cve", "normalized_key": "CVE-2021-44228"},
        {"type": "vuln_alias", "normalized_key": "log4shell"},
    ]
    sig = _build_event_signature(entities, ["CVE-2021-44228"])
    assert sig["confidence"] == "high"
    assert "log4shell" in sig["vuln_aliases"]
    assert "CVE-2021-44228" in sig["cve_ids"]


def test_build_event_signature_low_confidence_no_signals():
    sig = _build_event_signature([], [])
    assert sig["confidence"] == "low"


def test_updated_centroid_initializes_from_first_vec():
    vec = [1.0, 0.0, 0.0]
    result = _updated_centroid(None, vec, 1)
    assert result == vec


def test_updated_centroid_running_average():
    old = [1.0, 0.0]
    new_vec = [0.0, 1.0]
    result = _updated_centroid(old, new_vec, 2)
    assert abs(result[0] - 0.5) < 0.001
    assert abs(result[1] - 0.5) < 0.001


# ---------------------------------------------------------------------------
# cluster_article — delegates to find_best_cluster
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cluster_article_merges_when_cluster_found():
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
         patch("app.ingestion.clusterer.create_cluster", new_callable=AsyncMock) as mock_create:

        from app.ingestion.clusterer import cluster_article
        await cluster_article(article, "fortios-rce-001", entities)

    mock_best.assert_awaited_once()
    mock_merge.assert_awaited_once()
    assert mock_merge.call_args[0][0] == "cluster-abc"
    mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_cluster_article_creates_new_when_no_match():
    article = {
        "slug": "novel-article-001",
        "title": "New Threat",
        "cve_ids": [],
        "source_name": "Threatpost",
        "published_at": "2026-04-27T10:00:00Z",
    }

    with patch("app.ingestion.clusterer.embed_text", new_callable=AsyncMock, return_value=None), \
         patch("app.ingestion.clusterer.find_best_cluster", new_callable=AsyncMock, return_value=None), \
         patch("app.ingestion.clusterer.merge_into_cluster", new_callable=AsyncMock) as mock_merge, \
         patch("app.ingestion.clusterer.create_cluster", new_callable=AsyncMock) as mock_create:

        from app.ingestion.clusterer import cluster_article
        await cluster_article(article, "novel-article-001", [])

    mock_merge.assert_not_awaited()
    mock_create.assert_awaited_once()


# ---------------------------------------------------------------------------
# create_cluster — sets seed_cve_ids, event_signature, centroid_embedding
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_cluster_sets_seed_cve_ids():
    os_mock = AsyncMock()
    os_mock.index.return_value = {"_id": "new-cluster-001"}
    os_mock.update.return_value = {}

    article = {
        "slug": "cve-article-001",
        "title": "Critical Bug",
        "cve_ids": ["CVE-2026-9999"],
        "published_at": "2026-04-27T10:00:00Z",
        "source_name": "CISA",
    }
    entities = [{"type": "cve", "normalized_key": "CVE-2026-9999"}]

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer._rescore", new_callable=AsyncMock):
        from app.ingestion.clusterer import create_cluster
        await create_cluster(article, entities, embedding=[0.1] * 1024)

    indexed = os_mock.index.call_args.kwargs["body"]
    assert indexed["seed_cve_ids"] == ["CVE-2026-9999"]
    assert indexed["cve_ids"] == ["CVE-2026-9999"]
    assert indexed["centroid_embedding"] == [0.1] * 1024


@pytest.mark.asyncio
async def test_create_cluster_event_signature_confidence_high_when_cve_and_alias():
    os_mock = AsyncMock()
    os_mock.index.return_value = {"_id": "cluster-hi-conf"}
    os_mock.update.return_value = {}

    article = {
        "slug": "log4shell-001",
        "title": "Log4Shell exploited",
        "cve_ids": ["CVE-2021-44228"],
        "published_at": "2026-04-27T10:00:00Z",
        "source_name": "BleepingComputer",
    }
    entities = [
        {"type": "cve", "normalized_key": "CVE-2021-44228"},
        {"type": "vuln_alias", "normalized_key": "log4shell"},
    ]

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer._rescore", new_callable=AsyncMock):
        from app.ingestion.clusterer import create_cluster
        await create_cluster(article, entities)

    indexed = os_mock.index.call_args.kwargs["body"]
    assert indexed["event_signature"]["confidence"] == "high"
    assert "log4shell" in indexed["event_signature"]["vuln_aliases"]


# ---------------------------------------------------------------------------
# merge_into_cluster — does NOT update seed_cve_ids
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_merge_does_not_touch_seed_cve_ids():
    os_mock = AsyncMock()
    os_mock.get.return_value = {
        "_source": {
            "article_count": 1,
            "centroid_embedding": [0.5] * 1024,
            "event_signature": {"cve_ids": ["CVE-2026-1111"], "vuln_aliases": [],
                                 "campaign_names": [], "affected_products": [],
                                 "primary_actors": [], "confidence": "medium"},
        }
    }
    os_mock.update.return_value = {}

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer._rescore", new_callable=AsyncMock):
        from app.ingestion.clusterer import merge_into_cluster
        await merge_into_cluster(
            "cluster-existing", "article-new", ["fortios"], ["CVE-2026-1111"],
            source_name="CISA", title="Follow-up", published_at="2026-04-27T12:00:00Z",
        )

    for call in os_mock.update.call_args_list:
        script = call.kwargs.get("body", {}).get("script", {})
        if "source" in script:
            assert "seed_cve_ids" not in script["source"]
