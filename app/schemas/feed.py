from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class FeedSourceCreate(BaseModel):
    name: str = Field(json_schema_extra={"example": "CISA Advisories"})
    url: str = Field(json_schema_extra={"example": "https://www.cisa.gov/cybersecurity-advisories/all.xml"})
    default_type: str = Field("news", json_schema_extra={"example": "advisory"})
    default_category: str = Field("breaking", json_schema_extra={"example": "breaking"})
    default_severity: Optional[str] = Field(None, json_schema_extra={"example": "critical"})
    normalizer_key: str = Field("generic", json_schema_extra={"example": "cisa"})
    credibility_weight: float = Field(1.0, json_schema_extra={"example": 1.2})
    extract_cves: bool = Field(False, json_schema_extra={"example": True})
    extract_cvss: bool = Field(False, json_schema_extra={"example": True})
    fetch_interval_minutes: int = Field(60, json_schema_extra={"example": 30})

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
    name: Optional[str] = Field(None, json_schema_extra={"example": "CISA Advisories (Updated)"})
    url: Optional[str] = Field(None, json_schema_extra={"example": "https://www.cisa.gov/cybersecurity-advisories/all.xml"})
    default_type: Optional[str] = Field(None, json_schema_extra={"example": "advisory"})
    default_category: Optional[str] = Field(None, json_schema_extra={"example": "breaking"})
    default_severity: Optional[str] = Field(None, json_schema_extra={"example": "high"})
    normalizer_key: Optional[str] = Field(None, json_schema_extra={"example": "cisa"})
    credibility_weight: Optional[float] = Field(None, json_schema_extra={"example": 1.2})
    extract_cves: Optional[bool] = Field(None, json_schema_extra={"example": True})
    extract_cvss: Optional[bool] = Field(None, json_schema_extra={"example": True})
    is_active: Optional[bool] = Field(None, json_schema_extra={"example": True})
    fetch_interval_minutes: Optional[int] = Field(None, json_schema_extra={"example": 15})


class FeedSourceResponse(BaseModel):
    id: int = Field(json_schema_extra={"example": 1})
    name: str = Field(json_schema_extra={"example": "CISA Advisories"})
    url: str = Field(json_schema_extra={"example": "https://www.cisa.gov/cybersecurity-advisories/all.xml"})
    default_type: str = Field(json_schema_extra={"example": "advisory"})
    default_category: str = Field(json_schema_extra={"example": "breaking"})
    default_severity: Optional[str] = Field(None, json_schema_extra={"example": "critical"})
    normalizer_key: str = Field(json_schema_extra={"example": "cisa"})
    credibility_weight: float = Field(json_schema_extra={"example": 1.2})
    extract_cves: bool = Field(json_schema_extra={"example": True})
    extract_cvss: bool = Field(json_schema_extra={"example": True})
    is_active: bool = Field(json_schema_extra={"example": True})
    last_fetched_at: Optional[datetime] = Field(None, json_schema_extra={"example": "2026-03-15T12:00:00Z"})
    fetch_interval_minutes: int = Field(json_schema_extra={"example": 30})
    consecutive_failures: int = Field(json_schema_extra={"example": 0})
    created_at: datetime = Field(json_schema_extra={"example": "2026-03-01T00:00:00Z"})

    model_config = {"from_attributes": True}
