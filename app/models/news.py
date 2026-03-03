from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel


class NewsItem(BaseModel):
    id: str
    tags: List[str]
    title: str
    desc: Optional[str] = None
    keywords: List[str]
    time: str
    severity: Optional[str] = None
    type: str  # news | analysis | report | advisory
    category: str  # research | deep-dives | beginner | dark-web | breaking
    # Feed-enriched fields
    author: Optional[str] = None
    source_name: Optional[str] = None
    image_url: Optional[str] = None
    cvss_score: Optional[Decimal] = None
    cve_ids: List[str] = []


class NewsListResponse(BaseModel):
    items: List[NewsItem]
    total: int
