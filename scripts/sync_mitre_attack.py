#!/usr/bin/env python
"""Sync MITRE ATT&CK into the entity_intel Postgres table.

Downloads the enterprise-attack and ics-attack STIX bundles from the
mitre-attack/attack-stix-data GitHub repo, extracts groups (actors), malware,
tools, and campaigns with their aliases, and upserts them into entity_intel.

Usage:
    python scripts/sync_mitre_attack.py             # upsert to DB
    python scripts/sync_mitre_attack.py --dry-run   # print stats, no writes
    python scripts/sync_mitre_attack.py --json path/to/out.json  # also dump JSON
"""
import argparse
import asyncio
import json
import logging
import re
from collections import Counter
from datetime import datetime as _datetime, timezone as _timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

ENTERPRISE_URL = (
    "https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master/"
    "enterprise-attack/enterprise-attack.json"
)
ICS_URL = (
    "https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master/"
    "ics-attack/ics-attack.json"
)
# Keep for backward-compat with any callers passing --url
STIX_URL = ENTERPRISE_URL


# Display names that would produce constant false positives because they are common
# English words, standard protocol names, or OS built-in commands that appear in
# nearly every security article without indicating a specific threat.
# "at"  — English preposition: \bat\b fires on every sentence
# "net" — Windows net.exe: \bnet\b fires on .NET Framework, internet references
# "ftp" — File Transfer Protocol: appears in URLs and generic protocol descriptions
# "cmd" — Windows cmd.exe: \bcmd\b fires on generic "run cmd", "via cmd" phrasing
_SKIP_NAMES: frozenset[str] = frozenset({"at", "net", "ftp", "cmd"})

_STIX_TYPE_MAP = {
    "intrusion-set": "actor",
    "malware": "malware",
    "tool": "tool",
    "campaign": "campaign",
}

# For these types the primary name appears first in the aliases list — skip it.
_ALIASES_INCLUDE_PRIMARY = {"intrusion-set", "campaign"}


def _normalize_key(name: str) -> str:
    key = name.lower()
    key = re.sub(r"[^a-z0-9]+", "-", key)
    return key.strip("-")


def _parse_stix_bundle(stix: dict) -> tuple[dict, dict, dict]:
    """Parse a STIX 2.0 bundle dict into (keywords, aliases, source_ids) dicts.

    keywords:   {normalized_key: [display_name, entity_type]}
    aliases:    {alias_display_text: canonical_normalized_key}
    source_ids: {normalized_key: external_id}  e.g. {"apt29": "G0016"}
    """
    keywords: dict[str, list] = {}
    source_ids: dict[str, str] = {}
    # Collect raw alias pairs before deduplication
    raw_aliases: list[tuple[str, str]] = []

    # Pass 1: collect all canonical keyword entries
    for obj in stix.get("objects", []):
        if obj.get("revoked") or obj.get("x_mitre_deprecated"):
            continue

        obj_type = obj.get("type")
        if obj_type not in _STIX_TYPE_MAP:
            continue

        name = obj.get("name", "").strip()
        if not name or name.lower() in _SKIP_NAMES:
            continue

        etype = _STIX_TYPE_MAP[obj_type]
        canonical_key = _normalize_key(name)
        keywords[canonical_key] = [name, etype]

        # Extract ATT&CK external ID (e.g. G0016, S0002) from external_references
        for ref in obj.get("external_references", []):
            if ref.get("source_name") == "mitre-attack":
                ext_id = ref.get("external_id")
                if ext_id:
                    source_ids[canonical_key] = ext_id
                break

        # Collect aliases — field name and primary-skip logic differ by type
        if obj_type in _ALIASES_INCLUDE_PRIMARY:
            raw = obj.get("aliases") or []
            # First element is the primary name — skip it
            obj_aliases = [a.strip() for a in raw if a.strip() != name]
        else:
            raw = obj.get("x_mitre_aliases") or obj.get("aliases") or []
            obj_aliases = [a.strip() for a in raw if a.strip()]

        for alias in obj_aliases:
            if alias and alias != name:
                raw_aliases.append((alias, canonical_key))

    # Pass 2: add aliases only when their normalized form is not already a keyword
    aliases: dict[str, str] = {}
    for alias, canonical_key in raw_aliases:
        alias_normalized = _normalize_key(alias)
        if alias_normalized not in keywords:  # skip — text already covered by keyword entry
            aliases[alias] = canonical_key

    return keywords, aliases, source_ids


_SOURCE_PRIORITY = {"attack": 0, "ransomware.live": 1, "cisa_kev": 2, "manual": 3}


