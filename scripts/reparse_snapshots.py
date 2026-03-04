#!/usr/bin/env python
"""Re-parse stored RSS snapshots with current normalization logic.

Usage:
    python scripts/reparse_snapshots.py                              # all snapshots
    python scripts/reparse_snapshots.py --source "The Hacker News"   # one source
    python scripts/reparse_snapshots.py --snapshot-id 42             # one snapshot
    python scripts/reparse_snapshots.py --dry-run                    # preview only

Articles are upserted with ON CONFLICT DO NOTHING by default (safe — only
inserts missing articles). Pass --update to overwrite existing articles
with freshly-normalized data instead.
"""
import asyncio
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

import feedparser
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models.news import NewsArticle
from app.db.models.raw_feed_snapshot import RawFeedSnapshot
from app.db.session import AsyncSessionLocal
from app.ingestion.ingester import upsert_article
from app.ingestion.normalizer import NORMALIZER_REGISTRY
from app.ingestion.sources import FEED_SOURCES, FeedSource

logger = logging.getLogger(__name__)

# Build lookup: source_name → FeedSource dict
SOURCE_BY_NAME: dict[str, FeedSource] = {s["name"]: s for s in FEED_SOURCES}


async def _upsert_or_update(session, article: dict) -> bool:
    """INSERT ... ON CONFLICT (slug, published_at) DO UPDATE — overwrites all columns."""
    index_elements = ["slug", "published_at"]
    update_cols = {
        k: v for k, v in article.items()
        if k not in ("slug", "published_at", "id", "created_at")
    }
    stmt = (
        pg_insert(NewsArticle)
        .values(**article)
        .on_conflict_do_update(index_elements=index_elements, set_=update_cols)
    )
    result = await session.execute(stmt)
    return result.rowcount == 1


async def reparse_snapshot(snapshot: RawFeedSnapshot, *, dry_run: bool, update: bool) -> dict:
    """Re-parse one snapshot. Returns stats dict."""
    stats = {"entries": 0, "upserted": 0, "skipped": 0, "errors": 0}

    source = SOURCE_BY_NAME.get(snapshot.source_name)
    if source is None:
        logger.warning(
            "No source config for '%s' — skipping snapshot %d",
            snapshot.source_name, snapshot.id,
        )
        return stats

    normalizer_fn = NORMALIZER_REGISTRY.get(source["normalizer"])
    if normalizer_fn is None:
        logger.warning(
            "No normalizer '%s' for source '%s'",
            source["normalizer"], snapshot.source_name,
        )
        return stats

    feed = feedparser.parse(snapshot.raw_content)
    entries = feed.get("entries", [])
    stats["entries"] = len(entries)

    if dry_run:
        logger.info(
            "[DRY RUN] Snapshot %d (%s): %d entries would be re-processed",
            snapshot.id, snapshot.source_name, len(entries),
        )
        return stats

    async with AsyncSessionLocal() as session:
        async with session.begin():
            for entry in entries:
                try:
                    article = normalizer_fn(entry, source)
                    if article is None:
                        stats["errors"] += 1
                        continue

                    if update:
                        wrote = await _upsert_or_update(session, article)
                    else:
                        wrote = await upsert_article(session, article)

                    if wrote:
                        stats["upserted"] += 1
                    else:
                        stats["skipped"] += 1
                except Exception:
                    logger.exception("Error re-parsing entry in snapshot %d", snapshot.id)
                    stats["errors"] += 1

    return stats


async def main(args: argparse.Namespace) -> None:
    if AsyncSessionLocal is None:
        logger.error("DATABASE_URL not configured.")
        return

    query = select(RawFeedSnapshot).order_by(RawFeedSnapshot.fetched_at.asc())
    if args.source:
        query = query.where(RawFeedSnapshot.source_name == args.source)
    if args.snapshot_id:
        query = query.where(RawFeedSnapshot.id == args.snapshot_id)

    async with AsyncSessionLocal() as session:
        result = await session.execute(query)
        snapshots = result.scalars().all()

    logger.info("Found %d snapshot(s) to re-parse.", len(snapshots))

    totals = {"entries": 0, "upserted": 0, "skipped": 0, "errors": 0}
    for snap in snapshots:
        logger.info(
            "Re-parsing snapshot %d (%s, fetched %s)...",
            snap.id, snap.source_name, snap.fetched_at,
        )
        stats = await reparse_snapshot(snap, dry_run=args.dry_run, update=args.update)
        for k in totals:
            totals[k] += stats[k]
        logger.info(
            "  entries=%d upserted=%d skipped=%d errors=%d",
            stats["entries"], stats["upserted"],
            stats["skipped"], stats["errors"],
        )

    logger.info(
        "=== Totals: entries=%d upserted=%d skipped=%d errors=%d ===",
        totals["entries"], totals["upserted"],
        totals["skipped"], totals["errors"],
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Re-parse stored RSS snapshots")
    parser.add_argument("--source", type=str, help="Filter by source name")
    parser.add_argument("--snapshot-id", type=int, help="Re-parse a single snapshot")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument(
        "--update", action="store_true",
        help="Overwrite existing articles (ON CONFLICT DO UPDATE instead of DO NOTHING)",
    )
    asyncio.run(main(parser.parse_args()))
