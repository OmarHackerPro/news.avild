"""Regex + keyword entity extraction from normalized articles.

Extracts CVE IDs, vendor/product names, and threat actor/malware/tool names
from article text fields. No NLP — purely regex and seed-list matching.
"""
import json
import logging
import re
from decimal import Decimal
from pathlib import Path
from typing import Optional

from sqlalchemy import text

from app.ingestion.normalizer import NormalizedArticle, strip_html, _extract_cve_ids

logger = logging.getLogger(__name__)

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

# Product seed list — normalized_key → display name
PRODUCT_KEYWORDS: dict[str, str] = {
    # Fortinet
    "fortios": "FortiOS",
    "fortigate": "FortiGate",
    "fortimanager": "FortiManager",
    "fortianalyzer": "FortiAnalyzer",
    "fortisiem": "FortiSIEM",
    "fortiproxy": "FortiProxy",
    "fortiswitch": "FortiSwitch",
    "fortiadc": "FortiADC",
    "fortiweb": "FortiWeb",
    "forticlient": "FortiClient",
    # Microsoft
    "exchange": "Exchange",
    "windows-server": "Windows Server",
    "active-directory": "Active Directory",
    "azure-ad": "Azure AD",
    "entra-id": "Entra ID",
    "microsoft-365": "Microsoft 365",
    "outlook": "Outlook",
    "microsoft-edge": "Microsoft Edge",
    "sharepoint": "SharePoint",
    "windows": "Windows",
    "microsoft-defender": "Microsoft Defender",
    # VMware
    "vcenter": "vCenter",
    "esxi": "ESXi",
    "vsphere": "vSphere",
    "vmware-workstation": "VMware Workstation",
    # Google
    "chrome": "Chrome",
    "android": "Android",
    "chromium": "Chromium",
    "google-cloud": "Google Cloud",
    # Apple
    "ios": "iOS",
    "macos": "macOS",
    "safari": "Safari",
    "webkit": "WebKit",
    "ipados": "iPadOS",
    "watchos": "watchOS",
    # Palo Alto Networks
    "pan-os": "PAN-OS",
    "cortex-xdr": "Cortex XDR",
    "globalprotect": "GlobalProtect",
    "panorama": "Panorama",
    # Cisco
    "ios-xe": "IOS XE",
    "ios-xr": "IOS XR",
    "cisco-asa": "Cisco ASA",
    "firepower": "Firepower",
    "webex": "Webex",
    "meraki": "Meraki",
    "cisco-duo": "Cisco Duo",
    # Ivanti
    "ivanti-connect-secure": "Ivanti Connect Secure",
    "pulse-connect-secure": "Pulse Connect Secure",
    "ivanti-epmm": "Ivanti EPMM",
    # Citrix
    "netscaler": "NetScaler",
    "citrix-adc": "Citrix ADC",
    "xenserver": "XenServer",
    "citrix-workspace": "Citrix Workspace",
    # SonicWall
    "sonicos": "SonicOS",
    # Juniper
    "junos": "Junos",
    "juniper-srx": "Juniper SRX",
    # Atlassian
    "confluence": "Confluence",
    "jira": "Jira",
    "bitbucket": "Bitbucket",
    "bamboo": "Bamboo",
    # Infrastructure / DevOps
    "kubernetes": "Kubernetes",
    "docker": "Docker",
    "jenkins": "Jenkins",
    "nginx": "Nginx",
    "apache-http-server": "Apache HTTP Server",
    "terraform": "Terraform",
    "ansible": "Ansible",
    # Other notable products
    "apparmor": "AppArmor",
    "openssh": "OpenSSH",
    "openssl": "OpenSSL",
    "wing-ftp": "Wing FTP",
}

# ---------------------------------------------------------------------------
# Baseline seed list — used as fallback when data/threat_keywords.json is absent
# ---------------------------------------------------------------------------

