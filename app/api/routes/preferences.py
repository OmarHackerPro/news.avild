from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.news import _hit_to_item
from app.core.deps import get_current_user
from app.db.models.bookmark import Bookmark
from app.db.models.user import User
from app.db.opensearch import INDEX_NEWS, get_os_client
from app.db.session import get_db
from app.models.preferences import BookmarkListResponse, UserPreferences

router = APIRouter(prefix="/preferences", tags=["preferences"])


# ── Preferences ────────────────────────────────────────────────

@router.get("/", response_model=UserPreferences)
async def get_preferences(user: User = Depends(get_current_user)):
    """Get current user's preferences."""
    raw = user.preferences or {}
    return UserPreferences(**raw)


@router.put("/", response_model=UserPreferences)
async def replace_preferences(
    body: UserPreferences,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Replace all preferences."""
    user.preferences = body.model_dump()
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserPreferences(**user.preferences)


@router.patch("/", response_model=UserPreferences)
async def update_preferences(
    body: UserPreferences,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Partial update — merges provided fields into existing preferences."""
    current = user.preferences or {}
    update_data = body.model_dump(exclude_unset=True)
    current.update(update_data)
    user.preferences = current
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserPreferences(**user.preferences)


# ── Bookmarks ──────────────────────────────────────────────────

@router.get("/bookmarks", response_model=BookmarkListResponse)
async def list_bookmarks(
    limit: int = Query(12, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List bookmarked articles for the current user."""
    total_q = select(func.count()).select_from(Bookmark).where(Bookmark.user_id == user.id)
    total = (await db.execute(total_q)).scalar() or 0

    slug_q = (
        select(Bookmark.article_id)
        .where(Bookmark.user_id == user.id)
        .order_by(Bookmark.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    slugs = [row[0] for row in (await db.execute(slug_q)).all()]

    items = []
    if slugs:
        resp = await get_os_client().search(
            index=INDEX_NEWS,
            body={
                "query": {"ids": {"values": slugs}},
                "size": len(slugs),
                "_source": [
                    "slug", "title", "desc", "tags", "keywords", "published_at",
                    "severity", "type", "category", "author", "source_name",
                    "source_url", "image_url", "cvss_score", "cve_ids",
                ],
            },
        )
        items = [_hit_to_item(h) for h in resp["hits"]["hits"]]

    return BookmarkListResponse(items=items, total=total)


@router.post("/bookmarks/{article_id}", status_code=201)
async def add_bookmark(
    article_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Bookmark an article."""
    existing = await db.execute(
        select(Bookmark).where(
            Bookmark.user_id == user.id, Bookmark.article_id == article_id
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Already bookmarked")

    db.add(Bookmark(user_id=user.id, article_id=article_id))
    await db.commit()
    return {"detail": "Bookmarked"}


@router.delete("/bookmarks/{article_id}", status_code=204)
async def remove_bookmark(
    article_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a bookmark."""
    result = await db.execute(
        delete(Bookmark).where(
            Bookmark.user_id == user.id, Bookmark.article_id == article_id
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Bookmark not found")
    await db.commit()
