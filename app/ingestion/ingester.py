import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

import feedparser
import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.feed_source import FeedSource as FeedSourceModel
from app.db.models.news import NewsArticle
from app.db.models.raw_feed_snapshot import RawFeedSnapshot
from app.db.session import AsyncSessionLocal
from app.ingestion.normalizer import NORMALIZER_REGISTRY, NormalizedArticle
from app.ingestion.sources import FeedSource

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feed fetching
# ---------------------------------------------------------------------------

async def fetch_feed_content(url: str, client: httpx.AsyncClient) -> Optional[str]:
    """Fetch RSS feed via httpx and return the response body as a string.

    Returns None on any network or HTTP error so callers can skip the source.
    Feedparser receives a pre-fetched string instead of a URL so the HTTP
    layer stays fully async (feedparser's built-in fetch is synchronous urllib).
    """
    try:
        response = await client.get(
            url,
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; RSSBot/1.0)"},
        )
        response.raise_for_status()
        return response.text
    except httpx.TimeoutException:
        logger.error("Timeout fetching feed: %s", url)
    except httpx.HTTPStatusError as e:
        logger.error("HTTP %s fetching feed %s", e.response.status_code, url)
    except httpx.RequestError as e:
        logger.error("Request error fetching %s: %s", url, e)
    return None


# ---------------------------------------------------------------------------
# Upsert — INSERT ... ON CONFLICT (slug) DO NOTHING
# ---------------------------------------------------------------------------

async def upsert_article(session: AsyncSession, article: NormalizedArticle) -> bool:
    """Insert one article. Silently skips if a row with the same slug exists.

    Uses pg_insert (SQLAlchemy PostgreSQL dialect) instead of raw SQL text()
    so that ARRAY(String) columns and reserved-keyword column names (desc, type)
    are handled correctly without manual quoting.

    Returns True if a new row was inserted, False if it was a duplicate.
    """
    stmt = (
        pg_insert(NewsArticle)
        .values(**article)
        .on_conflict_do_nothing(index_elements=["slug", "published_at"])
    )
    result = await session.execute(stmt)
    return result.rowcount == 1


# ---------------------------------------------------------------------------
# Raw feed snapshot archival
# ---------------------------------------------------------------------------

