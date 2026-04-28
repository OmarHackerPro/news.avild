"""Unified cluster scoring: replaces the 3-tier CVE/entity/MLT waterfall.

Candidate retrieval: OpenSearch structured terms + k-NN, then score each candidate.
Best cluster above ASSIGN_THRESHOLD wins; None means create a new cluster.
"""
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from app.db.opensearch import INDEX_CLUSTERS, get_os_client

logger = logging.getLogger(__name__)

ASSIGN_THRESHOLD = float(os.getenv("CLUSTER_SCORE_THRESHOLD", "0.30"))
MERGE_THRESHOLD = float(os.getenv("CLUSTER_MERGE_THRESHOLD", "0.55"))

_W_CVE = float(os.getenv("CLUSTER_WEIGHT_CVE", "0.45"))
_W_ALIAS = float(os.getenv("CLUSTER_WEIGHT_ALIAS", "0.25"))
_W_ENTITY = float(os.getenv("CLUSTER_WEIGHT_ENTITY", "0.15"))
_W_EMBED = float(os.getenv("CLUSTER_WEIGHT_EMBED", "0.15"))

_KNN_K = 10
_STRUCTURED_WINDOW_DAYS = 14
_EMBED_WINDOW_HOURS = 72

_SOURCE_FIELDS = [
    "article_count", "state", "entity_keys",
    "event_signature", "centroid_embedding",
]


def _compute_score(
    article_entities: list[dict],
    cluster_source: dict,
    article_embedding: Optional[list[float]],
) -> float:
    sig = cluster_source.get("event_signature") or {}

    art_cves = {e["normalized_key"] for e in article_entities if e["type"] == "cve"}
    art_aliases = {
        e["normalized_key"]
        for e in article_entities
        if e["type"] in ("vuln_alias", "campaign")
    }
    art_others = {
        e["normalized_key"]
        for e in article_entities
        if e["type"] not in ("cve", "vuln_alias", "campaign", "vendor")
    }

    cl_cves = set(sig.get("cve_ids") or [])
    cl_aliases = set((sig.get("vuln_aliases") or []) + (sig.get("campaign_names") or []))
    cl_others = set(cluster_source.get("entity_keys") or []) - cl_cves - cl_aliases

    cve_overlap = 1.0 if art_cves & cl_cves else 0.0
    alias_overlap = 1.0 if art_aliases & cl_aliases else 0.0

    union_others = art_others | cl_others
    entity_jaccard = (
        len(art_others & cl_others) / len(union_others) if union_others else 0.0
    )

    cosine = 0.0
    centroid = cluster_source.get("centroid_embedding")
    if article_embedding and centroid:
        a = np.array(article_embedding, dtype=np.float32)
        c = np.array(centroid, dtype=np.float32)
        denom = np.linalg.norm(a) * np.linalg.norm(c)
        if denom > 0:
            cosine = max(0.0, float(np.dot(a, c) / denom))

    return (
        _W_CVE * cve_overlap
        + _W_ALIAS * alias_overlap
        + _W_ENTITY * entity_jaccard
        + _W_EMBED * cosine
    )


async def _get_candidates(
    article_entities: list[dict],
    article_embedding: Optional[list[float]],
) -> list[dict]:
    os_client = get_os_client()
    now = datetime.now(timezone.utc)
    cutoff_14d = (now - timedelta(days=_STRUCTURED_WINDOW_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    cutoff_72h = (now - timedelta(hours=_EMBED_WINDOW_HOURS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    cve_ids = [e["normalized_key"] for e in article_entities if e["type"] == "cve"]
    vuln_aliases = [e["normalized_key"] for e in article_entities if e["type"] == "vuln_alias"]
    campaign_names = [e["normalized_key"] for e in article_entities if e["type"] == "campaign"]

    candidates: dict[str, dict] = {}

    # Structured lookup (terms query on event_signature)
    should_clauses = []
    for cve in cve_ids:
        should_clauses.append({"term": {"event_signature.cve_ids": cve}})
    for alias in vuln_aliases:
        should_clauses.append({"term": {"event_signature.vuln_aliases": alias}})
    for campaign in campaign_names:
        should_clauses.append({"term": {"event_signature.campaign_names": campaign}})

    if should_clauses:
        structured_query = {
            "query": {
                "bool": {
                    "should": should_clauses,
                    "minimum_should_match": 1,
                    "filter": [
                        {"range": {"latest_at": {"gte": cutoff_14d}}},
                        {"bool": {"must_not": [{"term": {"state": "resolved"}}]}},
                    ],
                }
            },
            "_source": _SOURCE_FIELDS,
            "size": 20,
        }
        try:
            resp = await os_client.search(index=INDEX_CLUSTERS, body=structured_query)
            for hit in resp["hits"]["hits"]:
                candidates[hit["_id"]] = hit
        except Exception as exc:
            logger.warning("Structured candidate lookup failed: %s", exc)

    # k-NN lookup (embedding similarity)
    if article_embedding:
        knn_query = {
            "size": _KNN_K,
            "query": {
                "knn": {
                    "centroid_embedding": {
                        "vector": article_embedding,
                        "k": _KNN_K,
                        "filter": {
                            "bool": {
                                "must": [{"range": {"latest_at": {"gte": cutoff_72h}}}],
                                "must_not": [{"term": {"state": "resolved"}}],
                            }
                        },
                    }
                }
            },
            "_source": _SOURCE_FIELDS,
        }
        try:
            resp = await os_client.search(index=INDEX_CLUSTERS, body=knn_query)
            for hit in resp["hits"]["hits"]:
                if hit["_id"] not in candidates:
                    candidates[hit["_id"]] = hit
        except Exception as exc:
            logger.warning("k-NN candidate lookup failed: %s", exc)

    return list(candidates.values())


async def find_best_cluster(
    article_entities: list[dict],
    article_embedding: Optional[list[float]],
) -> Optional[str]:
    """Return the cluster_id of the best matching cluster, or None to create new."""
    candidates = await _get_candidates(article_entities, article_embedding)
    if not candidates:
        return None

    best_id: Optional[str] = None
    best_score = -1.0

    for hit in candidates:
        score = _compute_score(article_entities, hit["_source"], article_embedding)
        if score > best_score:
            best_score = score
            best_id = hit["_id"]

    if best_score >= ASSIGN_THRESHOLD:
        logger.debug("Best cluster %s score=%.3f", best_id, best_score)
        return best_id

    return None
