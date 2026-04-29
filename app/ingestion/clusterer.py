"""Cluster assignment: unified scorer replaces the 3-tier CVE/entity/MLT waterfall.

Public API (unchanged from previous version):
  cluster_article(article, slug, entities) → None
  merge_into_cluster(cluster_id, slug, entity_keys, cve_ids, *, ...) → None
  create_cluster(article, entities, *, embedding) → str
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from app.db.opensearch import INDEX_CLUSTERS, INDEX_NEWS, get_os_client
from app.ingestion.embedding_client import embed_text
from app.ingestion.scorer import rescore_cluster
from app.ingestion.unified_scorer import find_best_cluster

logger = logging.getLogger(__name__)

_EMBED_INPUT_MAX = 400  # chars of summary/desc to include in embedding input


def _build_embed_input(article: dict) -> str:
    text = article.get("title", "")
    snippet = article.get("summary") or article.get("desc") or ""
    if snippet:
        text += ". " + snippet[:_EMBED_INPUT_MAX]
    return text


def _build_event_signature(entities: list[dict], cve_ids: list[str]) -> dict:
    sig: dict = {
        "cve_ids": list(dict.fromkeys(cve_ids)),
        "vuln_aliases": [],
        "campaign_names": [],
        "affected_products": [],
        "primary_actors": [],
        "confidence": "low",
    }
    for e in entities:
        t = e["type"]
        k = e["normalized_key"]
        if t == "vuln_alias":
            sig["vuln_aliases"].append(k)
        elif t == "campaign":
            sig["campaign_names"].append(k)
        elif t == "product":
            sig["affected_products"].append(k)
        elif t == "actor":
            sig["primary_actors"].append(k)

    if len(sig["cve_ids"]) >= 2 or (sig["cve_ids"] and sig["vuln_aliases"]):
        sig["confidence"] = "high"
    elif sig["cve_ids"] or sig["vuln_aliases"] or sig["campaign_names"]:
        sig["confidence"] = "medium"
    return sig


def _updated_centroid(
    old_centroid: Optional[list[float]], new_vec: list[float], n: int
) -> list[float]:
    """Running average: new_centroid = (old * (n-1) + new) / n."""
    if old_centroid is None or n <= 1:
        return new_vec
    import numpy as np
    c = (np.array(old_centroid) * (n - 1) + np.array(new_vec)) / n
    return c.tolist()


async def cluster_article(
    article: dict,
    slug: str,
    entities: list[dict],
) -> None:
    """Assign article to an existing cluster or create a new one."""
    cve_ids: list[str] = article.get("cve_ids") or []
    embedding = await embed_text(_build_embed_input(article))

    cluster_id = await find_best_cluster(entities, embedding)

    if cluster_id:
        await merge_into_cluster(
            cluster_id,
            slug,
            [e["normalized_key"] for e in entities],
            cve_ids,
            source_name=article.get("source_name", ""),
            title=article.get("title", ""),
            published_at=article.get("published_at", ""),
            cvss_score=article.get("cvss_score"),
            credibility_weight=float(article.get("credibility_weight") or 1.0),
            new_entities=entities,
            new_embedding=embedding,
        )
    else:
        await create_cluster(article, entities, embedding=embedding)


async def merge_into_cluster(
    cluster_id: str,
    article_slug: str,
    entity_keys: list[str],
    cve_ids: list[str],
    *,
    source_name: str = "",
    title: str = "",
    published_at: str = "",
    cvss_score: Optional[float] = None,
    credibility_weight: float = 1.0,
    new_entities: Optional[list[dict]] = None,
    new_embedding: Optional[list[float]] = None,
) -> None:
    os_client = get_os_client()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Fetch current cluster to compute centroid update and merge event_signature
    try:
        existing = await os_client.get(index=INDEX_CLUSTERS, id=cluster_id, _source=True)
        src = existing["_source"]
        old_centroid = src.get("centroid_embedding")
        old_count = src.get("article_count", 1)
        old_sig = src.get("event_signature") or {}
    except Exception:
        old_centroid = None
        old_count = 1
        old_sig = {}

    new_count = old_count + 1
    new_centroid = (
        _updated_centroid(old_centroid, new_embedding, new_count)
        if new_embedding
        else old_centroid
    )

    # Merge event_signature fields
    new_sig_entities = new_entities or []
    sig_update = {
        "cve_ids": list(dict.fromkeys((old_sig.get("cve_ids") or []) + cve_ids)),
        "vuln_aliases": list(dict.fromkeys(
            (old_sig.get("vuln_aliases") or []) +
            [e["normalized_key"] for e in new_sig_entities if e["type"] == "vuln_alias"]
        )),
        "campaign_names": list(dict.fromkeys(
            (old_sig.get("campaign_names") or []) +
            [e["normalized_key"] for e in new_sig_entities if e["type"] == "campaign"]
        )),
        "affected_products": list(dict.fromkeys(
            (old_sig.get("affected_products") or []) +
            [e["normalized_key"] for e in new_sig_entities if e["type"] == "product"]
        )),
        "primary_actors": list(dict.fromkeys(
            (old_sig.get("primary_actors") or []) +
            [e["normalized_key"] for e in new_sig_entities if e["type"] == "actor"]
        )),
    }
    if len(sig_update["cve_ids"]) >= 2 or (sig_update["cve_ids"] and sig_update["vuln_aliases"]):
        sig_update["confidence"] = "high"
    elif sig_update["cve_ids"] or sig_update["vuln_aliases"] or sig_update["campaign_names"]:
        sig_update["confidence"] = "medium"
    else:
        sig_update["confidence"] = old_sig.get("confidence", "low")

    script_source = """
        // Dedup and add article
        if (!ctx._source.article_ids.contains(params.slug)) {
            ctx._source.article_ids.add(params.slug);
            ctx._source.article_count += 1;
        }

        // Lifecycle state
        if (ctx._source.article_count >= 3) {
            ctx._source.state = 'confirmed';
        } else if (ctx._source.article_count >= 2) {
            if (ctx._source.state == 'new') ctx._source.state = 'developing';
        }

        // Entity keys (dedup)
        for (key in params.entity_keys) {
            if (!ctx._source.entity_keys.contains(key)) {
                ctx._source.entity_keys.add(key);
            }
        }

        // CVE ids (dedup, grow)
        for (cve in params.cve_ids) {
            if (!ctx._source.cve_ids.contains(cve)) {
                ctx._source.cve_ids.add(cve);
            }
        }

        // CVSS max
        if (params.cvss_score != null && params.cvss_score > ctx._source.max_cvss) {
            ctx._source.max_cvss = params.cvss_score;
        }

        // Credibility max
        if (params.credibility_weight > ctx._source.max_credibility_weight) {
            ctx._source.max_credibility_weight = params.credibility_weight;
        }

        // Timeline (dedup by slug)
        boolean found = false;
        for (entry in ctx._source.timeline) {
            if (entry.article_slug == params.slug) { found = true; break; }
        }
        if (!found) {
            ctx._source.timeline.add(params.timeline_entry);
        }

        // Timestamps
        if (params.published_at > ctx._source.latest_at) {
            ctx._source.latest_at = params.published_at;
        }
        ctx._source.updated_at = params.now;

        // Event signature
        ctx._source.event_signature = params.event_signature;

        // Centroid embedding
        if (params.centroid != null) {
            ctx._source.centroid_embedding = params.centroid;
        }
    """

    await os_client.update(
        index=INDEX_CLUSTERS,
        id=cluster_id,
        body={
            "script": {
                "source": script_source,
                "lang": "painless",
                "params": {
                    "slug": article_slug,
                    "entity_keys": entity_keys,
                    "cve_ids": cve_ids,
                    "cvss_score": cvss_score,
                    "credibility_weight": credibility_weight,
                    "published_at": published_at or now,
                    "now": now,
                    "timeline_entry": {
                        "article_slug": article_slug,
                        "source_name": source_name,
                        "title": title,
                        "published_at": published_at or now,
                        "added_at": now,
                    },
                    "event_signature": sig_update,
                    "centroid": new_centroid,
                },
            },
        },
        retry_on_conflict=3,
    )

    await os_client.update(
        index=INDEX_NEWS,
        id=article_slug,
        body={"doc": {"cluster_id": cluster_id}},
        retry_on_conflict=3,
    )

    await _rescore(cluster_id)


async def create_cluster(
    article: dict,
    entities: list[dict],
    *,
    embedding: Optional[list[float]] = None,
) -> str:
    os_client = get_os_client()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    slug = article.get("slug", "")
    cve_ids: list[str] = article.get("cve_ids") or []
    entity_keys = [e["normalized_key"] for e in entities]
    published_at = article.get("published_at") or now

    doc = {
        "label": article.get("title", ""),
        "state": "new",
        "summary": "",
        "why_it_matters": "",
        "score": 0.0,
        "confidence": "low",
        "max_cvss": article.get("cvss_score") or 0.0,
        "cisa_kev": False,
        "max_credibility_weight": float(article.get("credibility_weight") or 1.0),
        "top_factors": [],
        "article_ids": [slug],
        "categories": [article.get("category")] if article.get("category") else [],
        "tags": [],
        "article_count": 1,
        "cve_ids": cve_ids,
        "seed_cve_ids": cve_ids,
        "entity_keys": entity_keys,
        "event_signature": _build_event_signature(entities, cve_ids),
        "centroid_embedding": embedding,
        "merged_into": None,
        "timeline": [{
            "article_slug": slug,
            "source_name": article.get("source_name", ""),
            "title": article.get("title", ""),
            "published_at": published_at,
            "added_at": now,
        }],
        "latest_at": published_at,
        "created_at": now,
        "updated_at": now,
    }

    resp = await os_client.index(index=INDEX_CLUSTERS, body=doc)
    cluster_id = resp["_id"]

    await os_client.update(
        index=INDEX_NEWS,
        id=slug,
        body={"doc": {"cluster_id": cluster_id}},
        retry_on_conflict=3,
    )
    await _rescore(cluster_id)
    return cluster_id


async def _rescore(cluster_id: str) -> None:
    try:
        await rescore_cluster(cluster_id)
    except Exception as exc:
        logger.warning("Rescore failed for %s: %s", cluster_id, exc)
