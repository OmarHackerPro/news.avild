"""Tests for app.ingestion.embedding_input."""
from unittest.mock import AsyncMock, patch

import pytest


def test_short_article_produces_one_chunk():
    from app.ingestion.embedding_input import build_chunk_inputs

    article = {"title": "Ivanti VPN flaw", "content_html": "<p>Short body.</p>"}
    chunks = build_chunk_inputs(article, ["ivanti", "cve-2025-1"])
    assert len(chunks) == 1
    assert chunks[0].startswith("Ivanti VPN flaw. Short body.")
    assert "Entities: ivanti, cve-2025-1" in chunks[0]


def test_long_article_splits_into_multiple_chunks():
    from app.ingestion.embedding_input import build_chunk_inputs, _CHUNK_CHARS

    body = "<p>" + ("word " * 2000) + "</p>"  # well over _CHUNK_CHARS
    article = {"title": "Big report", "content_html": body}
    chunks = build_chunk_inputs(article, ["apt28"])
    assert len(chunks) >= 2
    # title + entity line repeat on every chunk
    for c in chunks:
        assert c.startswith("Big report. ")
        assert "Entities: apt28" in c


def test_falls_back_to_summary_when_no_body():
    from app.ingestion.embedding_input import build_chunk_inputs

    article = {"title": "T", "content_html": "", "summary": "Summary text here."}
    chunks = build_chunk_inputs(article, [])
    assert len(chunks) == 1
    assert "Summary text here." in chunks[0]
    assert "Entities:" not in chunks[0]  # empty entity list -> no line


@pytest.mark.asyncio
async def test_embed_article_averages_chunk_vectors():
    from app.ingestion import embedding_input

    article = {"title": "T", "content_html": "<p>" + ("x" * 4000) + "</p>"}
    fake_vecs = [[2.0, 0.0], [4.0, 0.0]]  # 2 chunks
    with patch.object(embedding_input, "embed_batch", new=AsyncMock(return_value=fake_vecs)):
        result = await embedding_input.embed_article(article, ["e1"])
    assert result == [3.0, 0.0]  # element-wise mean


@pytest.mark.asyncio
async def test_embed_article_returns_none_when_all_chunks_fail():
    from app.ingestion import embedding_input

    article = {"title": "T", "content_html": "<p>body</p>"}
    with patch.object(embedding_input, "embed_batch", new=AsyncMock(return_value=[None])):
        result = await embedding_input.embed_article(article, [])
    assert result is None
