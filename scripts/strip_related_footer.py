#!/usr/bin/env python
"""Strip trailing related-links footers from already-stored article bodies.

Some sources (SecurityWeek) end the article <div> with `Related: <link>`
paragraphs, which Trafilatura includes as main content. Those leak unrelated
entities into clustering. This applies _strip_related_footer() in place on the
stored content_html — no re-fetch needed, the footer lines are already cleanly
separated in the extracted text.

Usage:
    python scripts/strip_related_footer.py --dry-run
    python scripts/strip_related_footer.py --source "SecurityWeek"
    python scripts/strip_related_footer.py            # all sources
"""
import asyncio
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.table import Table

from app.db.opensearch import INDEX_NEWS, get_os_client, close_os_client
from app.ingestion.body_extractor import _strip_related_footer

console = Console()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--source", default=None, help="limit to one source_name")
    p.add_argument("--dry-run", action="store_true", help="report without writing")
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    client = get_os_client()

    query: dict = {"match_all": {}}
    if args.source:
        query = {"term": {"source_name": args.source}}

    resp = await client.search(
        index=INDEX_NEWS,
        scroll="2m",
        body={"size": 500, "_source": ["source_name", "content_html"], "query": query},
    )
    scroll_id = resp["_scroll_id"]

    scanned = 0
    changed: dict[str, int] = {}
    chars_cut: dict[str, int] = {}

    while True:
        hits = resp["hits"]["hits"]
        if not hits:
            break
        for hit in hits:
            scanned += 1
            src = hit["_source"]
            source_name = src.get("source_name") or "?"
            body = src.get("content_html") or ""
            cleaned = _strip_related_footer(body)
            if cleaned == body:
                continue
            changed[source_name] = changed.get(source_name, 0) + 1
            chars_cut[source_name] = chars_cut.get(source_name, 0) + (len(body) - len(cleaned))
            if not args.dry_run:
                await client.update(
                    index=INDEX_NEWS,
                    id=hit["_id"],
                    body={"doc": {"content_html": cleaned}},
                )
        resp = await client.scroll(scroll_id=scroll_id, scroll="2m")
        scroll_id = resp["_scroll_id"]

    await close_os_client()

    table = Table(title=("Related-footer strip — DRY RUN" if args.dry_run else "Related-footer strip"))
    table.add_column("Source", style="cyan")
    table.add_column("Updated", justify="right")
    table.add_column("Chars cut", justify="right")
    for source_name in sorted(changed, key=lambda s: -changed[s]):
        table.add_row(source_name, str(changed[source_name]), str(chars_cut[source_name]))
    console.print(table)
    console.print(f"Scanned {scanned} articles · {sum(changed.values())} "
                  f"{'would be ' if args.dry_run else ''}updated")


if __name__ == "__main__":
    asyncio.run(main())
