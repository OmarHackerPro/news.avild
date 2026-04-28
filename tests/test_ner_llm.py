"""Tests for app.ingestion.ner_llm."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.ingestion.ner_llm import extract_entities_llm


def _make_anthropic_response(entities: list[dict]):
    """Build a mock Anthropic tool_use response."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.input = {"entities": entities}
    resp = MagicMock()
    resp.content = [tool_block]
    return resp


@pytest.mark.asyncio
async def test_extract_entities_returns_cve_and_vuln_alias():
    mock_entities = [
        {"type": "cve", "name": "CVE-2021-44228", "normalized_key": "CVE-2021-44228"},
        {"type": "vuln_alias", "name": "Log4Shell", "normalized_key": "log4shell"},
        {"type": "actor", "name": "Lazarus Group", "normalized_key": "lazarus-group"},
    ]

    mock_async_client = MagicMock()
    mock_async_client.messages.create = AsyncMock(return_value=_make_anthropic_response(mock_entities))

    with patch("app.ingestion.ner_llm._get_client", return_value=mock_async_client):
        result = await extract_entities_llm(
            slug="test-article",
            title="Log4Shell exploited by Lazarus Group",
            summary="North Korean threat actor actively exploiting CVE-2021-44228.",
            db_session=None,  # skip cache
        )

    assert any(e["type"] == "vuln_alias" and e["normalized_key"] == "log4shell" for e in result)
    assert any(e["type"] == "cve" and e["normalized_key"] == "CVE-2021-44228" for e in result)
    assert any(e["type"] == "actor" and e["normalized_key"] == "lazarus-group" for e in result)


@pytest.mark.asyncio
async def test_extract_entities_returns_empty_on_llm_failure():
    mock_async_client = MagicMock()
    mock_async_client.messages.create = AsyncMock(side_effect=Exception("API error"))

    with patch("app.ingestion.ner_llm._get_client", return_value=mock_async_client):
        result = await extract_entities_llm(
            slug="fail-article",
            title="Some article",
            summary="Some content.",
            db_session=None,
        )

    assert result == []


@pytest.mark.asyncio
async def test_extract_entities_uses_cache_on_hit():
    cached_entities = [
        {"type": "malware", "name": "LockBit", "normalized_key": "lockbit"}
    ]

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (cached_entities,)
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_async_client = MagicMock()
    mock_async_client.messages.create = AsyncMock()

    with patch("app.ingestion.ner_llm._get_client", return_value=mock_async_client):
        result = await extract_entities_llm(
            slug="cached-article",
            title="LockBit ransomware",
            summary="LockBit 3.0 targets healthcare.",
            db_session=mock_session,
        )

    mock_async_client.messages.create.assert_not_called()
    assert result == cached_entities


@pytest.mark.asyncio
async def test_extract_entities_caches_result():
    mock_entities = [
        {"type": "campaign", "name": "MOVEit campaign", "normalized_key": "moveit-campaign"}
    ]

    mock_session = AsyncMock()
    # Cache miss
    miss_result = MagicMock()
    miss_result.fetchone.return_value = None
    mock_session.execute = AsyncMock(return_value=miss_result)

    mock_async_client = MagicMock()
    mock_async_client.messages.create = AsyncMock(return_value=_make_anthropic_response(mock_entities))

    with patch("app.ingestion.ner_llm._get_client", return_value=mock_async_client):
        result = await extract_entities_llm(
            slug="new-article",
            title="MOVEit Transfer breach",
            summary="Mass exploitation of MOVEit Transfer.",
            db_session=mock_session,
        )

    assert mock_session.execute.call_count == 2  # one SELECT, one INSERT
    assert result == mock_entities


@pytest.mark.asyncio
async def test_extract_entities_does_not_cache_on_failure():
    """LLM failure must not write empty entities to cache."""
    mock_session = AsyncMock()
    # Simulate cache miss
    miss_result = MagicMock()
    miss_result.fetchone.return_value = None
    mock_session.execute = AsyncMock(return_value=miss_result)

    mock_async_client = MagicMock()
    mock_async_client.messages.create = AsyncMock(side_effect=Exception("API timeout"))

    with patch("app.ingestion.ner_llm._get_client", return_value=mock_async_client):
        result = await extract_entities_llm(
            slug="fail-article",
            title="Some article",
            summary="Some content.",
            db_session=mock_session,
        )

    # Only the SELECT should have been called, not INSERT
    assert mock_session.execute.call_count == 1
    assert result == []
