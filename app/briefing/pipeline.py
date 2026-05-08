"""Orchestrate the daily brief pipeline with idempotency."""
import asyncio
import logging
from datetime import date, datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.briefing.formatter import format_brief
from app.briefing.generator import generate_cluster_summary
from app.briefing.selector import fetch_top_clusters
from app.briefing.sender import send_brief

logger = logging.getLogger(__name__)


async def _check_already_sent(session: AsyncSession, period_date: date) -> bool:
    result = await session.execute(
        text("SELECT status FROM brief_log WHERE period_date = :d"),
        {"d": period_date},
    )
    row = result.fetchone()
    return row is not None and row[0] == "sent"


async def _write_brief_log(
    session: AsyncSession,
    period_date: date,
    cluster_count: int,
    body: str,
    status: str,
    error_msg: str | None = None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO brief_log (period_date, cluster_count, body, status, error_msg) "
            "VALUES (:d, :cc, :body, :status, :err) "
            "ON CONFLICT (period_date) DO UPDATE SET "
            "cluster_count = EXCLUDED.cluster_count, body = EXCLUDED.body, "
            "status = EXCLUDED.status, error_msg = EXCLUDED.error_msg, "
            "sent_at = NOW()"
        ),
        {"d": period_date, "cc": cluster_count, "body": body, "status": status, "err": error_msg},
    )
    await session.commit()


async def run_brief_pipeline(
    os_client,
    db_session: AsyncSession,
    brief_date: date | None = None,
    dry_run: bool = False,
    force: bool = False,
    top_n: int = 7,
    hours: int = 24,
) -> dict:
    """Run the full pipeline. Returns a result dict with keys: skipped, dry_run, cluster_count, body."""
    today = brief_date or datetime.now(timezone.utc).date()

    if not force and not dry_run:
        already = await _check_already_sent(db_session, today)
        if already:
            logger.info("Brief already sent for %s; skipping (use --force to override)", today)
            return {"skipped": True, "dry_run": False, "cluster_count": 0, "body": ""}

    try:
        clusters = await fetch_top_clusters(os_client, top_n=top_n, hours=hours)
    except Exception as exc:
        err_msg = f"OpenSearch error: {exc}"
        logger.error("Brief pipeline: cluster fetch failed for %s: %s", today, exc)
        if not dry_run:
            await _write_brief_log(db_session, today, 0, "", "failed", err_msg)
        return {"skipped": False, "dry_run": dry_run, "cluster_count": 0, "body": ""}

    if not clusters:
        logger.warning("No clusters found for brief date %s", today)
        if not dry_run:
            await _write_brief_log(db_session, today, 0, "", "failed", "No clusters found")
        return {"skipped": False, "dry_run": dry_run, "cluster_count": 0, "body": ""}

    summaries = await asyncio.gather(*[generate_cluster_summary(c) for c in clusters])
    enriched = [
        {**c, "summary_text": s}
        for c, s in zip(clusters, summaries)
    ]

    body = format_brief(enriched, today)

    if dry_run:
        logger.info("[DRY RUN] Brief for %s (%d clusters):\n%s", today, len(clusters), body)
        return {"skipped": False, "dry_run": True, "cluster_count": len(clusters), "body": body}

    date_str = today.isoformat()
    ok = await send_brief(body, date_str=date_str)
    status = "sent" if ok else "failed"
    await _write_brief_log(db_session, today, len(clusters), body, status)

    logger.info("Brief pipeline complete: date=%s clusters=%d status=%s", today, len(clusters), status)
    return {"skipped": False, "dry_run": False, "cluster_count": len(clusters), "body": body}
