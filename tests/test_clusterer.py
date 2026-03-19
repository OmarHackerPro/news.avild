"""Tests for app.ingestion.clusterer — async mocked OpenSearch client."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.fixture
def mock_os_client():
    client = AsyncMock()
    with patch("app.ingestion.clusterer.get_os_client", return_value=client):
        yield client


# ---------------------------------------------------------------------------
# find_cluster_by_cve
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_cluster_by_cve_returns_match(mock_os_client):
    from app.ingestion.clusterer import find_cluster_by_cve

    mock_os_client.search.return_value = {
        "hits": {"hits": [{"_id": "cluster-abc", "_score": 1.0}]}
    }

    result = await find_cluster_by_cve(["CVE-2026-1234"])
    assert result == "cluster-abc"

    # Verify the query used terms on cve_ids
    call_body = mock_os_client.search.call_args.kwargs["body"]
    terms = call_body["query"]["bool"]["filter"][0]
    assert terms == {"terms": {"cve_ids": ["CVE-2026-1234"]}}


# ---------------------------------------------------------------------------
# find_cluster_by_entities
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_cluster_by_entities_returns_match(mock_os_client):
    from app.ingestion.clusterer import find_cluster_by_entities

    mock_os_client.search.return_value = {
        "hits": {
            "hits": [{
                "_id": "cluster-xyz",
                "_score": 1.0,
                "_source": {"entity_keys": ["fortinet", "fortios", "vpn"]},
            }]
        }
    }

    result = await find_cluster_by_entities(["fortinet", "fortios"])
    assert result == "cluster-xyz"


@pytest.mark.asyncio
async def test_find_cluster_by_entities_insufficient_overlap(mock_os_client):
    from app.ingestion.clusterer import find_cluster_by_entities

    # Cluster only has "fortinet", not "fortios" — overlap is 1, below threshold
    mock_os_client.search.return_value = {
        "hits": {
            "hits": [{
                "_id": "cluster-xyz",
                "_score": 1.0,
                "_source": {"entity_keys": ["fortinet"]},
            }]
        }
    }

    result = await find_cluster_by_entities(["fortinet", "fortios"])
    assert result is None


# ---------------------------------------------------------------------------
# cluster_article — full path (CVE match)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cluster_article_cve_match_merges():
    from app.ingestion.clusterer import cluster_article

    article = {
        "slug": "test-article-001",
        "title": "Critical FortiOS RCE",
        "source_name": "BleepingComputer",
        "published_at": "2026-03-19T10:00:00+00:00",
        "cve_ids": ["CVE-2026-1234"],
        "summary": "A critical vulnerability...",
        "desc": "A critical vulnerability...",
        "category": "vulnerability",
    }

    with patch("app.ingestion.clusterer.find_cluster_by_cve", new_callable=AsyncMock) as mock_cve, \
         patch("app.ingestion.clusterer.merge_into_cluster", new_callable=AsyncMock) as mock_merge, \
         patch("app.ingestion.clusterer.create_cluster", new_callable=AsyncMock) as mock_create:

        mock_cve.return_value = "cluster-existing"

        await cluster_article(article, "test-article-001", ["fortinet", "fortios"])

        mock_cve.assert_awaited_once_with(["CVE-2026-1234"])
        mock_merge.assert_awaited_once()
        # Verify merge was called with the right cluster id and slug
        merge_args = mock_merge.call_args
        assert merge_args[0][0] == "cluster-existing"
        assert merge_args[0][1] == "test-article-001"
        mock_create.assert_not_awaited()
