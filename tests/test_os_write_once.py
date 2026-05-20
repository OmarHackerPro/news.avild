"""Tests for app.db.os_write_once."""
import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_upsert_immutable_passes_immutable_and_mutable_params():
    from app.db.os_write_once import upsert_immutable

    mock_client = AsyncMock()
    await upsert_immutable(
        client=mock_client,
        index="cve_topics",
        doc_id="CVE-2026-1234",
        immutable_fields={"cvss_score": 9.8, "cvss_severity": "CRITICAL"},
        mutable_fields={"updated_at": "2026-05-21T00:00:00Z"},
    )

    assert mock_client.update.called
    kwargs = mock_client.update.call_args.kwargs
    body = kwargs["body"]
    assert body["script"]["params"]["immutable"] == {"cvss_score": 9.8, "cvss_severity": "CRITICAL"}
    assert body["script"]["params"]["mutable"] == {"updated_at": "2026-05-21T00:00:00Z"}
    assert "if (!ctx._source.containsKey" in body["script"]["source"]
    assert body["upsert"]["cvss_score"] == 9.8
    assert body["upsert"]["updated_at"] == "2026-05-21T00:00:00Z"


@pytest.mark.asyncio
async def test_upsert_immutable_accepts_only_immutable_fields():
    from app.db.os_write_once import upsert_immutable

    mock_client = AsyncMock()
    await upsert_immutable(
        client=mock_client,
        index="cve_topics",
        doc_id="CVE-2026-5678",
        immutable_fields={"cvss_score": 7.5},
    )

    body = mock_client.update.call_args.kwargs["body"]
    assert body["script"]["params"]["immutable"] == {"cvss_score": 7.5}
    assert body["script"]["params"]["mutable"] == {}


@pytest.mark.asyncio
async def test_upsert_immutable_noop_when_both_empty():
    from app.db.os_write_once import upsert_immutable

    mock_client = AsyncMock()
    await upsert_immutable(
        client=mock_client,
        index="cve_topics",
        doc_id="CVE-2026-9999",
        immutable_fields={},
    )

    mock_client.update.assert_not_called()
