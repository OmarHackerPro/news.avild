"""Seed vuln_alias entries into data/threat_keywords.json.

Union of:
1. All vuln_alias entities Haiku has stored in ner_cache (filtered to plausible values).
2. A hand-curated canonical list of famous named vulnerabilities.

Output: writes a new vuln_alias section into the keywords map. Existing
non-vuln_alias entries are preserved untouched. Existing vuln_alias entries
(if any) are merged by normalized_key, with the canonical list taking
precedence on display-name conflicts.
"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio
import json
import re
from pathlib import Path

from sqlalchemy import text

from app.db.session import AsyncSessionLocal

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "threat_keywords.json"

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
    """Filter Haiku junk: too short, generic words, raw CVE-style ids."""
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

    # Load existing file
    with open(DATA_FILE) as f:
        data = json.load(f)

    keywords = data.setdefault("keywords", {})

    # Apply both sources, canonical takes precedence on display-name
    merged: dict[str, str] = {**from_cache, **CANONICAL_VULN_ALIASES}
    for key, name in merged.items():
        existing = keywords.get(key)
        if existing and existing[1] != "vuln_alias":
            # Don't clobber an entry that's already another type
            print(f"Skipping {key} (already classified as {existing[1]})")
            continue
        keywords[key] = [name, "vuln_alias"]

    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)

    total = sum(1 for v in keywords.values() if v[1] == "vuln_alias")
    print(f"Wrote {DATA_FILE} — total vuln_alias entries: {total}")


if __name__ == "__main__":
    asyncio.run(main())
