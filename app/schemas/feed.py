from datetime import datetime
from typing import Optional

from pydantic import BaseModel, HttpUrl, field_validator


class FeedSourceCreate(BaseModel):
    name: str
    url: str
    default_type: str = "news"
    default_category: str = "breaking"
    default_severity: Optional[str] = None
    normalizer_key: str = "generic"
    fetch_interval_minutes: int = 60

    @field_validator("default_type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        allowed = {"news", "analysis", "report", "advisory", "alert"}
        if v not in allowed:
            raise ValueError(f"default_type must be one of: {', '.join(sorted(allowed))}")
        return v

    @field_validator("default_category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        allowed = {"breaking", "research", "deep-dives", "beginner", "dark-web"}
        if v not in allowed:
            raise ValueError(f"default_category must be one of: {', '.join(sorted(allowed))}")
        return v


class FeedSourceUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    default_type: Optional[str] = None
    default_category: Optional[str] = None
    default_severity: Optional[str] = None
    normalizer_key: Optional[str] = None
    is_active: Optional[bool] = None
    fetch_interval_minutes: Optional[int] = None


class FeedSourceResponse(BaseModel):
    id: int
    name: str
    url: str
    default_type: str
    default_category: str
    default_severity: Optional[str] = None
    normalizer_key: str
    is_active: bool
    last_fetched_at: Optional[datetime] = None
    fetch_interval_minutes: int
    consecutive_failures: int
    created_at: datetime

    model_config = {"from_attributes": True}
