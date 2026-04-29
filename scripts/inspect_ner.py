#!/usr/bin/env python
"""Inspect NER extraction quality — shows article title/summary alongside extracted entities.

Usage:
    python scripts/inspect_ner.py                      # 20 most recent cached articles
    python scripts/inspect_ner.py --limit 50           # more articles
    python scripts/inspect_ner.py --type cve           # filter by entity type
    python scripts/inspect_ner.py --type vuln_alias    # check vuln_alias quality
    python scripts/inspect_ner.py --empty              # articles with 0 entities
    python scripts/inspect_ner.py --slug some-slug     # specific article
"""
import asyncio
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import print as rprint

from app.db.opensearch import INDEX_NEWS, get_os_client
from app.db.session import AsyncSessionLocal
from sqlalchemy import text

console = Console()

_TYPE_COLOR = {
    "cve": "bold red",
    "malware": "bold magenta",
    "actor": "bold yellow",
    "tool": "cyan",
    "vuln_alias": "bold orange3",
    "campaign": "bold blue",
    "product": "green",
}


async def main(args: argparse.Namespace) -> None:
    client = get_os_client()

    async with AsyncSessionLocal() as db:
        if args.slug:
            q = "SELECT slug, entities_json, extracted_at FROM ner_cache WHERE slug = :slug"
            params = {"slug": args.slug}
        elif args.empty:
            q = """SELECT slug, entities_json, extracted_at FROM ner_cache
                   WHERE jsonb_array_length(entities_json) = 0
                   ORDER BY extracted_at DESC LIMIT :limit"""
            params = {"limit": args.limit}
        elif args.type:
            q = """SELECT DISTINCT slug, entities_json, extracted_at FROM ner_cache,
                   jsonb_array_elements(entities_json) e
                   WHERE e->>'type' = :etype
                   ORDER BY extracted_at DESC LIMIT :limit"""
            params = {"etype": args.type, "limit": args.limit}
        else:
            q = """SELECT slug, entities_json, extracted_at FROM ner_cache
                   ORDER BY extracted_at DESC LIMIT :limit"""
            params = {"limit": args.limit}

        r = await db.execute(text(q), params)
        rows = r.fetchall()

    if not rows:
        console.print("[yellow]No rows matched.[/yellow]")
        return

    # Fetch article content from OpenSearch for each slug
    slugs = [row[0] for row in rows]
    resp = await client.search(
        index=INDEX_NEWS,
        body={
            "query": {"ids": {"values": slugs}},
            "_source": ["title", "summary", "desc", "source_name", "published_at"],
            "size": len(slugs),
        },
    )
    articles = {h["_id"]: h["_source"] for h in resp["hits"]["hits"]}

    for slug, entities, extracted_at in rows:
        art = articles.get(slug, {})
        title = art.get("title") or slug
        summary = (art.get("summary") or art.get("desc") or "")[:200]
        source = art.get("source_name", "")

        # Build entity display
        if entities:
            entity_parts = []
            for e in sorted(entities, key=lambda x: x["type"]):
                color = _TYPE_COLOR.get(e["type"], "white")
                entity_parts.append(f"[{color}][{e['type']}][/{color}] {e['name']}")
            entity_str = "\n".join(entity_parts)
        else:
            entity_str = "[dim]∅ no entities extracted[/dim]"

        panel_content = (
            f"[bold]{title}[/bold]\n"
            f"[dim]{source}[/dim]\n\n"
            f"[dim italic]{summary}[/dim italic]\n\n"
            f"─── Entities ───\n"
            f"{entity_str}"
        )
        console.print(Panel(
            panel_content,
            title=f"[dim]{slug[:60]}[/dim]",
            subtitle=f"[dim]cached {extracted_at}[/dim]",
            expand=False,
        ))

    # Summary stats
    table = Table(show_header=True, title=f"Stats for {len(rows)} articles")
    table.add_column("Type")
    table.add_column("Count", justify="right")
    from collections import Counter
    type_counts: Counter = Counter()
    for _, entities, _ in rows:
        for e in entities:
            type_counts[e["type"]] += 1
    for etype, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        color = _TYPE_COLOR.get(etype, "white")
        table.add_row(f"[{color}]{etype}[/{color}]", str(cnt))
    console.print(table)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inspect NER extraction quality")
    parser.add_argument("--limit", type=int, default=20, help="Number of articles to inspect (default 20)")
    parser.add_argument("--type", type=str, help="Filter by entity type (cve, actor, malware, tool, vuln_alias, campaign, product)")
    parser.add_argument("--empty", action="store_true", help="Show articles with 0 entities")
    parser.add_argument("--slug", type=str, help="Inspect a specific article by slug")
    asyncio.run(main(parser.parse_args()))
