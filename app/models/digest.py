from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.news import NewsItem


class CategoryDigest(BaseModel):
    count: int = Field(json_schema_extra={"example": 18})
    top: List[NewsItem]


class DailyDigest(BaseModel):
    date: str = Field(json_schema_extra={"example": "2026-03-15"})
    total_articles: int = Field(json_schema_extra={"example": 47})
    by_category: Dict[str, CategoryDigest]
    top_cves: List[str] = Field(json_schema_extra={"example": ["CVE-2026-12345", "CVE-2026-11111", "CVE-2026-22222"]})
    top_tags: List[dict] = Field(json_schema_extra={"example": [{"tag": "ransomware", "count": 12}, {"tag": "zero-day", "count": 8}]})  # [{tag, count}]


class WeeklyDigest(BaseModel):
    week: str = Field(json_schema_extra={"example": "2026-W11"})  # ISO week e.g. "2026-W11"
    total_articles: int = Field(json_schema_extra={"example": 312})
    by_category: Dict[str, CategoryDigest]
    top_cves: List[str] = Field(json_schema_extra={"example": ["CVE-2026-12345", "CVE-2026-11111"]})
    top_tags: List[dict] = Field(json_schema_extra={"example": [{"tag": "ransomware", "count": 45}, {"tag": "zero-day", "count": 28}]})


class TrendingTag(BaseModel):
    tag: str = Field(json_schema_extra={"example": "ransomware"})
    count: int = Field(json_schema_extra={"example": 15})
    delta: Optional[str] = Field(None, json_schema_extra={"example": "+40%"})


class TrendingSource(BaseModel):
    name: str = Field(json_schema_extra={"example": "BleepingComputer"})
    article_count: int = Field(json_schema_extra={"example": 23})


class TrendingResponse(BaseModel):
    period_hours: int = Field(json_schema_extra={"example": 24})
    tags: List[TrendingTag]
    sources: List[TrendingSource]
    top_articles: List[NewsItem]
