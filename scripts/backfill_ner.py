#!/usr/bin/env python
"""Backfill NER entities for existing articles by running LLM extraction.

Processes articles page-by-page (no upfront bulk load) so progress is never
lost to an OpenSearch timeout. Each OpenSearch request retries up to 5 times
with a 30s delay before giving up.

Usage:
    python scripts/backfill_ner.py                          # all articles
    python scripts/backfill_ner.py --dry-run --limit 10    # preview only
    python scripts/backfill_ner.py --limit 100             # first 100
    python scripts/backfill_ner.py --source "Krebs on Security"
    python scripts/backfill_ner.py --delay 1.0             # slower rate
    python scripts/backfill_ner.py --show-entities         # display extracted entities
    python scripts/backfill_ner.py --log-file logs/ner.log # explicit log path
"""
import asyncio
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich import print as rprint

from app.db.opensearch import INDEX_NEWS, get_os_client
from app.db.session import AsyncSessionLocal
from app.ingestion.ner_llm import extract_entities_llm
from app.ingestion.entity_store import store_article_entities

console = Console()

_RETRY_ATTEMPTS = 5
_RETRY_DELAY = 30  # seconds between retries

# Entity type → color
_TYPE_COLOR = {
    "cve":        "bold red",
    "malware":    "bold magenta",
    "actor":      "bold yellow",
    "tool":       "cyan",
    "vuln_alias": "bold orange3",
    "campaign":   "bold blue",
    "product":    "green",
}


def _setup_logging(log_file: str | None) -> logging.Logger:
    log_path = log_file or f"logs/backfill_ner_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Rich handler for terminal (INFO+)
    rich_handler = RichHandler(console=console, rich_tracebacks=True, show_path=False)
    rich_handler.setLevel(logging.INFO)
    root.addHandler(rich_handler)

    # File handler — full DEBUG audit trail
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s - %(message)s",
                          datefmt="%Y-%m-%d %H:%M:%S")
    )
    root.addHandler(file_handler)

    console.print(f"[dim]Audit log → {log_path}[/dim]")
    return logging.getLogger(__name__)


async def _os_search(client, body: dict) -> dict:
    """Run an OpenSearch search, retrying up to _RETRY_ATTEMPTS times."""
    logger = logging.getLogger(__name__)
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return await client.search(index=INDEX_NEWS, body=body)
        except Exception as exc:
            if attempt == _RETRY_ATTEMPTS - 1:
                raise
            logger.warning(
                "OpenSearch request attempt %d/%d failed: %s — retrying in %ds",
                attempt + 1, _RETRY_ATTEMPTS, exc, _RETRY_DELAY,
            )
            await asyncio.sleep(_RETRY_DELAY)
    raise RuntimeError("unreachable")


def _render_entities(entities: list[dict]) -> str:
    if not entities:
        return "[dim]∅ no entities[/dim]"
    parts = []
    for e in entities:
        color = _TYPE_COLOR.get(e["type"], "white")
        parts.append(f"[{color}]{e['type']}[/{color}]:[bold]{e['name']}[/bold]")
    return "  " + "  ".join(parts)


