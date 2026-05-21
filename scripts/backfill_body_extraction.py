#!/usr/bin/env python
"""One-shot backfill of article body extraction.

Usage:
    python scripts/backfill_body_extraction.py            # full backfill
    python scripts/backfill_body_extraction.py --pilot    # 3-5 articles per top-25 source
    python scripts/backfill_body_extraction.py --dry-run  # log what would happen, no writes

Idempotent: reruns pick up where left off (queries body_quality IS missing or "failed").
"""
import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select

from app.db.models.feed_source import FeedSource as FeedSourceModel
from app.db.opensearch import INDEX_NEWS, get_os_client
from app.db.session import AsyncSessionLocal
from app.ingestion.body_pipeline import maybe_extract_body
from opensearchpy.helpers import async_bulk

logger = logging.getLogger("backfill_body")

GLOBAL_CONCURRENCY = 10
PER_HOST_CONCURRENCY = 2
BULK_BATCH_SIZE = 50
PROGRESS_LOG_EVERY = 100

_RETRY_DELAYS = {1: timedelta(0), 2: timedelta(hours=1), 3: timedelta(hours=24)}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--pilot", action="store_true", help="3-5 articles per top-25 source")
    p.add_argument("--dry-run", action="store_true", help="log without writing")
    p.add_argument("--limit", type=int, default=None, help="cap total processed articles")
    p.add_argument("--reclean-rss", action="store_true",
                   help="re-process rss-full articles to strip raw HTML via Trafilatura")
    return p.parse_args()


def _is_retry_eligible(src: dict) -> bool:
    """Return True if this article should be (re-)attempted now."""
    body_quality = src.get("body_quality")
    if body_quality not in ("failed", "empty", None):
        return False  # already ok / weak — don't reprocess
    attempts = src.get("fetch_attempt_count") or 0
    if attempts == 0:
        return True
    if attempts >= 3:
        return False
    last_str = src.get("last_fetch_attempt_at")
    if not last_str:
        return True
    last = datetime.fromisoformat(last_str.replace("Z", "+00:00"))
    next_eligible = last + _RETRY_DELAYS[min(attempts + 1, 3)]
    return datetime.now(timezone.utc) >= next_eligible


class _PerHostSemaphore:
    """asyncio.Semaphore per hostname, lazily created."""

    def __init__(self, per_host: int):
        self._per_host = per_host
        self._locks: dict[str, asyncio.Semaphore] = {}

    def get(self, url: str) -> asyncio.Semaphore:
        host = urlparse(url).hostname or "unknown"
        if host not in self._locks:
            self._locks[host] = asyncio.Semaphore(self._per_host)
        return self._locks[host]


async def _process_one(
    hit: dict,
    sources_by_name: dict,
    global_sem: asyncio.Semaphore,
    host_sem: _PerHostSemaphore,
    force: bool = False,
) -> dict | None:
    """Run maybe_extract_body for a single article, return bulk-update doc or None."""
    src = hit["_source"]
    article_doc = {
        "slug": hit["_id"],
        "source_url": src.get("source_url", ""),
        "content_html": src.get("content_html", ""),
        "fetch_attempt_count": src.get("fetch_attempt_count") or 0,
    }
    source_dict = sources_by_name.get(src.get("source_name", ""), {})

    if not force and not _is_retry_eligible(src):
        return None

    async with global_sem, host_sem.get(article_doc["source_url"]):
        try:
            updates = await maybe_extract_body(article_doc, source_dict)
        except Exception as exc:
            logger.warning("extraction crashed for %s: %s", hit["_id"], exc)
            return None

    if not updates:
        return None
    return {
        "_op_type": "update",
        "_index": INDEX_NEWS,
        "_id": hit["_id"],
        "doc": updates,
    }


async def _flush_bulk(client, actions: list[dict]) -> None:
    success, errors = await async_bulk(client, actions, raise_on_error=False)
    if errors:
        logger.warning("bulk write had %d errors", len(errors))


