import pytest
from unittest.mock import AsyncMock, MagicMock
from app.briefing.selector import fetch_top_clusters


@pytest.mark.asyncio
async def test_fetch_top_clusters_returns_list():
    fake_hits = [
        {
            "_id": "cluster-1",
            "_source": {
                "label": "Apache RCE",
                "summary": "Remote code execution in Apache.",
                "why_it_matters": "Widely deployed.",
                "score": 85.0,
                "max_cvss": 9.8,
                "cisa_kev": True,
                "cve_ids": ["CVE-2026-1234"],
                "article_count": 5,
                "entity_keys": ["apache"],
            },
        }
    ]
    mock_client = MagicMock()
    mock_client.search = AsyncMock(return_value={
        "hits": {"hits": fake_hits, "total": {"value": 1}}
    })

    result = await fetch_top_clusters(mock_client, top_n=7, hours=24)
    assert len(result) == 1
    assert result[0]["id"] == "cluster-1"
    assert result[0]["label"] == "Apache RCE"
    assert result[0]["score"] == 85.0
    assert result[0]["cisa_kev"] is True


@pytest.mark.asyncio
async def test_fetch_top_clusters_empty_index():
    mock_client = MagicMock()
    mock_client.search = AsyncMock(return_value={
        "hits": {"hits": [], "total": {"value": 0}}
    })

    result = await fetch_top_clusters(mock_client, top_n=7, hours=24)
    assert result == []
