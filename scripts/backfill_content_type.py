#!/usr/bin/env python
"""Backfill content_type field on existing articles that predate the content_type feature.

Uses the same _infer_content_type() logic as live ingestion, driven by
source normalizer_key (from Postgres feed_sources) and article title.

Usage:
    python scripts/backfill_content_type.py --dry-run   # show counts by type
    python scripts/backfill_content_type.py             # apply to all articles
"""
import asyncio
import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if Path(".env").exists():
    from dotenv import load_dotenv
    load_dotenv()

from sqlalchemy import select

from app.db.models.feed_source import FeedSource as FeedSourceModel
from app.db.session import AsyncSessionLocal
from app.db.opensearch import INDEX_NEWS, get_os_client
from app.ingestion.normalizer import _infer_content_type

logger = logging.getLogger(__name__)

PAGE_SIZE = 500


async def _get_normalizer_map() -> dict[str, str]:
    """Return {source_name: normalizer_key} from Postgres."""
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(FeedSourceModel))).scalars().all()
        return {r.name: r.normalizer_key for r in rows}


async def run(*, dry_run: bool) -> None:
    os_client = get_os_client()
    normalizer_map = await _get_normalizer_map()

    # Only process articles that don't have content_type set yet
    query = {
        "bool": {
            "must_not": {"exists": {"field": "content_type"}}
        }
    }

    # Count first
    total = (await os_client.count(index=INDEX_NEWS, body={"query": query}))["count"]
    logger.info("Articles missing content_type: %d", total)

    if total == 0:
        logger.info("Nothing to do.")
        return

    # Page through and collect updates
    updates: list[dict] = []
    type_counts: Counter = Counter()
    after_key = None

    processed = 0
    while True:
        body: dict = {
            "query": query,
            "_source": ["title", "source_name"],
            "size": PAGE_SIZE,
            "sort": [{"_id": "asc"}],
        }
        if after_key:
            body["search_after"] = [after_key]

        resp = await os_client.search(index=INDEX_NEWS, body=body)
        hits = resp["hits"]["hits"]
        if not hits:
            break

        for hit in hits:
            slug = hit["_id"]
            src = hit["_source"]
            source_name = src.get("source_name", "")
            normalizer_key = normalizer_map.get(source_name, "generic")
            ct = _infer_content_type(src, normalizer_key)
            updates.append({"slug": slug, "content_type": ct})
            type_counts[ct] += 1

        after_key = hits[-1]["sort"][0]
        processed += len(hits)
        if processed % 500 == 0:
            logger.info("  scanned %d / %d", processed, total)

        if len(hits) < PAGE_SIZE:
            break

    logger.info("Content type distribution:")
    for ct, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        logger.info("  %-20s %d", ct, count)

    if dry_run:
        logger.info("[DRY RUN] Would update %d articles — no writes.", len(updates))
        return

    # Bulk update via update_by_query per content_type bucket (most efficient)
    by_type: dict[str, list[str]] = {}
    for u in updates:
        by_type.setdefault(u["content_type"], []).append(u["slug"])

    total_updated = 0
    for ct, slugs in by_type.items():
        resp = await os_client.update_by_query(
            index=INDEX_NEWS,
            body={
                "query": {"ids": {"values": slugs}},
                "script": {
                    "source": f"ctx._source.content_type = '{ct}'",
                    "lang": "painless",
                },
            },
            params={"conflicts": "proceed", "refresh": "true"},
        )
        updated = resp.get("updated", 0)
        total_updated += updated
        logger.info("  Set content_type='%s' on %d articles", ct, updated)

    logger.info("Done. Updated %d articles total.", total_updated)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run))
