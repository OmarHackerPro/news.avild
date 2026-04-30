#!/usr/bin/env python
"""Download MITRE ATT&CK enterprise STIX bundle and regenerate data/threat_keywords.json.

Usage:
    python scripts/fetch_mitre_attack.py               # fetch + write
    python scripts/fetch_mitre_attack.py --dry-run     # print stats, don't write
    python scripts/fetch_mitre_attack.py --url <url>   # override STIX source URL
"""
import argparse
import json
import re
import sys
from pathlib import Path

import requests

STIX_URL = (
    "https://raw.githubusercontent.com/mitre/cti/master/"
    "enterprise-attack/enterprise-attack.json"
)
OUTPUT_FILE = Path(__file__).resolve().parent.parent / "data" / "threat_keywords.json"

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


def _parse_stix_bundle(bundle: dict) -> tuple[dict, dict]:
    """Parse a STIX 2.0 bundle dict into (keywords, aliases) dicts.

    keywords: {normalized_key: [display_name, entity_type]}
    aliases:  {alias_display_text: canonical_normalized_key}
    """
    keywords: dict[str, list] = {}
    aliases: dict[str, str] = {}

    for obj in bundle.get("objects", []):
        obj_type = obj.get("type")
        if obj_type not in _STIX_TYPE_MAP:
            continue
        if obj.get("revoked") or obj.get("x_mitre_deprecated"):
            continue

        name = (obj.get("name") or "").strip()
        if not name:
            continue

        key = _normalize_key(name)
        entity_type = _STIX_TYPE_MAP[obj_type]
        keywords[key] = [name, entity_type]

        # Collect aliases — field name differs by type
        if obj_type in _ALIASES_INCLUDE_PRIMARY:
            raw = obj.get("aliases") or []
            # First element is the primary name — skip it
            alt_aliases = [a for a in raw if a.strip() != name]
        else:
            raw = obj.get("x_mitre_aliases") or obj.get("aliases") or []
            alt_aliases = [a for a in raw if a.strip()]

        for alias in alt_aliases:
            alias = alias.strip()
            if alias:
                aliases[alias] = key

    return keywords, aliases


def _fetch_bundle(url: str) -> dict:
    """Download and parse the STIX JSON bundle."""
    print(f"Downloading ATT&CK STIX bundle from {url} ...", flush=True)
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    return resp.json()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Regenerate data/threat_keywords.json from MITRE ATT&CK")
    parser.add_argument("--url", default=STIX_URL, help="STIX bundle URL")
    parser.add_argument("--dry-run", action="store_true", help="Print stats without writing")
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE, help="Output file path")
    args = parser.parse_args(argv)

    bundle = _fetch_bundle(args.url)
    keywords, aliases = _parse_stix_bundle(bundle)

    from collections import Counter
    type_counts = Counter(v[1] for v in keywords.values())
    print(f"Parsed {len(keywords)} keywords: {dict(type_counts)}")
    print(f"Parsed {len(aliases)} aliases")

    if args.dry_run:
        print("--dry-run: not writing.")
        return

    out = {"keywords": keywords, "aliases": aliases}
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Written to {args.output}")


if __name__ == "__main__":
    main()
