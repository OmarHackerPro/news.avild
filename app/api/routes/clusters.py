from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.news import _hit_to_item
from app.db.models.cluster import Cluster, ClusterArticle
from app.db.opensearch import INDEX_NEWS, get_os_client
from app.db.session import get_db
from app.models.cluster import ClusterDetail, ClusterListResponse, ClusterSummary

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


@router.get("/", response_model=ClusterListResponse)
async def list_clusters(
    category: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List article clusters."""
    # Count articles per cluster
    count_sub = (
        select(
            ClusterArticle.cluster_id,
            func.count(ClusterArticle.article_id).label("article_count"),
        )
        .group_by(ClusterArticle.cluster_id)
        .subquery()
    )

    query = (
        select(
            Cluster,
            func.coalesce(count_sub.c.article_count, 0).label("article_count"),
        )
        .outerjoin(count_sub, Cluster.id == count_sub.c.cluster_id)
    )

    if date_from:
        query = query.where(Cluster.created_at >= date_from)
    if date_to:
        query = query.where(Cluster.created_at <= date_to)

    # Total
    count_q = select(func.count()).select_from(Cluster)
    if date_from:
        count_q = count_q.where(Cluster.created_at >= date_from)
    if date_to:
        count_q = count_q.where(Cluster.created_at <= date_to)
    total = (await db.execute(count_q)).scalar() or 0

    query = query.order_by(Cluster.created_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(query)).all()

    items = []
    for row in rows:
        cluster = row.Cluster
        article_count = row.article_count

        # Get first article slug for top_article
        first_slug_q = (
            select(ClusterArticle.article_id)
            .where(ClusterArticle.cluster_id == cluster.id)
            .limit(1)
        )
        first_slug_result = await db.execute(first_slug_q)
        first_slug = first_slug_result.scalar_one_or_none()

        if first_slug:
            hits = await _fetch_articles_for_slugs([first_slug])
            top_article = _hit_to_item(hits[0]) if hits else None
        else:
            top_article = None

        if top_article is None:
            continue

        items.append(
            ClusterSummary(
                id=str(cluster.id),
                label=cluster.label,
                state=cluster.state,
                article_count=article_count,
                top_article=top_article,
                categories=[top_article.category],
                score=cluster.score,
                confidence=cluster.confidence,
                latest_at=top_article.published_at,
            )
        )

    return ClusterListResponse(items=items, total=total)


@router.get("/{cluster_id}", response_model=ClusterDetail)
async def get_cluster(
    cluster_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get cluster with all member articles."""
    from uuid import UUID

    try:
        uid = UUID(cluster_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid cluster ID")

    result = await db.execute(select(Cluster).where(Cluster.id == uid))
    cluster = result.scalar_one_or_none()
    if cluster is None:
        raise HTTPException(status_code=404, detail="Cluster not found")

    slug_result = await db.execute(
        select(ClusterArticle.article_id).where(ClusterArticle.cluster_id == uid)
    )
    slugs = [row[0] for row in slug_result.all()]

    hits = await _fetch_articles_for_slugs(slugs)
    articles = [_hit_to_item(h) for h in hits]

    categories = list({a.category for a in articles})
    tags = list({t for a in articles for t in a.tags})
    dates = [a.published_at for a in articles if a.published_at]

    return ClusterDetail(
        id=str(cluster.id),
        label=cluster.label,
        state=cluster.state,
        summary=cluster.summary,
        why_it_matters=cluster.why_it_matters,
        score=cluster.score,
        confidence=cluster.confidence,
        articles=articles,
        categories=categories,
        tags=tags,
        earliest_at=min(dates) if dates else "",
        latest_at=max(dates) if dates else "",
    )
