#!/usr/bin/env python
"""Seed the feed_sources table from the bootstrap SEED_SOURCES list.

Usage:
    python scripts/seed_sources.py            # insert missing sources
    python scripts/seed_sources.py --dry-run  # preview only

Idempotent: uses ON CONFLICT (name) DO UPDATE so managed bootstrap fields
stay aligned without touching operational fields like fetch history.
"""
import asyncio
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models.feed_source import FeedSource
from app.db.session import AsyncSessionLocal
from app.ingestion.sources import SEED_SOURCES

logger = logging.getLogger(__name__)


async def seed_sources(*, dry_run: bool = False) -> None:
    if AsyncSessionLocal is None:
        logger.error("DATABASE_URL not configured.")
        return

    rows = [
        {
            "name": s["name"],
            "url": s["url"],
            "default_type": s["default_type"],
            "default_category": s["default_category"],
            "default_severity": s["default_severity"],
            "normalizer_key": s["normalizer"],
            "credibility_weight": s.get("credibility_weight", 1.0),
            "extract_cves": s.get("extract_cves", False),
            "extract_cvss": s.get("extract_cvss", False),
        }
        for s in SEED_SOURCES
    ]

    if dry_run:
        for row in rows:
            logger.info("[DRY RUN] Would seed: %s (%s)", row["name"], row["url"])
        return

    inserted = 0
    async with AsyncSessionLocal() as session:
        async with session.begin():
            for row in rows:
                stmt = (
                    pg_insert(FeedSource)
                    .values(**row)
                    .on_conflict_do_update(
                        index_elements=["name"],
                        set_={
                            "url": row["url"],
                            "default_type": row["default_type"],
                            "default_category": row["default_category"],
                            "default_severity": row["default_severity"],
                            "normalizer_key": row["normalizer_key"],
                            "credibility_weight": row["credibility_weight"],
                            "extract_cves": row["extract_cves"],
                            "extract_cvss": row["extract_cvss"],
                        },
                    )
                )
                result = await session.execute(stmt)
                if result.rowcount == 1:
                    logger.info("Upserted: %s", row["name"])
                    inserted += 1

    logger.info("Seeding complete: %d row(s) inserted or updated.", inserted)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Seed feed_sources from SEED_SOURCES")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    asyncio.run(seed_sources(dry_run=args.dry_run))
