#!/usr/bin/env python
"""One-shot: migrate CVE intelligence from `entities` (+ `nvd_cache`) into `cve_topics`.

For every CVE-type entity, copies cvss_score/severity/vector/cwe_ids/cisa_kev/
nvd_last_modified/vuln_status into cve_topics. Pulls the raw NVD blob from
nvd_cache and embeds as cve_topics.nvd_raw. Write-once: never overwrites existing
cve_topics fields.

Idempotent — safe to re-run. Counts before/after.

Usage:
    docker compose exec ingestion python scripts/migrate_cve_intel_to_topics.py
    docker compose exec ingestion python scripts/migrate_cve_intel_to_topics.py --dry-run
"""
import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.db.opensearch import INDEX_CVE_TOPICS, INDEX_ENTITIES, INDEX_NVD_CACHE, get_os_client, close_os_client
from app.db.os_write_once import upsert_immutable

logger = logging.getLogger(__name__)

_FIELDS = ["cvss_score", "cvss_severity", "cvss_vector", "cwe_ids", "cisa_kev", "vuln_status", "nvd_last_modified"]


async def _scroll_cve_entities(client):
    resp = await client.search(
        index=INDEX_ENTITIES,
        body={
            "query": {"term": {"type": "cve"}},
            "size": 200,
            "_source": ["name", *_FIELDS],
        },
        scroll="2m",
    )
    scroll_id = resp["_scroll_id"]
    results = []
    while True:
        hits = resp["hits"]["hits"]
        if not hits:
            break
        results.extend(hits)
        resp = await client.scroll(scroll_id=scroll_id, scroll="2m")
    try:
        await client.clear_scroll(scroll_id=scroll_id)
    except Exception:
        pass
    return results


async def _get_nvd_raw(client, cve_id: str):
    try:
        resp = await client.get(index=INDEX_NVD_CACHE, id=cve_id, _source=["nvd_raw"])
        return resp["_source"].get("nvd_raw")
    except Exception:
        return None


async def run(args: argparse.Namespace) -> None:
    client = get_os_client()
    hits = await _scroll_cve_entities(client)
    logger.info("Found %d CVE entities to migrate", len(hits))

    if args.dry_run:
        sample = [h["_source"].get("name") for h in hits[:5]]
        logger.info("Dry run — first 5 CVEs: %s", sample)
        return

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    migrated = nvd_raw_attached = skipped = 0

    for h in hits:
        src = h["_source"]
        cve_id = (src.get("name") or "").upper()
        if not cve_id.startswith("CVE-"):
            skipped += 1
            continue
        immutable = {k: src[k] for k in _FIELDS if src.get(k) is not None}
        nvd_raw = await _get_nvd_raw(client, cve_id)
        if nvd_raw is not None:
            immutable["nvd_raw"] = nvd_raw
            nvd_raw_attached += 1
        immutable["enriched_at"] = now_iso
        try:
            await upsert_immutable(
                client=client,
                index=INDEX_CVE_TOPICS,
                doc_id=cve_id,
                immutable_fields=immutable,
                mutable_fields={"updated_at": now_iso},
            )
            migrated += 1
        except Exception:
            logger.exception("Failed to migrate %s", cve_id)
            skipped += 1

    logger.info("Done. migrated=%d  nvd_raw_attached=%d  skipped=%d", migrated, nvd_raw_attached, skipped)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Migrate CVE intel from entities + nvd_cache into cve_topics")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    finally:
        asyncio.run(close_os_client())


if __name__ == "__main__":
    main()