async def _upsert_to_db(
    keywords: dict, aliases: dict, source_ids: dict | None = None
) -> tuple[int, int]:
    """Upsert parsed ATT&CK data into entity_intel. Returns (inserted, updated).

    source_ids: {normalized_key: external_id} e.g. {"apt29": "G0016"}.
    """
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

    from app.db.session import AsyncSessionLocal
    from sqlalchemy import text

    if source_ids is None:
        source_ids = {}

    inserted = updated = 0
    now = _datetime.now(_timezone.utc)

    # Build key → [alias display texts] mapping from the aliases dict
    alias_map: dict[str, list[str]] = {key: [] for key in keywords}
    for display_text, canonical_key in aliases.items():
        if canonical_key in alias_map:
            alias_map[canonical_key].append(display_text)

    async with AsyncSessionLocal() as db:
        for key, entry in keywords.items():
            display_name, entity_type = entry[0], entry[1]
            alias_list = alias_map.get(key, [])
            source_id = source_ids.get(key)

            row = await db.execute(
                text("SELECT source FROM entity_intel WHERE normalized_key = :key"),
                {"key": key},
            )
            existing = row.fetchone()

            if existing is None:
                await db.execute(
                    text("""
                        INSERT INTO entity_intel
                            (normalized_key, display_name, entity_type, aliases, source, source_id, active, last_synced)
                        VALUES
                            (:key, :name, :etype, CAST(:aliases AS jsonb), 'attack', :source_id, true, :now)
                    """),
                    {"key": key, "name": display_name, "etype": entity_type,
                     "aliases": json.dumps(alias_list), "source_id": source_id, "now": now},
                )
                inserted += 1
            else:
                existing_source = existing[0]
                if _SOURCE_PRIORITY.get(existing_source, 99) >= _SOURCE_PRIORITY["attack"]:
                    await db.execute(
                        text("""
                            UPDATE entity_intel
                            SET display_name = :name,
                                entity_type  = :etype,
                                aliases      = CAST(:aliases AS jsonb),
                                source       = 'attack',
                                source_id    = :source_id,
                                last_synced  = :now
                            WHERE normalized_key = :key
                        """),
                        {"key": key, "name": display_name, "etype": entity_type,
                         "aliases": json.dumps(alias_list), "source_id": source_id, "now": now},
                    )
                    updated += 1
        await db.commit()

    return inserted, updated


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Sync MITRE ATT&CK into entity_intel")
    parser.add_argument("--dry-run", action="store_true", help="Print stats without writing")
    parser.add_argument("--json", type=Path, default=None, metavar="PATH",
                        help="Also dump parsed data to a JSON file")
    args = parser.parse_args(argv)

    # --- Enterprise bundle ---
    logger.info("Downloading MITRE ATT&CK Enterprise STIX from %s ...", ENTERPRISE_URL)
    resp = requests.get(ENTERPRISE_URL, timeout=120)
    resp.raise_for_status()
    stix_enterprise = resp.json()
    logger.info("Downloaded %d STIX objects (enterprise).", len(stix_enterprise.get("objects", [])))
    keywords, aliases, source_ids = _parse_stix_bundle(stix_enterprise)

    # --- ICS bundle — extend, don't overwrite enterprise keys ---
    logger.info("Downloading MITRE ATT&CK ICS STIX from %s ...", ICS_URL)
    try:
        ics_resp = requests.get(ICS_URL, timeout=120)
        ics_resp.raise_for_status()
        stix_ics = ics_resp.json()
        logger.info("Downloaded %d STIX objects (ics).", len(stix_ics.get("objects", [])))
        ics_kw, ics_al, ics_si = _parse_stix_bundle(stix_ics)
        for k, v in ics_kw.items():
            keywords.setdefault(k, v)
        for k, v in ics_al.items():
            aliases.setdefault(k, v)
        for k, v in ics_si.items():
            source_ids.setdefault(k, v)
        logger.info("Fetched ICS-ATT&CK: %d additional entries", len(ics_kw))
    except Exception as exc:
        logger.warning("ICS-ATT&CK fetch failed (%s) — continuing with enterprise only", exc)

    type_counts = Counter(v[1] for v in keywords.values())
    logger.info("Merged %d keywords: %s", len(keywords), dict(type_counts))
    logger.info("Merged %d aliases.", len(aliases))

    if args.dry_run:
        logger.info("--dry-run: not writing.")
        return

    ins, upd = asyncio.run(_upsert_to_db(keywords, aliases, source_ids))
    logger.info("DB upsert: %d inserted, %d updated", ins, upd)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        with open(args.json, "w") as f:
            json.dump({"keywords": keywords, "aliases": aliases}, f, indent=2, sort_keys=True)
        logger.info("JSON also written to %s", args.json)


if __name__ == "__main__":
    main()
