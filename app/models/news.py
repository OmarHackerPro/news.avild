from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class NewsItem(BaseModel):
    id: str
    slug: str
    tags: List[str]
    title: str
    desc: Optional[str] = None
    keywords: List[str]
    time: str
    severity: Optional[str] = None
    type: str  # news | analysis | report | advisory
    category: str  # research | deep-dives | beginner | dark-web | breaking
    author: Optional[str] = None
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    image_url: Optional[str] = None
    cvss_score: Optional[Decimal] = None
    cve_ids: List[str] = []
    published_at: str  # ISO-8601


class NewsDetail(NewsItem):
    content_html: Optional[str] = None
    raw_metadata: Optional[Dict[str, Any]] = None


class NewsListResponse(BaseModel):
    items: List[NewsItem]
    total: int
