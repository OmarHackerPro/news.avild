#!/usr/bin/env python
"""One-time migration: PostgreSQL news_articles + raw_feed_snapshots → OpenSearch.

Run this BEFORE deploying the updated application code. The app continues
writing to PostgreSQL until the new code is deployed, so it is safe to run
this against the live database.

Usage:
    python scripts/migrate_to_opensearch.py
    python scripts/migrate_to_opensearch.py --only news_articles
    python scripts/migrate_to_opensearch.py --only raw_feed_snapshots
    python scripts/migrate_to_opensearch.py --batch-size 500
    python scripts/migrate_to_opensearch.py --dry-run

Running the script twice is safe — bulk indexing defaults to op_type="index"
(unconditional upsert), so documents will be overwritten with identical data.
"""
import asyncio
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import func, select
from opensearchpy.helpers import async_bulk

from app.db.models.news import NewsArticle
from app.db.models.raw_feed_snapshot import RawFeedSnapshot
from app.db.opensearch import INDEX_NEWS, INDEX_SNAPSHOTS, ensure_indexes, get_os_client
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def _count_pg(model) -> int:
    async with AsyncSessionLocal() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def migrate_news_articles(*, dry_run: bool, batch_size: int) -> int:
    """Stream all news_articles rows from PG and bulk-index into OpenSearch."""
    client = get_os_client()
    total_pg = await _count_pg(NewsArticle)
    logger.info("PostgreSQL news_articles row count: %d", total_pg)

    if dry_run:
        logger.info("[DRY RUN] Would migrate %d news articles.", total_pg)
        return total_pg

    total = 0
    batch: list[dict] = []

    async with AsyncSessionLocal() as session:
        # stream() + yield_per avoids loading all rows into memory at once
        async_result = await session.stream_scalars(
            select(NewsArticle).order_by(NewsArticle.id).execution_options(yield_per=batch_size)
        )
        async for partition in async_result.partitions(batch_size):
            for row in partition:
                doc = {
                    "_index": INDEX_NEWS,
                    "_id": row.slug,
                    "_source": {
                        "slug":         row.slug,
                        "guid":         row.guid,
                        "source_id":    row.source_id,
                        "source_name":  row.source_name,
                        "title":        row.title,
                        "author":       row.author,
                        "desc":         row.desc,
                        "content_html": row.content_html,
                        "image_url":    row.image_url,
                        "tags":         row.tags or [],
                        "keywords":     row.keywords or [],
                        "published_at": row.published_at.isoformat(),
                        "severity":     row.severity,
                        "type":         row.type,
                        "category":     row.category,
                        "source_url":   row.source_url,
                        "cvss_score":   float(row.cvss_score) if row.cvss_score is not None else None,
                        "cve_ids":      row.cve_ids or [],
                        "raw_metadata": row.raw_metadata,
                        "created_at":   row.created_at.isoformat(),
                        "updated_at":   row.updated_at.isoformat() if row.updated_at else None,
                    },
                }
                batch.append(doc)

            success, errors = await async_bulk(client, batch, raise_on_error=False)
            total += success
            if errors:
                logger.error("Bulk errors (first 5): %s", errors[:5])
            logger.info("Migrated %d / %d news articles...", total, total_pg)
            batch.clear()

    logger.info("Done migrating news articles: %d indexed.", total)
    return total


async def migrate_raw_snapshots(*, dry_run: bool, batch_size: int) -> int:
    """Stream all raw_feed_snapshots rows from PG and bulk-index into OpenSearch."""
    client = get_os_client()
    total_pg = await _count_pg(RawFeedSnapshot)
    logger.info("PostgreSQL raw_feed_snapshots row count: %d", total_pg)

    if dry_run:
        logger.info("[DRY RUN] Would migrate %d raw snapshots.", total_pg)
        return total_pg

    total = 0
    batch: list[dict] = []

    async with AsyncSessionLocal() as session:
        async_result = await session.stream_scalars(
            select(RawFeedSnapshot)
            .order_by(RawFeedSnapshot.id)
            .execution_options(yield_per=batch_size)
        )
        async for partition in async_result.partitions(batch_size):
            for row in partition:
                doc = {
                    "_index": INDEX_SNAPSHOTS,
                    "_id": row.content_hash,
                    "_source": {
                        "content_hash": row.content_hash,
                        "source_name":  row.source_name,
                        "source_url":   row.source_url,
                        "raw_content":  row.raw_content,
                        "http_status":  row.http_status,
                        "fetched_at":   row.fetched_at.isoformat(),
                        "entry_count":  row.entry_count,
                        "created_at":   row.created_at.isoformat(),
                    },
                }
                batch.append(doc)

            success, errors = await async_bulk(client, batch, raise_on_error=False)
            total += success
            if errors:
                logger.error("Bulk errors (first 5): %s", errors[:5])
            logger.info("Migrated %d / %d raw snapshots...", total, total_pg)
            batch.clear()

    logger.info("Done migrating raw snapshots: %d indexed.", total)
    return total


async def main(args: argparse.Namespace) -> None:
    if AsyncSessionLocal is None:
        logger.error("DATABASE_URL not configured.")
        sys.exit(1)

    if not args.dry_run:
        logger.info("Ensuring OpenSearch indexes exist...")
        await ensure_indexes()

    only = args.only

    if only in (None, "news_articles"):
        await migrate_news_articles(dry_run=args.dry_run, batch_size=args.batch_size)

    if only in (None, "raw_feed_snapshots"):
        await migrate_raw_snapshots(dry_run=args.dry_run, batch_size=args.batch_size)

    if not args.dry_run:
        await get_os_client().close()

    logger.info("Migration complete.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Migrate PostgreSQL news data to OpenSearch")
    parser.add_argument(
        "--only",
        choices=["news_articles", "raw_feed_snapshots"],
        help="Migrate only one index (default: both)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Rows per bulk request (default: 200)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview row counts without writing")
    asyncio.run(main(parser.parse_args()))
