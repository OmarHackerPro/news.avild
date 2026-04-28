#!/usr/bin/env python
"""Backfill NER entities for existing articles by running LLM extraction.

Scrolls all articles from OpenSearch oldest-first, calls extract_entities_llm()
for each, and stores any results via store_article_entities(). The NER function
handles cache reads/writes internally — articles already in ner_cache are
returned instantly without hitting the LLM.

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


async def _scroll_articles(source: str | None, limit: int | None) -> list[dict]:
    client = get_os_client()
    filters = []
    if source:
        filters.append({"term": {"source_name": source}})

    query = {"bool": {"filter": filters}} if filters else {"match_all": {}}

    results = []
    page_size = 100
    from_offset = 0

    while True:
        resp = await client.search(
            index=INDEX_NEWS,
            body={
                "query": query,
                "sort": [{"published_at": {"order": "asc"}}],
                "size": page_size,
                "from": from_offset,
                "_source": ["slug", "title", "summary", "desc", "source_name"],
            },
        )
        hits = resp["hits"]["hits"]
        if not hits:
            break
        results.extend(hits)
        from_offset += len(hits)
        if len(hits) < page_size:
            break
        if limit is not None and len(results) >= limit:
            break

    if limit is not None:
        results = results[:limit]

    return results


async def main(args: argparse.Namespace) -> None:
    articles = await _scroll_articles(source=args.source, limit=args.limit)
    total = len(articles)
    logger.info("Found %d article(s) to process.", total)

    counts = {"processed": 0, "cached_hits": 0, "new_extractions": 0, "errors": 0}

    for i, hit in enumerate(articles, 1):
        slug = hit["_id"]
        src = hit["_source"]
        title = src.get("title") or ""
        summary = src.get("summary") or src.get("desc") or ""

        if args.dry_run:
            logger.info("[DRY RUN] %d/%d slug=%s", i, total, slug[:60])
            counts["processed"] += 1
            if i % 50 == 0:
                logger.info("Progress: %d/%d", i, total)
            continue

        try:
            async with AsyncSessionLocal() as db:
                from sqlalchemy import text as _text
                cached_check = await db.execute(
                    _text("SELECT 1 FROM ner_cache WHERE slug = :slug"),
                    {"slug": slug},
                )
                already_cached = cached_check.fetchone() is not None

                entities = await extract_entities_llm(slug, title, summary, db)

            if already_cached:
                counts["cached_hits"] += 1
            else:
                counts["new_extractions"] += 1
                # Only delay after a real LLM call
                if args.delay > 0:
                    await asyncio.sleep(args.delay)

            if entities:
                await store_article_entities(slug, entities)

            counts["processed"] += 1

        except Exception:
            logger.exception("Failed to process slug=%s", slug[:60])
            counts["errors"] += 1

        if i % 50 == 0:
            logger.info(
                "Progress: %d/%d — cached=%d new=%d errors=%d",
                i, total,
                counts["cached_hits"], counts["new_extractions"], counts["errors"],
            )

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
