#!/usr/bin/env python
"""RSS ingestion script — run from the project root:

    python scripts/ingest_feeds.py

Fetches all configured RSS feeds, normalizes entries, and upserts them
into the news_articles table. Safe to re-run: duplicate slugs are silently
skipped via ON CONFLICT DO NOTHING.
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Ensure the project root is importable regardless of working directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load .env before importing anything from app.* so DATABASE_URL is set
from dotenv import load_dotenv
load_dotenv()

from app.ingestion.ingester import ingest_all_feeds


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Silence verbose httpx connection logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RSS feed ingestion")
    parser.add_argument(
        "--update", action="store_true",
        help="Overwrite existing articles (reparse mode) instead of skipping duplicates",
    )
    args = parser.parse_args()

    _configure_logging()
    log = logging.getLogger(__name__)
    mode = "update/reparse" if args.update else "normal"
    log.info("RSS ingestion starting (mode=%s).", mode)
    asyncio.run(ingest_all_feeds(update=args.update))
    log.info("RSS ingestion complete.")
