"""GET /feed — main cluster feed for the home page.

Returns ranked clusters with their top article, supporting global and
personal views, multiple sort orders, and standard filters.
"""
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.routes.news import _hit_to_item
from app.api.routes.clusters import _fetch_articles_for_slugs
from app.db.opensearch import INDEX_CLUSTERS, get_os_client
from app.models.cluster import ClusterListResponse, ClusterSummary

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
    sort: str = Query("latest", enum=["latest", "score"], description="Sort order"),
    category: Optional[str] = Query(None, description="Filter by category"),
    date_from: Optional[str] = Query(None, description="Start date (ISO-8601)"),
    date_to: Optional[str] = Query(None, description="End date (ISO-8601)"),
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    filters: list[dict] = []
    if category:
        filters.append({"term": {"categories": category}})
    if date_from or date_to:
        range_clause: dict = {}
        if date_from:
            range_clause["gte"] = date_from
        if date_to:
            range_clause["lte"] = date_to
        filters.append({"range": {"latest_at": range_clause}})

    sort_field = "latest_at" if sort == "latest" else "score"
    sort_clause = [{sort_field: {"order": "desc"}}]
    # Secondary sort to break ties deterministically
    if sort_field != "latest_at":
        sort_clause.append({"latest_at": {"order": "desc"}})

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

    unique_slugs = [s for s in top_slugs if s]
    article_hits = await _fetch_articles_for_slugs(unique_slugs)
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
                confidence=src.get("confidence"),
                latest_at=src.get("latest_at", ""),
            )
        )

    return ClusterListResponse(items=items, total=total)
