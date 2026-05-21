#!/usr/bin/env python
"""Backfill cvss_score + severity on articles that have cve_ids but no cvss_score.

Reads CVE intel from cve_topics (populated by migrate_cve_intel_to_topics.py).
Updates news_articles in OpenSearch. Write-once: skips articles already scored.

Usage:
    docker compose exec ingestion python scripts/backfill_cve_intel.py
    docker compose exec ingestion python scripts/backfill_cve_intel.py --dry-run
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.db.opensearch import INDEX_NEWS, get_os_client, close_os_client
from app.ingestion.cve_intel import lookup_cve_intel, severity_from_cvss

logger = logging.getLogger(__name__)
_SCROLL_SIZE = 200


async def _scroll_unscored_articles(client):
    query = {
        "bool": {
            "must": [{"exists": {"field": "cve_ids"}}],
            "must_not": [{"exists": {"field": "cvss_score"}}],
        }
    }
    results = []
    resp = await client.search(
        index=INDEX_NEWS,
        body={"query": query, "size": _SCROLL_SIZE, "_source": ["cve_ids", "cvss_score"]},
        scroll="2m",
    )
    scroll_id = resp["_scroll_id"]
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


async def run(args: argparse.Namespace) -> None:
    client = get_os_client()
    hits = await _scroll_unscored_articles(client)
    logger.info("Articles with cve_ids but no cvss_score: %d", len(hits))

    if args.dry_run:
        logger.info("Dry run — no writes.")
        return

    updated = skipped = 0
    for h in hits:
        slug = h["_id"]
        cve_ids = h["_source"].get("cve_ids") or []
        if not cve_ids:
            skipped += 1
            continue
        intel = await lookup_cve_intel(cve_ids)
        if not intel:
            skipped += 1
            continue
        scores = [v["cvss_score"] for v in intel.values() if v.get("cvss_score") is not None]
        if not scores:
            skipped += 1
            continue
        max_score = max(scores)
        severity = severity_from_cvss(max_score)
        try:
            await client.update(
                index=INDEX_NEWS,
                id=slug,
                body={"doc": {"cvss_score": max_score, "severity": severity}},
                retry_on_conflict=3,
            )
            updated += 1
        except Exception:
            logger.exception("Failed to update %s", slug)
            skipped += 1

    logger.info("Done. updated=%d  skipped=%d", updated, skipped)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Backfill cvss_score + severity on articles from cve_topics")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    finally:
        asyncio.run(close_os_client())


if __name__ == "__main__":
    main()