async def main(args: argparse.Namespace) -> None:
    logger = _setup_logging(args.log_file)

    client = get_os_client()
    filters = [{"term": {"source_name": args.source}}] if args.source else []
    query = {"bool": {"filter": filters}} if filters else {"match_all": {}}

    count_resp = await _os_search(client, {"query": query, "size": 0})
    grand_total = count_resp["hits"]["total"]["value"]
    if args.limit is not None:
        grand_total = min(grand_total, args.limit)
    logger.info("Found %d article(s) to process.", grand_total)

    page_size = 100
    from_offset = 0
    processed_total = 0
    counts = {"processed": 0, "cached_hits": 0, "new_extractions": 0, "errors": 0}

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )

    with progress:
        task = progress.add_task("NER backfill", total=grand_total)

        while True:
            if args.limit is not None and processed_total >= args.limit:
                break

            fetch_size = page_size
            if args.limit is not None:
                fetch_size = min(page_size, args.limit - processed_total)

            resp = await _os_search(client, {
                "query": query,
                "sort": [{"published_at": {"order": "asc"}}],
                "size": fetch_size,
                "from": from_offset,
                "_source": ["slug", "title", "summary", "desc", "source_name"],
            })
            hits = resp["hits"]["hits"]
            if not hits:
                break

            for hit in hits:
                slug = hit["_id"]
                src = hit["_source"]
                title = src.get("title") or ""
                summary = src.get("summary") or src.get("desc") or ""
                processed_total += 1

                if args.dry_run:
                    logger.info("[DRY RUN] %d/%d slug=%s", processed_total, grand_total, slug[:60])
                    counts["processed"] += 1
                    progress.advance(task)
                    continue

                try:
                    async with AsyncSessionLocal() as db:
                        from sqlalchemy import text as _text
                        cached_row = await db.execute(
                            _text("SELECT 1 FROM ner_cache WHERE slug = :slug"),
                            {"slug": slug},
                        )
                        already_cached = cached_row.fetchone() is not None
                        entities = await extract_entities_llm(slug, title, summary, db)

                    status = "cache" if already_cached else "new  "
                    if already_cached:
                        counts["cached_hits"] += 1
                    else:
                        counts["new_extractions"] += 1
                        if args.delay > 0:
                            await asyncio.sleep(args.delay)

                    if entities:
                        await store_article_entities(slug, entities)

                    counts["processed"] += 1

                    # Audit log (always written to file)
                    logger.debug(
                        "[%s] %d/%d slug=%s entities=%s",
                        status, processed_total, grand_total, slug,
                        [f"{e['type']}:{e['name']}" for e in entities],
                    )

                    if args.show_entities:
                        entity_line = _render_entities(entities)
                        label_color = "dim" if already_cached else "bold green"
                        console.print(
                            f"  [{label_color}]{status}[/{label_color}] "
                            f"[dim]{processed_total:4d}/{grand_total}[/dim] "
                            f"[white]{slug[:55]}[/white]\n"
                            f"{entity_line}"
                        )

                except Exception:
                    logger.exception("Failed to process slug=%s", slug[:60])
                    counts["errors"] += 1

                progress.advance(task)

                if processed_total % 50 == 0:
                    logger.info(
                        "Progress: %d/%d — cached=%d new=%d errors=%d",
                        processed_total, grand_total,
                        counts["cached_hits"], counts["new_extractions"], counts["errors"],
                    )

            from_offset += len(hits)
            if len(hits) < fetch_size:
                break

    # Final summary table
    table = Table(title="NER Backfill Complete", show_header=True)
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("Processed", str(counts["processed"]))
    table.add_row("Cache hits", str(counts["cached_hits"]), style="dim")
    table.add_row("New extractions", str(counts["new_extractions"]), style="green")
    table.add_row("Errors", str(counts["errors"]), style="red" if counts["errors"] else "dim")
    console.print(table)

    logger.info(
        "=== Done: processed=%d cached_hits=%d new_extractions=%d errors=%d ===",
        counts["processed"], counts["cached_hits"],
        counts["new_extractions"], counts["errors"],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill NER entities for existing articles")
    parser.add_argument("--source", type=str, help="Filter by source_name")
    parser.add_argument("--dry-run", action="store_true", help="Preview without calling LLM or storing")
    parser.add_argument("--limit", type=int, default=None, help="Only process first N articles")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds to wait between LLM calls (default 0.5)")
    parser.add_argument("--show-entities", action="store_true", help="Print extracted entities per article")
    parser.add_argument("--log-file", type=str, default=None, help="Path to audit log file (default: logs/backfill_ner_<timestamp>.log)")
    asyncio.run(main(parser.parse_args()))
