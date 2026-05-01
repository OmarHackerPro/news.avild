#!/usr/bin/env python
"""Diagnose why articles aren't clustering.

Checks:
  1. How many articles have cve_ids in the articles index vs entities index
  2. Groups of articles sharing the same CVE that ended up in different clusters
  3. Sample articles to show what signals are available for matching
"""
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.table import Table
from app.db.opensearch import INDEX_NEWS, INDEX_ENTITIES, INDEX_CLUSTERS, get_os_client

console = Console()


async def main():
    client = get_os_client()

    # --- 1. Sample articles: cve_ids in articles vs entities index ---
    console.rule("[bold]1. CVE coverage: news_articles vs entities index")

    resp = await client.search(index=INDEX_NEWS, body={
        "query": {"match_all": {}},
        "size": 500,
        "_source": ["slug", "cve_ids", "cluster_id", "title"],
        "sort": [{"published_at": {"order": "asc"}}],
    })
    articles = resp["hits"]["hits"]

    with_cves = [a for a in articles if a["_source"].get("cve_ids")]
    console.print(f"Articles sampled: {len(articles)}")
    console.print(f"Articles with cve_ids in news_articles index: {len(with_cves)}")

    # For each article with CVEs, check if those CVEs exist in entities index
    missing_entity = []
    for a in with_cves[:50]:  # check first 50 to avoid hammering OS
        slug = a["_id"]
        cves = a["_source"]["cve_ids"]
        ent_resp = await client.search(index=INDEX_ENTITIES, body={
            "query": {"bool": {"filter": [
                {"term": {"article_ids": slug}},
                {"term": {"type": "cve"}},
            ]}},
            "size": 20,
            "_source": ["normalized_key"],
        })
        entity_cves = {h["_source"]["normalized_key"] for h in ent_resp["hits"]["hits"]}
        article_cves = set(cves)
        if not (article_cves & entity_cves):
            missing_entity.append({
                "slug": slug[:40],
                "title": a["_source"].get("title", "")[:50],
                "article_cves": list(article_cves)[:3],
                "entity_cves": list(entity_cves)[:3],
            })

    console.print(f"\nOf first 50 articles with CVEs: {len(missing_entity)} have NO matching CVE entities in the entities index")
    if missing_entity:
        console.print("[yellow]These articles have CVEs in news_articles but not in entity index → structured lookup finds nothing:[/yellow]")
        for m in missing_entity[:10]:
            console.print(f"  {m['slug']}...")
            console.print(f"    article cve_ids: {m['article_cves']}")
            console.print(f"    entity index CVEs: {m['entity_cves'] or '(none)'}")

    # --- 2. Articles sharing same CVE in different clusters ---
    console.rule("[bold]2. Articles sharing CVEs that ended up in different clusters")

    cve_to_articles: dict[str, list[dict]] = defaultdict(list)
    for a in articles:
        for cve in (a["_source"].get("cve_ids") or []):
            cve_to_articles[cve].append({
                "slug": a["_id"],
                "cluster_id": a["_source"].get("cluster_id"),
                "title": a["_source"].get("title", "")[:60],
            })

    split_groups = [
        (cve, arts)
        for cve, arts in cve_to_articles.items()
        if len(arts) >= 2 and len({a["cluster_id"] for a in arts}) > 1
    ]
    console.print(f"CVEs with 2+ articles that ended up in DIFFERENT clusters: {len(split_groups)}")

    if split_groups:
        table = Table(title="Split clusters (same CVE, different cluster)", show_lines=True)
        table.add_column("CVE", style="cyan")
        table.add_column("Title", style="white")
        table.add_column("Cluster ID", style="yellow")
        for cve, arts in split_groups[:10]:
            for i, a in enumerate(arts[:4]):
                table.add_row(
                    cve if i == 0 else "",
                    a["title"],
                    (a["cluster_id"] or "none")[:20],
                )
        console.print(table)
    else:
        console.print("[green]No split groups found (either CVEs matched correctly, or no shared CVEs in sample).[/green]")

    # --- 3. Entity index coverage summary ---
    console.rule("[bold]3. Entity index coverage for sampled articles")

    slugs = [a["_id"] for a in articles[:200]]
    ent_resp = await client.search(index=INDEX_ENTITIES, body={
        "query": {"terms": {"article_ids": slugs}},
        "size": 0,
        "aggs": {
            "by_type": {"terms": {"field": "type", "size": 20}},
            "articles_with_entities": {"cardinality": {"field": "article_ids"}},
        }
    })
    aggs = ent_resp["aggregations"]
    console.print(f"Unique articles in entity index (from sample): ~{aggs['articles_with_entities']['value']}")
    console.print("Entity type breakdown:")
    for b in aggs["by_type"]["buckets"]:
        console.print(f"  {b['key']}: {b['doc_count']} entities")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
