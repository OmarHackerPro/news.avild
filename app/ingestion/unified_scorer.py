"""Unified cluster scoring: replaces the 3-tier CVE/entity/MLT waterfall.

Candidate retrieval: OpenSearch structured terms + k-NN, then score each candidate.
Best cluster above ASSIGN_THRESHOLD wins; None means create a new cluster.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from app.db.opensearch import INDEX_CLUSTERS, get_os_client
from app.ingestion.entity_idf import ensure_idf_map, idf

logger = logging.getLogger(__name__)

ASSIGN_THRESHOLD = float(os.getenv("CLUSTER_SCORE_THRESHOLD", "0.31"))
MERGE_THRESHOLD = float(os.getenv("CLUSTER_MERGE_THRESHOLD", "0.55"))

_W_CVE = float(os.getenv("CLUSTER_WEIGHT_CVE", "0.10"))
_W_ALIAS = float(os.getenv("CLUSTER_WEIGHT_ALIAS", "0.15"))
_W_ACTOR = float(os.getenv("CLUSTER_WEIGHT_ACTOR", "0.22"))
_W_ENTITY = float(os.getenv("CLUSTER_WEIGHT_ENTITY", "0.18"))
_W_EMBED = float(os.getenv("CLUSTER_WEIGHT_EMBED", "0.35"))

_EMBED_LO = float(os.getenv("CLUSTER_EMBED_LO", "0.75"))
_EMBED_HI = float(os.getenv("CLUSTER_EMBED_HI", "0.90"))

_KNN_K = 10
_STRUCTURED_WINDOW_DAYS = int(os.getenv("CLUSTER_STRUCTURED_WINDOW_DAYS", "30"))
_EMBED_WINDOW_DAYS = int(os.getenv("CLUSTER_EMBED_WINDOW_DAYS", "30"))

_SOURCE_FIELDS = [
    "article_count", "state", "entity_keys",
    "founding_entity_keys", "founding_entity_types",
    "centroid_embedding", "latest_at",
]


def _embed_signal(cosine: float) -> float:
    """Calibration curve: 0 below _EMBED_LO, 1.0 at/above _EMBED_HI, linear between."""
    if _EMBED_HI <= _EMBED_LO:
        return 1.0 if cosine >= _EMBED_HI else 0.0
    return max(0.0, min(1.0, (cosine - _EMBED_LO) / (_EMBED_HI - _EMBED_LO)))


def _compute_score(
    article_entities: list[dict],
    cluster_source: dict,
    article_embedding: Optional[list[float]],
) -> float:
    # --- Article signal sets ---
    art_cves = {e["normalized_key"] for e in article_entities if e["type"] == "cve"}
    art_vuln_aliases = {
        e["normalized_key"] for e in article_entities if e["type"] == "vuln_alias"
    }
    art_actors_campaigns = {
        e["normalized_key"]
        for e in article_entities
        if e["type"] in ("actor", "campaign")
    }
    art_others = {
        e["normalized_key"]
        for e in article_entities
        if e["type"] not in ("cve", "vuln_alias", "actor", "campaign")
    }

    # --- Founding cluster signal sets (frozen at create time, never accumulated) ---
    founding_types = cluster_source.get("founding_entity_types") or []
    founding_cves = {ft["key"] for ft in founding_types if ft["type"] == "cve"}
    founding_vuln_aliases = {
        ft["key"] for ft in founding_types if ft["type"] == "vuln_alias"
    }
    founding_actors_campaigns = {
        ft["key"] for ft in founding_types if ft["type"] in ("actor", "campaign")
    }
    founding_others = {
        ft["key"]
        for ft in founding_types
        if ft["type"] not in ("cve", "vuln_alias", "actor", "campaign")
    }
    has_entity_anchor = bool(founding_types)

    # --- CVE and alias overlap (binary: a specific CVE match is always strong signal) ---
    cve_overlap = 1.0 if art_cves & founding_cves else 0.0
    alias_overlap = 1.0 if art_vuln_aliases & founding_vuln_aliases else 0.0

    # --- Actor/campaign overlap (IDF-weighted Jaccard — penalises mega-clusters) ---
    union_actors = art_actors_campaigns | founding_actors_campaigns
    shared_actors = art_actors_campaigns & founding_actors_campaigns
    if union_actors:
        num = sum(idf(k) for k in shared_actors)
        den = sum(idf(k) for k in union_actors)
        actor_campaign_overlap = num / den if den else 0.0
    else:
        actor_campaign_overlap = 0.0

    # --- Other entity overlap (IDF-weighted Jaccard — product/tool/malware/vendor) ---
    union_others = art_others | founding_others
    shared_others = art_others & founding_others
    if union_others:
        num = sum(idf(k) for k in shared_others)
        den = sum(idf(k) for k in union_others)
        entity_jaccard = num / den if den else 0.0
    else:
        entity_jaccard = 0.0

    # --- Embedding signal ---
    cosine = 0.0
    centroid = cluster_source.get("centroid_embedding")
    if article_embedding and centroid:
        a = np.array(article_embedding, dtype=np.float32)
        c = np.array(centroid, dtype=np.float32)
        denom = np.linalg.norm(a) * np.linalg.norm(c)
        if denom > 0:
            cosine = max(0.0, float(np.dot(a, c) / denom))

    # Entity-free clusters (no founding signal) require near-identical embedding
    # to merge — prevents editorial/topic drift clusters from absorbing loosely
    # related articles.
    if has_entity_anchor:
        embed_signal_val = _embed_signal(cosine)
    else:
        embed_signal_val = 1.0 if cosine >= _EMBED_HI else 0.0

    return (
        _W_CVE * cve_overlap
        + _W_ALIAS * alias_overlap
        + _W_ACTOR * actor_campaign_overlap
        + _W_ENTITY * entity_jaccard
        + _W_EMBED * embed_signal_val
    )


def _retrieval_key(entity: dict) -> str:
    """entity_keys stores CVE IDs uppercase; all other types lowercase."""
    key = entity["normalized_key"]
    return key.upper() if entity["type"] == "cve" else key


async def _get_candidates(
    article_entities: list[dict],
    article_embedding: Optional[list[float]],
    reference_time: Optional[datetime] = None,
) -> list[dict]:
    os_client = get_os_client()
    ref = reference_time or datetime.now(timezone.utc)
    cutoff_structured = (ref - timedelta(days=_STRUCTURED_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff_embed = (ref - timedelta(days=_EMBED_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")

    async def _structured_lookup() -> list[dict]:
        should_clauses = [
            {"term": {"entity_keys": _retrieval_key(e)}}
            for e in article_entities
        ]
        if not should_clauses:
            return []
        query = {
            "query": {
                "bool": {
                    "should": should_clauses,
                    "minimum_should_match": 1,
                    "filter": [
                        {"range": {"latest_at": {"gte": cutoff_structured}}},
                        {"bool": {"must_not": [{"term": {"state": "resolved"}}]}},
                    ],
                }
            },
            "_source": _SOURCE_FIELDS,
            "size": 20,
        }
        try:
            resp = await os_client.search(index=INDEX_CLUSTERS, body=query)
            return resp["hits"]["hits"]
        except Exception as exc:
            logger.warning("Structured candidate lookup failed: %s", exc)
            return []

    async def _knn_lookup() -> list[dict]:
        if not article_embedding:
            return []
        # NMSLIB does not support filters inside knn queries; post-filter in Python
        query = {
            "size": _KNN_K * 3,  # fetch extra to absorb post-filter losses
            "query": {
                "knn": {
                    "centroid_embedding": {
                        "vector": article_embedding,
                        "k": _KNN_K * 3,
                    }
                }
            },
            "_source": _SOURCE_FIELDS,
        }
        try:
            resp = await os_client.search(index=INDEX_CLUSTERS, body=query)
            hits = resp["hits"]["hits"]
            return [
                h for h in hits
                if h["_source"].get("state") != "resolved"
                and (h["_source"].get("latest_at") or "") >= cutoff_embed
            ][:_KNN_K]
        except Exception as exc:
            logger.warning("k-NN candidate lookup failed: %s", exc)
            return []

    structured_hits, knn_hits = await asyncio.gather(_structured_lookup(), _knn_lookup())

    candidates: dict[str, dict] = {}
    for hit in structured_hits:
        candidates[hit["_id"]] = hit
    for hit in knn_hits:
        if hit["_id"] not in candidates:
            candidates[hit["_id"]] = hit

    return list(candidates.values())


async def find_best_cluster(
    article_entities: list[dict],
    article_embedding: Optional[list[float]],
    reference_time: Optional[datetime] = None,
) -> Optional[str]:
    """Return the cluster_id of the best matching cluster, or None to create new."""
    await ensure_idf_map()
    candidates = await _get_candidates(article_entities, article_embedding, reference_time)
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
