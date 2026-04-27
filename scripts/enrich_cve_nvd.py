#!/usr/bin/env python
"""Enrich CVE entities in OpenSearch with CVSS, CWE, and full NVD data.

Writes to two indexes:
  entities   — structured queryable fields: cvss_score, cvss_severity,
               cvss_vector, cwe_ids, cisa_kev, vuln_status, nvd_last_modified
  nvd_cache  — full NVD JSON blob (stored, not indexed) for detail views

Rate limits:
  Without API key : 5 req / 30s  →  default 6s sleep between requests
  With API key    : 50 req / 30s →  default 1s sleep between requests

Usage:
    python scripts/enrich_cve_nvd.py
    python scripts/enrich_cve_nvd.py --force        # re-enrich even if done
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
# Parsing — two separate outputs
# ---------------------------------------------------------------------------

def _parse_entity_fields(cve_data: dict) -> dict:
    """Structured fields that go into the entities index (all indexed/queryable)."""
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


def _build_nvd_cache_doc(cve_id: str, cve_data: dict) -> dict:
    """Document for the nvd_cache index — full blob, stored not indexed."""
    return {
        "cve_id": cve_id,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "nvd_last_modified": cve_data.get("lastModified"),
        "nvd_raw": cve_data,
    }


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
    """Push new fields into entities and update nvd_cache mapping.

    Index creation requires admin credentials (OPENSEARCH_ADMIN_USER /
    OPENSEARCH_ADMIN_PASSWORD). Mapping updates use the regular client since
    kiber_app has indices_all on nvd_cache.
    """
    from app.db.opensearch import (
        INDEX_ENTITIES, INDEX_NVD_CACHE,
        _ENTITIES_MAPPING, _NVD_CACHE_MAPPING,
    )

    await client.indices.put_mapping(
        index=INDEX_ENTITIES,
        body={"properties": _ENTITIES_MAPPING["mappings"]["properties"]},
    )
    logger.info("Mapping updated: %s", INDEX_ENTITIES)

    # Try to create the index (needs admin). If it already exists, just update mapping.
    admin_user = os.environ.get("OPENSEARCH_ADMIN_USER")
    if admin_user:
        admin_client = _get_client(admin=True)
        try:
            await admin_client.indices.create(index=INDEX_NVD_CACHE, body=_NVD_CACHE_MAPPING)
            logger.info("Created index: %s", INDEX_NVD_CACHE)
        except Exception as exc:
            if "already exists" not in str(exc) and "resource_already_exists" not in str(exc):
                logger.warning("Could not create %s: %s", INDEX_NVD_CACHE, exc)
        finally:
            await admin_client.close()

    # Update mapping with regular client (kiber_app has indices_all on nvd_cache)
    try:
        await client.indices.put_mapping(
            index=INDEX_NVD_CACHE,
            body={"properties": _NVD_CACHE_MAPPING["mappings"]["properties"]},
        )
        logger.info("Mapping updated: %s", INDEX_NVD_CACHE)
    except Exception as exc:
        logger.warning(
            "Could not update %s mapping: %s — "
            "create the index first or set OPENSEARCH_ADMIN_USER + OPENSEARCH_ADMIN_PASSWORD.",
            INDEX_NVD_CACHE, exc,
        )


async def _scroll_cve_entities(client: AsyncOpenSearch, force: bool) -> list[dict]:
    from app.db.opensearch import INDEX_ENTITIES

    query = (
        {"term": {"type": "cve"}}
        if force
        else {
            "bool": {
                "must": {"term": {"type": "cve"}},
                "must_not": {"exists": {"field": "nvd_last_modified"}},
            }
        }
    )

    results = []
    resp = await client.search(
        index=INDEX_ENTITIES,
        body={"query": query, "size": _SCROLL_SIZE, "_source": ["name"]},
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


async def _update_entity(client: AsyncOpenSearch, doc_id: str, fields: dict) -> None:
    from app.db.opensearch import INDEX_ENTITIES

    set_clauses = " ".join(f"ctx._source['{f}'] = params.{f};" for f in fields)
    await client.update(
        index=INDEX_ENTITIES,
        id=doc_id,
        body={"script": {"source": set_clauses, "params": fields}},
        retry_on_conflict=3,
    )


async def _upsert_nvd_cache(client: AsyncOpenSearch, doc_id: str, doc: dict) -> bool:
    """Write full NVD blob to nvd_cache. Returns False if index doesn't exist yet."""
    from app.db.opensearch import INDEX_NVD_CACHE

    try:
        await client.index(index=INDEX_NVD_CACHE, id=doc_id, body=doc)
        return True
    except Exception as exc:
        if "index_not_found" in str(exc) or "no such index" in str(exc).lower():
            return False
        raise


# ---------------------------------------------------------------------------
# Post-enrichment cluster rescore
# ---------------------------------------------------------------------------

