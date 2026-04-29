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

_REFRESH_EVERY = 50  # force cluster index refresh every N articles during backfill
_RETRY_ATTEMPTS = 5
_RETRY_DELAY = 30  # seconds

logger = logging.getLogger(__name__)


async def _os_search(client, index: str, body: dict) -> dict:
    """Run an OpenSearch search with retry."""
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return await client.search(index=index, body=body)
        except Exception as exc:
            if attempt == _RETRY_ATTEMPTS - 1:
                raise
            logger.warning(
                "OpenSearch attempt %d/%d failed: %s — retrying in %ds",
                attempt + 1, _RETRY_ATTEMPTS, exc, _RETRY_DELAY,
            )
            await asyncio.sleep(_RETRY_DELAY)
    raise RuntimeError("unreachable")


async def _get_clustered_slugs(client) -> set[str]:
    """Collect all article slugs that are already in a cluster."""
    slugs: set[str] = set()
    page_size = 100
    from_offset = 0

    while True:
        resp = await _os_search(client, INDEX_CLUSTERS, {
            "query": {"match_all": {}},
            "size": page_size,
            "from": from_offset,
            "_source": ["article_ids"],
        })
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


async def _get_total_articles(client, query: dict) -> int:
    resp = await _os_search(client, INDEX_NEWS, {"query": query, "size": 0})
    return resp["hits"]["total"]["value"]


async def _get_entities_for_slug(client, slug: str) -> list[dict]:
    """Look up entities linked to an article slug."""
    resp = await _os_search(client, INDEX_ENTITIES, {
        "query": {"term": {"article_ids": slug}},
        "size": 200,
        "_source": ["normalized_key", "type", "cvss_score", "cisa_kev"],
    })
    return [
        {
            "normalized_key": hit["_source"]["normalized_key"],
            "type": hit["_source"].get("type", "unknown"),
            "cvss_score": hit["_source"].get("cvss_score"),
            "cisa_kev": hit["_source"].get("cisa_kev", False),
        }
        for hit in resp["hits"]["hits"]
    ]


async def _reset_clusters(client) -> None:
    """Delete all cluster documents so everything can be re-clustered from scratch."""
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            resp = await client.delete_by_query(
                index=INDEX_CLUSTERS,
                body={"query": {"match_all": {}}},
                params={"refresh": "true", "conflicts": "proceed"},
            )
            logger.info("Reset: deleted %d cluster documents.", resp.get("deleted", 0))
            return
        except Exception as exc:
            if attempt == _RETRY_ATTEMPTS - 1:
                raise
            logger.warning("Reset attempt %d failed: %s — retrying in %ds", attempt + 1, exc, _RETRY_DELAY)
            await asyncio.sleep(_RETRY_DELAY)


async def main(args: argparse.Namespace) -> None:
    client = get_os_client()

    if args.reset:
        logger.info("--reset: wiping all clusters before re-clustering.")
        await _reset_clusters(client)

    clustered = set() if args.reset else await _get_clustered_slugs(client)
    logger.info("Found %d article slugs already in clusters.", len(clustered))

    filters = [{"term": {"source_name": args.source}}] if args.source else []
    query = {"bool": {"filter": filters}} if filters else {"match_all": {}}

    total = await _get_total_articles(client, query)
    logger.info("Found %d total article(s) in index.", total)

    totals = {"processed": 0, "skipped": 0, "errors": 0}
    page_size = 100
    from_offset = 0
    article_num = 0  # global counter across pages

    while True:
        resp = await _os_search(client, INDEX_NEWS, {
            "query": query,
            "sort": [{"published_at": {"order": "asc"}}],
            "size": page_size,
            "from": from_offset,
            "_source": [
                "slug", "title", "desc", "summary", "cve_ids",
                "category", "tags", "published_at",
                "source_name", "credibility_weight", "cvss_score",
            ],
        })
        hits = resp["hits"]["hits"]
        if not hits:
            break

        for hit in hits:
            article_num += 1
            slug = hit["_id"]

            if slug in clustered:
                totals["skipped"] += 1
                continue

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

            entities = await _get_entities_for_slug(client, slug)

            if args.dry_run:
                cve_label = f"CVEs={article_dict['cve_ids']}" if article_dict["cve_ids"] else "no CVEs"
                logger.info(
                    "[DRY RUN] %d/%d %s — %d entities, %s",
                    article_num, total, slug[:50], len(entities), cve_label,
                )
                totals["processed"] += 1
                continue

            try:
                await cluster_article(article_dict, slug, entities)
                totals["processed"] += 1
            except Exception:
                logger.exception("Failed to cluster %s", slug[:50])
                totals["errors"] += 1

            if totals["processed"] % _REFRESH_EVERY == 0 and totals["processed"] > 0:
                for attempt in range(_RETRY_ATTEMPTS):
                    try:
                        await client.indices.refresh(index=INDEX_CLUSTERS)
                        break
                    except Exception as exc:
                        if attempt == _RETRY_ATTEMPTS - 1:
                            logger.warning("Cluster refresh failed after retries: %s", exc)
                        else:
                            await asyncio.sleep(_RETRY_DELAY)

            if article_num % args.batch_size == 0:
                logger.info(
                    "Progress: %d/%d — processed=%d skipped=%d errors=%d",
                    article_num, total, totals["processed"], totals["skipped"], totals["errors"],
                )

        from_offset += len(hits)
        if len(hits) < page_size:
            break

    logger.info(
        "=== Done: processed=%d skipped=%d errors=%d ===",
        totals["processed"], totals["skipped"], totals["errors"],
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
