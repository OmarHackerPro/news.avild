#!/usr/bin/env python
"""Deduplicate clusters that share CVE IDs.

For each CVE ID found in multiple clusters, keeps the cluster with the most
articles and merges the smaller ones into it, then deletes the empty shells.

Designed to run periodically (cron) — not blocking MVP.

Usage:
    python scripts/dedup_clusters.py             # merge duplicates
    python scripts/dedup_clusters.py --dry-run   # preview only
"""
import asyncio
import argparse
import logging
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.db.opensearch import INDEX_CLUSTERS, get_os_client

logger = logging.getLogger(__name__)


async def _scroll_all_clusters() -> list[dict]:
    """Load all clusters with their article_ids, cve_ids, and entity_keys."""
    client = get_os_client()
    results = []

    resp = await client.search(
        index=INDEX_CLUSTERS,
        body={
            "query": {"match_all": {}},
            "size": 500,
            "_source": ["article_ids", "cve_ids", "entity_keys", "article_count"],
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


async def _merge_cluster_into(
    target_id: str, source_cluster: dict,
) -> None:
    """Merge a source cluster's data into the target cluster, then delete source."""
    client = get_os_client()
    src = source_cluster["_source"]
    source_id = source_cluster["_id"]

    article_ids = src.get("article_ids") or []
    entity_keys = src.get("entity_keys") or []
    cve_ids = src.get("cve_ids") or []

    script = """
        for (slug in params.article_ids) {
            if (!ctx._source.article_ids.contains(slug)) {
                ctx._source.article_ids.add(slug);
            }
        }
        ctx._source.article_count = ctx._source.article_ids.length();
        for (key in params.entity_keys) {
            if (!ctx._source.entity_keys.contains(key)) {
                ctx._source.entity_keys.add(key);
            }
        }
        for (cve in params.cve_ids) {
            if (!ctx._source.cve_ids.contains(cve)) {
                ctx._source.cve_ids.add(cve);
            }
        }
    """

    await client.update(
        index=INDEX_CLUSTERS,
        id=target_id,
        body={
            "script": {
                "source": script,
                "params": {
                    "article_ids": article_ids,
                    "entity_keys": entity_keys,
                    "cve_ids": cve_ids,
                },
            },
        },
        retry_on_conflict=3,
    )

    await client.delete(index=INDEX_CLUSTERS, id=source_id)
    logger.info("Merged cluster %s into %s and deleted it.", source_id, target_id)


async def main(args: argparse.Namespace) -> None:
    clusters = await _scroll_all_clusters()
    logger.info("Loaded %d cluster(s).", len(clusters))

    # Build CVE → cluster mapping
    cve_to_clusters: dict[str, list[dict]] = defaultdict(list)
    for cluster in clusters:
        for cve_id in cluster["_source"].get("cve_ids") or []:
            cve_to_clusters[cve_id].append(cluster)

    # Find CVEs with multiple clusters
    duplicates = {cve: cs for cve, cs in cve_to_clusters.items() if len(cs) > 1}

    if not duplicates:
        logger.info("No duplicate clusters found. Nothing to do.")
        return

    logger.info("Found %d CVE(s) with duplicate clusters.", len(duplicates))

    # Track which clusters we've already merged/deleted to avoid double-processing
    deleted: set[str] = set()
    merge_count = 0

    for cve_id, cluster_list in duplicates.items():
        # Filter out already-deleted clusters
        active = [c for c in cluster_list if c["_id"] not in deleted]
        if len(active) < 2:
            continue

        # Keep the cluster with the most articles
        active.sort(key=lambda c: len(c["_source"].get("article_ids") or []), reverse=True)
        target = active[0]
        to_merge = active[1:]

        for source in to_merge:
            if source["_id"] in deleted:
                continue

            if args.dry_run:
                logger.info(
                    "[DRY RUN] Would merge cluster %s (%d articles) into %s (%d articles) — shared CVE %s",
                    source["_id"],
                    len(source["_source"].get("article_ids") or []),
                    target["_id"],
                    len(target["_source"].get("article_ids") or []),
                    cve_id,
                )
            else:
                try:
                    await _merge_cluster_into(target["_id"], source)
                    merge_count += 1
                except Exception:
                    logger.exception(
                        "Failed to merge cluster %s into %s",
                        source["_id"], target["_id"],
                    )

            deleted.add(source["_id"])

    logger.info(
        "=== Done: %d cluster(s) merged/deleted ===",
        merge_count if not args.dry_run else 0,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Deduplicate clusters sharing CVE IDs")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    asyncio.run(main(parser.parse_args()))
