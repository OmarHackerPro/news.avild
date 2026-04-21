#!/usr/bin/env python
"""Download MITRE ATT&CK STIX and generate data/threat_keywords.json.

Downloads the enterprise-attack STIX bundle from the mitre/cti GitHub repo,
extracts groups (actors), malware, and tools with their aliases, and writes
data/threat_keywords.json for use by entity_extractor.py.

Usage:
    python scripts/sync_mitre_attack.py
    python scripts/sync_mitre_attack.py --output path/to/custom.json
"""
import argparse
import json
import logging
import re
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

STIX_URL = (
    "https://raw.githubusercontent.com/mitre/cti/master/"
    "enterprise-attack/enterprise-attack.json"
)

_DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "threat_keywords.json"


def _normalize(name: str) -> str:
    key = name.lower()
    key = re.sub(r"[^a-z0-9]+", "-", key)
    return key.strip("-")


def _build_threat_data(stix: dict) -> dict:
    keywords: dict[str, list] = {}
    aliases: dict[str, str] = {}

    for obj in stix.get("objects", []):
        if obj.get("revoked") or obj.get("x_mitre_deprecated"):
            continue

        obj_type = obj.get("type")
        name = obj.get("name", "").strip()
        if not name:
            continue

        if obj_type == "intrusion-set":
            etype = "actor"
            obj_aliases = obj.get("aliases") or []
        elif obj_type == "malware":
            etype = "malware"
            obj_aliases = obj.get("x_mitre_aliases") or []
        elif obj_type == "tool":
            etype = "tool"
            obj_aliases = obj.get("x_mitre_aliases") or []
        else:
            continue

        canonical_key = _normalize(name)
        keywords[canonical_key] = [name, etype]

        for alias in obj_aliases:
            alias = alias.strip()
            if alias and alias != name:
                aliases[alias] = canonical_key

    return {"keywords": keywords, "aliases": aliases}


def main(output_path: Path) -> None:
    logger.info("Downloading MITRE ATT&CK STIX from GitHub...")
    resp = requests.get(STIX_URL, timeout=60)
    resp.raise_for_status()
    stix = resp.json()
    logger.info("Downloaded %d STIX objects.", len(stix.get("objects", [])))

    data = _build_threat_data(stix)
    logger.info(
        "Parsed %d keywords, %d aliases.",
        len(data["keywords"]),
        len(data["aliases"]),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    logger.info("Written to %s", output_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    args = parser.parse_args()
    main(args.output)
