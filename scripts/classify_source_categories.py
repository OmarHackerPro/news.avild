#!/usr/bin/env python
"""Classify RSS category labels for all active sources via local LLM.

For each source, collects distinct category labels seen in raw_feed_snapshots
(last 30 days), classifies them via the LLM, and upserts into source_categories.

Usage:
    python scripts/classify_source_categories.py
    python scripts/classify_source_categories.py --source "CISA Advisories"
    python scripts/classify_source_categories.py --dry-run

Requires Ollama running locally. Set OLLAMA_URL and OLLAMA_MODEL env vars
to override defaults (http://localhost:11434, llama3).
"""
import asyncio
import argparse
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models.feed_source import FeedSource as FeedSourceModel
from app.db.models.source_category import SourceCategory
from app.db.opensearch import INDEX_SNAPSHOTS, get_os_client
from app.db.session import AsyncSessionLocal
from app.ingestion.category_classifier import classify_categories

logger = logging.getLogger(__name__)


async def _collect_categories_from_snapshots(source_name: str, days: int = 30) -> list[str]:
    """Collect distinct RSS category labels from stored raw snapshots.

    Parses raw feed XML from OpenSearch snapshots to extract <category> tags.
    Falls back to empty list if no snapshots exist for the source.
    """
    import feedparser

    client = get_os_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    resp = await client.search(
        index=INDEX_SNAPSHOTS,
        body={
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"source_name": source_name}},
                        {"range": {"fetched_at": {"gte": cutoff}}},
                    ]
                }
            },
            "sort": [{"fetched_at": {"order": "desc"}}],
            "size": 5,
            "_source": ["raw_content"],
        },
    )

    category_set: set[str] = set()
    for hit in resp["hits"]["hits"]:
        raw = hit["_source"].get("raw_content", "")
        if not raw:
            continue
        feed = feedparser.parse(raw)
        for entry in feed.get("entries", []):
            for tag in entry.get("tags", []):
                term = (tag.get("term") or "").strip()
                if term:
                    category_set.add(term)

    return sorted(category_set)


async def classify_source(
    source: FeedSourceModel,
    dry_run: bool,
    session,
) -> int:
    """Classify categories for one source. Returns number of decisions written."""
    labels = await _collect_categories_from_snapshots(source.name)

    if not labels:
        logger.info("[%s] No category labels found in snapshots — skipping", source.name)
        return 0

    logger.info("[%s] Classifying %d labels: %s", source.name, len(labels), labels[:10])

    decisions = await classify_categories(source.name, labels)

    if dry_run:
        for d in decisions:
            logger.info(
                "[%s] DRY-RUN: label=%r ingest=%s modifier=%.1f notes=%s",
                source.name, d.label, d.ingest, d.priority_modifier, d.notes,
            )
        return len(decisions)

    # Upsert into source_categories
    for decision in decisions:
        stmt = pg_insert(SourceCategory).values(
            source_id=source.id,
            category_label=decision.label,
            ingest=decision.ingest,
            priority_modifier=decision.priority_modifier,
            classified_by="llm",
            classification_notes=decision.notes,
        ).on_conflict_do_update(
            constraint="uq_source_categories",
            set_={
                "ingest": decision.ingest,
                "priority_modifier": decision.priority_modifier,
                "classified_by": "llm",
                "classification_notes": decision.notes,
            },
        )
        await session.execute(stmt)

    await session.commit()
    logger.info("[%s] Wrote %d category decisions", source.name, len(decisions))
    return len(decisions)


async def run(source_filter: str | None, dry_run: bool) -> None:
    async with AsyncSessionLocal() as session:
        query = select(FeedSourceModel).where(FeedSourceModel.is_active.is_(True))
        if source_filter:
            query = query.where(FeedSourceModel.name == source_filter)
        result = await session.execute(query)
        sources = list(result.scalars().all())

    if not sources:
        logger.warning("No matching active sources found.")
        return

    logger.info("Classifying categories for %d source(s)...", len(sources))
    total = 0
    for source in sources:
        async with AsyncSessionLocal() as session:
            count = await classify_source(source, dry_run=dry_run, session=session)
            total += count

    logger.info("Done. Total decisions: %d", total)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Classify source category labels via LLM")
    parser.add_argument("--source", help="Process only this source name")
    parser.add_argument("--dry-run", action="store_true", help="Print decisions without writing")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
    )

    asyncio.run(run(args.source, args.dry_run))
