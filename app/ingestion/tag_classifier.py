"""Three-layer RSS tag classifier: junk filter → entity classifier → topic mapper."""
import re
from typing import TypedDict

from app.ingestion.entity_extractor import _DB_ENTITY_MAP, _normalize_key

VALID_TOPICS = frozenset([
    "vulnerability", "malware", "data-breach", "nation-state",
    "phishing", "supply-chain", "ics-ot", "privacy",
    "ai-security", "funding", "policy-law", "cryptography",
])

TOPIC_MAP: dict[str, str] = {
    # vulnerability
    "cve": "vulnerability",
    "vulnerabilities": "vulnerability",
    "vulnerability": "vulnerability",
    "exploited": "vulnerability",
    "zero-day": "vulnerability",
    "zero day": "vulnerability",
    "cisa kev": "vulnerability",
    "exploit": "vulnerability",
    "exploits": "vulnerability",
    "n-day": "vulnerability",
    "rce": "vulnerability",
    "remote code execution": "vulnerability",
    "patch": "vulnerability",
    "patch tuesday": "vulnerability",
    "patches": "vulnerability",
    "patching": "vulnerability",
    "unpatched": "vulnerability",
    "vulnerability disclosure": "vulnerability",
    "vulnerability management": "vulnerability",
    "vulnerability reports": "vulnerability",
    "software vulnerabilities": "vulnerability",
    "known exploited vulnerabilities (kev)": "vulnerability",
    "ics patch tuesday": "vulnerability",
    # malware
    "malware": "malware",
    "ransomware": "malware",
    "trojan": "malware",
    "spyware": "malware",
    "backdoor": "malware",
    "wiper": "malware",
    "botnet": "malware",
    "infostealer": "malware",
    "infostealers": "malware",
    "rat": "malware",
    "keyloggers": "malware",
    "malware & threats": "malware",
    "malware descriptions": "malware",
    "malware technologies": "malware",
    "windows malware": "malware",
    "mobile malware": "malware",
    "financial malware": "malware",
    "unix and macos malware": "malware",
    "macros malware": "malware",
    "worm": "malware",
    "adware": "malware",
    "crimeware": "malware",
    "malware-as-a-service": "malware",
    # data-breach
    "data breaches": "data-breach",
    "data breach": "data-breach",
    "data exfiltration": "data-breach",
    "credential harvesting": "data-breach",
    "stolen credentials": "data-breach",
    "credential theft": "data-breach",
    "stolen data": "data-breach",
    "data theft": "data-breach",
    "exfiltration": "data-breach",
    # phishing
    "phishing": "phishing",
    "spear phishing": "phishing",
    "social engineering": "phishing",
    "bec": "phishing",
    "scam": "phishing",
    "scams": "phishing",
    "phishing kit": "phishing",
    "phishing websites": "phishing",
    "spam and phishing": "phishing",
    # supply-chain
    "supply chain": "supply-chain",
    "supply chain security": "supply-chain",
    "supply chain attack": "supply-chain",
    "supply chain attacks": "supply-chain",
    "supply-chain attack": "supply-chain",
    "open source software": "supply-chain",
    "dependency confusion": "supply-chain",
    "sbom": "supply-chain",
    # ics-ot
    "ics/ot": "ics-ot",
    "ics": "ics-ot",
    "ot": "ics-ot",
    "industrial control systems": "ics-ot",
    "industrial control systems (ics)": "ics-ot",
    "scada": "ics-ot",
    "operational technology": "ics-ot",
    # privacy
    "privacy": "privacy",
    "surveillance": "privacy",
    "data protection": "privacy",
    "gdpr": "privacy",
    "tracking": "privacy",
    "data brokers": "privacy",
    "de-anonymization": "privacy",
    # ai-security
    "prompt injection": "ai-security",
    "indirect prompt injection": "ai-security",
    "llm": "ai-security",
    "llms": "ai-security",
    "genai": "ai-security",
    "agentic ai": "ai-security",
    "ai agents": "ai-security",
    "jailbreak": "ai-security",
    "large language models": "ai-security",
    "shadow ai": "ai-security",
    "ai security": "ai-security",
    # funding
    "funding": "funding",
    "cybersecurity funding": "funding",
    "m&a": "funding",
    "acquisition": "funding",
    "seed funding": "funding",
    "acquires": "funding",
    "funding/m&a": "funding",
    "emerge from stealth": "funding",
    # policy-law
    "policy": "policy-law",
    "legislation": "policy-law",
    "government": "policy-law",
    "congress": "policy-law",
    "sanctions": "policy-law",
    "regulation": "policy-law",
    "law enforcement": "policy-law",
    "executive order": "policy-law",
    "privacy law": "policy-law",
    "cybersecurity compliance": "policy-law",
    "cybersecurity strategy": "policy-law",
    "cyber strategy": "policy-law",
    "export control": "policy-law",
    "guilty": "policy-law",
    "sentenced": "policy-law",
    "extradited": "policy-law",
    "plead guilty": "policy-law",
    "prison": "policy-law",
    "takedown": "policy-law",
    # cryptography
    "encryption": "cryptography",
    "post quantum": "cryptography",
    "post quantum cryptography": "cryptography",
    "cryptanalysis": "cryptography",
    "pki": "cryptography",
    "public key infrastructure": "cryptography",
    "q-day": "cryptography",
    "quantum computing": "cryptography",
    "history of cryptography": "cryptography",
}

