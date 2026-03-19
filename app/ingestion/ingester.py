import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

import feedparser
import httpx
from opensearchpy.exceptions import ConflictError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.feed_source import FeedSource as FeedSourceModel
from app.db.opensearch import INDEX_NEWS, INDEX_SNAPSHOTS, get_os_client, NEWS_MAPPING
from app.db.session import AsyncSessionLocal
from app.ingestion.clusterer import cluster_article
from app.ingestion.entity_extractor import extract_entities
from app.ingestion.entity_store import store_article_entities
from app.ingestion.normalizer import NORMALIZER_REGISTRY, NormalizedArticle
from app.ingestion.sources import FeedSource

logger = logging.getLogger(__name__)

_ALLOWED_FIELDS = frozenset(NEWS_MAPPING["mappings"]["properties"].keys())


# ---------------------------------------------------------------------------
# Feed fetching
# ---------------------------------------------------------------------------

FETCH_RETRIES = 3
FETCH_BACKOFF_BASE = 2  # seconds: 2, 4, 8


async def fetch_feed_content(url: str, client: httpx.AsyncClient) -> Optional[str]:
    """Fetch RSS feed via httpx with retry + exponential backoff.

    Returns None on persistent failure so callers can skip the source.
    Retries on timeouts, 5xx errors, and connection errors.
    Does NOT retry on 4xx (client errors are permanent).
    """
    for attempt in range(1, FETCH_RETRIES + 1):
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
            logger.warning("Timeout fetching %s (attempt %d/%d)", url, attempt, FETCH_RETRIES)
        except httpx.HTTPStatusError as e:
            if e.response.status_code < 500:
                logger.error("HTTP %s fetching %s — not retrying", e.response.status_code, url)
                return None
            logger.warning("HTTP %s fetching %s (attempt %d/%d)", e.response.status_code, url, attempt, FETCH_RETRIES)
        except httpx.RequestError as e:
            logger.warning("Request error fetching %s: %s (attempt %d/%d)", url, e, attempt, FETCH_RETRIES)

        if attempt < FETCH_RETRIES:
            delay = FETCH_BACKOFF_BASE ** attempt
            await asyncio.sleep(delay)

    logger.error("Failed to fetch %s after %d attempts", url, FETCH_RETRIES)
    return None


# ---------------------------------------------------------------------------
# Upsert — index with op_type="create" (DO NOTHING on duplicate slug)
# ---------------------------------------------------------------------------

def _prepare_article_doc(article: NormalizedArticle) -> tuple[str, dict]:
    """Coerce types and return (slug, doc) ready for OpenSearch."""
    doc = dict(article)
    if isinstance(doc.get("published_at"), datetime):
        doc["published_at"] = doc["published_at"].isoformat()
    if isinstance(doc.get("created_at"), datetime):
        doc["created_at"] = doc["created_at"].isoformat()
    if isinstance(doc.get("updated_at"), datetime):
        doc["updated_at"] = doc["updated_at"].isoformat()
    if doc.get("cvss_score") is not None:
        doc["cvss_score"] = float(doc["cvss_score"])
    doc.setdefault("tags", [])
    doc.setdefault("keywords", [])
    doc.setdefault("cve_ids", [])
    doc.setdefault("content_html", None)
    doc.setdefault("summary", None)
    doc.setdefault("content_source", None)
    # Strip unknown fields to prevent dynamic:strict indexing errors
    unexpected = set(doc.keys()) - _ALLOWED_FIELDS
    for key in unexpected:
        logger.warning("Dropping unknown field '%s' from article '%s'", key, doc.get("slug"))
        doc.pop(key)
    return doc["slug"], doc


async def upsert_article(article: NormalizedArticle) -> bool:
    """Index one article. Silently skips if a document with the same slug exists.

    Returns True if a new document was indexed, False if it was a duplicate.
    """
    slug, doc = _prepare_article_doc(article)
    try:
        await get_os_client().index(
            index=INDEX_NEWS,
            id=slug,
            body=doc,
            op_type="create",
            params={"refresh": "false"},
        )
        return True
    except ConflictError:
        return False


async def overwrite_article(article: NormalizedArticle) -> bool:
    """Index one article unconditionally (upsert). Used by --update reparse mode."""
    slug, doc = _prepare_article_doc(article)
    await get_os_client().index(
        index=INDEX_NEWS,
        id=slug,
        body=doc,
        params={"refresh": "false"},
    )
    return True


# ---------------------------------------------------------------------------
# Raw feed snapshot archival
# ---------------------------------------------------------------------------

