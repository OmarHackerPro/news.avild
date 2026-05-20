"""Seed vuln_alias entries into entity_intel.

Union of:
1. All vuln_alias entities Haiku has stored in ner_cache (filtered to plausible values).
2. A hand-curated canonical list of famous named vulnerabilities.

Canonical entries take precedence over cache entries on display-name conflicts.
Uses ON CONFLICT (normalized_key) DO NOTHING for cache entries so existing
authoritative rows (e.g. from MITRE ATT&CK) are never overwritten.
Canonical entries use DO UPDATE to always win.
"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio
import json
import re
from datetime import datetime, timezone

from sqlalchemy import text

from app.db.session import AsyncSessionLocal

CANONICAL_VULN_ALIASES: dict[str, str] = {
    "log4shell": "Log4Shell",
    "printnightmare": "PrintNightmare",
    "heartbleed": "Heartbleed",
    "citrixbleed": "CitrixBleed",
    "citrixbleed-2": "CitrixBleed 2",
    "spectre": "Spectre",
    "meltdown": "Meltdown",
    "bluekeep": "BlueKeep",
    "eternalblue": "EternalBlue",
    "zerologon": "ZeroLogon",
    "proxylogon": "ProxyLogon",
    "proxyshell": "ProxyShell",
    "follina": "Follina",
    "moveit": "MOVEit",
    "shellshock": "Shellshock",
    "poodle": "POODLE",
    "krack": "KRACK",
    "freak": "FREAK",
    "logjam": "Logjam",
    "drown": "DROWN",
    "rowhammer": "Rowhammer",
    "downfall": "Downfall",
    "regresshion": "regreSSHion",
    "looney-tunables": "Looney Tunables",
    "dirty-pipe": "Dirty Pipe",
    "dirty-cow": "Dirty COW",
}

_TRIVIAL_TOKENS = {"the", "a", "an", "vulnerability", "vuln", "rce", "lpe", "exploit"}


def _plausible(name: str, key: str) -> bool:
    if not name or len(name) < 3:
        return False
    if name.lower().strip() in _TRIVIAL_TOKENS:
        return False
    if re.match(r"^cve-\d{4}-\d+", name.lower()):
        return False
    if not key or len(key) < 3:
        return False
    return True


async def _collect_from_cache() -> dict[str, str]:
    found: dict[str, str] = {}
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            text("SELECT entities_json FROM ner_cache WHERE model_version = 'haiku-4-5'")
        )
        for (entities_json,) in rows.fetchall():
            for ent in entities_json or []:
                if ent.get("type") != "vuln_alias":
                    continue
                name = ent.get("name", "")
                key = ent.get("normalized_key", "")
                if not _plausible(name, key):
                    continue
                found.setdefault(key, name)
    return found


async def main() -> None:
    from_cache = await _collect_from_cache()
    print(f"Found {len(from_cache)} vuln_alias entries in ner_cache")
    print(f"Adding {len(CANONICAL_VULN_ALIASES)} canonical entries")

    now = datetime.now(timezone.utc)
    inserted = skipped = updated = 0

    async with AsyncSessionLocal() as db:
        # Cache entries — DO NOTHING if key already exists
        for key, name in from_cache.items():
            if key in CANONICAL_VULN_ALIASES:
                continue  # canonical pass handles these
            result = await db.execute(
                text("""
                    INSERT INTO entity_intel
                        (normalized_key, display_name, entity_type, aliases, source, source_id, active, last_synced)
                    VALUES
                        (:key, :name, 'vuln_alias', '[]'::jsonb, 'curated', NULL, true, :now)
                    ON CONFLICT (normalized_key) DO NOTHING
                """),
                {"key": key, "name": name, "now": now},
            )
            if result.rowcount:
                inserted += 1
            else:
                skipped += 1

        # Canonical entries — upsert (canonical wins on display_name)
        for key, name in CANONICAL_VULN_ALIASES.items():
            result = await db.execute(
                text("""
                    INSERT INTO entity_intel
                        (normalized_key, display_name, entity_type, aliases, source, source_id, active, last_synced)
                    VALUES
                        (:key, :name, 'vuln_alias', '[]'::jsonb, 'curated', NULL, true, :now)
                    ON CONFLICT (normalized_key) DO UPDATE
                        SET display_name = EXCLUDED.display_name,
                            entity_type  = 'vuln_alias',
                            last_synced  = EXCLUDED.last_synced
                    WHERE entity_intel.entity_type = 'vuln_alias'
                """),
                {"key": key, "name": name, "now": now},
            )
            if result.rowcount:
                inserted += 1
            else:
                skipped += 1

        await db.commit()

    print(f"entity_intel: {inserted} inserted/updated, {skipped} skipped (already present)")


if __name__ == "__main__":
    asyncio.run(main())
