from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel

from app.models.news import NewsItem


class ClusterSummary(BaseModel):
    id: str
    label: str
    state: str  # new | developing | confirmed | resolved
    article_count: int
    top_article: NewsItem
    categories: List[str]
    score: Optional[Decimal] = None
    confidence: Optional[str] = None
    latest_at: str  # ISO-8601


class ClusterDetail(BaseModel):
    id: str
    label: str
    state: str
    summary: Optional[str] = None  # TL;DR
    why_it_matters: Optional[str] = None
    score: Optional[Decimal] = None
    confidence: Optional[str] = None
    articles: List[NewsItem]
    categories: List[str]
    tags: List[str]
    earliest_at: str  # ISO-8601
    latest_at: str  # ISO-8601


class ClusterListResponse(BaseModel):
    items: List[ClusterSummary]
    total: int
