"""CVE intelligence lookups against cve_topics. Read-only.

This module is the single read-side interface for "what do we know about this CVE?".
Used at ingest time to populate article.cvss_score and article.severity from
already-enriched data in cve_topics. Never calls external APIs — if a CVE isn't
in cve_topics, the caller gets nothing back, which is the correct signal that
NVD enrichment hasn't reached this CVE yet.
"""
from typing import Optional

from app.db.opensearch import INDEX_CVE_TOPICS, get_os_client


# CVSS v3 severity bands (per FIRST CVSS spec):
#   9.0-10.0 critical, 7.0-8.9 high, 4.0-6.9 medium, 0.1-3.9 low
_SEVERITY_THRESHOLDS = [
    (9.0, "critical"),
    (7.0, "high"),
    (4.0, "medium"),
    (0.0, "low"),  # >0 because we treat 0.0/negative/None as "no severity"
]


def severity_from_cvss(score: Optional[float]) -> Optional[str]:
    """Map CVSS base score → severity label.

    Returns None for None, 0.0, or negative scores (treated as "no severity").
    """
    if score is None or score <= 0:
        return None
    for threshold, label in _SEVERITY_THRESHOLDS:
        if score >= threshold:
            return label
    return None


async def lookup_cve_intel(cve_ids: list[str]) -> dict[str, dict]:
    """Look up CVE intelligence in cve_topics. Read-only, no API calls.

    Returns {cve_id_upper: {cvss_score, cvss_severity, cvss_vector, cisa_kev,
                            epss_score, epss_percentile, cwe_ids, ...}}.
    Missing CVEs are absent from the result (not returned as null).
    Caller decides how to handle absence.
    """
    if not cve_ids:
        return {}

    # Normalize and dedupe — cve_topics doc IDs are uppercase
    ids = list({cid.upper() for cid in cve_ids if cid})
    if not ids:
        return {}

    resp = await get_os_client().search(
        index=INDEX_CVE_TOPICS,
        body={
            "query": {"ids": {"values": ids}},
            "size": len(ids),
            "_source": [
                "cvss_score", "cvss_severity", "cvss_vector",
                "cwe_ids", "cisa_kev", "kev_added_at",
                "epss_score", "epss_percentile", "epss_updated_at",
                "vuln_status", "nvd_last_modified",
            ],
        },
    )
    return {hit["_id"]: hit["_source"] for hit in resp["hits"]["hits"]}
