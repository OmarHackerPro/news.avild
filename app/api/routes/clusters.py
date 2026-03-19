from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from opensearchpy import NotFoundError

from app.api.routes.news import _hit_to_item
from app.db.opensearch import INDEX_CLUSTERS, INDEX_NEWS, get_os_client
from app.models.cluster import ClusterDetail, ClusterListResponse, ClusterSummary, ClusterTimelineEntry
from app.models.errors import ErrorResponse

router = APIRouter(prefix="/clusters", tags=["clusters"])


async def _fetch_articles_for_slugs(slugs: list[str]) -> list[dict]:
    """Fetch article hits from OpenSearch by slug list."""
    if not slugs:
        return []
    resp = await get_os_client().search(
        index=INDEX_NEWS,
        body={
            "query": {"ids": {"values": slugs}},
            "size": len(slugs),
            "sort": [{"published_at": {"order": "desc"}}],
            "_source": [
                "slug", "title", "desc", "tags", "keywords", "published_at",
                "severity", "type", "category", "author", "source_name",
                "source_url", "image_url", "cvss_score", "cve_ids",
            ],
        },
    )
    return resp["hits"]["hits"]


@router.get(
    "/",
    response_model=ClusterListResponse,
    summary="List clusters",
    description="Returns a paginated list of deduplicated article clusters, optionally filtered by category and date range. Each cluster includes its top article.",
)
async def list_clusters(
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
        filters.append({"range": {"created_at": range_clause}})

    body: dict = {
        "query": {"bool": {"filter": filters}} if filters else {"match_all": {}},
        "sort": [{"created_at": {"order": "desc"}}],
        "from": offset,
        "size": limit,
    }

    resp = await get_os_client().search(index=INDEX_CLUSTERS, body=body)
    total = resp["hits"]["total"]["value"]
    hits = resp["hits"]["hits"]

    # Batch-fetch top articles (first article_id from each cluster)
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
        top_article = slug_to_article[top_slug]
        items.append(
            ClusterSummary(
                id=h["_id"],
                label=src["label"],
                state=src.get("state", "new"),
                article_count=src.get("article_count", 0),
                top_article=top_article,
                categories=src.get("categories", []),
                score=Decimal(str(src["score"])) if src.get("score") is not None else None,
                confidence=src.get("confidence"),
                latest_at=src.get("latest_at", ""),
            )
        )

    return ClusterListResponse(items=items, total=total)


@router.get(
    "/{cluster_id}",
    response_model=ClusterDetail,
    summary="Get cluster detail",
    description="Returns full cluster details including TL;DR summary, why-it-matters, score, confidence, and all member articles.",
    responses={404: {"model": ErrorResponse, "description": "Cluster not found"}},
)
async def get_cluster(cluster_id: str):
    try:
        resp = await get_os_client().get(index=INDEX_CLUSTERS, id=cluster_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Cluster not found")

    src = resp["_source"]
    article_ids = src.get("article_ids", [])

    hits = await _fetch_articles_for_slugs(article_ids)
    articles = [_hit_to_item(h) for h in hits]

    tags = list({t for a in articles for t in a.tags})
    dates = [a.published_at for a in articles if a.published_at]

    timeline = [
        ClusterTimelineEntry(**entry)
        for entry in (src.get("timeline") or [])
    ]

    return ClusterDetail(
        id=resp["_id"],
        label=src["label"],
        state=src.get("state", "new"),
        summary=src.get("summary"),
        why_it_matters=src.get("why_it_matters"),
        score=Decimal(str(src["score"])) if src.get("score") is not None else None,
        confidence=src.get("confidence"),
        articles=articles,
        categories=src.get("categories", []),
        tags=tags,
        timeline=timeline,
        earliest_at=min(dates) if dates else "",
        latest_at=max(dates) if dates else "",
    )
