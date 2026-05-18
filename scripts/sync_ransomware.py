#!/usr/bin/env python
"""Sync ransomware groups from ransomware.live into entity_intel.

Usage:
    python scripts/sync_ransomware.py
    python scripts/sync_ransomware.py --dry-run

Requires RANSOMWARE_LIVE_API_KEY env var (free Pro key from ransomware.live).
If the key is absent, tries unauthenticated (may be rate-limited).
"""
import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

API_URL = "https://api.ransomware.live/v2/groups"

_SOURCE = "ransomware.live"
_SOURCE_PRIORITY = {"attack": 0, "ransomware.live": 1, "cisa_kev": 2, "manual": 3}


def _normalize_key(name: str) -> str:
    key = name.lower()
    key = re.sub(r"[^a-z0-9]+", "-", key)
    return key.strip("-")


def _parse_groups(raw: list[dict]) -> list[dict]:
    """Parse ransomware.live group list into entity_intel row dicts."""
    seen: dict[str, dict] = {}
    for g in raw:
        name = (g.get("name") or "").strip()
        if not name:
            continue
        key = _normalize_key(name)
        if not key:
            continue
        aliases = [
            a.strip()
            for a in (g.get("aliases") or [])
            if a.strip() and a.strip() != name
        ]
        seen[key] = {
            "normalized_key": key,
            "display_name": name,
            "entity_type": "actor",
            "aliases": aliases,
            "source": _SOURCE,
            "active": (g.get("status") or "").lower() == "active",
        }
    return list(seen.values())


async def _sync_to_db(rows: list[dict]) -> tuple[int, int]:
    """Upsert rows into entity_intel. Returns (inserted, updated)."""
    from app.db.session import AsyncSessionLocal
    from sqlalchemy import text

    now = datetime.now(timezone.utc)
    ins = upd = 0

    async with AsyncSessionLocal() as db:
        for row in rows:
            existing = (await db.execute(
                text("SELECT source FROM entity_intel WHERE normalized_key = :key"),
                {"key": row["normalized_key"]},
            )).fetchone()

            if existing is None:
                await db.execute(
                    text("""
                        INSERT INTO entity_intel
                            (normalized_key, display_name, entity_type, aliases,
                             source, active, last_synced)
                        VALUES
                            (:key, :name, :etype, CAST(:aliases AS jsonb),
                             :source, :active, :now)
                    """),
                    {
                        "key": row["normalized_key"],
                        "name": row["display_name"],
                        "etype": row["entity_type"],
                        "aliases": json.dumps(row["aliases"]),
                        "source": row["source"],
                        "active": row["active"],
                        "now": now,
                    },
                )
                ins += 1
            else:
                existing_source = existing[0]
                if _SOURCE_PRIORITY.get(existing_source, 99) >= _SOURCE_PRIORITY[_SOURCE]:
                    # Existing row is same or lower priority — ransomware.live can update active/last_synced
                    await db.execute(
                        text("""
                            UPDATE entity_intel SET
                                active = :active,
                                last_synced = :now
                            WHERE normalized_key = :key
                        """),
                        {"key": row["normalized_key"], "active": row["active"], "now": now},
                    )
                    upd += 1
                # else: existing row is higher priority (attack) — only last_synced
                else:
                    await db.execute(
                        text("UPDATE entity_intel SET last_synced = :now WHERE normalized_key = :key"),
                        {"key": row["normalized_key"], "now": now},
                    )

        await db.commit()

    return ins, upd


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Sync ransomware.live groups to entity_intel")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    api_key = os.environ.get("RANSOMWARE_LIVE_API_KEY", "")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    if not api_key:
        logger.warning("RANSOMWARE_LIVE_API_KEY not set — trying unauthenticated (may be rate-limited)")

    logger.info("Fetching groups from ransomware.live ...")
    try:
        resp = requests.get(API_URL, headers=headers, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        logger.error("Failed to fetch ransomware groups: %s", exc)
        raise SystemExit(1)

    rows = _parse_groups(resp.json())
    logger.info("Parsed %d groups", len(rows))

    if args.dry_run:
        active = sum(1 for r in rows if r["active"])
        logger.info("--dry-run: %d active, %d inactive", active, len(rows) - active)
        return

    ins, upd = asyncio.run(_sync_to_db(rows))
    logger.info("entity_intel: %d inserted, %d updated", ins, upd)


if __name__ == "__main__":
    main()
