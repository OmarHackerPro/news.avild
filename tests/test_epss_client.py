"""Tests for app.ingestion.epss_client."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _mock_response(data: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"status": "OK", "data": data}
    return resp


@pytest.mark.asyncio
async def test_fetch_epss_returns_parsed_scores():
    from app.ingestion.epss_client import fetch_epss

    mock_resp = _mock_response([
        {"cve": "CVE-2024-1234", "epss": "0.12345", "percentile": "0.87654", "date": "2026-05-01"},
        {"cve": "CVE-2024-5678", "epss": "0.00123", "percentile": "0.54321", "date": "2026-05-01"},
    ])

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await fetch_epss(["CVE-2024-1234", "CVE-2024-5678"])

    assert result["CVE-2024-1234"]["epss_score"] == pytest.approx(0.12345)
    assert result["CVE-2024-1234"]["epss_percentile"] == pytest.approx(0.87654)
    assert result["CVE-2024-1234"]["epss_updated_at"] == "2026-05-01"
    assert "CVE-2024-5678" in result


@pytest.mark.asyncio
async def test_fetch_epss_empty_input_returns_empty_dict():
    from app.ingestion.epss_client import fetch_epss
    result = await fetch_epss([])
    assert result == {}


@pytest.mark.asyncio
async def test_fetch_epss_handles_api_error_gracefully():
    from app.ingestion.epss_client import fetch_epss

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("network error"))

        result = await fetch_epss(["CVE-2024-1234"])

    assert result == {}
