#!/usr/bin/env python
"""Detect and merge duplicate clusters that formed independently.

Scans recently-updated clusters for event_signature overlap, scores each
candidate pair with the same formula used during ingestion, and merges
clusters above MERGE_THRESHOLD.

Usage:
    python scripts/detect_cluster_merges.py
    python scripts/detect_cluster_merges.py --dry-run
    python scripts/detect_cluster_merges.py --window-hours 48
"""
import asyncio
import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

import numpy as np

from app.db.opensearch import INDEX_CLUSTERS, INDEX_NEWS, get_os_client
from app.ingestion.unified_scorer import MERGE_THRESHOLD, _compute_score
from app.ingestion.scorer import rescore_cluster

logger = logging.getLogger(__name__)

_FETCH_FIELDS = [
    "article_count", "article_ids", "entity_keys", "cve_ids",
    "event_signature", "centroid_embedding", "state", "timeline",
    "label", "latest_at", "max_cvss", "max_credibility_weight",
]


def _entities_from_signature(sig: dict) -> list[dict]:
    entities = []
    for cve in sig.get("cve_ids") or []:
        entities.append({"type": "cve", "normalized_key": cve})
    for alias in sig.get("vuln_aliases") or []:
        entities.append({"type": "vuln_alias", "normalized_key": alias})
    for campaign in sig.get("campaign_names") or []:
        entities.append({"type": "campaign", "normalized_key": campaign})
    for product in sig.get("affected_products") or []:
        entities.append({"type": "product", "normalized_key": product})
    for actor in sig.get("primary_actors") or []:
        entities.append({"type": "actor", "normalized_key": actor})
    return entities


async def _fetch_recent_clusters(window_hours: int) -> list[dict]:
    client = get_os_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    results = []
    page_size = 100
    from_offset = 0

    while True:
        resp = await client.search(
            index=INDEX_CLUSTERS,
            body={
                "query": {
                    "bool": {
                        "filter": [{"range": {"updated_at": {"gte": cutoff}}}],
                        "must_not": [{"term": {"state": "resolved"}}],
                    }
                },
                "_source": _FETCH_FIELDS,
                "size": page_size,
                "from": from_offset,
            },
        )
        hits = resp["hits"]["hits"]
        if not hits:
            break
        results.extend(hits)
        from_offset += len(hits)
        if len(hits) < page_size:
            break

    return results


async def _find_overlap_candidates(cluster_id: str, sig: dict) -> list[dict]:
    client = get_os_client()
    cve_ids = sig.get("cve_ids") or []
    vuln_aliases = sig.get("vuln_aliases") or []
    campaign_names = sig.get("campaign_names") or []

    should_clauses = []
    if cve_ids:
        should_clauses.append({"terms": {"event_signature.cve_ids": cve_ids}})
    if vuln_aliases:
        should_clauses.append({"terms": {"event_signature.vuln_aliases": vuln_aliases}})
    if campaign_names:
        should_clauses.append({"terms": {"event_signature.campaign_names": campaign_names}})

    if not should_clauses:
        return []

    query = {
        "query": {
            "bool": {
                "should": should_clauses,
                "minimum_should_match": 1,
                "must_not": [
                    {"term": {"state": "resolved"}},
                    {"ids": {"values": [cluster_id]}},
                ],
            }
        },
        "_source": _FETCH_FIELDS,
        "size": 20,
    }

    try:
        resp = await client.search(index=INDEX_CLUSTERS, body=query)
        return resp["hits"]["hits"]
    except Exception as exc:
        logger.warning("Candidate lookup failed for cluster %s: %s", cluster_id, exc)
        return []


