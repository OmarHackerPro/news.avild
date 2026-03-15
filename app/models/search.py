from typing import Dict, List

from pydantic import BaseModel, Field

from app.models.news import NewsItem


class FacetBucket(BaseModel):
    name: str = Field(json_schema_extra={"example": "breaking"})
    count: int = Field(json_schema_extra={"example": 42})


class SearchResponse(BaseModel):
    items: List[NewsItem]
    total: int = Field(json_schema_extra={"example": 37})
    query: str = Field(json_schema_extra={"example": "fortinet vulnerability"})
    facets: Dict[str, List[FacetBucket]] = Field(
        json_schema_extra={
            "example": {
                "categories": [{"name": "breaking", "count": 15}, {"name": "research", "count": 12}],
                "sources": [{"name": "BleepingComputer", "count": 8}, {"name": "The Hacker News", "count": 6}],
                "severity": [{"name": "critical", "count": 10}, {"name": "high", "count": 7}],
            }
        }
    )
