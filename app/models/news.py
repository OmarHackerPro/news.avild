from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class NewsItem(BaseModel):
    id: str = Field(json_schema_extra={"example": "cisa-warns-fortinet-rce-cve-2026-12345"})
    slug: str = Field(json_schema_extra={"example": "cisa-warns-fortinet-rce-cve-2026-12345"})
    tags: List[str] = Field(json_schema_extra={"example": ["vulnerability", "zero-day", "fortinet"]})
    title: str = Field(json_schema_extra={"example": "CISA Warns of Critical Fortinet FortiOS RCE Vulnerability"})
    desc: Optional[str] = Field(None, json_schema_extra={"example": "CISA has added CVE-2026-12345 to its Known Exploited Vulnerabilities catalog after active exploitation was confirmed in the wild."})
    summary: Optional[str] = Field(None, json_schema_extra={"example": "CISA has added CVE-2026-12345 to its Known Exploited Vulnerabilities catalog after active exploitation was confirmed in the wild. The vulnerability affects FortiOS versions prior to 7.4.3 and allows remote code execution without authentication."})
    keywords: List[str] = Field(json_schema_extra={"example": ["fortinet", "cve-2026-12345", "rce"]})
    time: str = Field(json_schema_extra={"example": "3h"})
    severity: Optional[str] = Field(None, json_schema_extra={"example": "critical"})
    type: str = Field(json_schema_extra={"example": "advisory"})  # news | analysis | report | advisory
    category: str = Field(json_schema_extra={"example": "breaking"})  # research | deep-dives | beginner | dark-web | breaking
    author: Optional[str] = Field(None, json_schema_extra={"example": "CISA"})
    source_name: Optional[str] = Field(None, json_schema_extra={"example": "CISA Advisories"})
    source_url: Optional[str] = Field(None, json_schema_extra={"example": "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"})
    image_url: Optional[str] = Field(None, json_schema_extra={"example": "https://news.avild.com/static/images/cisa-logo.png"})
    cvss_score: Optional[Decimal] = Field(None, json_schema_extra={"example": 9.8})
    cve_ids: List[str] = Field(default=[], json_schema_extra={"example": ["CVE-2026-12345"]})
    published_at: str = Field(json_schema_extra={"example": "2026-03-15T08:30:00Z"})  # ISO-8601


class NewsDetail(NewsItem):
    content_html: Optional[str] = Field(None, json_schema_extra={"example": "<p>CISA has added <strong>CVE-2026-12345</strong> to its Known Exploited Vulnerabilities catalog...</p>"})
    content_source: Optional[str] = Field(None, json_schema_extra={"example": "rss"})
    raw_metadata: Optional[Dict[str, Any]] = Field(None, json_schema_extra={"example": {"advisory_id": "AA26-074A", "cvss_vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}})


class NewsListResponse(BaseModel):
    items: List[NewsItem]
    total: int = Field(json_schema_extra={"example": 142})