async def _merge_clusters(
    surviving_id: str,
    surviving_src: dict,
    dissolved_id: str,
    dissolved_src: dict,
    dry_run: bool,
) -> None:
    client = get_os_client()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    s_count = surviving_src.get("article_count") or 1
    d_count = dissolved_src.get("article_count") or 1

    s_centroid = surviving_src.get("centroid_embedding")
    d_centroid = dissolved_src.get("centroid_embedding")
    if s_centroid and d_centroid:
        s_arr = np.array(s_centroid, dtype=np.float32)
        d_arr = np.array(d_centroid, dtype=np.float32)
        merged_centroid = ((s_arr * s_count + d_arr * d_count) / (s_count + d_count)).tolist()
    else:
        merged_centroid = s_centroid or d_centroid

    d_article_ids = dissolved_src.get("article_ids") or []
    d_entity_keys = dissolved_src.get("entity_keys") or []
    d_cve_ids = dissolved_src.get("cve_ids") or []
    d_timeline = dissolved_src.get("timeline") or []

    sig_s = surviving_src.get("event_signature") or {}
    sig_d = dissolved_src.get("event_signature") or {}

    merged_sig = {
        "cve_ids": list(dict.fromkeys((sig_s.get("cve_ids") or []) + (sig_d.get("cve_ids") or []))),
        "vuln_aliases": list(dict.fromkeys((sig_s.get("vuln_aliases") or []) + (sig_d.get("vuln_aliases") or []))),
        "campaign_names": list(dict.fromkeys((sig_s.get("campaign_names") or []) + (sig_d.get("campaign_names") or []))),
        "affected_products": list(dict.fromkeys((sig_s.get("affected_products") or []) + (sig_d.get("affected_products") or []))),
        "primary_actors": list(dict.fromkeys((sig_s.get("primary_actors") or []) + (sig_d.get("primary_actors") or []))),
    }
    if len(merged_sig["cve_ids"]) >= 2 or (merged_sig["cve_ids"] and merged_sig["vuln_aliases"]):
        merged_sig["confidence"] = "high"
    elif merged_sig["cve_ids"] or merged_sig["vuln_aliases"] or merged_sig["campaign_names"]:
        merged_sig["confidence"] = "medium"
    else:
        merged_sig["confidence"] = sig_s.get("confidence", "low")

    if dry_run:
        return

    absorb_script = """
        for (art in params.article_ids) {
            if (!ctx._source.article_ids.contains(art)) {
                ctx._source.article_ids.add(art);
                ctx._source.article_count += 1;
            }
        }
        if (ctx._source.article_count >= 3) {
            ctx._source.state = 'confirmed';
        } else if (ctx._source.article_count >= 2 && ctx._source.state == 'new') {
            ctx._source.state = 'developing';
        }
        for (key in params.entity_keys) {
            if (!ctx._source.entity_keys.contains(key)) {
                ctx._source.entity_keys.add(key);
            }
        }
        for (cve in params.cve_ids) {
            if (!ctx._source.cve_ids.contains(cve)) {
                ctx._source.cve_ids.add(cve);
            }
        }
        for (entry in params.timeline) {
            boolean found = false;
            for (ex in ctx._source.timeline) {
                if (ex.article_slug == entry.article_slug) { found = true; break; }
            }
            if (!found) { ctx._source.timeline.add(entry); }
        }
        ctx._source.event_signature = params.event_signature;
        ctx._source.centroid_embedding = params.centroid;
        ctx._source.updated_at = params.now;
    """

    await client.update(
        index=INDEX_CLUSTERS,
        id=surviving_id,
        body={
            "script": {
                "source": absorb_script,
                "lang": "painless",
                "params": {
                    "article_ids": d_article_ids,
                    "entity_keys": d_entity_keys,
                    "cve_ids": d_cve_ids,
                    "timeline": d_timeline,
                    "event_signature": merged_sig,
                    "centroid": merged_centroid,
                    "now": now,
                },
            }
        },
        retry_on_conflict=3,
    )

    await client.update(
        index=INDEX_CLUSTERS,
        id=dissolved_id,
        body={
            "doc": {
                "state": "resolved",
                "merged_into": surviving_id,
                "updated_at": now,
            }
        },
        retry_on_conflict=3,
    )

    if d_article_ids:
        bulk_body = []
        for slug in d_article_ids:
            bulk_body.append({"update": {"_index": INDEX_NEWS, "_id": slug}})
            bulk_body.append({"doc": {"cluster_id": surviving_id}})
        await client.bulk(body=bulk_body, params={"refresh": "false"})

    try:
        await rescore_cluster(surviving_id)
    except Exception as exc:
        logger.warning("Rescore failed for %s after merge: %s", surviving_id, exc)


async def main(args: argparse.Namespace) -> None:
    recent = await _fetch_recent_clusters(args.window_hours)
    logger.info("Found %d recently-updated clusters to inspect.", len(recent))

    # scored_pairs maps sorted(id_a, id_b) → (score, surviving_id, surviving_src, dissolved_id, dissolved_src)
    # Sorted pair key ensures (A,B) and (B,A) are deduplicated.
    scored_pairs: dict[tuple[str, str], tuple] = {}

    for hit in recent:
        cluster_id = hit["_id"]
        src = hit["_source"]
        sig = src.get("event_signature") or {}

        candidates = await _find_overlap_candidates(cluster_id, sig)
        if not candidates:
            continue

        article_entities = _entities_from_signature(sig)
        embedding = src.get("centroid_embedding")

        for cand in candidates:
            cand_id = cand["_id"]
            pair_key = tuple(sorted([cluster_id, cand_id]))
            if pair_key in scored_pairs:
                continue

            score = _compute_score(article_entities, cand["_source"], embedding)
            if score < MERGE_THRESHOLD:
                continue

            a_count = src.get("article_count") or 1
            b_count = cand["_source"].get("article_count") or 1
            if a_count >= b_count:
                surviving_id, surviving_src = cluster_id, src
                dissolved_id, dissolved_src = cand_id, cand["_source"]
            else:
                surviving_id, surviving_src = cand_id, cand["_source"]
                dissolved_id, dissolved_src = cluster_id, src

            scored_pairs[pair_key] = (score, surviving_id, surviving_src, dissolved_id, dissolved_src)

    merges_executed = 0
    for score, surviving_id, surviving_src, dissolved_id, dissolved_src in scored_pairs.values():
        s_count = surviving_src.get("article_count") or 1
        d_count = dissolved_src.get("article_count") or 1
        logger.info(
            "Merging cluster %s (%d arts) into %s (%d arts), score=%.3f",
            dissolved_id, d_count, surviving_id, s_count, score,
        )
        if args.dry_run:
            continue
        await _merge_clusters(surviving_id, surviving_src, dissolved_id, dissolved_src, dry_run=False)
        merges_executed += 1

    logger.info(
        "=== Done: pairs_checked=%d merges_executed=%d ===",
        len(scored_pairs), merges_executed,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Detect and merge duplicate clusters")
    parser.add_argument("--dry-run", action="store_true", help="Print pairs without executing merges")
    parser.add_argument("--window-hours", type=int, default=24, help="How far back to look for recently-updated clusters")
    asyncio.run(main(parser.parse_args()))
