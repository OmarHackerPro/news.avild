"""Regex + keyword entity extraction from normalized articles.

Extracts CVE IDs, vendor/product names, and threat actor/malware/tool names
from article text fields. No NLP — purely regex and seed-list matching.
"""
import re
from decimal import Decimal
from typing import Optional

from app.ingestion.normalizer import NormalizedArticle, strip_html, _extract_cve_ids

# ---------------------------------------------------------------------------
# Seed lists — normalized_key → display name
# ---------------------------------------------------------------------------

VENDOR_KEYWORDS: dict[str, str] = {
    "fortinet": "Fortinet",
    "citrix": "Citrix",
    "microsoft": "Microsoft",
    "cisco": "Cisco",
    "apache": "Apache",
    "vmware": "VMware",
    "palo-alto-networks": "Palo Alto Networks",
    "ivanti": "Ivanti",
    "juniper": "Juniper",
    "google": "Google",
    "apple": "Apple",
    "adobe": "Adobe",
    "oracle": "Oracle",
    "sap": "SAP",
    "f5": "F5",
    "zyxel": "Zyxel",
    "sonicwall": "SonicWall",
    "sophos": "Sophos",
    "atlassian": "Atlassian",
    "linux": "Linux",
    "samsung": "Samsung",
    "huawei": "Huawei",
    "ibm": "IBM",
    "dell": "Dell",
    "hp": "HP",
    "lenovo": "Lenovo",
    "qualcomm": "Qualcomm",
    "intel": "Intel",
    "amd": "AMD",
    "nvidia": "NVIDIA",
    "aws": "AWS",
    "cloudflare": "Cloudflare",
    "wordpress": "WordPress",
    "drupal": "Drupal",
    "gitlab": "GitLab",
    "github": "GitHub",
    "mozilla": "Mozilla",
    "zoom": "Zoom",
    "openai": "OpenAI",
    "telegram": "Telegram",
    "whatsapp": "WhatsApp",
    "signal": "Signal",
}

# normalized_key → (display_name, entity_type)
THREAT_KEYWORDS: dict[str, tuple[str, str]] = {
    # Malware families
    "lockbit": ("LockBit", "malware"),
    "alphv": ("ALPHV", "malware"),
    "blackcat": ("BlackCat", "malware"),
    "clop": ("Clop", "malware"),
    "revil": ("REvil", "malware"),
    "conti": ("Conti", "malware"),
    "emotet": ("Emotet", "malware"),
    "qakbot": ("Qakbot", "malware"),
    "trickbot": ("TrickBot", "malware"),
    "ryuk": ("Ryuk", "malware"),
    "blackbasta": ("Black Basta", "malware"),
    "ragnar-locker": ("Ragnar Locker", "malware"),
    "hive": ("Hive", "malware"),
    "play": ("Play", "malware"),
    "akira": ("Akira", "malware"),
    "medusa": ("Medusa", "malware"),
    "royal": ("Royal", "malware"),
    "blacksuit": ("BlackSuit", "malware"),
    "rhysida": ("Rhysida", "malware"),
    "bianlian": ("BianLian", "malware"),
    # Threat actors
    "scattered-spider": ("Scattered Spider", "actor"),
    "lazarus-group": ("Lazarus Group", "actor"),
    "lazarus": ("Lazarus", "actor"),
    "apt28": ("APT28", "actor"),
    "apt29": ("APT29", "actor"),
    "apt41": ("APT41", "actor"),
    "fancy-bear": ("Fancy Bear", "actor"),
    "cozy-bear": ("Cozy Bear", "actor"),
    "sandworm": ("Sandworm", "actor"),
    "turla": ("Turla", "actor"),
    "kimsuky": ("Kimsuky", "actor"),
    "volt-typhoon": ("Volt Typhoon", "actor"),
    "salt-typhoon": ("Salt Typhoon", "actor"),
    "charming-kitten": ("Charming Kitten", "actor"),
    "mustang-panda": ("Mustang Panda", "actor"),
    "fin7": ("FIN7", "actor"),
    "fin11": ("FIN11", "actor"),
    "ta505": ("TA505", "actor"),
    "lapsus": ("LAPSUS$", "actor"),
    # Tools
    "cobalt-strike": ("Cobalt Strike", "tool"),
    "mimikatz": ("Mimikatz", "tool"),
    "metasploit": ("Metasploit", "tool"),
    "brute-ratel": ("Brute Ratel", "tool"),
    "sliver": ("Sliver", "tool"),
    "bloodhound": ("BloodHound", "tool"),
    "impacket": ("Impacket", "tool"),
}

# Pre-compiled patterns for keyword matching
# Short names (<=3 chars) use case-sensitive matching to avoid false positives
_VENDOR_PATTERNS: list[tuple[str, str, re.Pattern]] = []
for _key, _name in VENDOR_KEYWORDS.items():
    _flags = 0 if len(_name) <= 3 else re.IGNORECASE
    _VENDOR_PATTERNS.append((_key, _name, re.compile(r"\b" + re.escape(_name) + r"\b", _flags)))

_THREAT_PATTERNS: list[tuple[str, str, str, re.Pattern]] = []
for _key, (_name, _etype) in THREAT_KEYWORDS.items():
    _flags = 0 if len(_name) <= 3 else re.IGNORECASE
    _THREAT_PATTERNS.append((_key, _name, _etype, re.compile(r"\b" + re.escape(_name) + r"\b", _flags)))


def _normalize_key(name: str) -> str:
    """Convert entity name to a slug-form normalized key."""
    key = name.lower()
    key = re.sub(r"[^a-z0-9]+", "-", key)
    return key.strip("-")


def extract_entities(article: NormalizedArticle) -> list[dict]:
    """Extract structured entities from an article dict.

    Returns a list of dicts with keys: type, name, normalized_key, cvss_score (optional).
    """
    # Build combined search text
    parts = [
        article.get("title") or "",
        article.get("desc") or "",
    ]
    content_html = article.get("content_html")
    if content_html:
        parts.append(strip_html(content_html))

    existing_cves = article.get("cve_ids") or []
    if existing_cves:
        parts.append(" ".join(existing_cves))

    combined = " ".join(parts)
    if not combined.strip():
        return []

    seen: dict[str, dict] = {}  # normalized_key → entity dict
    article_cvss: Optional[Decimal] = None
    raw_cvss = article.get("cvss_score")
    if raw_cvss is not None:
        try:
            article_cvss = Decimal(str(raw_cvss))
        except Exception:
            pass

    # --- CVE extraction ---
    cve_ids = _extract_cve_ids(combined)
    # Also include CVEs already extracted by the normalizer
    for cve in existing_cves:
        if cve not in cve_ids:
            cve_ids.append(cve)

    for i, cve_id in enumerate(cve_ids):
        key = cve_id.lower()
        if key not in seen:
            entity: dict = {
                "type": "cve",
                "name": cve_id,
                "normalized_key": key,
            }
            # Attach CVSS score to first CVE if only one found
            if article_cvss is not None and len(cve_ids) == 1:
                entity["cvss_score"] = article_cvss
            seen[key] = entity

    # --- Vendor extraction ---
    for key, name, pattern in _VENDOR_PATTERNS:
        if key not in seen and pattern.search(combined):
            seen[key] = {
                "type": "vendor",
                "name": name,
                "normalized_key": key,
            }

    # --- Threat extraction (malware, actors, tools) ---
    for key, name, etype, pattern in _THREAT_PATTERNS:
        if key not in seen and pattern.search(combined):
            seen[key] = {
                "type": etype,
                "name": name,
                "normalized_key": key,
            }

    return list(seen.values())
