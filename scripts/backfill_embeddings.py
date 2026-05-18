#!/usr/bin/env python
"""Backfill article_embedding for existing articles.

Usage:
    python scripts/backfill_embeddings.py
    python scripts/backfill_embeddings.py --source "Krebs on Security"
    python scripts/backfill_embeddings.py --dry-run
    python scripts/backfill_embeddings.py --limit 500
    python scripts/backfill_embeddings.py --force
"""
import asyncio
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.table import Table

from app.db.opensearch import INDEX_NEWS, INDEX_ENTITIES, get_os_client
from app.ingestion.embedding_input import embed_article
from app.utils.progress import make_script_progress

logger = logging.getLogger(__name__)
console = Console()

_EMBED_CONCURRENCY = 4


async def _scroll_articles(source: str | None, limit: int | None, force: bool) -> list[dict]:
    client = get_os_client()

    query: dict = {"bool": {}}
    if not force:
        query["bool"]["must_not"] = {"exists": {"field": "article_embedding"}}
    if source:
        query["bool"]["filter"] = [{"term": {"source_name": source}}]
    if not query["bool"]:
        query = {"match_all": {}}

    results = []
    page_size = 100
    from_offset = 0

    while True:
        remaining = (limit - len(results)) if limit is not None else page_size
        fetch_size = min(page_size, remaining) if limit is not None else page_size
        if fetch_size <= 0:
            break

        resp = await client.search(
            index=INDEX_NEWS,
            body={
                "query": query,
                "sort": [{"published_at": {"order": "asc"}}],
                "size": fetch_size,
                "from": from_offset,
                "_source": ["slug", "title", "summary", "desc", "content_html", "source_name"],
            },
        )
        hits = resp["hits"]["hits"]
        if not hits:
            break
        results.extend(hits)
        from_offset += len(hits)
        if len(hits) < fetch_size:
            break
        if limit is not None and len(results) >= limit:
            break

    return results


async def _entity_keys_for(slugs: list[str]) -> dict[str, list[str]]:
    from collections import defaultdict

    keys: dict[str, list[str]] = defaultdict(list)
    if not slugs:
        return keys
    client = get_os_client()
    resp = await client.search(
        index=INDEX_ENTITIES,
        body={
            "query": {"terms": {"article_ids": slugs}},
            "size": 5000,
            "_source": ["normalized_key", "article_ids"],
        },
    )
    slug_set = set(slugs)
    for hit in resp["hits"]["hits"]:
        nk = hit["_source"].get("normalized_key")
        if not nk:
            continue
        for sid in hit["_source"].get("article_ids") or []:
            if sid in slug_set:
                keys[sid].append(nk)
    return keys


async def main(args: argparse.Namespace) -> None:
    with console.status("[cyan]Scanning articles…"):
        articles = await _scroll_articles(source=args.source, limit=args.limit, force=args.force)

    total = len(articles)
    console.print(f"[bold]Embedding {total} articles (concurrency={_EMBED_CONCURRENCY})…[/bold]")

    totals = {"ok": 0, "skipped": 0, "errors": 0}
    semaphore = asyncio.Semaphore(_EMBED_CONCURRENCY)
    batch_size = args.batch_size

    with make_script_progress(console) as progress:
        task = progress.add_task("Embedding", total=total)

        def _stats() -> str:
            return f"ok={totals['ok']} skipped={totals['skipped']} errors={totals['errors']}"

        for batch_start in range(0, total, batch_size):
            batch = articles[batch_start : batch_start + batch_size]
            slugs = [hit["_id"] for hit in batch]
            entity_keys = await _entity_keys_for(slugs)

            if args.dry_run:
                for hit in batch:
                    slug = hit["_id"]
                    progress.update(task, description=f"[dim]dry-run[/dim]  {_stats()}")
                    progress.console.print(
                        f"[DRY RUN] {slug[:50]} — entities={len(entity_keys.get(slug, []))}"
                    )
                    totals["ok"] += 1
                    progress.advance(task)
                continue

            async def _process_one(hit: dict) -> str:
                slug = hit["_id"]
                async with semaphore:
                    progress.update(task, description=f"[cyan]{slug[:40]}[/cyan]  {_stats()}")
                    try:
                        vec = await embed_article(hit["_source"], entity_keys.get(slug, []))
                        if vec is None:
                            return "skipped"
                        return "ok"
                    except Exception:
                        logger.exception("Embed failed for %s", slug)
                        return "error"

            outcomes = await asyncio.gather(*[_process_one(h) for h in batch])

            for outcome in outcomes:
                totals[outcome] = totals.get(outcome, 0) + 1
                progress.advance(task)
            progress.update(task, description=f"[dim]batch {batch_start // batch_size + 1}[/dim]  {_stats()}")

    table = Table(title="Embedding Complete", show_header=True, header_style="bold magenta")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="bold")
    table.add_row("Total", str(total))
    table.add_row("Succeeded", str(totals["ok"]))
    table.add_row("Skipped (embed failed)", str(totals["skipped"]))
    table.add_row("Errors", str(totals.get("errors", 0)), style="red" if totals.get("errors") else "")
    console.print(table)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("app.ingestion.embedding_client").setLevel(logging.ERROR)
    logging.getLogger("opensearch").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.ERROR)

    parser = argparse.ArgumentParser(description="Backfill article embeddings")
    parser.add_argument("--source", type=str, help="Filter by source name")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--batch-size", type=int, default=64, help="Articles per fetch batch")
    parser.add_argument("--limit", type=int, default=None, help="Only process first N articles")
    parser.add_argument("--force", action="store_true", help="Re-embed articles that already have an embedding")

    async def _run():
        try:
            await main(parser.parse_args())
        finally:
            from app.db.opensearch import close_os_client
            await close_os_client()

    asyncio.run(_run())
