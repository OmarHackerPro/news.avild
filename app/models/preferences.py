from typing import List, Optional

from pydantic import BaseModel

from app.models.news import NewsItem


class UserPreferences(BaseModel):
    followed_categories: List[str] = []
    muted_sources: List[str] = []
    severity_filter: Optional[str] = None
    email_digest: str = "none"  # daily | weekly | none
    language: str = "en"
    theme: str = "dark"  # dark | light


class BookmarkListResponse(BaseModel):
    items: List[NewsItem]
    total: int
