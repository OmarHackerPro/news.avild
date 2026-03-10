#!/usr/bin/env python
"""Re-parse stored RSS snapshots with current normalization logic.

Usage:
    python scripts/reparse_snapshots.py                              # all snapshots
    python scripts/reparse_snapshots.py --source "The Hacker News"   # one source
    python scripts/reparse_snapshots.py --snapshot-id <content_hash> # one snapshot
    python scripts/reparse_snapshots.py --dry-run                    # preview only

Articles are inserted with op_type="create" (DO NOTHING) by default.
Pass --update to overwrite existing articles with freshly-normalized data.
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

from app.db.models.feed_source import FeedSource as FeedSourceModel
from app.db.opensearch import INDEX_SNAPSHOTS, get_os_client
from app.db.session import AsyncSessionLocal
from app.ingestion.ingester import overwrite_article, upsert_article
from app.ingestion.normalizer import NORMALIZER_REGISTRY

logger = logging.getLogger(__name__)


async def _load_source_lookup() -> dict[str, dict]:
    """Build source_name → source dict from the feed_sources DB table."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(FeedSourceModel))
        sources = result.scalars().all()
    return {s.name: s.to_source_dict() for s in sources}


async def _load_snapshots(
    source: str | None,
    snapshot_id: str | None,
) -> list[dict]:
    """Load snapshot documents from OpenSearch using scroll for large result sets."""
    client = get_os_client()
    filters = []
    if source:
        filters.append({"term": {"source_name": source}})
    if snapshot_id:
        filters.append({"ids": {"values": [snapshot_id]}})

    query = {"bool": {"filter": filters}} if filters else {"match_all": {}}

    results = []
    resp = await client.search(
        index=INDEX_SNAPSHOTS,
        body={
            "query": query,
            "sort": [{"fetched_at": {"order": "asc"}}],
            "size": 500,
            "_source": True,
        },
        params={"scroll": "2m"},
    )
    scroll_id = resp.get("_scroll_id")
    hits = resp["hits"]["hits"]

    while hits:
        results.extend(hits)
        if not scroll_id:
            break
        resp = await client.scroll(scroll_id=scroll_id, params={"scroll": "2m"})
        scroll_id = resp.get("_scroll_id")
        hits = resp["hits"]["hits"]

    if scroll_id:
        try:
            await client.clear_scroll(scroll_id=scroll_id)
        except Exception:
            pass

    return results


async def reparse_snapshot(
    snap_hit: dict,
    source_by_name: dict[str, dict],
    *,
    dry_run: bool,
    update: bool,
) -> dict:
    """Re-parse one snapshot hit. Returns stats dict."""
    stats = {"entries": 0, "upserted": 0, "skipped": 0, "errors": 0}
    src = snap_hit["_source"]
    snap_id = snap_hit["_id"]
    source_name = src["source_name"]

    source = source_by_name.get(source_name)
    if source is None:
        logger.warning(
            "No source config for '%s' — skipping snapshot %s",
            source_name, snap_id,
        )
        return stats

    normalizer_fn = NORMALIZER_REGISTRY.get(source["normalizer"])
    if normalizer_fn is None:
        logger.warning(
            "No normalizer '%s' for source '%s'",
            source["normalizer"], source_name,
        )
        return stats

    feed = feedparser.parse(src["raw_content"])
    entries = feed.get("entries", [])
    stats["entries"] = len(entries)

    if dry_run:
        logger.info(
            "[DRY RUN] Snapshot %s (%s): %d entries would be re-processed",
            snap_id[:8], source_name, len(entries),
        )
        return stats

    write_fn = overwrite_article if update else upsert_article
    for entry in entries:
        try:
            article = normalizer_fn(entry, source)
            if article is None:
                stats["errors"] += 1
                continue

            wrote = await write_fn(article)
            if wrote:
                stats["upserted"] += 1
            else:
                stats["skipped"] += 1
        except Exception:
            logger.exception("Error re-parsing entry in snapshot %s", snap_id[:8])
            stats["errors"] += 1

    return stats


async def main(args: argparse.Namespace) -> None:
    if AsyncSessionLocal is None:
        logger.error("DATABASE_URL not configured.")
        return

    source_by_name = await _load_source_lookup()
    if not source_by_name:
        logger.error("No feed sources found in DB. Run scripts/seed_sources.py first.")
        return

    snapshots = await _load_snapshots(
        source=args.source,
        snapshot_id=args.snapshot_id,
    )

    logger.info("Found %d snapshot(s) to re-parse.", len(snapshots))

    totals = {"entries": 0, "upserted": 0, "skipped": 0, "errors": 0}
    for snap in snapshots:
        src = snap["_source"]
        logger.info(
            "Re-parsing snapshot %.8s (%s, fetched %s)...",
            snap["_id"], src["source_name"], src.get("fetched_at", "?"),
        )
        stats = await reparse_snapshot(
            snap, source_by_name, dry_run=args.dry_run, update=args.update,
        )
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
    parser.add_argument("--snapshot-id", type=str, help="Re-parse a single snapshot by content_hash")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument(
        "--update", action="store_true",
        help="Overwrite existing articles (unconditional upsert instead of create-only)",
    )
    asyncio.run(main(parser.parse_args()))
