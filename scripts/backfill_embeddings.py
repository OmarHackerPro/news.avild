#!/usr/bin/env python
"""Backfill article_embedding for existing articles that don't have one yet.

Usage:
    python scripts/backfill_embeddings.py
    python scripts/backfill_embeddings.py --source "Krebs on Security"
    python scripts/backfill_embeddings.py --dry-run
    python scripts/backfill_embeddings.py --limit 500
    python scripts/backfill_embeddings.py --batch-size 32
"""
import asyncio
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.db.opensearch import INDEX_NEWS, INDEX_ENTITIES, get_os_client
from app.ingestion.embedding_input import embed_article

logger = logging.getLogger(__name__)


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
    """Return {slug: [normalized_key, ...]} for the given article slugs."""
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


async def _update_embedding(slug: str, embedding: list[float]) -> None:
    client = get_os_client()
    await client.update(
        index=INDEX_NEWS,
        id=slug,
        body={"doc": {"article_embedding": embedding}},
    )


async def main(args: argparse.Namespace) -> None:
    articles = await _scroll_articles(source=args.source, limit=args.limit, force=args.force)
    logger.info("Found %d unembedded article(s) to process.", len(articles))

    totals = {"total": len(articles), "embedded": 0, "skipped": 0, "errors": 0}
    batch_size = args.batch_size

    for batch_start in range(0, len(articles), batch_size):
        batch = articles[batch_start : batch_start + batch_size]
        slugs = [hit["_id"] for hit in batch]
        entity_keys = await _entity_keys_for(slugs)

        if args.dry_run:
            for i, hit in enumerate(batch):
                logger.info(
                    "[DRY RUN] %d/%d %s — entities=%d",
                    batch_start + i + 1, len(articles), hit["_id"],
                    len(entity_keys.get(hit["_id"], [])),
                )
            totals["embedded"] += len(batch)
            continue

        async def _embed_one(hit: dict) -> tuple[str, list[float] | None]:
            slug = hit["_id"]
            vec = await embed_article(hit["_source"], entity_keys.get(slug, []))
            return slug, vec

        embedded_pairs = await asyncio.gather(*[_embed_one(h) for h in batch])

        async def _update_one(slug: str, embedding: list[float] | None) -> str:
            if embedding is None:
                return "skipped"
            try:
                await _update_embedding(slug, embedding)
                return "ok"
            except Exception:
                logger.exception("Update failed for %s", slug)
                return "error"

        results = await asyncio.gather(
            *[_update_one(slug, emb) for slug, emb in embedded_pairs]
        )

        for outcome in results:
            if outcome == "ok":
                totals["embedded"] += 1
            elif outcome == "skipped":
                totals["skipped"] += 1
            else:
                totals["errors"] += 1

        processed_so_far = batch_start + len(batch)
        logger.info(
            "Progress: %d/%d — embedded=%d skipped=%d errors=%d",
            processed_so_far, len(articles),
            totals["embedded"], totals["skipped"], totals["errors"],
        )

    logger.info(
        "=== Done: total=%d embedded=%d skipped=%d errors=%d ===",
        totals["total"], totals["embedded"], totals["skipped"], totals["errors"],
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Backfill article_embedding for existing articles")
    parser.add_argument("--source", type=str, help="Filter by source name")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--batch-size", type=int, default=64, help="Articles per embedding batch (max 256)")
    parser.add_argument("--limit", type=int, default=None, help="Only process first N unembedded articles")
    parser.add_argument("--force", action="store_true", help="Re-embed articles even if they already have an embedding")
    asyncio.run(main(parser.parse_args()))
