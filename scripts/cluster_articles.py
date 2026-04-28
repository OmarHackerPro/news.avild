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

    page_size = 100
    from_offset = 0

    while True:
        resp = await client.search(
            index=INDEX_CLUSTERS,
            body={
                "query": {"match_all": {}},
                "size": page_size,
                "from": from_offset,
                "_source": ["article_ids"],
            },
        )
        hits = resp["hits"]["hits"]
        if not hits:
            break
        for hit in hits:
            for slug in hit["_source"].get("article_ids") or []:
                slugs.add(slug)
        from_offset += len(hits)
        if len(hits) < page_size:
            break

    return slugs


async def _scroll_articles(source: str | None) -> list[dict]:
    """Load all articles sorted by published_at ascending."""
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
                "_source": [
                    "slug", "title", "desc", "summary", "cve_ids",
                    "category", "tags", "published_at",
                    "source_name", "credibility_weight", "cvss_score",
                ],
            },
        )
        hits = resp["hits"]["hits"]
        if not hits:
            break
        results.extend(hits)
        from_offset += len(hits)
        if len(hits) < page_size:
            break

    return results


async def _get_entities_for_slug(slug: str) -> list[dict]:
    """Look up entities linked to an article slug, including NVD fields for CVEs."""
    client = get_os_client()
    resp = await client.search(
        index=INDEX_ENTITIES,
        body={
            "query": {"term": {"article_ids": slug}},
            "size": 200,
            "_source": ["normalized_key", "type", "cvss_score", "cisa_kev"],
        },
    )
    return [
        {
            "normalized_key": hit["_source"]["normalized_key"],
            "type": hit["_source"].get("type", "unknown"),
            "cvss_score": hit["_source"].get("cvss_score"),
            "cisa_kev": hit["_source"].get("cisa_kev", False),
        }
        for hit in resp["hits"]["hits"]
    ]


async def _reset_clusters() -> None:
    """Delete all cluster documents so everything can be re-clustered from scratch."""
    client = get_os_client()
    resp = await client.delete_by_query(
        index=INDEX_CLUSTERS,
        body={"query": {"match_all": {}}},
        params={"refresh": "true", "conflicts": "proceed"},
    )
    deleted = resp.get("deleted", 0)
    logger.info("Reset: deleted %d cluster documents.", deleted)


async def main(args: argparse.Namespace) -> None:
    if args.reset:
        logger.info("--reset: wiping all clusters before re-clustering.")
        await _reset_clusters()

    # Collect already-clustered slugs to skip them (empty after reset)
    clustered = set() if args.reset else await _get_clustered_slugs()
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
            "source_name": src.get("source_name", ""),
            "credibility_weight": src.get("credibility_weight", 1.0),
            "cvss_score": src.get("cvss_score"),
        }

        entities = await _get_entities_for_slug(slug)

        if args.dry_run:
            cve_label = f"CVEs={article_dict['cve_ids']}" if article_dict["cve_ids"] else "no CVEs"
            logger.info(
                "[DRY RUN] %d/%d %s — %d entities, %s",
                i, len(unclustered), slug[:50], len(entities), cve_label,
            )
            totals["processed"] += 1
            continue

        try:
            await cluster_article(article_dict, slug, entities)
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
    parser.add_argument("--reset", action="store_true", help="Delete all clusters first, then re-cluster everything")
    asyncio.run(main(parser.parse_args()))
