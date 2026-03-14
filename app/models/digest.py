from typing import Dict, List, Optional

from pydantic import BaseModel

from app.models.news import NewsItem


class CategoryDigest(BaseModel):
    count: int
    top: List[NewsItem]


class DailyDigest(BaseModel):
    date: str
    total_articles: int
    by_category: Dict[str, CategoryDigest]
    top_cves: List[str]
    top_tags: List[dict]  # [{tag, count}]


class WeeklyDigest(BaseModel):
    week: str  # ISO week e.g. "2026-W11"
    total_articles: int
    by_category: Dict[str, CategoryDigest]
    top_cves: List[str]
    top_tags: List[dict]


class TrendingTag(BaseModel):
    tag: str
    count: int
    delta: Optional[str] = None


class TrendingSource(BaseModel):
    name: str
    article_count: int


class TrendingResponse(BaseModel):
    period_hours: int
    tags: List[TrendingTag]
    sources: List[TrendingSource]
    top_articles: List[NewsItem]
