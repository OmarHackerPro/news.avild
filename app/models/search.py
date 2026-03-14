from typing import Dict, List

from pydantic import BaseModel

from app.models.news import NewsItem


class FacetBucket(BaseModel):
    name: str
    count: int


class SearchResponse(BaseModel):
    items: List[NewsItem]
    total: int
    query: str
    facets: Dict[str, List[FacetBucket]]
