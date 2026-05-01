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
