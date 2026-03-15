from typing import List, Optional

from pydantic import BaseModel, Field

from app.models.news import NewsItem


class UserPreferences(BaseModel):
    followed_categories: List[str] = Field(default=[], json_schema_extra={"example": ["breaking", "research"]})
    muted_sources: List[str] = Field(default=[], json_schema_extra={"example": ["example-blog"]})
    severity_filter: Optional[str] = Field(None, json_schema_extra={"example": "critical"})
    email_digest: str = Field("none", json_schema_extra={"example": "daily"})  # daily | weekly | none
    language: str = Field("en", json_schema_extra={"example": "en"})
    theme: str = Field("dark", json_schema_extra={"example": "dark"})  # dark | light


class BookmarkListResponse(BaseModel):
    items: List[NewsItem]
    total: int = Field(json_schema_extra={"example": 5})
