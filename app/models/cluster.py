from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field

from app.models.news import NewsItem


class ClusterTimelineEntry(BaseModel):
    article_slug: str = Field(json_schema_extra={"example": "fortios-rce-cve-2026-12345"})
    source_name: str = Field(json_schema_extra={"example": "BleepingComputer"})
    title: str = Field(json_schema_extra={"example": "Critical FortiOS RCE vulnerability exploited in the wild"})
    published_at: str = Field(json_schema_extra={"example": "2026-03-14T09:00:00Z"})
    added_at: str = Field(json_schema_extra={"example": "2026-03-14T09:05:00Z"})


class ClusterSummary(BaseModel):
    id: str = Field(json_schema_extra={"example": "cluster-fortinet-rce-2026-03"})
    label: str = Field(json_schema_extra={"example": "Fortinet FortiOS Critical RCE (CVE-2026-12345)"})
    state: str = Field(json_schema_extra={"example": "confirmed"})  # new | developing | confirmed | resolved
    article_count: int = Field(json_schema_extra={"example": 7})
    top_article: NewsItem
    categories: List[str] = Field(json_schema_extra={"example": ["breaking", "research"]})
    score: Optional[Decimal] = Field(None, json_schema_extra={"example": 87.5})
    confidence: Optional[str] = Field(None, json_schema_extra={"example": "high"})
    latest_at: str = Field(json_schema_extra={"example": "2026-03-15T14:22:00Z"})  # ISO-8601


class ClusterDetail(BaseModel):
    id: str = Field(json_schema_extra={"example": "cluster-fortinet-rce-2026-03"})
    label: str = Field(json_schema_extra={"example": "Fortinet FortiOS Critical RCE (CVE-2026-12345)"})
    state: str = Field(json_schema_extra={"example": "confirmed"})
    summary: Optional[str] = Field(None, json_schema_extra={"example": "A critical remote code execution vulnerability in Fortinet FortiOS (CVE-2026-12345, CVSS 9.8) is being actively exploited. CISA has added it to the KEV catalog. Patches are available."})  # TL;DR
    why_it_matters: Optional[str] = Field(None, json_schema_extra={"example": "FortiOS is deployed across 500K+ enterprises globally. Active exploitation confirmed by multiple threat intelligence sources. Unauthenticated RCE allows full device takeover."})
    score: Optional[Decimal] = Field(None, json_schema_extra={"example": 87.5})
    confidence: Optional[str] = Field(None, json_schema_extra={"example": "high"})
    articles: List[NewsItem]
    categories: List[str] = Field(json_schema_extra={"example": ["breaking", "research"]})
    tags: List[str] = Field(json_schema_extra={"example": ["fortinet", "zero-day", "rce", "cve-2026-12345"]})
    timeline: List[ClusterTimelineEntry] = Field(default_factory=list)
    earliest_at: str = Field(json_schema_extra={"example": "2026-03-14T09:00:00Z"})  # ISO-8601
    latest_at: str = Field(json_schema_extra={"example": "2026-03-15T14:22:00Z"})  # ISO-8601


class ClusterListResponse(BaseModel):
    items: List[ClusterSummary]
    total: int = Field(json_schema_extra={"example": 23})
