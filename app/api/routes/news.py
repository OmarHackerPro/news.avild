from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

<<<<<<< HEAD
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.news import NewsArticle as NewsArticleDB
from app.db.session import get_db
from app.schemas.news import NewsItem, NewsListResponse
=======
from fastapi import APIRouter, HTTPException, Query
from opensearchpy.exceptions import NotFoundError

from app.db.opensearch import INDEX_NEWS, get_os_client
from app.models.news import NewsItem, NewsListResponse
>>>>>>> b96dbdc4518bac7fdd85d95f9139ab771fff9fe4

router = APIRouter(prefix="/news", tags=["news"])


def _time_ago(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        remaining = minutes % 60
        return f"{hours}h{f' {remaining}m' if remaining else ''}"
    days = hours // 24
    return f"{days}d"


def _hit_to_item(hit: dict) -> NewsItem:
    src = hit["_source"]
    published_at = datetime.fromisoformat(src["published_at"])
    return NewsItem(
<<<<<<< HEAD
        id=str(row.id),
        tags=row.tags or [],
        title=row.title,
        desc=row.desc,
        keywords=row.keywords or [],
        time=_time_ago(row.published_at),
        severity=row.severity,
        type=row.type,
        category=row.category,
        author=row.author,
        source_name=row.source_name,
        source_url=row.source_url,
        image_url=row.image_url,
        cvss_score=row.cvss_score,
        cve_ids=row.cve_ids or [],
=======
        id=hit["_id"],
        tags=src.get("tags") or [],
        title=src["title"],
        desc=src.get("desc"),
        keywords=src.get("keywords") or [],
        time=_time_ago(published_at),
        severity=src.get("severity"),
        type=src["type"],
        category=src["category"],
        author=src.get("author"),
        source_name=src.get("source_name"),
        image_url=src.get("image_url"),
        cvss_score=Decimal(str(src["cvss_score"])) if src.get("cvss_score") is not None else None,
        cve_ids=src.get("cve_ids") or [],
>>>>>>> b96dbdc4518bac7fdd85d95f9139ab771fff9fe4
    )


@router.get("/", response_model=NewsListResponse)
async def get_news(
    category: Optional[str] = Query(None, description="Filter by category"),
    type: Optional[str] = Query(None, description="Filter by type"),
    severity: Optional[str] = Query(None, description="Filter by severity"),
    q: Optional[str] = Query(None, description="Full-text search across title, desc, tags"),
    limit: int = Query(12, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
<<<<<<< HEAD
    stmt = select(NewsArticleDB).order_by(NewsArticleDB.published_at.desc())

    if category:
        stmt = stmt.where(NewsArticleDB.category == category)
    if type:
        stmt = stmt.where(NewsArticleDB.type == type)
    if severity:
        stmt = stmt.where(NewsArticleDB.severity == severity)
    if q:
        term = f"%{q}%"
        stmt = stmt.where(
            or_(
                NewsArticleDB.title.ilike(term),
                NewsArticleDB.desc.ilike(term),
            )
        )

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.offset(offset).limit(limit))).scalars().all()

    return NewsListResponse(
        items=[_row_to_item(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{news_id}", response_model=NewsItem)
async def get_news_item(news_id: int, db: AsyncSession = Depends(get_db)):
    row = (
        await db.execute(select(NewsArticleDB).where(NewsArticleDB.id == news_id))
    ).scalar_one_or_none()
    if row is None:
=======
    filters = []
    if category:
        filters.append({"term": {"category": category}})
    if type:
        filters.append({"term": {"type": type}})
    if severity:
        filters.append({"term": {"severity": severity}})

    query_body = {
        "query": {"bool": {"filter": filters}} if filters else {"match_all": {}},
        "sort": [{"published_at": {"order": "desc"}}],
        "from": offset,
        "size": limit,
        "_source": [
            "slug", "title", "desc", "tags", "keywords", "published_at",
            "severity", "type", "category", "author", "source_name",
            "image_url", "cvss_score", "cve_ids",
        ],
    }

    resp = await get_os_client().search(index=INDEX_NEWS, body=query_body)
    total = resp["hits"]["total"]["value"]
    items = [_hit_to_item(h) for h in resp["hits"]["hits"]]
    return NewsListResponse(items=items, total=total)


@router.get("/{slug}", response_model=NewsItem)
async def get_news_item(slug: str):
    try:
        resp = await get_os_client().get(index=INDEX_NEWS, id=slug)
        return _hit_to_item(resp)
    except NotFoundError:
>>>>>>> b96dbdc4518bac7fdd85d95f9139ab771fff9fe4
        raise HTTPException(status_code=404, detail="News item not found")