async def _rescore_clusters_for_cves(enriched_cve_names: list[str]) -> None:
    """Find clusters containing any of the enriched CVEs and rescore them."""
    if not enriched_cve_names:
        return

    from app.db.opensearch import INDEX_CLUSTERS
    from app.ingestion.scorer import rescore_cluster

    client = _get_client()
    try:
        resp = await client.search(
            index=INDEX_CLUSTERS,
            body={
                "query": {"terms": {"cve_ids": enriched_cve_names}},
                "size": 500,
                "_source": False,
            },
        )
        cluster_ids = [hit["_id"] for hit in resp["hits"]["hits"]]
        logger.info("Rescoring %d clusters affected by NVD enrichment", len(cluster_ids))
        for cluster_id in cluster_ids:
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

    docs = await _scroll_cve_entities(client, force=args.force)
    total = len(docs)
    logger.info("CVE entities to enrich: %d", total)

    if total == 0:
        logger.info("Nothing to do. Use --force to re-enrich existing entries.")
        await client.close()
        return

    api_key: str | None = args.api_key or os.environ.get("NVD_API_KEY") or None
    sleep_s: float = args.sleep if args.sleep is not None else (1.0 if api_key else 6.0)

    logger.info(
        "API key: %s | Sleep: %.1fs | Estimated time: ~%dm",
        "yes" if api_key else "no — register free at nvd.nist.gov/developers/request-an-api-key",
        sleep_s,
        int(total * sleep_s / 60),
    )

    if args.dry_run:
        logger.info("Dry run — no NVD requests made.")
        await client.close()
        return

    done = not_found = failed = cached = 0
    enriched_cves: list[str] = []
    t_start = time.monotonic()

    for i, hit in enumerate(docs, 1):
        doc_id = hit["_id"]
        cve_name = hit["_source"]["name"]

        elapsed = time.monotonic() - t_start
        eta_s = (elapsed / i) * (total - i) if i > 1 else total * sleep_s
        logger.info("[%d/%d] %s  (ETA ~%dm%02ds)", i, total, cve_name, int(eta_s // 60), int(eta_s % 60))

        cve_data = _nvd_fetch(cve_name, api_key, sleep_s)

        if cve_data is None:
            mitre_state = _mitre_check(cve_name)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            if mitre_state == "reserved":
                # Real CVE, not yet published to NVD — leave nvd_last_modified unset so next run re-checks
                update = {"vuln_status": "Reserved"}
                logger.info("  → Reserved (MITRE confirmed, will re-check next run)")
            elif mitre_state == "rejected":
                # Formally rejected — set nvd_last_modified to prevent future re-checks
                update = {"vuln_status": "Rejected", "nvd_last_modified": now}
                logger.info("  → Rejected CVE, will not re-check")
            elif mitre_state == "published":
                # NVD transient miss — re-check next run (no nvd_last_modified)
                update = {"vuln_status": "Pending NVD"}
                logger.info("  → MITRE shows Published but NVD missed it, will retry")
            else:
                # FP entity extraction — set nvd_last_modified to stop re-checking
                update = {"vuln_status": "Invalid", "nvd_last_modified": now}
                logger.info("  → Invalid CVE ID (entity extraction FP), will not re-check")
            try:
                await _update_entity(client, doc_id, update)
            except Exception:
                logger.exception("Failed to update status for %s", doc_id)
            not_found += 1
        else:
            try:
                entity_fields = _parse_entity_fields(cve_data)
                await _update_entity(client, doc_id, entity_fields)
                enriched_cves.append(cve_name)
                done += 1

                cache_doc = _build_nvd_cache_doc(cve_name, cve_data)
                if await _upsert_nvd_cache(client, doc_id, cache_doc):
                    cached += 1
            except Exception:
                logger.exception("Failed to enrich %s", doc_id)
                failed += 1

        if i < total:
            time.sleep(sleep_s)

    await client.close()
    logger.info(
        "Done. enriched=%d  cached_to_nvd_cache=%d  pending_nvd=%d  failed=%d",
        done, cached, not_found, failed,
    )

    await _rescore_clusters_for_cves(enriched_cves)
    if cached < done:
        logger.info(
            "%d CVEs written to entities only (nvd_cache not available — "
            "create index + grant kiber_app write access, then re-run with --force)",
            done - cached,
        )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Enrich CVE entities from NVD API v2")
    parser.add_argument("--api-key", help="NVD API key (or set NVD_API_KEY env var)")
    parser.add_argument("--sleep", type=float, help="Seconds between requests (default: 1 with key, 6 without)")
    parser.add_argument("--force", action="store_true", help="Re-enrich CVEs that already have NVD data")
    parser.add_argument("--dry-run", action="store_true", help="Apply mapping and count CVEs without fetching")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
