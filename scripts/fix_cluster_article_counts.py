#!/usr/bin/env python
"""One-time fix: recount article_ids against news_articles and update stale article_count fields.

After a bulk article purge (e.g. MSRC removal) the article_count stored in each
cluster document may exceed the number of articles that still exist in the index.
This script scrolls all clusters, checks how many of their article_ids are still
present, and bulk-updates the ones where the count has drifted.

Usage:
    docker compose exec ingestion python scripts/fix_cluster_article_counts.py
    docker compose exec ingestion python scripts/fix_cluster_article_counts.py --dry-run
"""
import asyncio
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

from app.db.opensearch import INDEX_CLUSTERS, INDEX_NEWS, get_os_client


async def run(dry_run: bool) -> None:
    client = get_os_client()
    scroll_size = 200
    updated = 0
    checked = 0

    log.info("Scrolling clusters (scroll_size=%d) ...", scroll_size)

    resp = await client.search(
        index=INDEX_CLUSTERS,
        scroll="2m",
        body={
            "query": {"match_all": {}},
            "size": scroll_size,
            "_source": ["article_ids", "article_count"],
        },
    )
    scroll_id = resp["_scroll_id"]

    try:
        while True:
            hits = resp["hits"]["hits"]
            if not hits:
                break

            # Collect all article_ids from this batch
            cluster_ids = [h["_id"] for h in hits]
            id_lists = {h["_id"]: (h["_source"].get("article_ids") or []) for h in hits}
            stored_counts = {h["_id"]: h["_source"].get("article_count", 0) for h in hits}

            all_slugs = list({slug for slugs in id_lists.values() for slug in slugs})

            # Check which slugs still exist in news_articles
            existing: set[str] = set()
            if all_slugs:
                mget_resp = await client.mget(
                    index=INDEX_NEWS,
                    body={"ids": all_slugs},
                    _source=False,
                )
                existing = {doc["_id"] for doc in mget_resp["docs"] if doc.get("found")}

            # Build bulk update for stale clusters
            bulk_body: list[dict] = []
            for cid in cluster_ids:
                checked += 1
                actual = sum(1 for s in id_lists[cid] if s in existing)
                stored = stored_counts[cid]
                if actual != stored:
                    log.info(
                        "cluster %s: stored=%d actual=%d%s",
                        cid, stored, actual, " [dry-run]" if dry_run else "",
                    )
                    if not dry_run:
                        bulk_body.append({"update": {"_index": INDEX_CLUSTERS, "_id": cid}})
                        bulk_body.append({"doc": {"article_count": actual}})
                    updated += 1

            if bulk_body and not dry_run:
                await client.bulk(body=bulk_body)

            resp = await client.scroll(scroll_id=scroll_id, scroll="2m")

    finally:
        try:
            await client.clear_scroll(scroll_id=scroll_id)
        except Exception:
            pass  # kiber_app lacks scroll/clear permission; scroll context expires on its own

    log.info(
        "Done. Checked %d clusters. %s %d with stale article_count.",
        checked,
        "Would update" if dry_run else "Updated",
        updated,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fix stale cluster article_count fields")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
