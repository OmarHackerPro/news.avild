from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel

from app.models.news import NewsItem


class EntityItem(BaseModel):
    id: str
    type: str
    name: str
    normalized_key: str
    cvss_score: Optional[Decimal] = None
    first_seen: datetime
    last_seen: datetime
    article_count: int


class EntityDetail(EntityItem):
    articles: List[NewsItem]


class EntityListResponse(BaseModel):
    items: List[EntityItem]
    total: int
