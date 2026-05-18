#!/usr/bin/env python
"""Backfill clusters for existing articles that aren't in any cluster yet.

Scrolls articles oldest-first so clusters form chronologically, then runs
each through the same cluster_article() decision tree used during ingestion.

Usage:
    python scripts/cluster_articles.py                          # all articles
    python scripts/cluster_articles.py --source "Krebs on Security"
    python scripts/cluster_articles.py --dry-run                # preview only
    python scripts/cluster_articles.py --concurrency 8
    python scripts/cluster_articles.py --limit 50               # quick test
"""
import asyncio
import argparse
import logging
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console

from app.utils.progress import make_script_progress
from rich.table import Table

from app.db.opensearch import INDEX_NEWS, INDEX_ENTITIES, INDEX_CLUSTERS, INDEX_CVE_TOPICS, get_os_client
from app.ingestion.clusterer import cluster_article
from app.ingestion.entity_idf import refresh_idf_map

_RETRY_ATTEMPTS = 5
_RETRY_DELAY = 10  # seconds
_DEFAULT_CONCURRENCY = 6
_REBUILD_REFRESH_INTERVAL = "1s"   # during rebuild (default mapping is "10s")
_NORMAL_REFRESH_INTERVAL = "10s"

logger = logging.getLogger(__name__)
console = Console()


async def _os_search(client, index: str, body: dict) -> dict:
    """Run an OpenSearch search with retry."""
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return await client.search(index=index, body=body)
        except Exception as exc:
            if attempt == _RETRY_ATTEMPTS - 1:
                raise
            logger.warning(
                "OpenSearch attempt %d/%d failed: %s — retrying in %ds",
                attempt + 1, _RETRY_ATTEMPTS, exc, _RETRY_DELAY,
            )
            await asyncio.sleep(_RETRY_DELAY)
    raise RuntimeError("unreachable")


async def _get_clustered_slugs(client) -> set[str]:
    """Collect all article slugs that are already in a cluster."""
    slugs: set[str] = set()
    page_size = 100
    from_offset = 0

    with console.status("[cyan]Scanning existing clusters…"):
        while True:
            resp = await _os_search(client, INDEX_CLUSTERS, {
                "query": {"match_all": {}},
                "size": page_size,
                "from": from_offset,
                "_source": ["article_ids"],
            })
            hits = resp["hits"]["hits"]
            if not hits:
                break
            for hit in hits:
                for slug in hit["_source"].get("article_ids") or []:
                    slugs.add(slug)
            from_offset += len(hits)
            if len(hits) < page_size:
                break

    return slugs


async def _get_total_articles(client, query: dict) -> int:
    resp = await _os_search(client, INDEX_NEWS, {"query": query, "size": 0})
    return resp["hits"]["total"]["value"]


async def _get_entities_batch(client, slugs: list[str]) -> dict[str, list[dict]]:
    """Fetch entities for a batch of article slugs in a single query."""
    result: dict[str, list[dict]] = defaultdict(list)
    if not slugs:
        return result
    resp = await _os_search(client, INDEX_ENTITIES, {
        "query": {"terms": {"article_ids": slugs}},
        "size": 5000,
        "_source": ["normalized_key", "type", "cvss_score", "cisa_kev", "article_ids"],
    })
    slugs_set = set(slugs)
    for hit in resp["hits"]["hits"]:
        ent = {
            "normalized_key": hit["_source"]["normalized_key"],
            "type": hit["_source"].get("type", "unknown"),
            "cvss_score": hit["_source"].get("cvss_score"),
            "cisa_kev": hit["_source"].get("cisa_kev", False),
        }
        for article_id in hit["_source"].get("article_ids") or []:
            if article_id in slugs_set:
                result[article_id].append(ent)
    return result


async def _reset_clusters(client) -> None:
    """Delete all cluster documents so everything can be re-clustered from scratch."""
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            resp = await client.delete_by_query(
                index=INDEX_CLUSTERS,
                body={"query": {"match_all": {}}},
                params={"refresh": "true", "conflicts": "proceed"},
            )
            console.print(f"[yellow]Reset: deleted {resp.get('deleted', 0)} cluster documents.[/yellow]")
            return
        except Exception as exc:
            if attempt == _RETRY_ATTEMPTS - 1:
                raise
            console.print(f"[red]Reset attempt {attempt + 1} failed: {exc} — retrying in {_RETRY_DELAY}s[/red]")
            await asyncio.sleep(_RETRY_DELAY)


async def _set_refresh_interval(client, interval: str) -> None:
    try:
        await client.indices.put_settings(
            index=INDEX_CLUSTERS,
            body={"index": {"refresh_interval": interval}},
        )
    except Exception as exc:
        logger.warning("Could not set refresh_interval=%s: %s", interval, exc)


