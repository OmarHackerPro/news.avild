#!/usr/bin/env python
"""Enrich CVE stubs in cve_topics with CVSS, CWE, and full NVD data.

Reads unenriched docs from cve_topics (missing nvd_last_modified), fetches
NVD API v2, and writes back via upsert_immutable.

Rate limits:
  Without API key : 5 req / 30s  →  default 6s sleep between requests
  With API key    : 50 req / 30s →  default 1s sleep between requests

Usage:
    python scripts/enrich_cve_nvd.py
    python scripts/enrich_cve_nvd.py --api-key KEY  # or set NVD_API_KEY env var
    python scripts/enrich_cve_nvd.py --sleep 2      # override sleep seconds
    python scripts/enrich_cve_nvd.py --dry-run      # list CVEs, apply mapping, exit
"""
import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

import requests
from opensearchpy import AsyncOpenSearch

NVD_CVE_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
MITRE_CVE_URL = "https://cveawg.mitre.org/api/cve"
_SCROLL_SIZE = 200

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# NVD fetch
# ---------------------------------------------------------------------------

def _nvd_fetch(cve_id: str, api_key: str | None, sleep_s: float) -> dict | None:
    """Fetch a single CVE from NVD. Returns the raw cve dict or None on 404."""
    headers = {"apiKey": api_key} if api_key else {}

    for attempt in range(3):
        try:
            resp = requests.get(
                NVD_CVE_URL,
                params={"cveId": cve_id},
                headers=headers,
                timeout=30,
            )
        except requests.RequestException as exc:
            logger.warning("%s: request error (attempt %d): %s", cve_id, attempt + 1, exc)
            time.sleep(sleep_s * 2)
            continue

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 30))
            logger.warning("%s: rate limited, sleeping %ds", cve_id, retry_after)
            time.sleep(retry_after)
            continue

        if resp.status_code == 404:
            return None

        if resp.status_code != 200:
            logger.warning("%s: HTTP %d", cve_id, resp.status_code)
            return None

        vulns = resp.json().get("vulnerabilities", [])
        return vulns[0].get("cve") if vulns else None

    return None


# ---------------------------------------------------------------------------
# MITRE CVE check — distinguishes RESERVED from invalid/FP entity extractions
# ---------------------------------------------------------------------------

def _mitre_check(cve_id: str) -> str:
    """Query MITRE CVE Services API for a CVE not found in NVD.

    Returns one of:
      'reserved'  — real CVE, not yet published to NVD (re-check next run)
      'rejected'  — CVE was formally rejected (stop checking)
      'published' — NVD missed it transiently (retry NVD next run)
      'invalid'   — not a real CVE ID (FP entity extraction, stop checking)
    """
    try:
        resp = requests.get(f"{MITRE_CVE_URL}/{cve_id}", timeout=10)
    except requests.RequestException as exc:
        logger.warning("%s: MITRE API error: %s", cve_id, exc)
        return "invalid"

    if resp.status_code == 404:
        return "invalid"
    if resp.status_code != 200:
        logger.warning("%s: MITRE API HTTP %d", cve_id, resp.status_code)
        return "invalid"

    state = resp.json().get("cveMetadata", {}).get("state", "").upper()
    if state == "RESERVED":
        return "reserved"
    if state == "REJECTED":
        return "rejected"
    return "published"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_entity_fields(cve_data: dict) -> dict:
    """Structured fields that go into the cve_topics index (all indexed/queryable)."""
    fields: dict = {}
    metrics = cve_data.get("metrics", {})

    # CVSS — prefer 3.1, fall back to 4.0, then 2.0
    for key in ("cvssMetricV31", "cvssMetricV40", "cvssMetricV2"):
        entries = metrics.get(key, [])
        if not entries:
            continue
        primary = next((m for m in entries if m.get("type") == "Primary"), entries[0])
        data = primary.get("cvssData", {})
        score = data.get("baseScore")
        if score is not None:
            fields["cvss_score"] = float(score)
            fields["cvss_severity"] = data.get("baseSeverity") or data.get("severity")
            fields["cvss_vector"] = data.get("vectorString")
        break

    # CWE IDs (deduplicated, order preserved)
    cwe_ids = []
    for weakness in cve_data.get("weaknesses", []):
        for desc in weakness.get("description", []):
            val = desc.get("value", "")
            if val.startswith("CWE-") and val not in cwe_ids:
                cwe_ids.append(val)
    if cwe_ids:
        fields["cwe_ids"] = cwe_ids

    fields["cisa_kev"] = "cisaExploitAdd" in cve_data
    fields["vuln_status"] = cve_data.get("vulnStatus")
    fields["nvd_last_modified"] = cve_data.get("lastModified")

    return fields


