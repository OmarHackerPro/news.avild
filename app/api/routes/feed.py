"""GET /feed — main cluster feed for the home page.

Returns ranked clusters with their top article, supporting global and
personal views, multiple sort orders, and standard filters.
"""
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.routes.news import _hit_to_item
from app.api.routes.clusters import _fetch_articles_for_slugs
from app.db.opensearch import INDEX_CLUSTERS, INDEX_NEWS, get_os_client
from app.models.cluster import ClusterListResponse, ClusterSummary, ScoringFactor

router = APIRouter(prefix="/feed", tags=["feed"])


@router.get(
    "",
    response_model=ClusterListResponse,
    summary="Cluster feed",
    description=(
        "Main feed of ranked clusters for the home page. "
        "Supports global and personal views, sorting by latest or score, "
        "and filtering by category and date range."
    ),
)
async def get_feed(
    view: str = Query("global", enum=["global", "personal"], description="Feed view"),
    sort: str = Query("latest", enum=["latest", "score", "coverage", "severity", "trending"], description="Sort order"),
    category: Optional[str] = Query(None, description="Filter by category"),
    date_from: Optional[str] = Query(None, description="Start date (ISO-8601)"),
    date_to: Optional[str] = Query(None, description="End date (ISO-8601)"),
    types: str = Query("news,analysis,report", description="Comma-separated content types to include"),
    topic: Optional[str] = Query(None, description="Filter by normalized topic (e.g. malware, vulnerability)"),
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    filters: list[dict] = []

    # Topic filter: pre-query articles by topic → collect cluster_ids → filter clusters
    if topic:
        topic_resp = await get_os_client().search(
            index=INDEX_NEWS,
            body={
                "query": {"term": {"normalized_topics": topic}},
                "_source": ["cluster_id"],
                "size": 1000,
            },
        )
        topic_cluster_ids = list({
            h["_source"]["cluster_id"]
            for h in topic_resp["hits"]["hits"]
            if h["_source"].get("cluster_id")
        })
        if not topic_cluster_ids:
            return ClusterListResponse(items=[], total=0)
        filters.append({"terms": {"_id": topic_cluster_ids}})

    if category:
        filters.append({"term": {"categories": category}})
    if date_from or date_to:
        range_clause: dict = {}
        if date_from:
            range_clause["gte"] = date_from
        if date_to:
            range_clause["lte"] = date_to
        filters.append({"range": {"latest_at": range_clause}})

    if sort == "latest":
        sort_clause = [{"latest_at": {"order": "desc"}}]
    elif sort == "score":
        sort_clause = [{"score": {"order": "desc"}}, {"latest_at": {"order": "desc"}}]
    elif sort == "coverage":
        sort_clause = [{"article_count": {"order": "desc"}}, {"latest_at": {"order": "desc"}}]
    elif sort == "severity":
        sort_clause = [{"max_cvss": {"order": "desc", "missing": "_last"}}, {"latest_at": {"order": "desc"}}]
    elif sort == "trending":
        sort_clause = [{"article_count": {"order": "desc"}}, {"updated_at": {"order": "desc"}}]
    else:
        sort_clause = [{"latest_at": {"order": "desc"}}]

    body: dict = {
        "query": {"bool": {"filter": filters}} if filters else {"match_all": {}},
        "sort": sort_clause,
        "from": offset,
        "size": limit,
    }

    # TODO: view=personal — apply entity/keyword filter from user preferences
    # Requires auth dependency + preferences lookup. For now, personal == global.

    resp = await get_os_client().search(index=INDEX_CLUSTERS, body=body)
    total = resp["hits"]["total"]["value"]
    hits = resp["hits"]["hits"]

    # Batch-fetch top article per cluster
    top_slugs = []
    for h in hits:
        ids = h["_source"].get("article_ids", [])
        top_slugs.append(ids[0] if ids else None)

    allowed_types = [t.strip() for t in types.split(",") if t.strip()]

    unique_slugs = [s for s in top_slugs if s]
    article_hits = await _fetch_articles_for_slugs(unique_slugs, allowed_types=allowed_types or None)
    slug_to_article = {h["_id"]: _hit_to_item(h) for h in article_hits}

    items = []
    for h, top_slug in zip(hits, top_slugs):
        if top_slug is None or top_slug not in slug_to_article:
            continue
        src = h["_source"]
        items.append(
            ClusterSummary(
                id=h["_id"],
                label=src["label"],
                state=src.get("state", "new"),
                article_count=src.get("article_count", 0),
                top_article=slug_to_article[top_slug],
                categories=src.get("categories", []),
                score=Decimal(str(src["score"])) if src.get("score") is not None else None,
                max_cvss=src.get("max_cvss"),
                confidence=src.get("confidence"),
                top_factors=[ScoringFactor(**f) for f in (src.get("top_factors") or [])],
                latest_at=src.get("latest_at", ""),
            )
        )

    return ClusterListResponse(items=items, total=total)
