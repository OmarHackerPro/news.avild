import logging
from typing import Optional

import feedparser
import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.news import NewsArticle
from app.db.session import AsyncSessionLocal
from app.ingestion.normalizer import NORMALIZER_REGISTRY, NormalizedArticle
from app.ingestion.sources import FEED_SOURCES, FeedSource

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
    """Run ingestion for every source in FEED_SOURCES.

    A single httpx.AsyncClient is shared across all sources for connection reuse.
    Source-level exceptions are caught so one broken source never blocks others.
    """
    async with httpx.AsyncClient() as client:
        for source in FEED_SOURCES:
            logger.info("=== Ingesting: %s ===", source["name"])
            try:
                stats = await ingest_source(source, client)
                logger.info(
                    "[%s] Done - fetched=%d inserted=%d skipped=%d errors=%d",
                    source["name"],
                    stats["fetched"], stats["inserted"],
                    stats["skipped"], stats["errors"],
                )
            except Exception:
                logger.exception("Fatal error ingesting '%s'", source["name"])
