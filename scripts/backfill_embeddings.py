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

from app.db.opensearch import INDEX_NEWS, get_os_client
from app.ingestion.embedding_client import embed_batch

logger = logging.getLogger(__name__)

_EMBED_INPUT_MAX = 400


def _build_embed_input(article: dict) -> str:
    text = article.get("title", "")
    snippet = article.get("summary") or article.get("desc") or ""
    if snippet:
        text += ". " + snippet[:_EMBED_INPUT_MAX]
    return text


async def _scroll_unembedded(source: str | None, limit: int | None) -> list[dict]:
    client = get_os_client()

    query: dict = {
        "bool": {
            "must_not": {"exists": {"field": "article_embedding"}},
        }
    }
    if source:
        query["bool"]["filter"] = [{"term": {"source_name": source}}]

    results = []
    page_size = 100
    from_offset = 0

    while True:
        remaining = (limit - len(results)) if limit is not None else page_size
        fetch_size = min(page_size, remaining) if limit is not None else page_size

        resp = await client.search(
            index=INDEX_NEWS,
            body={
                "query": query,
                "sort": [{"published_at": {"order": "asc"}}],
                "size": fetch_size,
                "from": from_offset,
                "_source": ["slug", "title", "summary", "desc", "source_name"],
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


async def _update_embedding(slug: str, embedding: list[float]) -> None:
    client = get_os_client()
    await client.update(
        index=INDEX_NEWS,
        id=slug,
        body={"doc": {"article_embedding": embedding}},
    )


async def main(args: argparse.Namespace) -> None:
    articles = await _scroll_unembedded(source=args.source, limit=args.limit)
    logger.info("Found %d unembedded article(s) to process.", len(articles))

    totals = {"total": len(articles), "embedded": 0, "skipped": 0, "errors": 0}
    batch_size = args.batch_size

    for batch_start in range(0, len(articles), batch_size):
        batch = articles[batch_start : batch_start + batch_size]
        slugs = [hit["_id"] for hit in batch]
        texts = [_build_embed_input(hit["_source"]) for hit in batch]

        if args.dry_run:
            for i, (slug, text) in enumerate(zip(slugs, texts)):
                logger.info(
                    "[DRY RUN] %d/%d %s — input: %.80s…",
                    batch_start + i + 1, len(articles), slug, text,
                )
            totals["embedded"] += len(batch)
            continue

        embeddings = await embed_batch(texts)

        async def _update_one(slug: str, embedding: list[float] | None, idx: int) -> str:
            if embedding is None:
                return "skipped"
            try:
                await _update_embedding(slug, embedding)
                return "ok"
            except Exception:
                logger.exception("Update failed for %s", slug)
                return "error"

        results = await asyncio.gather(
            *[_update_one(slug, emb, i) for i, (slug, emb) in enumerate(zip(slugs, embeddings))]
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
    asyncio.run(main(parser.parse_args()))
