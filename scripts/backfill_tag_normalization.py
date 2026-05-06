#!/usr/bin/env python
"""Backfill raw_tags and normalized_topics for all existing news_articles.

Reads existing articles from OpenSearch (using the old `tags` field), runs
classify_tags() on them, and writes raw_tags + normalized_topics back.

Usage:
    python scripts/backfill_tag_normalization.py           # process all articles
    python scripts/backfill_tag_normalization.py --dry-run # log changes, no writes
"""
import asyncio
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select

from app.db.models.feed_source import FeedSource as FeedSourceModel
from app.db.opensearch import INDEX_NEWS, get_os_client
from app.db.session import AsyncSessionLocal
from app.ingestion.tag_classifier import classify_tags

logger = logging.getLogger(__name__)

BATCH_SIZE = 100
CONCURRENCY = 20


async def _load_junk_map() -> dict[str, list[str]]:
    """Return source_name → junk_tags from Postgres."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(FeedSourceModel))
        sources = result.scalars().all()
    return {s.name: s.junk_tags or [] for s in sources}


async def _process_article(
    doc: dict,
    junk_map: dict[str, list[str]],
    client,
    sem: asyncio.Semaphore,
    *,
    dry_run: bool,
) -> None:
    async with sem:
        src = doc["_source"]
        raw_tags = src.get("tags") or []
        source_name = src.get("source_name", "")
        source_junk = junk_map.get(source_name, [])

        result = await asyncio.to_thread(classify_tags, raw_tags, source_junk)

        if dry_run:
            logger.info(
                "[DRY RUN] %s | topics=%s entities=%d clean_tags=%d",
                doc["_id"],
                result["normalized_topics"],
                len(result["tag_entities"]),
                len(result["clean_tags"]),
            )
            return

        await client.update(
            index=INDEX_NEWS,
            id=doc["_id"],
            body={
                "doc": {
                    "raw_tags": result["clean_tags"],
                    "normalized_topics": result["normalized_topics"],
                }
            },
        )


async def backfill(*, dry_run: bool = False) -> None:
    client = get_os_client()
    junk_map = await _load_junk_map()
    sem = asyncio.Semaphore(CONCURRENCY)

    scroll_resp = await client.search(
        index=INDEX_NEWS,
        scroll="5m",
        body={
            "query": {"match_all": {}},
            "size": BATCH_SIZE,
            "_source": ["tags", "source_name"],
        },
    )

    scroll_id = scroll_resp["_scroll_id"]
    total = scroll_resp["hits"]["total"]["value"]
    processed = 0
    batch_num = 0

    logger.info("Total articles to backfill: %d", total)

    try:
        hits = scroll_resp["hits"]["hits"]
        while hits:
            batch_num += 1
            await asyncio.gather(*[
                _process_article(doc, junk_map, client, sem, dry_run=dry_run)
                for doc in hits
            ])
            processed += len(hits)
            logger.info(
                "Batch %d complete — %d/%d articles processed",
                batch_num, processed, total,
            )

            scroll_resp = await client.scroll(scroll_id=scroll_id, scroll="5m")
            scroll_id = scroll_resp["_scroll_id"]
            hits = scroll_resp["hits"]["hits"]
    finally:
        try:
            await client.clear_scroll(scroll_id=scroll_id)
        except Exception:
            pass  # scroll context will expire on its own; clear_scroll may lack permission

    logger.info("Backfill complete. %d articles processed. dry_run=%s", processed, dry_run)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Backfill tag normalization for existing articles")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    asyncio.run(backfill(dry_run=args.dry_run))
