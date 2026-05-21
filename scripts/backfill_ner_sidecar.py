#!/usr/bin/env python
"""Backfill NER entities for existing articles using the local NER sidecar.

Calls the running NER sidecar container over HTTP (no Anthropic API).
Merges sidecar output with regex extraction (same pipeline as live ingestion).
Writes results to ner_cache (Postgres) and the entities index (OpenSearch).

Skips kev_catalog articles — they have no article body worth extracting from.

Usage:
    python scripts/backfill_ner_sidecar.py --limit 20 --show-entities   # test run
    python scripts/backfill_ner_sidecar.py --dry-run                     # count only
    python scripts/backfill_ner_sidecar.py                               # all articles
    python scripts/backfill_ner_sidecar.py --force                       # re-run even if cached
    python scripts/backfill_ner_sidecar.py --source "Krebs on Security"
    python scripts/backfill_ner_sidecar.py --concurrency 4
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

from app.utils.progress import make_script_progress
from rich.table import Table
from sqlalchemy import text

from app.core.config import settings
from app.db.opensearch import INDEX_NEWS, get_os_client
from app.db.session import AsyncSessionLocal
from app.ingestion.entity_extractor import extract_entities
from app.ingestion.entity_store import store_article_entities
from app.ingestion.normalizer import strip_html

console = Console()
logger = logging.getLogger(__name__)

_RETRY_ATTEMPTS = 5
_RETRY_DELAY = 10

_TYPE_COLOR = {
    "cve":        "bold red",
    "malware":    "bold magenta",
    "actor":      "bold yellow",
    "tool":       "cyan",
    "vuln_alias": "bold orange3",
    "campaign":   "bold blue",
    "product":    "green",
    "vendor":     "dim green",
}


async def _os_search(client, body: dict) -> dict:
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return await client.search(index=INDEX_NEWS, body=body)
        except Exception as exc:
            if attempt == _RETRY_ATTEMPTS - 1:
                raise
            logger.warning("OpenSearch attempt %d/%d failed: %s — retrying in %ds",
                           attempt + 1, _RETRY_ATTEMPTS, exc, _RETRY_DELAY)
            await asyncio.sleep(_RETRY_DELAY)
    raise RuntimeError("unreachable")


def _render_entities(entities: list[dict]) -> str:
    if not entities:
        return "  [dim]∅ no entities[/dim]"
    parts = []
    for e in entities:
        color = _TYPE_COLOR.get(e.get("type", ""), "white")
        parts.append(f"[{color}]{e['type']}[/{color}]:[bold]{e['name']}[/bold]")
    return "  " + "  ".join(parts)


async def _is_cached(slug: str, model_version: str) -> bool:
    async with AsyncSessionLocal() as db:
        row = await db.execute(
            text("SELECT 1 FROM ner_cache WHERE slug = :slug AND model_version = :ver"),
            {"slug": slug, "ver": model_version},
        )
        return row.fetchone() is not None


def _stats(counts: dict) -> str:
    return f"new={counts['new_extractions']} cached={counts['cached_hits']} errors={counts['errors']}"


async def _process_article(
    hit: dict,
    *,
    dry_run: bool,
    force: bool,
    show_entities: bool,
    counts: dict,
    progress,
    task,
) -> None:
    slug = hit["_id"]
    src = hit["_source"]
    title = src.get("title") or slug
    model_version = settings.NER_ACTIVE_MODEL

    if not force and not dry_run:
        if await _is_cached(slug, model_version):
            counts["cached_hits"] += 1
            progress.advance(task)
            progress.update(task, description=f"[dim]cached[/dim]  {_stats(counts)}")
            return

    content_html = src.get("content_html") or ""
    body_text = strip_html(content_html) if content_html else (src.get("summary") or src.get("desc") or "")

    if dry_run:
        console.print(f"[DRY RUN] {slug[:55]}  body_chars={len(body_text)}")
        counts["processed"] += 1
        progress.advance(task)
        progress.update(task, description=f"[dim]dry-run[/dim]  {_stats(counts)}")
        return

    article_dict = {
        "title": title,
        "desc": src.get("desc"),
        "summary": src.get("summary"),
        "content_html": content_html,
        "cve_ids": src.get("cve_ids") or [],
        "cvss_score": src.get("cvss_score"),
    }

    progress.update(task, description=f"[cyan]{title[:45]}[/cyan]  {_stats(counts)}")

    try:
        async with AsyncSessionLocal() as db:
            entities = await extract_entities(article_dict, slug=slug, db_session=db)

        if entities:
            await store_article_entities(slug, entities)

        counts["new_extractions"] += 1
        counts["processed"] += 1

        if show_entities:
            entity_line = _render_entities(entities)
            console.print(
                f"  [bold green]new[/bold green]  [dim]{slug[:55]}[/dim]\n{entity_line}"
            )

    except Exception as exc:
        logger.error("Failed slug=%s: %s", slug, exc)
        counts["errors"] += 1

    progress.advance(task)
    progress.update(task, description=f"[cyan]{title[:45]}[/cyan]  {_stats(counts)}")


async def main(args: argparse.Namespace) -> None:
    client = get_os_client()
    model_version = settings.NER_ACTIVE_MODEL
    console.print(f"[dim]NER model: {model_version}[/dim]")

    # Exclude kev_catalog — no article body worth extracting from
    base_filters: list[dict] = [
        {"bool": {"must_not": {"term": {"content_type": "kev_catalog"}}}}
    ]
    if args.source:
        base_filters.append({"term": {"source_name": args.source}})
    if args.days:
        base_filters.append({"range": {"published_at": {"gte": f"now-{args.days}d/d"}}})

    query = {"bool": {"filter": base_filters}}

    total_resp = await _os_search(client, {"query": query, "size": 0})
    total = total_resp["hits"]["total"]["value"]
    if args.limit:
        total = min(total, args.limit)

    console.print(f"[bold]Processing up to {total} articles (model={model_version})…[/bold]")

    counts = {"processed": 0, "cached_hits": 0, "new_extractions": 0, "errors": 0}
    semaphore = asyncio.Semaphore(args.concurrency)
    page_size = 100
    from_offset = 0
    processed_total = 0

    with make_script_progress(console) as progress:
        task = progress.add_task(f"[dim]starting…[/dim]  {_stats(counts)}", total=total)

        while True:
            if args.limit and processed_total >= args.limit:
                break

            fetch_size = page_size
            if args.limit:
                fetch_size = min(page_size, args.limit - processed_total)

            resp = await _os_search(client, {
                "query": query,
                "sort": [{"published_at": {"order": "desc"}}],
                "size": fetch_size,
                "from": from_offset,
                "_source": [
                    "title", "desc", "summary", "content_html",
                    "cve_ids", "cvss_score", "content_type", "source_name",
                ],
            })
            hits = resp["hits"]["hits"]
            if not hits:
                break

            async def _sem_process(hit):
                async with semaphore:
                    await _process_article(
                        hit,
                        dry_run=args.dry_run,
                        force=args.force,
                        show_entities=args.show_entities,
                        counts=counts,
                        progress=progress,
                        task=task,
                    )

            await asyncio.gather(*[_sem_process(h) for h in hits])
            processed_total += len(hits)
            from_offset += len(hits)
            if len(hits) < fetch_size:
                break

    table = Table(title="NER Sidecar Backfill Complete", show_header=True, header_style="bold magenta")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="bold")
    table.add_row("Processed (new extractions)", str(counts["new_extractions"]))
    table.add_row("Skipped (already cached)", str(counts["cached_hits"]), style="dim")
    table.add_row("Errors", str(counts["errors"]), style="red" if counts["errors"] else "")
    console.print(table)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # NER sidecar timeouts on long articles are expected — suppress to avoid
    # flooding the progress bar. Failures fall through to regex-only extraction.
    logging.getLogger("app.ingestion.ner_client").setLevel(logging.ERROR)

    parser = argparse.ArgumentParser(description="Backfill NER via local sidecar")
    parser.add_argument("--source", type=str, help="Filter by source_name")
    parser.add_argument("--days", type=int, default=0, help="Only process articles published in the last N days (0 = all time)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--limit", type=int, default=0, help="Stop after N articles (0 = all)")
    parser.add_argument("--force", action="store_true", help="Re-run even if already in ner_cache")
    parser.add_argument("--show-entities", action="store_true", help="Print extracted entities per article")
    parser.add_argument("--concurrency", type=int, default=1, help="Parallel sidecar calls (default 1 — sidecar serializes via lock anyway)")

    async def _run():
        try:
            await main(parser.parse_args())
        finally:
            from app.db.opensearch import close_os_client
            await close_os_client()

    asyncio.run(_run())
