from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field

from app.models.news import NewsItem


class EntityItem(BaseModel):
    id: str = Field(json_schema_extra={"example": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"})
    type: str = Field(json_schema_extra={"example": "cve"})
    name: str = Field(json_schema_extra={"example": "CVE-2026-12345"})
    normalized_key: str = Field(json_schema_extra={"example": "cve-2026-12345"})
    cvss_score: Optional[Decimal] = Field(None, json_schema_extra={"example": 9.8})
    first_seen: datetime = Field(json_schema_extra={"example": "2026-03-14T09:00:00Z"})
    last_seen: datetime = Field(json_schema_extra={"example": "2026-03-15T14:22:00Z"})
    article_count: int = Field(json_schema_extra={"example": 12})


class EntityDetail(EntityItem):
    aliases: List[str] = Field(default=[], json_schema_extra={"example": ["FortiOS RCE", "Fortinet CVE-2026-12345"]})
    description: Optional[str] = Field(None, json_schema_extra={"example": "Critical remote code execution vulnerability in Fortinet FortiOS SSL-VPN allowing unauthenticated attackers to execute arbitrary code via crafted HTTP requests."})
    articles: List[NewsItem] = []


class EntityListResponse(BaseModel):
    items: List[EntityItem]
    total: int = Field(json_schema_extra={"example": 56})
