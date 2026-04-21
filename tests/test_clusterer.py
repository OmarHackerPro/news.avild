"""Tests for app.ingestion.clusterer — async mocked OpenSearch client."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.ingestion.clusterer import _signal_keys, cluster_article


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

        entities = [
            {"normalized_key": "fortinet", "type": "vendor"},
            {"normalized_key": "fortios", "type": "product"},
        ]
        await cluster_article(article, "test-article-001", entities)

        mock_cve.assert_awaited_once_with(["CVE-2026-1234"])
        mock_merge.assert_awaited_once()
        # Verify merge was called with the right cluster id and slug
        merge_args = mock_merge.call_args
        assert merge_args[0][0] == "cluster-existing"
        assert merge_args[0][1] == "test-article-001"
        mock_create.assert_not_awaited()


# ---------------------------------------------------------------------------
# _signal_keys helper
# ---------------------------------------------------------------------------

def test_signal_keys_excludes_vendors():
    entities = [
        {"normalized_key": "microsoft", "type": "vendor"},
        {"normalized_key": "cve-2024-1234", "type": "cve"},
        {"normalized_key": "lockbit", "type": "malware"},
    ]
    assert _signal_keys(entities) == ["cve-2024-1234", "lockbit"]


def test_signal_keys_empty_input():
    assert _signal_keys([]) == []


def test_signal_keys_all_vendors_returns_empty():
    entities = [
        {"normalized_key": "microsoft", "type": "vendor"},
        {"normalized_key": "google", "type": "vendor"},
    ]
    assert _signal_keys(entities) == []


# ---------------------------------------------------------------------------
# cluster_article — CVE cap (roundup articles skip CVE lookup)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cluster_article_skips_cve_lookup_for_roundup():
    """Articles with >3 CVEs skip CVE-based cluster matching."""
    entities = [
        {"normalized_key": f"cve-2024-{i:04d}", "type": "cve"} for i in range(5)
    ]
    article = {"slug": "patch-tuesday", "title": "Patch Tuesday", "published_at": "2024-01-01T00:00:00Z"}

    with patch("app.ingestion.clusterer.find_cluster_by_cve", new_callable=AsyncMock) as mock_cve, \
         patch("app.ingestion.clusterer.find_cluster_by_entities", new_callable=AsyncMock, return_value=None), \
         patch("app.ingestion.clusterer.find_cluster_by_mlt", new_callable=AsyncMock, return_value=None), \
         patch("app.ingestion.clusterer.create_cluster", new_callable=AsyncMock):
        await cluster_article(article, "patch-tuesday", entities)
        mock_cve.assert_not_called()


# ---------------------------------------------------------------------------
# cluster_article — vendor entities excluded from entity matching
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cluster_article_excludes_vendors_from_entity_matching():
    """Vendor entities are excluded from signal_keys used for cluster matching."""
    entities = [
        {"normalized_key": "microsoft", "type": "vendor"},
        {"normalized_key": "google", "type": "vendor"},
    ]
    article = {"slug": "generic-article", "title": "Generic Article", "published_at": "2024-01-01T00:00:00Z"}

    with patch("app.ingestion.clusterer.find_cluster_by_entities", new_callable=AsyncMock, return_value=None) as mock_entities, \
         patch("app.ingestion.clusterer.find_cluster_by_cve", new_callable=AsyncMock, return_value=None), \
         patch("app.ingestion.clusterer.find_cluster_by_mlt", new_callable=AsyncMock, return_value=None), \
         patch("app.ingestion.clusterer.create_cluster", new_callable=AsyncMock):
        await cluster_article(article, "generic-article", entities)
        # Should be called with empty signal_keys (vendors excluded)
        mock_entities.assert_awaited_once_with([])


# ---------------------------------------------------------------------------
# find_cluster_by_mlt — stop words and size cap
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mlt_query_includes_stop_words(mock_os_client):
    """MLT query must include a stop_words list to suppress boilerplate terms."""
    from app.ingestion.clusterer import find_cluster_by_mlt

    mock_os_client.count.return_value = {"count": 25}
    mock_os_client.search.return_value = {"hits": {"hits": []}}

    await find_cluster_by_mlt("Critical vulnerability in Apache", None)

    call_body = mock_os_client.search.call_args.kwargs["body"]
    mlt_query = call_body["query"]["bool"]["must"][0]["more_like_this"]
    assert "stop_words" in mlt_query
    assert "advisory" in mlt_query["stop_words"]
    assert "vulnerability" in mlt_query["stop_words"]
    assert "critical" in mlt_query["stop_words"]


@pytest.mark.asyncio
async def test_mlt_query_caps_cluster_size(mock_os_client):
    """MLT query must filter out clusters with article_count > 15."""
    from app.ingestion.clusterer import find_cluster_by_mlt

    mock_os_client.count.return_value = {"count": 25}
    mock_os_client.search.return_value = {"hits": {"hits": []}}

    await find_cluster_by_mlt("Ransomware gang targets hospitals", None)

    call_body = mock_os_client.search.call_args.kwargs["body"]
    filters = call_body["query"]["bool"]["filter"]
    size_filter = next(
        (f for f in filters if "range" in f and "article_count" in f["range"]),
        None,
    )
    assert size_filter is not None, "Expected article_count range filter"
    assert size_filter["range"]["article_count"]["lte"] == 15