async def store_raw_snapshot(
    source_name: str,
    source_url: str,
    content: str,
    entry_count: int | None = None,
) -> str | None:
    """Store raw feed XML. Returns content_hash if new, None if content was a duplicate.

    Uses SHA-256 content_hash with op_type="create" so identical fetches
    (common when feeds update infrequently) are silently deduplicated.
    """
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    now_iso = datetime.now(timezone.utc).isoformat()
    doc = {
        "content_hash": content_hash,
        "source_name": source_name,
        "source_url": source_url,
        "raw_content": content,
        "fetched_at": now_iso,
        "entry_count": entry_count,
        "created_at": now_iso,
    }
    try:
        await get_os_client().index(
            index=INDEX_SNAPSHOTS,
            id=content_hash,
            body=doc,
            op_type="create",
            params={"refresh": "false"},
        )
        return content_hash
    except ConflictError:
        return None


# ---------------------------------------------------------------------------
# Source queries (PostgreSQL — unchanged)
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

async def ingest_source(source: FeedSource, client: httpx.AsyncClient, *, update: bool = False) -> dict:
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

    # Archive raw content before article writes.
    # Each OpenSearch index call is individually durable, so a failure here
    # does not affect article indexing and vice versa.
    try:
        snap_hash = await store_raw_snapshot(
            source_name=name,
            source_url=source["url"],
            content=content,
            entry_count=len(entries),
        )
        if snap_hash:
            logger.info("[%s] Stored raw snapshot (hash=%.8s, %d entries)", name, snap_hash, len(entries))
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

            inserted = await (overwrite_article if update else upsert_article)(article)
            if inserted:
                stats["inserted"] += 1
                entities = []
                try:
                    entities = extract_entities(article)
                    if entities:
                        await store_article_entities(
                            article["slug"], entities,
                        )
                        # Write entity names back to article keywords
                        keyword_list = list(dict.fromkeys(
                            e["name"] for e in entities
                        ))
                        try:
                            await get_os_client().update(
                                index=INDEX_NEWS,
                                id=article["slug"],
                                body={"doc": {"keywords": keyword_list}},
                            )
                        except Exception:
                            logger.exception(
                                "[%s] Failed to update keywords for '%s'",
                                name, article.get("slug"),
                            )
                except Exception:
                    logger.exception(
                        "[%s] Entity extraction failed for '%s'",
                        name, article.get("slug"),
                    )

                # Clustering — always attempt, even without entities
                try:
                    entity_keys = [e["normalized_key"] for e in entities]
                    await cluster_article(article, article["slug"], entity_keys)
                except Exception:
                    logger.exception(
                        "[%s] Clustering failed for '%s'",
                        name, article.get("slug"),
                    )
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

CONCURRENCY = 20  # max feeds fetched in parallel per batch


async def _ingest_one(src, client: httpx.AsyncClient, *, update: bool = False) -> None:
    """Ingest a single source and update its operational state."""
    source_dict = src.to_source_dict()
    try:
        stats = await ingest_source(source_dict, client, update=update)
        logger.info(
            "[%s] Done - fetched=%d inserted=%d skipped=%d errors=%d",
            src.name,
            stats["fetched"], stats["inserted"],
            stats["skipped"], stats["errors"],
        )
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


async def ingest_all_feeds(*, update: bool = False) -> None:
    """Run ingestion for every active source from the feed_sources DB table.

    Processes feeds concurrently in batches of CONCURRENCY (default 20).
    A single httpx.AsyncClient is shared across all sources for connection reuse.
    Source-level exceptions are caught so one broken source never blocks others.

    When update=True, existing documents are overwritten (reparse mode).
    """
    if AsyncSessionLocal is None:
        logger.error("Database not configured (DATABASE_URL missing).")
        return

    async with AsyncSessionLocal() as session:
        sources = await get_active_sources(session)

    if not sources:
        logger.warning("No active feed sources found in DB. Run scripts/seed_sources.py first.")
        return

    logger.info("Found %d active feed source(s). Processing in batches of %d.", len(sources), CONCURRENCY)

    async with httpx.AsyncClient() as client:
        for i in range(0, len(sources), CONCURRENCY):
            batch = sources[i : i + CONCURRENCY]
            logger.info("=== Batch %d/%d (%d sources) ===", i // CONCURRENCY + 1, -(-len(sources) // CONCURRENCY), len(batch))
            await asyncio.gather(*[_ingest_one(src, client, update=update) for src in batch])
