"""Tests for app.ingestion.cve_topic_manager."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


def _make_os_client(exists: bool = False, existing_ids: list[str] | None = None) -> AsyncMock:
    client = AsyncMock()
    client.exists = AsyncMock(return_value=exists)
    client.update = AsyncMock()
    client.index = AsyncMock()
    hits = [{"_id": cid} for cid in (existing_ids or [])]
    client.search = AsyncMock(return_value={"hits": {"hits": hits}})
    return client


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
    with patch("app.ingestion.cve_topic_manager.get_os_client", return_value=mock_client), \
         patch("app.ingestion.cve_topic_manager.fetch_epss", new_callable=AsyncMock, return_value={}):
        await create_cve_topic_stubs(["CVE-2024-1111", "CVE-2024-2222"])

    assert mock_client.index.call_count == 2
    first_doc = mock_client.index.call_args_list[0].kwargs["body"]
    assert first_doc["article_ids"] == []
    assert first_doc["article_count"] == 0


@pytest.mark.asyncio
async def test_create_cve_topic_stubs_skips_existing():
    from app.ingestion.cve_topic_manager import create_cve_topic_stubs

    mock_client = _make_os_client(exists=True)
    with patch("app.ingestion.cve_topic_manager.get_os_client", return_value=mock_client), \
         patch("app.ingestion.cve_topic_manager.fetch_epss", new_callable=AsyncMock, return_value={}):
        await create_cve_topic_stubs(["CVE-2024-1111"])

    mock_client.index.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_cve_topics_omits_embedding_when_none():
    from app.ingestion.cve_topic_manager import upsert_cve_topics

    mock_client = _make_os_client()
    with patch("app.ingestion.cve_topic_manager.get_os_client", return_value=mock_client), \
         patch("app.ingestion.cve_topic_manager.fetch_epss", new_callable=AsyncMock, return_value={}):
        await upsert_cve_topics(["CVE-2024-1234"], "test-article", [], embedding=None)

    body = mock_client.update.call_args.kwargs["body"]
    assert "cve_embedding" not in body["upsert"]


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
