#!/usr/bin/env python
"""Extract entities from existing articles in OpenSearch and store in PostgreSQL.

Usage:
    python scripts/extract_entities.py                          # all articles
    python scripts/extract_entities.py --source "CISA Advisories"  # one source
    python scripts/extract_entities.py --dry-run                # preview only
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
from app.ingestion.entity_extractor import extract_entities
from app.ingestion.entity_store import store_article_entities

logger = logging.getLogger(__name__)


async def _scroll_articles(source: str | None) -> list[dict]:
    """Load all articles from OpenSearch using scroll."""
    client = get_os_client()
    filters = []
    if source:
        filters.append({"term": {"source_name": source}})

    query = {"bool": {"filter": filters}} if filters else {"match_all": {}}

    results = []
    resp = await client.search(
        index=INDEX_NEWS,
        body={
            "query": query,
            "sort": [{"published_at": {"order": "asc"}}],
            "size": 500,
            "_source": [
                "slug", "title", "desc", "content_html",
                "cve_ids", "cvss_score", "tags", "keywords",
                "source_name",
            ],
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


async def main(args: argparse.Namespace) -> None:
    if AsyncSessionLocal is None:
        logger.error("DATABASE_URL not configured.")
        return

    articles = await _scroll_articles(source=args.source)
    logger.info("Found %d article(s) to process.", len(articles))

    totals = {"articles": 0, "entities_found": 0, "links_created": 0}

    for hit in articles:
        slug = hit["_id"]
        src = hit["_source"]
        totals["articles"] += 1

        # Build a dict compatible with extract_entities
        article_dict = {
            "slug": slug,
            "title": src.get("title", ""),
            "desc": src.get("desc"),
            "content_html": src.get("content_html"),
            "cve_ids": src.get("cve_ids") or [],
            "cvss_score": src.get("cvss_score"),
            "tags": src.get("tags") or [],
            "keywords": src.get("keywords") or [],
        }

        entities = extract_entities(article_dict)
        if not entities:
            logger.debug("[%s] No entities found.", slug[:40])
            continue

        totals["entities_found"] += len(entities)
        entity_names = [f"{e['type']}:{e['name']}" for e in entities]

        if args.dry_run:
            logger.info(
                "[DRY RUN] %s → %d entities: %s",
                slug[:50], len(entities), ", ".join(entity_names),
            )
            continue

        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    await store_article_entities(slug, entities, session)
            totals["links_created"] += len(entities)
            logger.info(
                "[%s] Linked %d entities: %s",
                slug[:40], len(entities), ", ".join(entity_names),
            )
        except Exception:
            logger.exception("Failed to store entities for %s", slug[:40])

    logger.info(
        "=== Done: articles=%d entities_found=%d links_created=%d ===",
        totals["articles"], totals["entities_found"], totals["links_created"],
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Extract entities from existing articles")
    parser.add_argument("--source", type=str, help="Filter by source name")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")
    asyncio.run(main(parser.parse_args()))