async def _post_annotate(client) -> None:
    """Post-clustering annotation pass — fixes ordering-dependent flags.

    Runs after all articles are clustered so chronological processing order
    can't cause misses:
      - is_advisory=True  → any cluster whose article_ids includes an ics_advisory article
      - cisa_kev=True     → any cluster whose cve_ids overlap a kev_catalog article's CVE list
    """
    console.print("[dim]Running post-cluster annotation (is_advisory, cisa_kev)…[/dim]")

    # --- is_advisory: collect cluster_ids of ICS advisory articles ---
    ics_resp = await client.search(
        index=INDEX_NEWS,
        body={
            "query": {"term": {"content_type": "ics_advisory"}},
            "_source": ["cluster_id"],
            "size": 10000,
        },
    )
    ics_cluster_ids = list({
        h["_source"]["cluster_id"]
        for h in ics_resp["hits"]["hits"]
        if h["_source"].get("cluster_id")
    })
    if ics_cluster_ids:
        await client.update_by_query(
            index=INDEX_CLUSTERS,
            body={
                "query": {"ids": {"values": ics_cluster_ids}},
                "script": {"source": "ctx._source.is_advisory = true", "lang": "painless"},
            },
            params={"conflicts": "proceed", "refresh": "true"},
        )
        console.print(f"[dim]  is_advisory=True set on up to {len(ics_cluster_ids)} clusters.[/dim]")

    # --- cisa_kev: collect all CVE IDs from kev_catalog articles ---
    kev_resp = await client.search(
        index=INDEX_NEWS,
        body={
            "query": {"term": {"content_type": "kev_catalog"}},
            "_source": ["cve_ids"],
            "size": 10000,
        },
    )
    kev_cves: list[str] = []
    for h in kev_resp["hits"]["hits"]:
        kev_cves.extend(h["_source"].get("cve_ids") or [])
    kev_cves = list(dict.fromkeys(kev_cves))  # deduplicate, preserve order
    if kev_cves:
        await client.update_by_query(
            index=INDEX_CLUSTERS,
            body={
                "query": {"terms": {"cve_ids": kev_cves}},
                "script": {"source": "ctx._source.cisa_kev = true", "lang": "painless"},
            },
            params={"conflicts": "proceed", "refresh": "true"},
        )
        console.print(f"[dim]  cisa_kev=True applied for {len(kev_cves)} KEV CVE IDs.[/dim]")

    console.print("[dim]Post-annotation done.[/dim]")


