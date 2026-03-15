from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.news import _hit_to_item
from app.db.models.entity import ArticleEntity, Entity
from app.db.opensearch import INDEX_NEWS, get_os_client
from app.db.session import get_db
from app.models.entity import EntityDetail, EntityItem, EntityListResponse
from app.models.errors import ErrorResponse

router = APIRouter(prefix="/entities", tags=["entities"])


@router.get(
    "/",
    response_model=EntityListResponse,
    summary="List entities",
    description="Returns a paginated list of entities (CVEs, vendors, products, actors, malware, tools) with optional type filter and name prefix search.",
)
async def list_entities(
    type: Optional[str] = Query(None, description="Filter by entity type (cve|vendor|product|actor|malware|tool)"),
    q: Optional[str] = Query(None, description="Prefix search on entity name"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    # Base query: entities with article count
    count_sub = (
        select(
            ArticleEntity.entity_id,
            func.count(ArticleEntity.article_id).label("article_count"),
        )
        .group_by(ArticleEntity.entity_id)
        .subquery()
    )

    query = (
        select(
            Entity,
            func.coalesce(count_sub.c.article_count, 0).label("article_count"),
        )
        .outerjoin(count_sub, Entity.id == count_sub.c.entity_id)
    )

    if type:
        query = query.where(Entity.type == type)
    if q:
        query = query.where(Entity.name.ilike(f"{q}%"))

    # Total count
    count_query = select(func.count()).select_from(Entity)
    if type:
        count_query = count_query.where(Entity.type == type)
    if q:
        count_query = count_query.where(Entity.name.ilike(f"{q}%"))
    total = (await db.execute(count_query)).scalar() or 0

    # Paginated results
    query = query.order_by(Entity.last_seen.desc()).limit(limit).offset(offset)
    rows = (await db.execute(query)).all()

    items = [
        EntityItem(
            id=str(row.Entity.id),
            type=row.Entity.type,
            name=row.Entity.name,
            normalized_key=row.Entity.normalized_key,
            cvss_score=row.Entity.cvss_score,
            first_seen=row.Entity.first_seen,
            last_seen=row.Entity.last_seen,
            article_count=row.article_count,
        )
        for row in rows
    ]

    return EntityListResponse(items=items, total=total)


@router.get(
    "/{entity_id}",
    response_model=EntityDetail,
    summary="Get entity detail",
    description="Returns full entity details including aliases, description, and linked articles.",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid entity ID format"},
        404: {"model": ErrorResponse, "description": "Entity not found"},
    },
)
async def get_entity(
    entity_id: str,
    db: AsyncSession = Depends(get_db),
):
    # Fetch entity
    try:
        uid = UUID(entity_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid entity ID")

    result = await db.execute(select(Entity).where(Entity.id == uid))
    entity = result.scalar_one_or_none()
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    # Count linked articles
    count_result = await db.execute(
        select(func.count(ArticleEntity.article_id))
        .where(ArticleEntity.entity_id == uid)
    )
    article_count = count_result.scalar() or 0

    # Fetch linked article slugs
    slug_result = await db.execute(
        select(ArticleEntity.article_id)
        .where(ArticleEntity.entity_id == uid)
    )
    slugs = [row[0] for row in slug_result.all()]

    # Fetch articles from OpenSearch
    articles = []
    if slugs:
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
        articles = [_hit_to_item(h) for h in resp["hits"]["hits"]]

    return EntityDetail(
        id=str(entity.id),
        type=entity.type,
        name=entity.name,
        normalized_key=entity.normalized_key,
        aliases=entity.aliases or [],
        description=entity.description,
        cvss_score=entity.cvss_score,
        first_seen=entity.first_seen,
        last_seen=entity.last_seen,
        article_count=article_count,
        articles=articles,
    )
