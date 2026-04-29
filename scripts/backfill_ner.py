#!/usr/bin/env python
"""Backfill NER entities for existing articles by running LLM extraction.

Processes articles page-by-page (no upfront bulk load) so progress is never
lost to an OpenSearch timeout. Each OpenSearch request retries up to 5 times
with a 30s delay before giving up.

Usage:
    python scripts/backfill_ner.py                          # all articles
    python scripts/backfill_ner.py --dry-run --limit 10    # preview only
    python scripts/backfill_ner.py --limit 100             # first 100
    python scripts/backfill_ner.py --source "Krebs on Security"
    python scripts/backfill_ner.py --delay 1.0             # slower rate
"""
import asyncio
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.db.opensearch import INDEX_NEWS, get_os_client
from app.db.session import AsyncSessionLocal
from app.ingestion.ner_llm import extract_entities_llm
from app.ingestion.entity_store import store_article_entities

logger = logging.getLogger(__name__)

_RETRY_ATTEMPTS = 5
_RETRY_DELAY = 30  # seconds between retries


async def _os_search(client, body: dict) -> dict:
    """Run an OpenSearch search, retrying up to _RETRY_ATTEMPTS times."""
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return await client.search(index=INDEX_NEWS, body=body)
        except Exception as exc:
            if attempt == _RETRY_ATTEMPTS - 1:
                raise
            logger.warning(
                "OpenSearch request attempt %d/%d failed: %s — retrying in %ds",
                attempt + 1, _RETRY_ATTEMPTS, exc, _RETRY_DELAY,
            )
            await asyncio.sleep(_RETRY_DELAY)
    raise RuntimeError("unreachable")


async def main(args: argparse.Namespace) -> None:
    client = get_os_client()
    filters = [{"term": {"source_name": args.source}}] if args.source else []
    query = {"bool": {"filter": filters}} if filters else {"match_all": {}}

    # Get total count for progress reporting
    count_resp = await _os_search(client, {"query": query, "size": 0})
    grand_total = count_resp["hits"]["total"]["value"]
    if args.limit is not None:
        grand_total = min(grand_total, args.limit)
    logger.info("Found %d article(s) to process.", grand_total)

    page_size = 100
    from_offset = 0
    processed_total = 0
    counts = {"processed": 0, "cached_hits": 0, "new_extractions": 0, "errors": 0}

    while True:
        if args.limit is not None and processed_total >= args.limit:
            break

        fetch_size = page_size
        if args.limit is not None:
            fetch_size = min(page_size, args.limit - processed_total)

        resp = await _os_search(client, {
            "query": query,
            "sort": [{"published_at": {"order": "asc"}}],
            "size": fetch_size,
            "from": from_offset,
            "_source": ["slug", "title", "summary", "desc", "source_name"],
        })
        hits = resp["hits"]["hits"]
        if not hits:
            break

        for hit in hits:
            slug = hit["_id"]
            src = hit["_source"]
            title = src.get("title") or ""
            summary = src.get("summary") or src.get("desc") or ""
            processed_total += 1

            if args.dry_run:
                logger.info(
                    "[DRY RUN] %d/%d slug=%s", processed_total, grand_total, slug[:60]
                )
                counts["processed"] += 1
                if processed_total % 50 == 0:
                    logger.info("Progress: %d/%d", processed_total, grand_total)
                continue

            try:
                async with AsyncSessionLocal() as db:
                    from sqlalchemy import text as _text
                    cached_row = await db.execute(
                        _text("SELECT 1 FROM ner_cache WHERE slug = :slug"),
                        {"slug": slug},
                    )
                    already_cached = cached_row.fetchone() is not None
                    entities = await extract_entities_llm(slug, title, summary, db)

                if already_cached:
                    counts["cached_hits"] += 1
                else:
                    counts["new_extractions"] += 1
                    if args.delay > 0:
                        await asyncio.sleep(args.delay)

                if entities:
                    await store_article_entities(slug, entities)

                counts["processed"] += 1

            except Exception:
                logger.exception("Failed to process slug=%s", slug[:60])
                counts["errors"] += 1

            if processed_total % 50 == 0:
                logger.info(
                    "Progress: %d/%d — cached=%d new=%d errors=%d",
                    processed_total, grand_total,
                    counts["cached_hits"], counts["new_extractions"], counts["errors"],
                )

        from_offset += len(hits)
        if len(hits) < fetch_size:
            break

    logger.info(
        "=== Done: processed=%d cached_hits=%d new_extractions=%d errors=%d ===",
        counts["processed"], counts["cached_hits"],
        counts["new_extractions"], counts["errors"],
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Backfill NER entities for existing articles")
    parser.add_argument("--source", type=str, help="Filter by source_name")
    parser.add_argument("--dry-run", action="store_true", help="Preview without calling LLM or storing")
    parser.add_argument("--limit", type=int, default=None, help="Only process first N articles")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds to wait between LLM calls (default 0.5)")
    asyncio.run(main(parser.parse_args()))
