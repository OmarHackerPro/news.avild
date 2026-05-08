#!/usr/bin/env python
"""Send the daily WhatsApp brief.

Usage:
    python scripts/send_daily_brief.py              # send today's brief
    python scripts/send_daily_brief.py --dry-run    # format only, no send, no DB write
    python scripts/send_daily_brief.py --force      # override idempotency check
    python scripts/send_daily_brief.py --top-n 5    # select top 5 clusters (default 7)
    python scripts/send_daily_brief.py --hours 48   # 48h look-back window (default 24)
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.briefing.pipeline import run_brief_pipeline
from app.db.opensearch import get_os_client
from app.db.session import AsyncSessionLocal

logger = logging.getLogger("send_daily_brief")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--top-n", type=int, default=7)
    p.add_argument("--hours", type=int, default=24)
    return p.parse_args()


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = parse_args()
    logger.info(
        "send_daily_brief starting | dry_run=%s force=%s top_n=%s hours=%s",
        args.dry_run, args.force, args.top_n, args.hours,
    )

    os_client = get_os_client()
    async with AsyncSessionLocal() as session:
        result = await run_brief_pipeline(
            os_client=os_client,
            db_session=session,
            dry_run=args.dry_run,
            force=args.force,
            top_n=args.top_n,
            hours=args.hours,
        )

    if result["skipped"]:
        logger.info("Brief skipped (already sent today)")
    elif result["dry_run"]:
        logger.info("[DRY RUN] Brief generated for %d clusters", result["cluster_count"])
    else:
        logger.info("Brief sent | clusters=%d", result["cluster_count"])


if __name__ == "__main__":
    asyncio.run(main())
