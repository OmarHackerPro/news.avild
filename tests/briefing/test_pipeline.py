import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_pipeline_idempotency_blocks_resend():
    """If brief_log already has a 'sent' row for today, pipeline returns early."""
    from app.briefing.pipeline import run_brief_pipeline

    with patch("app.briefing.pipeline._check_already_sent", new_callable=AsyncMock, return_value=True):
        result = await run_brief_pipeline(
            os_client=MagicMock(),
            db_session=MagicMock(),
            brief_date=date(2026, 5, 8),
            dry_run=False,
            force=False,
            top_n=7,
        )
    assert result["skipped"] is True


@pytest.mark.asyncio
async def test_pipeline_force_overrides_idempotency():
    """--force flag bypasses the idempotency check."""
    from app.briefing.pipeline import run_brief_pipeline

    clusters = [
        {
            "id": "c1", "label": "Test", "summary": "Summary.",
            "why_it_matters": "", "cve_ids": [], "max_cvss": None,
            "cisa_kev": False, "article_count": 1, "entity_keys": [],
            "score": 50.0,
        }
    ]

    with (
        patch("app.briefing.pipeline._check_already_sent", new_callable=AsyncMock, return_value=True),
        patch("app.briefing.pipeline.fetch_top_clusters", new_callable=AsyncMock, return_value=clusters),
        patch("app.briefing.pipeline.generate_cluster_summary", new_callable=AsyncMock, return_value="Summary."),
        patch("app.briefing.pipeline.send_brief", new_callable=AsyncMock, return_value=True),
        patch("app.briefing.pipeline._write_brief_log", new_callable=AsyncMock),
    ):
        result = await run_brief_pipeline(
            os_client=MagicMock(),
            db_session=MagicMock(),
            brief_date=date(2026, 5, 8),
            dry_run=False,
            force=True,
            top_n=7,
        )
    assert result["skipped"] is False
    assert result["cluster_count"] == 1


@pytest.mark.asyncio
async def test_pipeline_dry_run_skips_send():
    """dry_run=True formats the brief but does not send or write to DB."""
    from app.briefing.pipeline import run_brief_pipeline

    clusters = [
        {
            "id": "c1", "label": "Test", "summary": "Summary.",
            "why_it_matters": "", "cve_ids": [], "max_cvss": None,
            "cisa_kev": False, "article_count": 1, "entity_keys": [],
            "score": 50.0,
        }
    ]
    send_calls = []

    async def fake_send(text, **kwargs):
        send_calls.append(text)
        return True

    with (
        patch("app.briefing.pipeline._check_already_sent", new_callable=AsyncMock, return_value=False),
        patch("app.briefing.pipeline.fetch_top_clusters", new_callable=AsyncMock, return_value=clusters),
        patch("app.briefing.pipeline.generate_cluster_summary", new_callable=AsyncMock, return_value="Summary."),
        patch("app.briefing.pipeline.send_brief", side_effect=fake_send),
        patch("app.briefing.pipeline._write_brief_log", new_callable=AsyncMock),
    ):
        result = await run_brief_pipeline(
            os_client=MagicMock(),
            db_session=MagicMock(),
            brief_date=date(2026, 5, 8),
            dry_run=True,
            force=False,
            top_n=7,
        )
    assert len(send_calls) == 0
    assert result["dry_run"] is True
