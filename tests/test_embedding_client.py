"""Tests for app.ingestion.embedding_client."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx


@pytest.mark.asyncio
async def test_embed_text_returns_list_of_floats():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"embedding": [0.1] * 1024}

    with patch("app.ingestion.embedding_client.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        from app.ingestion.embedding_client import embed_text
        result = await embed_text("Critical vulnerability in Apache Log4j")

    assert isinstance(result, list)
    assert len(result) == 1024
    assert all(isinstance(v, float) for v in result)


@pytest.mark.asyncio
async def test_embed_text_returns_none_on_timeout():
    with patch("app.ingestion.embedding_client.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_client_cls.return_value = mock_client

        from app.ingestion.embedding_client import embed_text
        result = await embed_text("some article text")

    assert result is None


@pytest.mark.asyncio
async def test_embed_batch_returns_list_of_embeddings():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"embeddings": [[0.1] * 1024, [0.2] * 1024]}

    with patch("app.ingestion.embedding_client.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        from app.ingestion.embedding_client import embed_batch
        result = await embed_batch(["article one", "article two"])

    assert len(result) == 2
    assert len(result[0]) == 1024


@pytest.mark.asyncio
async def test_embed_batch_returns_nones_on_error():
    with patch("app.ingestion.embedding_client.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
        mock_client_cls.return_value = mock_client

        from app.ingestion.embedding_client import embed_batch
        result = await embed_batch(["a", "b", "c"])

    assert result == [None, None, None]


@pytest.mark.asyncio
async def test_embed_batch_returns_empty_list_for_empty_input():
    from app.ingestion.embedding_client import embed_batch
    result = await embed_batch([])
    assert result == []
