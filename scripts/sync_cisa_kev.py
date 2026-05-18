#!/usr/bin/env python
"""Sync CISA Known Exploited Vulnerabilities into cisa_kev + entity_intel tables.

Usage:
    python scripts/sync_cisa_kev.py
    python scripts/sync_cisa_kev.py --dry-run
"""
import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

KEV_URL = (
    "https://raw.githubusercontent.com/cisagov/kev-data/develop/"
    "known_exploited_vulnerabilities.json"
)

_LEGAL_SUFFIX_RE = re.compile(
    r"\s+(corp\.?|inc\.?|ltd\.?|llc\.?|gmbh|co\.|limited|incorporated)\s*$",
    re.IGNORECASE,
)
_PARENTHETICAL_RE = re.compile(r"\s*\(.*?\)\s*$")


def _normalize_vendor(raw: str) -> str:
    """Strip legal suffixes and parentheticals from a KEV vendorProject string."""
    name = raw.strip()
    name = _PARENTHETICAL_RE.sub("", name)
    name = _LEGAL_SUFFIX_RE.sub("", name)
    return name.strip()


def _parse_kev(data: dict) -> list[dict]:
    """Parse KEV JSON into a list of row dicts ready for DB insert."""
    rows = []
    for v in data.get("vulnerabilities", []):
        vendor = _normalize_vendor(v.get("vendorProject", ""))
        if not vendor or len(vendor) < 2:
            continue
        try:
            raw_date_added = v.get("dateAdded")
            raw_due_date = v.get("dueDate") or None
            date_added = date.fromisoformat(raw_date_added) if raw_date_added else None
            due_date = date.fromisoformat(raw_due_date) if raw_due_date else None
        except (ValueError, TypeError):
            logger.warning("Skipping %s — invalid date fields", v.get("cveID", "unknown"))
            continue
        rows.append({
            "cve_id": v["cveID"],
            "vendor": vendor,
            "product": v.get("product", ""),
            "vulnerability_name": v.get("vulnerabilityName", ""),
            "date_added": date_added,
            "due_date": due_date,
            "known_ransomware_use": v.get("knownRansomwareCampaignUse", "Unknown") == "Known",
            "cwes": v.get("cwes") or [],
        })
    return rows


async def _sync_to_db(rows: list[dict]) -> tuple[int, int, int]:
    """Upsert KEV rows and vendor entries. Returns (kev_inserted, kev_updated, vendors_upserted)."""
    from app.db.session import AsyncSessionLocal
    from sqlalchemy import text

    now = datetime.now(timezone.utc)
    kev_ins = kev_upd = vendor_ups = 0

    # Collect unique normalized vendor display names (keyed by normalized slug)
    unique_vendors: dict[str, str] = {}  # normalized_key -> display_name
    for row in rows:
        key = re.sub(r"[^a-z0-9]+", "-", row["vendor"].lower()).strip("-")
        if key and key not in unique_vendors:
            unique_vendors[key] = row["vendor"]

    async with AsyncSessionLocal() as db:
        # Upsert cisa_kev rows
        for row in rows:
            existing = (await db.execute(
                text("SELECT 1 FROM cisa_kev WHERE cve_id = :cve_id"),
                {"cve_id": row["cve_id"]},
            )).fetchone()

            if existing is None:
                await db.execute(
                    text("""
                        INSERT INTO cisa_kev
                            (cve_id, vendor, product, vulnerability_name,
                             date_added, due_date, known_ransomware_use, cwes, last_synced)
                        VALUES
                            (:cve_id, :vendor, :product, :vulnerability_name,
                             :date_added, :due_date, :known_ransomware_use,
                             CAST(:cwes AS jsonb), :now)
                    """),
                    {**row, "cwes": json.dumps(row["cwes"]), "now": now},
                )
                kev_ins += 1
            else:
                await db.execute(
                    text("""
                        UPDATE cisa_kev SET
                            vendor = :vendor, product = :product,
                            vulnerability_name = :vulnerability_name,
                            known_ransomware_use = :known_ransomware_use,
                            cwes = CAST(:cwes AS jsonb), last_synced = :now
                        WHERE cve_id = :cve_id
                    """),
                    {**row, "cwes": json.dumps(row["cwes"]), "now": now},
                )
                kev_upd += 1

        # Upsert vendor entries in entity_intel (cisa_kev is lowest priority)
        for norm_key, display_name in unique_vendors.items():
            existing = (await db.execute(
                text("SELECT source FROM entity_intel WHERE normalized_key = :key"),
                {"key": norm_key},
            )).fetchone()

            if existing is None:
                await db.execute(
                    text("""
                        INSERT INTO entity_intel
                            (normalized_key, display_name, entity_type, aliases,
                             source, active, last_synced)
                        VALUES
                            (:key, :name, 'vendor', CAST(:aliases AS jsonb),
                             'cisa_kev', true, :now)
                    """),
                    {"key": norm_key, "name": display_name,
                     "aliases": json.dumps([display_name]), "now": now},
                )
                vendor_ups += 1
            else:
                # cisa_kev is lowest priority -- only update last_synced
                await db.execute(
                    text("UPDATE entity_intel SET last_synced = :now WHERE normalized_key = :key"),
                    {"key": norm_key, "now": now},
                )

        await db.commit()

    return kev_ins, kev_upd, vendor_ups


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Sync CISA KEV to DB")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--url", default=KEV_URL)
    args = parser.parse_args(argv)

    logger.info("Fetching KEV from %s ...", args.url)
    resp = requests.get(args.url, timeout=60)
    resp.raise_for_status()
    rows = _parse_kev(resp.json())
    logger.info("Parsed %d KEV entries", len(rows))

    if args.dry_run:
        vendors = {r["vendor"] for r in rows}
        logger.info("--dry-run: %d unique vendors, %d CVEs", len(vendors), len(rows))
        return

    kev_ins, kev_upd, vendor_ups = asyncio.run(_sync_to_db(rows))
    logger.info("cisa_kev: %d inserted, %d updated", kev_ins, kev_upd)
    logger.info("entity_intel vendors: %d upserted", vendor_ups)


if __name__ == "__main__":
    main()
