"""Tests for app.ingestion.ner_client."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.ner_client import extract_entities_local


@pytest.mark.asyncio
async def test_extract_returns_normalized_entities_on_cache_miss():
    fake_response = {
        "entities": [
            {"type": "malware", "name": "LockBit 3.0", "normalized_key": "lockbit-3-0", "score": 0.92, "char_offset": 42},
            {"type": "actor", "name": "Lazarus Group", "normalized_key": "lazarus-group", "score": 0.87, "char_offset": 110},
        ],
        "model_version": "securebert-v1",
    }

    mock_session = AsyncMock()
    miss = MagicMock()
    miss.fetchone.return_value = None
    mock_session.execute = AsyncMock(return_value=miss)

    mock_http = AsyncMock()
    http_resp = MagicMock()
    http_resp.raise_for_status = MagicMock()
    http_resp.json = MagicMock(return_value=fake_response)
    mock_http.post = AsyncMock(return_value=http_resp)

    with patch("app.ingestion.ner_client._get_http", return_value=mock_http):
        result = await extract_entities_local(
            slug="test-1",
            title="LockBit hits hospital",
            body="Lazarus Group affiliate deployed LockBit 3.0 in attack.",
            db_session=mock_session,
        )

    assert len(result) == 2
    assert result[0]["type"] == "malware"
    assert result[0]["normalized_key"] == "lockbit-3-0"
    # Cached: one SELECT + one INSERT
    assert mock_session.execute.call_count == 2


@pytest.mark.asyncio
async def test_extract_returns_cache_hit_without_http():
    cached_json = [
        {"type": "tool", "name": "Cobalt Strike", "normalized_key": "cobalt-strike", "score": 0.99, "char_offset": 5}
    ]
    mock_session = AsyncMock()
    hit = MagicMock()
    hit.fetchone.return_value = (cached_json,)
    mock_session.execute = AsyncMock(return_value=hit)

    mock_http = AsyncMock()
    mock_http.post = AsyncMock()

    with patch("app.ingestion.ner_client._get_http", return_value=mock_http):
        result = await extract_entities_local(
            slug="cache-hit",
            title="Cobalt Strike beacon",
            body="Discovered Cobalt Strike beacon.",
            db_session=mock_session,
        )

    mock_http.post.assert_not_called()
    assert result == cached_json


@pytest.mark.asyncio
async def test_extract_returns_empty_on_http_failure_and_does_not_cache():
    mock_session = AsyncMock()
    miss = MagicMock()
    miss.fetchone.return_value = None
    mock_session.execute = AsyncMock(return_value=miss)

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(side_effect=Exception("boom"))

    with patch("app.ingestion.ner_client._get_http", return_value=mock_http):
        result = await extract_entities_local(
            slug="fail-1",
            title="x",
            body="y",
            db_session=mock_session,
        )

    assert result == []
    # Only SELECT, no INSERT
    assert mock_session.execute.call_count == 1


@pytest.mark.asyncio
async def test_extract_skips_cache_when_db_session_none():
    fake_response = {"entities": [], "model_version": "securebert-v1"}
    mock_http = AsyncMock()
    http_resp = MagicMock()
    http_resp.raise_for_status = MagicMock()
    http_resp.json = MagicMock(return_value=fake_response)
    mock_http.post = AsyncMock(return_value=http_resp)

    with patch("app.ingestion.ner_client._get_http", return_value=mock_http):
        result = await extract_entities_local(
            slug="no-cache",
            title="t",
            body="b",
            db_session=None,
        )

    assert result == []
    mock_http.post.assert_called_once()
