#!/usr/bin/env python
"""Backup and restore the clusters OpenSearch index to/from a local NDJSON file.

Usage:
    python scripts/backup_clusters.py backup           # save clusters to backup file
    python scripts/backup_clusters.py restore          # restore from backup file
    python scripts/backup_clusters.py backup --file clusters_backup_20260507.ndjson
    python scripts/backup_clusters.py restore --file clusters_backup_20260507.ndjson
    python scripts/backup_clusters.py count            # count docs in index vs backup file
"""
import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.db.opensearch import INDEX_CLUSTERS, get_os_client

logger = logging.getLogger(__name__)

DEFAULT_FILE = Path(__file__).parent / "clusters_backup.ndjson"
BATCH_SIZE = 500
BULK_CHUNK = 100


async def backup(file: Path) -> None:
    client = get_os_client()

    resp = await client.search(
        index=INDEX_CLUSTERS,
        scroll="5m",
        body={"query": {"match_all": {}}, "size": BATCH_SIZE},
    )
    scroll_id = resp["_scroll_id"]
    total = resp["hits"]["total"]["value"]
    saved = 0

    logger.info("Backing up %d cluster documents → %s", total, file)

    with file.open("w", encoding="utf-8") as f:
        hits = resp["hits"]["hits"]
        while hits:
            for h in hits:
                f.write(json.dumps({"_id": h["_id"], "_source": h["_source"]}) + "\n")
            saved += len(hits)
            logger.info("  %d / %d", saved, total)
            resp = await client.scroll(scroll_id=scroll_id, scroll="5m")
            scroll_id = resp["_scroll_id"]
            hits = resp["hits"]["hits"]

    try:
        await client.clear_scroll(scroll_id=scroll_id)
    except Exception:
        pass

    logger.info("Backup complete. %d documents saved to %s", saved, file)


async def restore(file: Path) -> None:
    if not file.exists():
        logger.error("Backup file not found: %s", file)
        sys.exit(1)

    client = get_os_client()

    lines = file.read_text(encoding="utf-8").strip().splitlines()
    docs = [json.loads(l) for l in lines]
    total = len(docs)

    logger.info("Restoring %d documents from %s → %s", total, file, INDEX_CLUSTERS)

    restored = 0
    for i in range(0, total, BULK_CHUNK):
        chunk = docs[i : i + BULK_CHUNK]
        body = []
        for doc in chunk:
            body.append(json.dumps({"index": {"_index": INDEX_CLUSTERS, "_id": doc["_id"]}}))
            body.append(json.dumps(doc["_source"]))
        await client.bulk(body="\n".join(body) + "\n")
        restored += len(chunk)
        logger.info("  %d / %d", restored, total)

    await client.indices.refresh(index=INDEX_CLUSTERS)
    logger.info("Restore complete. %d documents written to %s", restored, INDEX_CLUSTERS)


async def count(file: Path) -> None:
    client = get_os_client()
    resp = await client.count(index=INDEX_CLUSTERS, body={"query": {"match_all": {}}})
    index_count = resp["count"]

    file_count = 0
    if file.exists():
        file_count = sum(1 for _ in file.open(encoding="utf-8"))

    logger.info("Index '%s': %d documents", INDEX_CLUSTERS, index_count)
    logger.info("Backup file '%s': %d documents", file, file_count)
    if file_count and index_count == file_count:
        logger.info("✓ Counts match")
    elif file_count:
        logger.warning("✗ Count mismatch! index=%d file=%d", index_count, file_count)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Backup/restore clusters index")
    parser.add_argument("action", choices=["backup", "restore", "count"])
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_FILE,
        help="NDJSON backup file path (default: scripts/clusters_backup.ndjson)",
    )
    args = parser.parse_args()

    if args.action == "backup":
        asyncio.run(backup(args.file))
    elif args.action == "restore":
        asyncio.run(restore(args.file))
    elif args.action == "count":
        asyncio.run(count(args.file))
