#!/usr/bin/env python
"""Backfill clusters for existing articles that aren't in any cluster yet.

Scrolls articles oldest-first so clusters form chronologically, then runs
each through the same cluster_article() decision tree used during ingestion.

Usage:
    python scripts/cluster_articles.py                          # all articles
    python scripts/cluster_articles.py --source "Krebs on Security"
    python scripts/cluster_articles.py --dry-run                # preview only
    python scripts/cluster_articles.py --batch-size 50
"""
import asyncio
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.db.opensearch import INDEX_NEWS, INDEX_ENTITIES, INDEX_CLUSTERS, get_os_client
from app.ingestion.clusterer import cluster_article

logger = logging.getLogger(__name__)


async def _get_clustered_slugs() -> set[str]:
    """Collect all article slugs that are already in a cluster."""
    client = get_os_client()
    slugs: set[str] = set()

    resp = await client.search(
        index=INDEX_CLUSTERS,
        body={
            "query": {"match_all": {}},
            "size": 1000,
            "_source": ["article_ids"],
        },
        params={"scroll": "2m"},
    )
    scroll_id = resp.get("_scroll_id")
    hits = resp["hits"]["hits"]

    while hits:
        for hit in hits:
            for slug in hit["_source"].get("article_ids") or []:
                slugs.add(slug)
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

    return slugs


async def _scroll_articles(source: str | None) -> list[dict]:
    """Load all articles sorted by published_at ascending."""
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
                "slug", "title", "desc", "summary", "cve_ids",
                "category", "tags", "published_at",
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


async def _get_entity_keys_for_slug(slug: str) -> list[str]:
    """Look up entity normalized_keys linked to an article slug."""
    client = get_os_client()
    resp = await client.search(
        index=INDEX_ENTITIES,
        body={
            "query": {"term": {"article_ids": slug}},
            "size": 200,
            "_source": ["normalized_key"],
        },
    )
    return [hit["_source"]["normalized_key"] for hit in resp["hits"]["hits"]]


async def main(args: argparse.Namespace) -> None:
    # Collect already-clustered slugs to skip them
    clustered = await _get_clustered_slugs()
    logger.info("Found %d article slugs already in clusters.", len(clustered))

    articles = await _scroll_articles(source=args.source)
    logger.info("Found %d total article(s).", len(articles))

    # Filter out already-clustered
    unclustered = [a for a in articles if a["_id"] not in clustered]
    logger.info("%d article(s) need clustering.", len(unclustered))

    totals = {"processed": 0, "created": 0, "merged": 0, "errors": 0}

    for i, hit in enumerate(unclustered, 1):
        slug = hit["_id"]
        src = hit["_source"]

        article_dict = {
            "slug": slug,
            "title": src.get("title", ""),
            "desc": src.get("desc"),
            "summary": src.get("summary"),
            "cve_ids": src.get("cve_ids") or [],
            "category": src.get("category"),
            "tags": src.get("tags") or [],
            "published_at": src.get("published_at"),
        }

        entity_keys = await _get_entity_keys_for_slug(slug)

        if args.dry_run:
            cve_label = f"CVEs={article_dict['cve_ids']}" if article_dict["cve_ids"] else "no CVEs"
            logger.info(
                "[DRY RUN] %d/%d %s — %d entities, %s",
                i, len(unclustered), slug[:50], len(entity_keys), cve_label,
            )
            totals["processed"] += 1
            continue

        try:
            await cluster_article(article_dict, slug, entity_keys)
            totals["processed"] += 1
        except Exception:
            logger.exception("Failed to cluster %s", slug[:50])
            totals["errors"] += 1

        if i % args.batch_size == 0:
            logger.info("Progress: %d/%d processed.", i, len(unclustered))

    logger.info(
        "=== Done: processed=%d errors=%d ===",
        totals["processed"], totals["errors"],
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Backfill clusters for existing articles")
    parser.add_argument("--source", type=str, help="Filter by source name")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--batch-size", type=int, default=100, help="Log progress every N articles")
    asyncio.run(main(parser.parse_args()))
