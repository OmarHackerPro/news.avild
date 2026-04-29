from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.feed_source import FeedSource
from app.db.session import get_db
from app.models.errors import ErrorResponse
from app.schemas.feed import FeedSourceCreate, FeedSourceResponse, FeedSourceUpdate

router = APIRouter(prefix="/feeds", tags=["feeds"])


@router.get(
    "/",
    response_model=List[FeedSourceResponse],
    summary="List feed sources",
    description="Returns all configured RSS/advisory feed sources.",
)
async def list_feeds(db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(FeedSource).order_by(FeedSource.id))
    ).scalars().all()
    return rows


@router.get(
    "/{feed_id}",
    response_model=FeedSourceResponse,
    summary="Get feed source",
    description="Returns details for a specific feed source by ID.",
    responses={404: {"model": ErrorResponse, "description": "Feed source not found"}},
)
async def get_feed(feed_id: int, db: AsyncSession = Depends(get_db)):
    row = (
        await db.execute(select(FeedSource).where(FeedSource.id == feed_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Feed source not found")
    return row


@router.post(
    "/",
    response_model=FeedSourceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create feed source",
    description="Registers a new RSS/advisory feed source for ingestion.",
    responses={409: {"model": ErrorResponse, "description": "Feed URL already exists"}},
)
async def create_feed(body: FeedSourceCreate, db: AsyncSession = Depends(get_db)):
    existing = (
        await db.execute(select(FeedSource).where(FeedSource.url == body.url))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Feed with this URL already exists")
    feed = FeedSource(
        name=body.name,
        url=body.url,
        default_type=body.default_type,
        default_category=body.default_category,
        default_severity=body.default_severity,
        normalizer_key=body.normalizer_key,
        credibility_weight=body.credibility_weight,
        extract_cves=body.extract_cves,
        extract_cvss=body.extract_cvss,
        fetch_interval_minutes=body.fetch_interval_minutes,
    )
    db.add(feed)
    await db.commit()
    await db.refresh(feed)
    return feed


@router.patch(
    "/{feed_id}",
    response_model=FeedSourceResponse,
    summary="Update feed source",
    description="Partially updates a feed source's configuration.",
    responses={404: {"model": ErrorResponse, "description": "Feed source not found"}},
)
async def update_feed(
    feed_id: int, body: FeedSourceUpdate, db: AsyncSession = Depends(get_db)
):
    feed = (
        await db.execute(select(FeedSource).where(FeedSource.id == feed_id))
    ).scalar_one_or_none()
    if feed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Feed source not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(feed, field, value)
    await db.commit()
    await db.refresh(feed)
    return feed


@router.delete(
    "/{feed_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete feed source",
    description="Permanently removes a feed source. Existing articles from this source are not affected.",
    responses={404: {"model": ErrorResponse, "description": "Feed source not found"}},
)
async def delete_feed(feed_id: int, db: AsyncSession = Depends(get_db)):
    feed = (
        await db.execute(select(FeedSource).where(FeedSource.id == feed_id))
    ).scalar_one_or_none()
    if feed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Feed source not found")
    await db.delete(feed)
    await db.commit()