# ---------------------------------------------------------------------------
# OpenSearch helpers
# ---------------------------------------------------------------------------

def _get_client(admin: bool = False) -> AsyncOpenSearch:
    url = os.environ.get("OPENSEARCH_URL", "http://opensearch:9200")
    if admin:
        user = os.environ.get("OPENSEARCH_ADMIN_USER")
        password = os.environ.get("OPENSEARCH_ADMIN_PASSWORD")
    else:
        user = os.environ.get("OPENSEARCH_USER")
        password = os.environ.get("OPENSEARCH_PASSWORD")
    return AsyncOpenSearch(
        hosts=[url],
        use_ssl=url.startswith("https"),
        verify_certs=False,
        ssl_show_warn=False,
        http_auth=(user, password) if user else None,
    )


async def _apply_mappings(client: AsyncOpenSearch) -> None:
    """Push new fields into cve_topics mapping (idempotent)."""
    from app.db.opensearch import INDEX_CVE_TOPICS, _CVE_TOPICS_MAPPING

    await client.indices.put_mapping(
        index=INDEX_CVE_TOPICS,
        body={"properties": _CVE_TOPICS_MAPPING["mappings"]["properties"]},
    )
    logger.info("Mapping updated: %s", INDEX_CVE_TOPICS)


async def _scroll_unenriched_cve_topics(client: AsyncOpenSearch) -> list[dict]:
    """Scan cve_topics for CVEs that haven't been NVD-enriched yet."""
    from app.db.opensearch import INDEX_CVE_TOPICS

    query = {
        "bool": {
            "must": [{"match_all": {}}],
            "must_not": [{"exists": {"field": "nvd_last_modified"}}],
        }
    }

    results = []
    resp = await client.search(
        index=INDEX_CVE_TOPICS,
        body={"query": query, "size": _SCROLL_SIZE},
        scroll="2m",
    )
    scroll_id = resp["_scroll_id"]

    while True:
        hits = resp["hits"]["hits"]
        if not hits:
            break
        results.extend(hits)
        resp = await client.scroll(scroll_id=scroll_id, scroll="2m")

    try:
        await client.clear_scroll(scroll_id=scroll_id)
    except Exception:
        pass

    return results


async def _update_cve_topic(client: AsyncOpenSearch, cve_id: str, immutable: dict, mutable: dict) -> None:
    """Write NVD fields to cve_topics via write-once helper."""
    from app.db.opensearch import INDEX_CVE_TOPICS
    from app.db.os_write_once import upsert_immutable

    await upsert_immutable(
        client=client,
        index=INDEX_CVE_TOPICS,
        doc_id=cve_id,
        immutable_fields=immutable,
        mutable_fields=mutable,
    )


# ---------------------------------------------------------------------------
# Post-enrichment cluster rescore
# ---------------------------------------------------------------------------

