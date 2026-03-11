from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel


class NewsItem(BaseModel):
    id: str
    title: str
    desc: Optional[str] = None
    tags: List[str] = []
    keywords: List[str] = []
    time: str
    severity: Optional[str] = None
    type: str
    category: str
    author: Optional[str] = None
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    image_url: Optional[str] = None
    cvss_score: Optional[Decimal] = None
    cve_ids: List[str] = []


class NewsListResponse(BaseModel):
    items: List[NewsItem]
    total: int
    limit: int
    offset: int