# normalized_key → (display_name, entity_type)
_BASELINE_KEYWORDS: dict[str, tuple[str, str]] = {
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

_BASELINE_ALIASES: dict[str, str] = {}

# ---------------------------------------------------------------------------
# File-based loader — loads from data/threat_keywords.json when available
# ---------------------------------------------------------------------------

_DATA_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "threat_keywords.json"


def _load_threat_data() -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """Load threat keywords and alias table from data/threat_keywords.json.

    When the file exists, its data is merged with the baseline so that entries
    present in the baseline but absent from the file are always available.
    Falls back to the baseline entirely if the file is missing or unreadable.
    """
    if _DATA_FILE.exists():
        try:
            with open(_DATA_FILE) as f:
                data = json.load(f)
            # File data takes precedence; baseline fills any gaps
            keywords: dict[str, tuple[str, str]] = {**_BASELINE_KEYWORDS}
            keywords.update({k: tuple(v) for k, v in data["keywords"].items()})
            aliases: dict[str, str] = {**_BASELINE_ALIASES}
            aliases.update(data.get("aliases", {}))
            logger.debug(
                "Loaded %d keywords, %d aliases from %s",
                len(keywords), len(aliases), _DATA_FILE,
            )
            return keywords, aliases
        except Exception:
            logger.warning("Failed to load %s, falling back to baseline", _DATA_FILE, exc_info=True)
    return _BASELINE_KEYWORDS, _BASELINE_ALIASES


# Load at module init — replaced by file data when available
THREAT_KEYWORDS, _THREAT_ALIASES = _load_threat_data()

# Pre-compiled patterns for keyword matching
# Short names (<=3 chars) use case-sensitive matching to avoid false positives
_VENDOR_PATTERNS: list[tuple[str, str, re.Pattern]] = []
for _key, _name in VENDOR_KEYWORDS.items():
    _flags = 0 if len(_name) <= 3 else re.IGNORECASE
    _VENDOR_PATTERNS.append((_key, _name, re.compile(r"\b" + re.escape(_name) + r"\b", _flags)))

_PRODUCT_PATTERNS: list[tuple[str, str, re.Pattern]] = []
for _key, _name in PRODUCT_KEYWORDS.items():
    _flags = 0 if len(_name) <= 3 else re.IGNORECASE
    _PRODUCT_PATTERNS.append((_key, _name, re.compile(r"\b" + re.escape(_name) + r"\b", _flags)))

_THREAT_PATTERNS: list[tuple[str, str, str, re.Pattern]] = []
for _key, (_name, _etype) in THREAT_KEYWORDS.items():
    _flags = 0 if len(_name) <= 3 else re.IGNORECASE
    _THREAT_PATTERNS.append((_key, _name, _etype, re.compile(r"\b" + re.escape(_name) + r"\b", _flags)))

_CWE_RE = re.compile(r"\bCWE-\d+\b", re.IGNORECASE)
# ATT&CK technique IDs: T1xxx range only (T1000–T1699) to avoid serial/model number FPs
_TTP_RE = re.compile(r"\bT1[0-6]\d{2}(?:\.\d{3})?\b")

# Pre-compiled alias patterns built from loaded alias table
_ALIAS_PATTERNS: list[tuple[str, re.Pattern]] = [
    (canonical_key, re.compile(r"\b" + re.escape(display_text) + r"\b", re.IGNORECASE))
    for display_text, canonical_key in _THREAT_ALIASES.items()
]

# ---------------------------------------------------------------------------
# DB-backed entity registry (populated at startup via refresh_entity_intel)
# ---------------------------------------------------------------------------

# normalized_key → (display_name, entity_type)
_DB_ENTITY_MAP: dict[str, tuple[str, str]] = {}
# raw alias display text → canonical normalized_key  (for rebuilding _ALIAS_PATTERNS)
_DB_ALIAS_DISPLAY: dict[str, str] = {}
# normalized alias key → canonical normalized_key  (for _resolve_aliases NER lookup)
_DB_ALIAS_INDEX: dict[str, str] = {}


def _normalize_key(name: str) -> str:
    """Convert entity name to a slug-form normalized key."""
    key = name.lower()
    key = re.sub(r"[^a-z0-9]+", "-", key)
    return key.strip("-")


def _rebuild_patterns_from_db() -> None:
    """Rebuild _VENDOR_PATTERNS, _PRODUCT_PATTERNS, _THREAT_PATTERNS, _ALIAS_PATTERNS from _DB_ENTITY_MAP."""
    _VENDOR_PATTERNS.clear()
    for key, (name, etype) in _DB_ENTITY_MAP.items():
        if etype == "vendor":
            flags = 0 if len(name) <= 3 else re.IGNORECASE
            _VENDOR_PATTERNS.append(
                (key, name, re.compile(r"\b" + re.escape(name) + r"\b", flags))
            )
    if not _VENDOR_PATTERNS:
        logger.warning("No vendor patterns loaded from DB")

    _PRODUCT_PATTERNS.clear()
    for key, (name, etype) in _DB_ENTITY_MAP.items():
        if etype == "product":
            flags = 0 if len(name) <= 3 else re.IGNORECASE
            _PRODUCT_PATTERNS.append(
                (key, name, re.compile(r"\b" + re.escape(name) + r"\b", flags))
            )
    if not _PRODUCT_PATTERNS:
        logger.warning("No product patterns loaded from DB")

    _THREAT_PATTERNS.clear()
    for key, (name, etype) in _DB_ENTITY_MAP.items():
        if etype in ("actor", "malware", "tool", "campaign", "vuln_alias"):
            flags = 0 if len(name) <= 3 else re.IGNORECASE
            _THREAT_PATTERNS.append(
                (key, name, etype, re.compile(r"\b" + re.escape(name) + r"\b", flags))
            )
    if not _THREAT_PATTERNS:
        logger.warning("No threat patterns loaded from DB")

    _ALIAS_PATTERNS.clear()
    for display_text, canonical_key in _DB_ALIAS_DISPLAY.items():
        _ALIAS_PATTERNS.append(
            (canonical_key, re.compile(r"\b" + re.escape(display_text) + r"\b", re.IGNORECASE))
        )
    if not _ALIAS_PATTERNS:
        logger.warning("No alias patterns loaded from DB — alias resolution inactive")


async def refresh_entity_intel(db_session) -> int:
    """Load entity_intel from DB into module-level dicts. Returns count of rows loaded.

    Call once at app startup. Falls back to hardcoded lists if table is empty.
    """
    result = await db_session.execute(
        text("SELECT normalized_key, display_name, entity_type, aliases FROM entity_intel")
    )
    rows = result.fetchall()

    if not rows:
        return 0

    new_entity_map: dict[str, tuple[str, str]] = {}
    new_alias_display: dict[str, str] = {}
    new_alias_index: dict[str, str] = {}

    for row in rows:
        norm_key, display_name, entity_type, aliases = row
        new_entity_map[norm_key] = (display_name, entity_type)
        for alias in (aliases or []):
            alias_display = alias.strip()
            if not alias_display:
                continue
            alias_norm = _normalize_key(alias_display)
            if alias_norm != norm_key:
                new_alias_display[alias_display] = norm_key
                new_alias_index[alias_norm] = norm_key

    _DB_ENTITY_MAP.clear()
    _DB_ENTITY_MAP.update(new_entity_map)
    _DB_ALIAS_DISPLAY.clear()
    _DB_ALIAS_DISPLAY.update(new_alias_display)
    _DB_ALIAS_INDEX.clear()
    _DB_ALIAS_INDEX.update(new_alias_index)

    _rebuild_patterns_from_db()
    logger.info(
        "Entity intel: loaded %d entities, %d aliases from DB",
        len(_DB_ENTITY_MAP), len(_DB_ALIAS_INDEX),
    )
    return len(_DB_ENTITY_MAP)


def _resolve_aliases(entities: list[dict]) -> list[dict]:
    """Stage 4: rewrite NER entity keys/names to canonical using _DB_ALIAS_INDEX.

    If two entities resolve to the same canonical key, the one with higher
    mentions wins; the other is dropped.
    """
    if not _DB_ALIAS_INDEX:
        return entities

    canonical_winner: dict[str, dict] = {}

    for e in entities:
        canonical = (
            _DB_ALIAS_INDEX.get(e["normalized_key"])
            or _DB_ALIAS_INDEX.get(_normalize_key(e.get("name", "")))
        )
        if canonical and canonical in _DB_ENTITY_MAP:
            display, etype = _DB_ENTITY_MAP[canonical]
            e = {**e, "normalized_key": canonical, "name": display, "type": etype}

        key = e["normalized_key"]
        existing = canonical_winner.get(key)
        if existing is None:
            canonical_winner[key] = e
        elif e.get("mentions", 1) > existing.get("mentions", 1):
            canonical_winner[key] = e

    return list(canonical_winner.values())


async def _enrich_from_kev(cve_ids: list[str], db_session) -> list[dict]:
    """Look up CVE IDs in cisa_kev, emit trusted vendor+product entities for each hit."""
    if not cve_ids or db_session is None:
        return []

    result = await db_session.execute(
        text("SELECT vendor, product FROM cisa_kev WHERE cve_id = ANY(:ids)"),
        {"ids": cve_ids},
    )
    rows = result.fetchall()

    seen: set[str] = set()
    entities: list[dict] = []
    for vendor, product in rows:
        vendor_key = _normalize_key(vendor)
        if vendor_key and vendor_key not in seen:
            entities.append({"type": "vendor", "name": vendor, "normalized_key": vendor_key})
            seen.add(vendor_key)
        product_key = _normalize_key(product)
        if product_key and product_key not in seen:
            entities.append({"type": "product", "name": product, "normalized_key": product_key})
            seen.add(product_key)

    return entities


def _extract_regex(article: NormalizedArticle) -> list[dict]:
    """Extract structured entities from an article dict using regex and keyword matching.

    Returns a list of dicts with keys: type, name, normalized_key, cvss_score (optional).
    """
    # Build combined search text
    parts = [
        article.get("title") or "",
        article.get("desc") or "",
        article.get("summary") or "",
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

    # --- Product extraction ---
    for key, name, pattern in _PRODUCT_PATTERNS:
        if key not in seen and pattern.search(combined):
            seen[key] = {
                "type": "product",
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

    # Alias loop — maps alternative names to canonical keys (e.g. "Midnight Blizzard" → "apt29")
    for canonical_key, pattern in _ALIAS_PATTERNS:
        if canonical_key not in seen and pattern.search(combined):
            if canonical_key in THREAT_KEYWORDS:
                name, etype = THREAT_KEYWORDS[canonical_key]
                seen[canonical_key] = {
                    "type": etype,
                    "name": name,
                    "normalized_key": canonical_key,
                }
            elif canonical_key in _DB_ENTITY_MAP:
                name, etype = _DB_ENTITY_MAP[canonical_key]
                seen[canonical_key] = {
                    "type": etype,
                    "name": name,
                    "normalized_key": canonical_key,
                }

    # --- CWE IDs ---
    for match in _CWE_RE.finditer(combined):
        cwe = match.group(0).upper()
        key = cwe.lower()
        if key not in seen:
            seen[key] = {"type": "cwe", "name": cwe, "normalized_key": key}

    # --- ATT&CK TTP IDs ---
    for match in _TTP_RE.finditer(combined):
        ttp = match.group(0).upper()
        key = ttp.lower()
        if key not in seen:
            seen[key] = {"type": "ttp", "name": ttp, "normalized_key": key}

    return list(seen.values())


async def extract_entities(
    article: NormalizedArticle,
    *,
    slug: str | None = None,
    db_session=None,
) -> list[dict]:
    """Extract entities from article. Local NER runs first if slug is provided; regex fills gaps."""
    model_entities: list[dict] = []
    if slug:
        from app.ingestion.ner_client import extract_entities_local
        body = article.get("content_html") or article.get("summary") or article.get("desc") or ""
        if body:
            body = strip_html(body)
        model_entities = await extract_entities_local(
            slug=slug,
            title=article.get("title") or "",
            body=body,
            db_session=db_session,
        )

    # CVEs are handled by regex; drop any CVE entities the NER model emits.
    model_entities = [e for e in model_entities if e.get("type") != "cve"]

    # Stage 4: resolve NER output to canonical keys via DB alias index
    model_entities = _resolve_aliases(model_entities)

    regex_entities = _extract_regex(article)

    # KEV enrichment: deterministic vendor+product for CVEs found in text
    cve_ids = [e["name"] for e in regex_entities if e.get("type") == "cve"]
    kev_entities = await _enrich_from_kev(cve_ids, db_session)

    seen_keys = {e["normalized_key"] for e in model_entities}
    merged = list(model_entities)
    for e in kev_entities + regex_entities:
        key = e["normalized_key"]
        # suppress if exact match OR if model already has a more-specific variant
        if key not in seen_keys and not any(k.startswith(key + "-") for k in seen_keys):
            merged.append(e)
            seen_keys.add(key)
    return merged


def merge_entities(text_entities: list[dict], tag_entities: list[dict]) -> list[dict]:
    """Merge text-derived and tag-derived entity lists, deduplicating by normalized_key.

    Text entities get sources=["text"]. Tag entities already carry sources=["tag"].
    Overlapping keys have their sources lists merged.
    """
    merged: dict[str, dict] = {}
    for e in text_entities:
        key = e["normalized_key"]
        merged[key] = {**e, "sources": ["text"]}
    for e in tag_entities:
        key = e["normalized_key"]
        if key in merged:
            existing_sources = merged[key].get("sources", ["text"])
            new_sources = e.get("sources", ["tag"])
            merged[key]["sources"] = sorted(set(existing_sources) | set(new_sources))
        else:
            merged[key] = {**e}
    return list(merged.values())
