"""Select top clusters from OpenSearch for the daily brief."""
import logging
from typing import Any

from app.db.opensearch import INDEX_CLUSTERS

logger = logging.getLogger(__name__)

_SOURCE_FIELDS = [
    "label", "summary", "why_it_matters", "score",
    "max_cvss", "cisa_kev", "cve_ids", "article_count", "entity_keys",
]


async def fetch_top_clusters(
    client: Any,
    top_n: int = 7,
    hours: int = 24,
) -> list[dict]:
    """Return up to top_n clusters with activity in the last `hours` hours, sorted by score desc."""
    body = {
        "size": top_n,
        "_source": _SOURCE_FIELDS,
        "query": {
            "range": {
                "latest_at": {"gte": f"now-{hours}h"}
            }
        },
        "sort": [{"score": {"order": "desc"}}],
    }
    try:
        resp = await client.search(index=INDEX_CLUSTERS, body=body)
    except Exception as exc:
        logger.error("OpenSearch cluster query failed: %s", exc)
        return []

    clusters = []
    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        clusters.append({
            "id": hit["_id"],
            "label": src.get("label", ""),
            "summary": src.get("summary", ""),
            "why_it_matters": src.get("why_it_matters", ""),
            "score": src.get("score", 0.0),
            "max_cvss": src.get("max_cvss"),
            "cisa_kev": src.get("cisa_kev", False),
            "cve_ids": src.get("cve_ids") or [],
            "article_count": src.get("article_count", 0),
            "entity_keys": src.get("entity_keys") or [],
        })
    return clusters