async def _load_sources() -> dict[str, dict]:
    """Return {source_name: {min_body_chars: int|None, ...}} from Postgres."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(FeedSourceModel))
        sources = result.scalars().all()
    return {s.name: {"min_body_chars": s.min_body_chars} for s in sources}


async def _eligible_articles(client, limit: int | None, reclean_rss: bool = False):
    """Yield batches of articles eligible for (re-)processing.

    Uses a single large fetch (no scroll) so the OpenSearch context can't
    expire while long-timeout fetches are in flight.
    """
    should = [
        {"bool": {"must_not": {"exists": {"field": "body_quality"}}}},
        {"term": {"body_quality": "failed"}},
        {"term": {"body_quality": "empty"}},
    ]
    if reclean_rss:
        should.append({"term": {"body_source": "rss-full"}})
    size = limit if (limit is not None and limit <= 10000) else 10000
    body = {
        "size": size,
        "_source": ["slug", "source_url", "source_name", "content_html",
                    "body_quality", "body_source", "fetch_attempt_count",
                    "last_fetch_attempt_at"],
        "query": {"bool": {"should": should, "minimum_should_match": 1}},
        "sort": [{"published_at": {"order": "desc"}}],
    }
    response = await client.search(index=INDEX_NEWS, body=body)
    hits = response["hits"]["hits"]
    if limit is not None:
        hits = hits[:limit]
    # Yield in batches of 50 for progress logging granularity
    for i in range(0, len(hits), 50):
        yield hits[i:i + 50]


async def _pilot_articles(client, sources_by_name: dict):
    """Yield ~3-5 articles per top-25 source, single batch."""
    body = {
        "size": 0,
        "aggs": {
            "by_source": {
                "terms": {"field": "source_name", "size": 25},
                "aggs": {
                    "samples": {
                        "top_hits": {
                            "size": 5,
                            "_source": ["slug", "source_url", "source_name",
                                        "content_html", "body_quality",
                                        "fetch_attempt_count", "last_fetch_attempt_at"],
                            "sort": [{"published_at": {"order": "desc"}}],
                        }
                    }
                },
            }
        },
    }
    response = await client.search(index=INDEX_NEWS, body=body)
    hits = []
    for bucket in response["aggregations"]["by_source"]["buckets"]:
        for hit in bucket["samples"]["hits"]["hits"]:
            hits.append(hit)
    yield hits


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args()
    logger.info("backfill starting | pilot=%s dry_run=%s reclean_rss=%s limit=%s",
                args.pilot, args.dry_run, args.reclean_rss, args.limit)

    client = get_os_client()
    sources_by_name = await _load_sources()

    if args.pilot:
        article_iter = _pilot_articles(client, sources_by_name)
    else:
        article_iter = _eligible_articles(client, args.limit, reclean_rss=args.reclean_rss)

    processed = 0
    successes = 0
    failures = 0
    weak = 0
    started = time.time()

    global_sem = asyncio.Semaphore(GLOBAL_CONCURRENCY)
    host_sem = _PerHostSemaphore(PER_HOST_CONCURRENCY)
    pending_updates: list[dict] = []

    async for batch in article_iter:
        tasks = [
            _process_one(hit, sources_by_name, global_sem, host_sem, force=args.reclean_rss)
            for hit in batch
        ]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        for r in results:
            if r is None:
                continue
            doc = r["doc"]
            quality = doc.get("body_quality")
            if quality == "ok":
                successes += 1
            elif quality == "weak":
                weak += 1
            else:
                failures += 1

            if args.dry_run:
                logger.info("[DRY RUN] would update %s body_quality=%s", r["_id"], quality)
            else:
                pending_updates.append(r)

        processed += len(batch)

        if not args.dry_run and len(pending_updates) >= BULK_BATCH_SIZE:
            await _flush_bulk(client, pending_updates)
            pending_updates.clear()

        if processed % PROGRESS_LOG_EVERY < len(batch):
            elapsed = time.time() - started
            rate = processed / max(elapsed / 60, 0.001)
            logger.info(
                "PROGRESS %d processed | ok=%d weak=%d failed=%d | %.1f articles/min",
                processed, successes, weak, failures, rate,
            )

    if not args.dry_run and pending_updates:
        await _flush_bulk(client, pending_updates)

    elapsed = time.time() - started
    logger.info(
        "backfill done | processed=%d ok=%d weak=%d failed=%d elapsed=%.1fs",
        processed, successes, weak, failures, elapsed,
    )


if __name__ == "__main__":
    asyncio.run(main())
