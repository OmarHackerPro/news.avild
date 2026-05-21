#!/usr/bin/env python
"""Full rebuild pipeline: NER → embeddings → EPSS refresh → clustering.

Runs all three backfill steps in sequence. Each step is resumable:
re-running skips already-processed articles unless --force is passed.

Usage:
    python scripts/rebuild_all.py                    # full pipeline
    python scripts/rebuild_all.py --force            # redo everything from scratch
    python scripts/rebuild_all.py --skip-ner         # skip NER step
    python scripts/rebuild_all.py --skip-embed       # skip embedding step
    python scripts/rebuild_all.py --skip-epss        # skip EPSS step
    python scripts/rebuild_all.py --skip-cluster     # skip clustering step
    python scripts/rebuild_all.py --dry-run          # preview counts only
"""
import asyncio
import argparse
import logging
import sys
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.rule import Rule

console = Console()


def _step_header(label: str, n: int, total: int) -> None:
    console.print()
    console.print(Rule(f"[bold cyan]Step {n}/{total}: {label}[/bold cyan]"))
    console.print()


async def run_ner(force: bool, dry_run: bool) -> None:
    from scripts.backfill_ner_sidecar import main as ner_main
    args = Namespace(
        source=None,
        days=0,
        dry_run=dry_run,
        limit=0,
        force=force,
        show_entities=False,
        concurrency=1,
    )
    await ner_main(args)


async def run_embeddings(force: bool, dry_run: bool) -> None:
    from scripts.backfill_embeddings import main as embed_main
    args = Namespace(
        source=None,
        dry_run=dry_run,
        batch_size=64,
        limit=None,
        force=force,
    )
    await embed_main(args)


async def run_epss_sync(force: bool, dry_run: bool) -> None:
    # EPSS always overwrites (FIRST.org recomputes daily) — `force` is unused.
    from scripts.refresh_epss import main as epss_main
    args = Namespace(dry_run=dry_run, limit=0)
    await epss_main(args)


async def run_clustering(force: bool, dry_run: bool) -> None:
    from scripts.cluster_articles import main as cluster_main
    args = Namespace(
        source=None,
        dry_run=dry_run,
        batch_size=100,
        limit=0,
        reset=force,
        concurrency=6,
    )
    await cluster_main(args)


async def main(args: argparse.Namespace) -> None:
    steps = []
    if not args.skip_ner:
        steps.append(("NER extraction", run_ner))
    if not args.skip_embed:
        steps.append(("Embeddings", run_embeddings))
    if not args.skip_epss:
        steps.append(("EPSS refresh", run_epss_sync))
    if not args.skip_cluster:
        steps.append(("Clustering", run_clustering))

    total = len(steps)
    for n, (label, fn) in enumerate(steps, 1):
        _step_header(label, n, total)
        await fn(force=args.force, dry_run=args.dry_run)

    console.print()
    console.print(Rule("[bold green]Pipeline complete[/bold green]"))


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("app.ingestion.embedding_client").setLevel(logging.ERROR)
    logging.getLogger("app.ingestion.ner_client").setLevel(logging.ERROR)
    logging.getLogger("opensearch").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.ERROR)

    parser = argparse.ArgumentParser(description="Full rebuild: NER → embeddings → clustering")
    parser.add_argument("--force", action="store_true",
                        help="Redo all steps from scratch (NER: re-extract, embed: re-embed, cluster: --reset)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--skip-ner", action="store_true", help="Skip NER extraction step")
    parser.add_argument("--skip-embed", action="store_true", help="Skip embedding step")
    parser.add_argument("--skip-epss", action="store_true", help="Skip EPSS refresh step")
    parser.add_argument("--skip-cluster", action="store_true", help="Skip clustering step")

    async def _run():
        try:
            await main(parser.parse_args())
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted — rerun to resume from where each step left off.[/yellow]")
        finally:
            from app.db.opensearch import close_os_client
            await close_os_client()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
