from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.news import NewsArticle as NewsArticleDB
from app.db.session import get_db
from app.schemas.news import NewsItem, NewsListResponse

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


def _row_to_item(row: NewsArticleDB) -> NewsItem:
    return NewsItem(
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
    )


@router.get("/", response_model=NewsListResponse)
async def get_news(
    category: Optional[str] = Query(None, description="Filter by category"),
    type: Optional[str] = Query(None, description="Filter by type"),
    severity: Optional[str] = Query(None, description="Filter by severity"),
    q: Optional[str] = Query(None, description="Full-text search across title, desc, tags"),
    limit: int = Query(12, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
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
        raise HTTPException(status_code=404, detail="News item not found")
    return _row_to_item(row)
