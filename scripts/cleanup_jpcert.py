#!/usr/bin/env python
"""Disable JPCERT/CC source and remove its articles and solo clusters.

Usage (inside container):
    python scripts/cleanup_jpcert.py --dry-run   # preview counts only
    python scripts/cleanup_jpcert.py             # apply changes

Safe to re-run: Postgres UPDATE is idempotent; OpenSearch deletes are no-ops
if documents are already gone.
"""
import asyncio
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
if Path(".env").exists():
    from dotenv import load_dotenv
    load_dotenv()

from sqlalchemy import update as sa_update

from app.db.models.feed_source import FeedSource as FeedSourceModel
from app.db.session import AsyncSessionLocal
from app.db.opensearch import INDEX_NEWS, INDEX_CLUSTERS, get_os_client

SOURCE_NAME = "JPCERT/CC"
logger = logging.getLogger(__name__)


async def run(*, dry_run: bool) -> None:
    os_client = get_os_client()

    # --- 1. Find all JPCERT article slugs ---
    resp = await os_client.search(
        index=INDEX_NEWS,
        body={
            "query": {"term": {"source_name": SOURCE_NAME}},
            "_source": False,
            "size": 10000,
        },
    )
    jpcert_slugs = {h["_id"] for h in resp["hits"]["hits"]}
    logger.info("Found %d JPCERT articles", len(jpcert_slugs))

    # --- 2. Find solo clusters whose only article is a JPCERT slug ---
    cluster_resp = await os_client.search(
        index=INDEX_CLUSTERS,
        body={
            "query": {"term": {"article_count": 1}},
            "_source": ["article_ids"],
            "size": 10000,
        },
    )
    solo_cluster_ids = [
        h["_id"]
        for h in cluster_resp["hits"]["hits"]
        if h["_source"].get("article_ids", []) and
           h["_source"]["article_ids"][0] in jpcert_slugs
    ]
    logger.info("Found %d solo clusters to delete", len(solo_cluster_ids))

    if dry_run:
        logger.info("[DRY RUN] Would delete %d articles and %d clusters", len(jpcert_slugs), len(solo_cluster_ids))
        logger.info("[DRY RUN] Would set is_active=False for '%s' in Postgres", SOURCE_NAME)
        return

    # --- 3. Delete solo clusters ---
    for cid in solo_cluster_ids:
        try:
            await os_client.delete(index=INDEX_CLUSTERS, id=cid)
        except Exception as e:
            logger.warning("Could not delete cluster %s: %s", cid, e)
    logger.info("Deleted %d clusters", len(solo_cluster_ids))

    # --- 4. Delete articles ---
    if jpcert_slugs:
        await os_client.delete_by_query(
            index=INDEX_NEWS,
            body={"query": {"term": {"source_name": SOURCE_NAME}}},
        )
    logger.info("Deleted %d articles", len(jpcert_slugs))

    # --- 5. Disable source in Postgres ---
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(
                sa_update(FeedSourceModel)
                .where(FeedSourceModel.name == SOURCE_NAME)
                .values(is_active=False)
            )
    logger.info("Set is_active=False for '%s' in Postgres", SOURCE_NAME)


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