async def _rescore_clusters_for_cves(enriched_cve_names: list[str]) -> None:
    """Find clusters containing any of the enriched CVEs, update max_cvss from
    the cve_topics index, then rescore."""
    if not enriched_cve_names:
        return

    from app.db.opensearch import INDEX_CLUSTERS
    from app.ingestion.scorer import rescore_cluster

    client = _get_client()
    try:
        # Fetch enriched CVSS scores from cve_topics
        from app.db.opensearch import INDEX_CVE_TOPICS
        topic_resp = await client.search(
            index=INDEX_CVE_TOPICS,
            body={
                "query": {"ids": {"values": enriched_cve_names}},
                "size": len(enriched_cve_names),
                "_source": ["cvss_score"],
            },
        )
        cve_cvss: dict[str, float] = {}
        for hit in topic_resp["hits"]["hits"]:
            score = hit["_source"].get("cvss_score")
            if score is not None:
                cve_cvss[hit["_id"].upper()] = float(score)

        # Find affected clusters (fetch cve_ids to compute max_cvss per cluster)
        resp = await client.search(
            index=INDEX_CLUSTERS,
            body={
                "query": {"terms": {"cve_ids": enriched_cve_names}},
                "size": 500,
                "_source": ["cve_ids", "max_cvss"],
            },
        )
        hits = resp["hits"]["hits"]
        logger.info("Rescoring %d clusters affected by NVD enrichment", len(hits))

        for hit in hits:
            cluster_id = hit["_id"]
            src = hit["_source"]
            cluster_cves = [c.upper() for c in (src.get("cve_ids") or [])]
            new_max_cvss = max(
                (cve_cvss[c] for c in cluster_cves if c in cve_cvss),
                default=src.get("max_cvss") or 0.0,
            )
            if new_max_cvss > (src.get("max_cvss") or 0.0):
                try:
                    await client.update(
                        index=INDEX_CLUSTERS,
                        id=cluster_id,
                        body={"doc": {"max_cvss": new_max_cvss}},
                        retry_on_conflict=3,
                    )
                except Exception:
                    logger.exception("Failed to update max_cvss for cluster %s", cluster_id)
            try:
                await rescore_cluster(cluster_id)
            except Exception:
                logger.exception("Failed to rescore cluster %s", cluster_id)
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(args: argparse.Namespace) -> None:
    client = _get_client()
    await _apply_mappings(client)

    docs = await _scroll_unenriched_cve_topics(client)
    total = len(docs)
    logger.info("Unenriched CVEs in cve_topics: %d", total)

    if total == 0:
        logger.info("Nothing to do. All CVE topics already enriched.")
        await client.close()
        return

    api_key: str | None = args.api_key or os.environ.get("NVD_API_KEY") or None
    sleep_s: float = args.sleep if args.sleep is not None else (1.0 if api_key else 6.0)

    logger.info(
        "API key: %s | Sleep: %.1fs | Estimated time: ~%dm",
        "yes" if api_key else "no",
        sleep_s,
        int(total * sleep_s / 60),
    )

    if args.dry_run:
        logger.info("Dry run — no NVD requests made.")
        await client.close()
        return

    done = not_found = failed = 0
    enriched_cves: list[str] = []
    t_start = time.monotonic()

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    for i, hit in enumerate(docs, 1):
        cve_id = hit["_id"]

        elapsed = time.monotonic() - t_start
        eta_s = (elapsed / i) * (total - i) if i > 1 else total * sleep_s
        logger.info("[%d/%d] %s  (ETA ~%dm%02ds)", i, total, cve_id, int(eta_s // 60), int(eta_s % 60))

        cve_data = _nvd_fetch(cve_id, api_key, sleep_s)

        if cve_data is None:
            mitre_state = _mitre_check(cve_id)
            if mitre_state == "reserved":
                mutable = {"vuln_status": "Reserved"}
                logger.info("  → Reserved")
            elif mitre_state == "rejected":
                mutable = {"vuln_status": "Rejected", "nvd_last_modified": now_iso}
                logger.info("  → Rejected")
            elif mitre_state == "published":
                mutable = {"vuln_status": "Pending NVD"}
                logger.info("  → MITRE Published, NVD missed it — retry next run")
            else:
                mutable = {"vuln_status": "Invalid", "nvd_last_modified": now_iso}
                logger.info("  → Invalid CVE ID")
            try:
                await _update_cve_topic(client, cve_id, immutable={}, mutable=mutable)
            except Exception:
                logger.exception("Failed to update status for %s", cve_id)
            not_found += 1
        else:
            try:
                immutable = _parse_entity_fields(cve_data)
                immutable["nvd_raw"] = cve_data
                immutable["enriched_at"] = now_iso
                await _update_cve_topic(client, cve_id, immutable=immutable, mutable={"updated_at": now_iso})
                enriched_cves.append(cve_id)
                done += 1
            except Exception:
                logger.exception("Failed to enrich %s", cve_id)
                failed += 1

        if i < total:
            time.sleep(sleep_s)

    await client.close()
    logger.info("Done. enriched=%d  pending=%d  failed=%d", done, not_found, failed)

    await _rescore_clusters_for_cves(enriched_cves)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Enrich CVE topics from NVD API v2")
    parser.add_argument("--api-key", help="NVD API key (or set NVD_API_KEY env var)")
    parser.add_argument("--sleep", type=float, help="Seconds between requests (default: 1 with key, 6 without)")
    parser.add_argument("--dry-run", action="store_true", help="Apply mapping and count CVEs without fetching")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
