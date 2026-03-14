from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.feed_source import FeedSource
from app.db.session import get_db

router = APIRouter(prefix="/sources", tags=["sources"])


class SourceItem(BaseModel):
    id: int
    name: str
    default_category: str
    default_type: str


@router.get("/", response_model=List[SourceItem])
async def list_sources(db: AsyncSession = Depends(get_db)):
    """List active feed sources."""
    result = await db.execute(
        select(FeedSource)
        .where(FeedSource.is_active.is_(True))
        .order_by(FeedSource.name)
    )
    sources = result.scalars().all()
    return [
        SourceItem(
            id=s.id,
            name=s.name,
            default_category=s.default_category,
            default_type=s.default_type,
        )
        for s in sources
    ]
