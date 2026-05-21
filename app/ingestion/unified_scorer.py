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
from app.ingestion import cluster_cache
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
_EMBED_HI = float(os.getenv("CLUSTER_EMBED_HI", "0.82"))

_KNN_K = 10
_STRUCTURED_WINDOW_DAYS = int(os.getenv("CLUSTER_STRUCTURED_WINDOW_DAYS", "30"))
_EMBED_WINDOW_DAYS = int(os.getenv("CLUSTER_EMBED_WINDOW_DAYS", "30"))
# Upper bound on how far *forward* from an article's publish date a matching cluster
# can sit. Prevents batch-reset temporal contamination: old articles processed late
# would otherwise match young clusters created earlier in the same run.
_TEMPORAL_FORWARD_DAYS = int(os.getenv("CLUSTER_TEMPORAL_FORWARD_DAYS", "14"))

# Excluded from Jaccard entity scoring — too taxonomic/ubiquitous to discriminate events.
_ENTITY_SCORING_EXCLUDE = frozenset({"vendor", "cwe"})

_SOURCE_FIELDS = [
    "article_count", "state", "entity_keys",
    "founding_entity_keys", "founding_entity_types",
    "centroid_embedding", "latest_at", "cve_ids",
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
        if e["type"] not in {"cve", "vuln_alias", "actor", "campaign"} | _ENTITY_SCORING_EXCLUDE
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
        if ft["type"] not in {"cve", "vuln_alias", "actor", "campaign"} | _ENTITY_SCORING_EXCLUDE
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

    entity_score = (
        _W_CVE * cve_overlap
        + _W_ALIAS * alias_overlap
        + _W_ACTOR * actor_campaign_overlap
        + _W_ENTITY * entity_jaccard
    )

    # Entity-anchored cluster + zero entity overlap: the article shares nothing
    # with the cluster's frozen founding identity. High cosine here means "same
    # topic", not "same event" — letting embedding merge it alone turns the
    # cluster into a topic bucket. Embedding boosts a real signal; it is never one.
    if has_entity_anchor and entity_score == 0.0:
        return 0.0

    return entity_score + _W_EMBED * embed_signal_val


def _retrieval_key(entity: dict) -> str:
    return entity["normalized_key"]


def _cache_candidates(
    article_entities: list[dict],
    article_embedding: Optional[list[float]],
    article_cve_ids: list[str],
    cutoff_structured: str,
    cutoff_embed: str,
    cutoff_forward: str,
) -> list[dict]:
    """Mirror `_structured_lookup` + `_knn_lookup` over the in-process cache.

    Lets `find_best_cluster` see clusters created earlier in the same batch run
    before OpenSearch has refreshed. Returns the same hit shape as OpenSearch.
    Empty when the cache is disabled (live ingestion).
    """
    all_hits = cluster_cache.hits()
    if not all_hits:
        return []

    selected: dict[str, dict] = {}

    # structured-equivalent: shares a non-CVE entity key OR a raw CVE ID, in window
    non_cve_keys = {_retrieval_key(e) for e in article_entities if e["type"] != "cve"}
    raw_cve_set = set(article_cve_ids)
    if non_cve_keys or raw_cve_set:
        for h in all_hits:
            src = h["_source"]
            if src.get("state") == "resolved":
                continue
            latest = src.get("latest_at") or ""
            if latest < cutoff_structured or latest > cutoff_forward:
                continue
            if non_cve_keys & set(src.get("entity_keys") or []):
                selected[h["_id"]] = h
            elif raw_cve_set & set(src.get("cve_ids") or []):
                selected[h["_id"]] = h

    # k-NN-equivalent: top-K by centroid cosine, in window, not resolved
    if article_embedding:
        a = np.array(article_embedding, dtype=np.float32)
        na = float(np.linalg.norm(a))
        scored: list[tuple[float, dict]] = []
        for h in all_hits:
            src = h["_source"]
            if src.get("state") == "resolved":
                continue
            latest = src.get("latest_at") or ""
            if latest < cutoff_embed or latest > cutoff_forward:
                continue
            centroid = src.get("centroid_embedding")
            if not centroid:
                continue
            c = np.array(centroid, dtype=np.float32)
            denom = na * float(np.linalg.norm(c))
            cos = float(np.dot(a, c) / denom) if denom > 0 else 0.0
            scored.append((cos, h))
        scored.sort(key=lambda x: x[0], reverse=True)
        for _cos, h in scored[:_KNN_K]:
            selected.setdefault(h["_id"], h)

    return list(selected.values())


async def _get_candidates(
    article_entities: list[dict],
    article_embedding: Optional[list[float]],
    article_cve_ids: Optional[list[str]] = None,
    reference_time: Optional[datetime] = None,
) -> list[dict]:
    os_client = get_os_client()
    ref = reference_time or datetime.now(timezone.utc)
    cutoff_structured = (ref - timedelta(days=_STRUCTURED_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff_embed = (ref - timedelta(days=_EMBED_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff_forward = (ref + timedelta(days=_TEMPORAL_FORWARD_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cve_ids = article_cve_ids or []

    async def _structured_lookup() -> list[dict]:
        # Non-CVE entities via entity_keys; CVEs via cluster.cve_ids directly.
        should_clauses = [
            {"term": {"entity_keys": _retrieval_key(e)}}
            for e in article_entities
            if e["type"] != "cve"
        ]
        for cve in cve_ids:
            should_clauses.append({"term": {"cve_ids": cve}})
        if not should_clauses:
            return []
        query = {
            "query": {
                "bool": {
                    "should": should_clauses,
                    "minimum_should_match": 1,
                    "filter": [
                        {"range": {"latest_at": {"gte": cutoff_structured, "lte": cutoff_forward}}},
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
                and (h["_source"].get("latest_at") or "") <= cutoff_forward
            ][:_KNN_K]
        except Exception as exc:
            logger.warning("k-NN candidate lookup failed: %s", exc)
            return []

    structured_hits, knn_hits = await asyncio.gather(_structured_lookup(), _knn_lookup())
    cache_hits = _cache_candidates(
        article_entities, article_embedding, cve_ids,
        cutoff_structured, cutoff_embed, cutoff_forward,
    )

    candidates: dict[str, dict] = {}
    # Cache entries win on id collisions — they carry the freshest same-run state.
    for hit in cache_hits:
        candidates[hit["_id"]] = hit
    for hit in structured_hits:
        candidates.setdefault(hit["_id"], hit)
    for hit in knn_hits:
        candidates.setdefault(hit["_id"], hit)

    return list(candidates.values())


async def find_best_cluster(
    article_entities: list[dict],
    article_embedding: Optional[list[float]],
    article_cve_ids: Optional[list[str]] = None,
    reference_time: Optional[datetime] = None,
) -> Optional[str]:
    """Return the cluster_id of the best matching cluster, or None to create new."""
    await ensure_idf_map()

    # Augment NER entities with synthetic CVE entities from the normalizer-extracted
    # cve_ids so scoring sees CVE overlap even when NER missed an extraction.
    cve_keys_in_entities = {e["normalized_key"] for e in article_entities if e["type"] == "cve"}
    synthetic_cves = [
        {"type": "cve", "normalized_key": cve.lower()}
        for cve in (article_cve_ids or [])
        if cve.lower() not in cve_keys_in_entities
    ]
    scoring_entities = article_entities + synthetic_cves

    candidates = await _get_candidates(article_entities, article_embedding, article_cve_ids, reference_time)
    if not candidates:
        return None

    best_id: Optional[str] = None
    best_score = -1.0

    for hit in candidates:
        score = _compute_score(scoring_entities, hit["_source"], article_embedding)
        if score > best_score:
            best_score = score
            best_id = hit["_id"]

    if best_score >= ASSIGN_THRESHOLD:
        logger.debug("Best cluster %s score=%.3f", best_id, best_score)
        return best_id

    return None
