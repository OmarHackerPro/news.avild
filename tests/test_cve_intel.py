"""Tests for app.ingestion.cve_intel."""
import pytest
from unittest.mock import AsyncMock, patch


# ---------------------------------------------------------------------------
# severity_from_cvss
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("score, expected", [
    (10.0, "critical"),
    (9.0, "critical"),
    (8.9, "high"),
    (7.0, "high"),
    (6.9, "medium"),
    (4.0, "medium"),
    (3.9, "low"),
    (0.1, "low"),
])
def test_severity_from_cvss_thresholds(score, expected):
    from app.ingestion.cve_intel import severity_from_cvss
    assert severity_from_cvss(score) == expected


@pytest.mark.parametrize("score", [None, 0.0, -1.0])
def test_severity_from_cvss_returns_none_for_zero_or_none(score):
    from app.ingestion.cve_intel import severity_from_cvss
    assert severity_from_cvss(score) is None


# ---------------------------------------------------------------------------
# lookup_cve_intel
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lookup_cve_intel_returns_empty_when_no_cves():
    from app.ingestion.cve_intel import lookup_cve_intel
    result = await lookup_cve_intel([])
    assert result == {}


@pytest.mark.asyncio
async def test_lookup_cve_intel_keys_results_by_upper_cve_id():
    from app.ingestion.cve_intel import lookup_cve_intel

    mock_client = AsyncMock()
    mock_client.search = AsyncMock(return_value={
        "hits": {"hits": [
            {"_id": "CVE-2026-1234", "_source": {"cvss_score": 9.8, "cvss_severity": "CRITICAL", "cisa_kev": True}},
            {"_id": "CVE-2026-5678", "_source": {"cvss_score": 7.2, "cvss_severity": "HIGH", "cisa_kev": False}},
        ]}
    })
    with patch("app.ingestion.cve_intel.get_os_client", return_value=mock_client):
        result = await lookup_cve_intel(["cve-2026-1234", "CVE-2026-5678"])

    assert result["CVE-2026-1234"]["cvss_score"] == 9.8
    assert result["CVE-2026-1234"]["cisa_kev"] is True
    assert result["CVE-2026-5678"]["cvss_severity"] == "HIGH"


@pytest.mark.asyncio
async def test_lookup_cve_intel_omits_missing_cves():
    from app.ingestion.cve_intel import lookup_cve_intel

    mock_client = AsyncMock()
    mock_client.search = AsyncMock(return_value={"hits": {"hits": []}})
    with patch("app.ingestion.cve_intel.get_os_client", return_value=mock_client):
        result = await lookup_cve_intel(["CVE-9999-9999"])
    assert result == {}