async def main(args: argparse.Namespace) -> None:
    client = get_os_client()

    idf_count = await refresh_idf_map()
    console.print(f"[dim]IDF map: {idf_count} entities[/dim]")

    if args.reset:
        console.print("[bold yellow]--reset: wiping all clusters before re-clustering.[/bold yellow]")
        await _reset_clusters(client)

    # Speed up rebuild: create_cluster uses refresh=wait_for; lowering the
    # refresh interval from 10s → 1s cuts the per-article wait from ~5s to ~0.5s.
    await _set_refresh_interval(client, _REBUILD_REFRESH_INTERVAL)
    console.print(f"[dim]Refresh interval set to {_REBUILD_REFRESH_INTERVAL} for rebuild.[/dim]")

    clustered = set() if args.reset else await _get_clustered_slugs(client)
    if not args.reset:
        console.print(f"[dim]Found {len(clustered)} article slugs already in clusters (will skip).[/dim]")

    filters = [{"term": {"source_name": args.source}}] if args.source else []
    query = {"bool": {"filter": filters}} if filters else {"match_all": {}}

    total = await _get_total_articles(client, query)
    if args.limit:
        total = min(total, args.limit)
    concurrency = args.concurrency
    console.print(f"[bold]Clustering {total} articles (concurrency={concurrency})…[/bold]")

    totals = {"processed": 0, "skipped": 0, "errors": 0}
    semaphore = asyncio.Semaphore(concurrency)
    page_size = 100
    from_offset = 0

    with make_script_progress(console) as progress:
        task = progress.add_task("Clustering", total=total)

        def _stats() -> str:
            return f"processed={totals['processed']} skipped={totals['skipped']} errors={totals['errors']}"

        while True:
            resp = await _os_search(client, INDEX_NEWS, {
                "query": query,
                "sort": [{"published_at": {"order": "asc"}}],
                "size": page_size,
                "from": from_offset,
                "_source": [
                    "slug", "title", "desc", "summary", "cve_ids",
                    "category", "tags", "published_at",
                    "source_name", "credibility_weight", "cvss_score", "content_type",
                ],
            })
            hits = resp["hits"]["hits"]
            if not hits:
                break

            # Apply --limit cut on the page
            if args.limit:
                remaining = args.limit - (totals["processed"] + totals["skipped"])
                if remaining <= 0:
                    break
                hits = hits[:remaining]

            # Separate skips from work
            to_process = []
            for hit in hits:
                if hit["_id"] in clustered:
                    totals["skipped"] += 1
                    progress.advance(task)
                    progress.update(task, description=f"[dim]skip[/dim]  {_stats()}")
                else:
                    to_process.append(hit)

            if to_process:
                # One entity query for the entire page instead of one per article
                page_slugs = [h["_id"] for h in to_process]
                entities_map = await _get_entities_batch(client, page_slugs)

                async def _process(hit):
                    slug = hit["_id"]
                    src = hit["_source"]
                    article_dict = {
                        "slug": slug,
                        "title": src.get("title", ""),
                        "desc": src.get("desc"),
                        "summary": src.get("summary"),
                        "cve_ids": src.get("cve_ids") or [],
                        "category": src.get("category"),
                        "tags": src.get("tags") or [],
                        "published_at": src.get("published_at"),
                        "source_name": src.get("source_name", ""),
                        "credibility_weight": src.get("credibility_weight", 1.0),
                        "cvss_score": src.get("cvss_score"),
                        "content_type": src.get("content_type", "news"),
                    }
                    entities = entities_map.get(slug, [])

                    if args.dry_run:
                        cve_label = f"CVEs={article_dict['cve_ids']}" if article_dict["cve_ids"] else "no CVEs"
                        progress.console.print(
                            f"[DRY RUN] {slug[:50]} — {len(entities)} entities, {cve_label}"
                        )
                        totals["processed"] += 1
                        progress.advance(task)
                        progress.update(task, description=f"[dim]dry-run[/dim]  {_stats()}")
                        return

                    async with semaphore:
                        progress.update(task, description=f"[cyan]{src.get('title', slug)[:40]}[/cyan]  {_stats()}")
                        try:
                            await cluster_article(article_dict, slug, entities)
                            totals["processed"] += 1
                        except Exception as exc:
                            progress.console.print(f"[red]Error {slug[:45]}: {exc}[/red]")
                            totals["errors"] += 1
                        progress.advance(task)
                        progress.update(task, description=f"[cyan]{src.get('title', slug)[:40]}[/cyan]  {_stats()}")

                await asyncio.gather(*[_process(h) for h in to_process])

                # Refresh after each page so new clusters are visible for the next page
                try:
                    await client.indices.refresh(index=INDEX_CLUSTERS)
                except Exception as exc:
                    progress.console.print(f"[yellow]Refresh warning: {exc}[/yellow]")

            from_offset += len(hits)
            if len(hits) < page_size:
                break

    # Restore normal refresh interval
    await _set_refresh_interval(client, _NORMAL_REFRESH_INTERVAL)
    console.print(f"[dim]Refresh interval restored to {_NORMAL_REFRESH_INTERVAL}.[/dim]")

    # Post-cluster annotation pass (runs after all articles are clustered so
    # chronological ordering can't cause misses):
    # 1. Mark clusters that contain an ICS advisory member as is_advisory=True.
    # 2. Mark clusters whose cve_ids overlap any KEV catalog article as cisa_kev=True.
    if args.reset or not args.source:
        await _post_annotate(client)

    # Count cve_topics created during this rebuild
    cve_topic_count = 0
    try:
        resp = await _os_search(client, INDEX_CVE_TOPICS, {"query": {"match_all": {}}, "size": 0})
        cve_topic_count = resp["hits"]["total"]["value"]
    except Exception:
        pass

    table = Table(title="Clustering Complete", show_header=True, header_style="bold magenta")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="bold")
    table.add_row("Processed", str(totals["processed"]))
    table.add_row("Skipped (already clustered)", str(totals["skipped"]))
    table.add_row("Errors", str(totals["errors"]), style="red" if totals["errors"] else "")
    table.add_row("CVE topics in index", str(cve_topic_count))
    console.print(table)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("app.ingestion.embedding_client").setLevel(logging.ERROR)

    parser = argparse.ArgumentParser(description="Backfill clusters for existing articles")
    parser.add_argument("--source", type=str, help="Filter by source name")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--batch-size", type=int, default=100, help="(unused, kept for compat)")
    parser.add_argument("--limit", type=int, default=0, help="Stop after N articles (0 = no limit)")
    parser.add_argument("--reset", action="store_true", help="Delete all clusters first, then re-cluster everything")
    parser.add_argument("--concurrency", type=int, default=_DEFAULT_CONCURRENCY,
                        help=f"Articles processed in parallel (default: {_DEFAULT_CONCURRENCY})")
    async def _run():
        try:
            await main(parser.parse_args())
        finally:
            from app.db.opensearch import close_os_client
            await close_os_client()

    asyncio.run(_run())
