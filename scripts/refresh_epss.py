#!/usr/bin/env python
"""Refresh EPSS scores for all CVE topics.

Fetches current EPSS scores from FIRST.org and updates cve_topics documents.
Run daily after NVD enrichment.

Usage:
    python scripts/refresh_epss.py
    python scripts/refresh_epss.py --dry-run
    python scripts/refresh_epss.py --limit 100
"""
import asyncio
import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console

from app.db.opensearch import INDEX_CVE_TOPICS, get_os_client
from app.ingestion.epss_client import fetch_epss

logger = logging.getLogger(__name__)
console = Console()


async def _scroll_cve_ids(client, limit: int) -> list[str]:
    cve_ids: list[str] = []
    from_offset = 0
    page_size = 100
    while True:
        resp = await client.search(
            index=INDEX_CVE_TOPICS,
            body={
                "query": {"match_all": {}},
                "size": page_size,
                "from": from_offset,
                "_source": [],
            },
        )
        hits = resp["hits"]["hits"]
        if not hits:
            break
        cve_ids.extend(h["_id"] for h in hits)
        from_offset += len(hits)
        if len(hits) < page_size:
            break
        if limit and len(cve_ids) >= limit:
            cve_ids = cve_ids[:limit]
            break
    return cve_ids


async def main(args: argparse.Namespace) -> None:
    client = get_os_client()

    with console.status("[cyan]Scanning CVE topics…"):
        cve_ids = await _scroll_cve_ids(client, args.limit)

    console.print(f"[bold]Found {len(cve_ids)} CVE topics to refresh.[/bold]")

    if args.dry_run:
        console.print(f"[dim][DRY RUN] Would fetch EPSS for {len(cve_ids)} CVEs. First 5: {cve_ids[:5]}[/dim]")
        return

    with console.status("[cyan]Fetching EPSS scores from FIRST.org…"):
        epss_data = await fetch_epss(cve_ids)

    console.print(f"[dim]EPSS scores returned for {len(epss_data)}/{len(cve_ids)} CVEs.[/dim]")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    updated = 0
    missed = 0

    for cve_id in cve_ids:
        if cve_id not in epss_data:
            missed += 1
            continue
        scores = epss_data[cve_id]
        try:
            await client.update(
                index=INDEX_CVE_TOPICS,
                id=cve_id,
                body={"doc": {
                    "epss_score": scores["epss_score"],
                    "epss_percentile": scores["epss_percentile"],
                    "epss_updated_at": scores["epss_updated_at"],
                    "updated_at": now,
                }},
                retry_on_conflict=3,
            )
            updated += 1
        except Exception as exc:
            logger.warning("Failed to update EPSS for %s: %s", cve_id, exc)

    console.print(f"[green]Updated {updated} CVE topics with EPSS scores.[/green]")
    if missed:
        console.print(f"[yellow]{missed} CVEs not found in EPSS (may be reserved/rejected IDs).[/yellow]")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Refresh EPSS scores for all CVE topics")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--limit", type=int, default=0, help="Process only first N CVEs (0 = no limit)")

    async def _run():
        try:
            await main(parser.parse_args())
        finally:
            from app.db.opensearch import close_os_client
            await close_os_client()

    asyncio.run(_run())