assert set(TOPIC_MAP.values()) <= VALID_TOPICS, (
    f"TOPIC_MAP contains invalid topics: {set(TOPIC_MAP.values()) - VALID_TOPICS}"
)

_GLOBAL_JUNK: frozenset[str] = frozenset([
    "full", "large", "medium", "thumbnail",
    "security",
])

_EMAIL_RE = re.compile(r"@")
_NUMERIC_RE = re.compile(r"^\d+$")

_ENTITY_TYPE_TO_TOPIC: dict[str, str] = {
    "malware": "malware",
    "actor": "nation-state",
    "tool": "malware",
    "cve": "vulnerability",
}


class TagClassification(TypedDict):
    normalized_topics: list[str]
    tag_entities: list[dict]
    clean_tags: list[str]


def _filter_junk(tags: list[str], source_junk_tags: list[str]) -> list[str]:
    source_lower = {t.lower() for t in source_junk_tags}
    result = []
    for tag in tags:
        lower = tag.lower().strip()
        if not lower:
            continue
        if lower in _GLOBAL_JUNK:
            continue
        if _EMAIL_RE.search(lower):
            continue
        if _NUMERIC_RE.match(lower):
            continue
        if lower in source_lower:
            continue
        result.append(tag)
    return result


def _classify_entities(tags: list[str]) -> tuple[list[dict], list[str], set[str]]:
    entities: list[dict] = []
    remaining: list[str] = []
    topics: set[str] = set()
    seen_keys: set[str] = set()

    for tag in tags:
        upper = tag.upper().strip()
        if re.match(r"CVE-\d{4}-\d+", upper):
            key = upper.lower()
            if key not in seen_keys:
                entities.append({
                    "type": "cve",
                    "name": upper,
                    "normalized_key": key,
                    "source": "tag",
                    "sources": ["tag"],
                })
                seen_keys.add(key)
                topics.add("vulnerability")
            continue

        norm_key = _normalize_key(tag)

        if norm_key in _DB_ENTITY_MAP and norm_key not in seen_keys:
            name, etype = _DB_ENTITY_MAP[norm_key]
            if etype in ("vendor", "product"):
                entities.append({
                    "type": etype,
                    "name": name,
                    "normalized_key": norm_key,
                    "source": "tag",
                    "sources": ["tag"],
                })
                seen_keys.add(norm_key)
                continue
            if etype in ("actor", "malware", "tool", "campaign", "vuln_alias"):
                entities.append({
                    "type": etype,
                    "name": name,
                    "normalized_key": norm_key,
                    "source": "tag",
                    "sources": ["tag"],
                })
                seen_keys.add(norm_key)
                inferred = _ENTITY_TYPE_TO_TOPIC.get(etype)
                if inferred:
                    topics.add(inferred)
                continue

        remaining.append(tag)

    return entities, remaining, topics


def _map_topics(tags: list[str]) -> set[str]:
    topics: set[str] = set()
    for tag in tags:
        topic = TOPIC_MAP.get(tag.lower().strip())
        if topic:
            topics.add(topic)
    return topics


def classify_tags(raw_tags: list[str], source_junk_tags: list[str]) -> TagClassification:
    clean = _filter_junk(raw_tags, source_junk_tags)
    tag_entities, remaining, entity_topics = _classify_entities(clean)
    mapper_topics = _map_topics(remaining)
    all_topics = sorted(entity_topics | mapper_topics)

    return TagClassification(
        normalized_topics=all_topics,
        tag_entities=tag_entities,
        clean_tags=clean,
    )