async def store_raw_snapshot(
    session: AsyncSession,
    source_name: str,
    source_url: str,
    content: str,
    entry_count: int | None = None,
) -> int | None:
    """Store raw feed XML. Returns snapshot id, or None if content was a duplicate.

    Uses SHA-256 content_hash with ON CONFLICT DO NOTHING so identical fetches
    (common when feeds update infrequently) are silently deduplicated.
    """
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    stmt = (
        pg_insert(RawFeedSnapshot)
        .values(
            source_name=source_name,
            source_url=source_url,
            raw_content=content,
            content_hash=content_hash,
            entry_count=entry_count,
        )
        .on_conflict_do_nothing(index_elements=["content_hash"])
        .returning(RawFeedSnapshot.id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Source queries
# ---------------------------------------------------------------------------

async def get_active_sources(session: AsyncSession) -> list[FeedSourceModel]:
    """Return all feed sources with is_active = true, ordered by id."""
    result = await session.execute(
        select(FeedSourceModel)
        .where(FeedSourceModel.is_active.is_(True))
        .order_by(FeedSourceModel.id)
    )
    return list(result.scalars().all())


async def mark_source_success(session: AsyncSession, source_id: int) -> None:
    """Update last_fetched_at and reset consecutive_failures after a successful fetch."""
    await session.execute(
        update(FeedSourceModel)
        .where(FeedSourceModel.id == source_id)
        .values(
            last_fetched_at=datetime.now(timezone.utc),
            consecutive_failures=0,
        )
    )


async def mark_source_failure(session: AsyncSession, source_id: int) -> None:
    """Increment consecutive_failures after a failed fetch."""
    await session.execute(
        update(FeedSourceModel)
        .where(FeedSourceModel.id == source_id)
        .values(
            consecutive_failures=FeedSourceModel.consecutive_failures + 1,
        )
    )


# ---------------------------------------------------------------------------
# Per-source ingestion
# ---------------------------------------------------------------------------

async def ingest_source(source: FeedSource, client: httpx.AsyncClient) -> dict:
    """Fetch, parse, normalize, and store all entries for one FeedSource.

    Returns stats: {"fetched": int, "inserted": int, "skipped": int, "errors": int}

    Error isolation:
    - Network failure → log, return empty stats (source skipped entirely)
    - Malformed XML (bozo) → log warning, continue with whatever entries parsed
    - Bad individual entry → log, count as error, continue to next entry
    """
    stats = {"fetched": 0, "inserted": 0, "skipped": 0, "errors": 0}
    name = source["name"]

    content = await fetch_feed_content(source["url"], client)
    if content is None:
        logger.warning("[%s] Could not fetch feed — skipping.", name)
        return stats

    feed = feedparser.parse(content)

    if feed.bozo:
        logger.warning(
            "[%s] Malformed feed XML (%s: %s) — attempting partial parse.",
            name, type(feed.bozo_exception).__name__, feed.bozo_exception,
        )

    entries = feed.get("entries", [])

    # Archive raw XML before any DB article writes.
    # Committed in its own transaction so it persists even if article upserts fail.
    if AsyncSessionLocal is not None:
        try:
            async with AsyncSessionLocal() as snap_session:
                async with snap_session.begin():
                    snap_id = await store_raw_snapshot(
                        snap_session,
                        source_name=name,
                        source_url=source["url"],
                        content=content,
                        entry_count=len(entries),
                    )
                    if snap_id:
                        logger.info("[%s] Stored raw snapshot id=%d (%d entries)", name, snap_id, len(entries))
                    else:
                        logger.debug("[%s] Feed content unchanged — snapshot deduplicated.", name)
        except Exception:
            logger.exception("[%s] Failed to store raw snapshot (continuing ingestion).", name)

    if not entries:
        logger.info("[%s] Feed parsed but contained 0 entries.", name)
        return stats

    stats["fetched"] = len(entries)
    logger.info("[%s] Fetched %d entries.", name, len(entries))

    normalizer_fn = NORMALIZER_REGISTRY.get(source["normalizer"])
    if normalizer_fn is None:
        logger.error("[%s] Unknown normalizer '%s' — skipping.", name, source["normalizer"])
        return stats

    if AsyncSessionLocal is None:
        logger.error("[%s] Database not configured (DATABASE_URL missing).", name)
        return stats

    # One session per source: atomic per feed, isolated between feeds
    async with AsyncSessionLocal() as session:
        async with session.begin():
            for entry in entries:
                try:
                    article = normalizer_fn(entry, source)
                    if article is None:
                        logger.debug(
                            "[%s] Skipped entry (normalizer returned None): %s",
                            name, entry.get("title", "<no title>"),
                        )
                        stats["errors"] += 1
                        continue

                    inserted = await upsert_article(session, article)
                    if inserted:
                        stats["inserted"] += 1
                    else:
                        stats["skipped"] += 1

                except Exception:
                    logger.exception(
                        "[%s] Unexpected error on entry '%s'",
                        name, entry.get("title", "<no title>"),
                    )
                    stats["errors"] += 1

    return stats


# ---------------------------------------------------------------------------
# Top-level runner
# ---------------------------------------------------------------------------

async def ingest_all_feeds() -> None:
    """Run ingestion for every active source from the feed_sources DB table.

    A single httpx.AsyncClient is shared across all sources for connection reuse.
    Source-level exceptions are caught so one broken source never blocks others.
    Operational state (last_fetched_at, consecutive_failures) is updated after
    each source.
    """
    if AsyncSessionLocal is None:
        logger.error("Database not configured (DATABASE_URL missing).")
        return

    async with AsyncSessionLocal() as session:
        sources = await get_active_sources(session)

    if not sources:
        logger.warning("No active feed sources found in DB. Run scripts/seed_sources.py first.")
        return

    logger.info("Found %d active feed source(s).", len(sources))

    async with httpx.AsyncClient() as client:
        for src in sources:
            source_dict = src.to_source_dict()
            logger.info("=== Ingesting: %s ===", src.name)
            try:
                stats = await ingest_source(source_dict, client)
                logger.info(
                    "[%s] Done - fetched=%d inserted=%d skipped=%d errors=%d",
                    src.name,
                    stats["fetched"], stats["inserted"],
                    stats["skipped"], stats["errors"],
                )
                # Update operational state
                async with AsyncSessionLocal() as session:
                    async with session.begin():
                        await mark_source_success(session, src.id)
            except Exception:
                logger.exception("Fatal error ingesting '%s'", src.name)
                try:
                    async with AsyncSessionLocal() as session:
                        async with session.begin():
                            await mark_source_failure(session, src.id)
                except Exception:
                    logger.exception("Failed to record failure for '%s'", src.name)
