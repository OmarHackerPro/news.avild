import pytest
from unittest.mock import AsyncMock, MagicMock
from app.briefing.generator import generate_cluster_summary


@pytest.mark.asyncio
async def test_generate_summary_returns_text():
    cluster = {
        "id": "c1",
        "label": "Log4Shell Exploitation",
        "summary": "Attackers exploiting Log4j.",
        "why_it_matters": "Millions of systems affected.",
        "cve_ids": ["CVE-2021-44228"],
        "max_cvss": 10.0,
    }

    fake_response = MagicMock()
    fake_response.content = [MagicMock(type="text", text="Critical Log4j RCE. Millions affected.")]

    fake_client = MagicMock()
    fake_client.messages = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=fake_response)

    result = await generate_cluster_summary(cluster, client=fake_client)
    assert len(result) > 10


@pytest.mark.asyncio
async def test_generate_summary_falls_back_on_failure():
    cluster = {
        "id": "c1",
        "label": "Test Cluster",
        "summary": "Fallback summary text.",
        "why_it_matters": "Important.",
        "cve_ids": [],
        "max_cvss": None,
    }

    fake_client = MagicMock()
    fake_client.messages = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=Exception("API error"))

    result = await generate_cluster_summary(cluster, client=fake_client)
    assert result == "Fallback summary text."
