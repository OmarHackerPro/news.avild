#!/usr/bin/env python
"""Download MITRE ATT&CK STIX and generate data/threat_keywords.json.

Downloads the enterprise-attack STIX bundle from the mitre/cti GitHub repo,
extracts groups (actors), malware, tools, and campaigns with their aliases,
and writes data/threat_keywords.json for use by entity_extractor.py.

Usage:
    python scripts/sync_mitre_attack.py
    python scripts/sync_mitre_attack.py --output path/to/custom.json
    python scripts/sync_mitre_attack.py --dry-run
    python scripts/sync_mitre_attack.py --url <url>
    python scripts/sync_mitre_attack.py --db
"""
import argparse
import asyncio as _asyncio
import json
import logging
import re
from collections import Counter
from datetime import datetime as _datetime, timezone as _timezone
import json as _json
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

STIX_URL = (
    "https://raw.githubusercontent.com/mitre/cti/master/"
    "enterprise-attack/enterprise-attack.json"
)

_DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "threat_keywords.json"


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


def _parse_stix_bundle(stix: dict) -> tuple[dict, dict]:
    """Parse a STIX 2.0 bundle dict into (keywords, aliases) dicts.

    keywords: {normalized_key: [display_name, entity_type]}
    aliases:  {alias_display_text: canonical_normalized_key}
    """
    keywords: dict[str, list] = {}
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

    return keywords, aliases


_SOURCE_PRIORITY = {"attack": 0, "ransomware.live": 1, "cisa_kev": 2, "manual": 3}


async def _upsert_to_db(keywords: dict, aliases: dict) -> tuple[int, int]:
    """Upsert parsed ATT&CK data into entity_intel. Returns (inserted, updated)."""
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

    from app.db.session import AsyncSessionLocal
    from sqlalchemy import text

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

            row = await db.execute(
                text("SELECT source FROM entity_intel WHERE normalized_key = :key"),
                {"key": key},
            )
            existing = row.fetchone()

            if existing is None:
                await db.execute(
                    text("""
                        INSERT INTO entity_intel
                            (normalized_key, display_name, entity_type, aliases, source, active, last_synced)
                        VALUES
                            (:key, :name, :etype, CAST(:aliases AS jsonb), 'attack', true, :now)
                    """),
                    {"key": key, "name": display_name, "etype": entity_type,
                     "aliases": _json.dumps(alias_list), "now": now},
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
                                last_synced  = :now
                            WHERE normalized_key = :key
                        """),
                        {"key": key, "name": display_name, "etype": entity_type,
                         "aliases": _json.dumps(alias_list), "now": now},
                    )
                    updated += 1
        await db.commit()

    return inserted, updated


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Regenerate data/threat_keywords.json from MITRE ATT&CK"
    )
    parser.add_argument("--url", default=STIX_URL, help="STIX bundle URL")
    parser.add_argument("--dry-run", action="store_true", help="Print stats without writing")
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT, help="Output file path")
    parser.add_argument(
        "--db", action="store_true",
        help="Upsert into entity_intel PostgreSQL table (in addition to JSON output)",
    )
    args = parser.parse_args(argv)

    logger.info("Downloading MITRE ATT&CK STIX from %s ...", args.url)
    resp = requests.get(args.url, timeout=120)
    resp.raise_for_status()
    stix = resp.json()
    logger.info("Downloaded %d STIX objects.", len(stix.get("objects", [])))

    keywords, aliases = _parse_stix_bundle(stix)

    type_counts = Counter(v[1] for v in keywords.values())
    logger.info("Parsed %d keywords: %s", len(keywords), dict(type_counts))
    logger.info("Parsed %d aliases.", len(aliases))

    if args.dry_run:
        logger.info("--dry-run: not writing.")
        return

    data = {"keywords": keywords, "aliases": aliases}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    logger.info("Written to %s", args.output)

    if args.db:
        ins, upd = _asyncio.run(_upsert_to_db(keywords, aliases))
        logger.info("DB upsert: %d inserted, %d updated", ins, upd)


if __name__ == "__main__":
    main()
