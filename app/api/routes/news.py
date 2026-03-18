from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from opensearchpy.exceptions import NotFoundError

from app.db.opensearch import INDEX_NEWS, get_os_client
from app.models.errors import ErrorResponse
from app.models.news import NewsDetail, NewsItem, NewsListResponse

router = APIRouter(prefix="/news", tags=["news"])

# Fields returned for list endpoints (lightweight)
_LIST_SOURCE_FIELDS = [
    "slug", "title", "desc", "summary", "tags", "keywords", "published_at",
    "severity", "type", "category", "author", "source_name",
    "source_url", "image_url", "cvss_score", "cve_ids",
]


def _time_ago(dt: datetime) -> str:
    """Format a datetime as a human-readable time-ago string."""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        remaining = minutes % 60
        return f"{hours}h{f' {remaining}m' if remaining else ''}"
    days = hours // 24
    return f"{days}d"


def _hit_to_item(hit: dict) -> NewsItem:
    src = hit["_source"]
    published_at = datetime.fromisoformat(src["published_at"])
    return NewsItem(
        id=hit["_id"],
        slug=src.get("slug") or hit["_id"],
        tags=src.get("tags") or [],
        title=src["title"],
        desc=src.get("desc"),
        summary=src.get("summary"),
        keywords=src.get("keywords") or [],
        time=_time_ago(published_at),
        severity=src.get("severity"),
        type=src["type"],
        category=src["category"],
        author=src.get("author"),
        source_name=src.get("source_name"),
        source_url=src.get("source_url"),
        image_url=src.get("image_url"),
        cvss_score=Decimal(str(src["cvss_score"])) if src.get("cvss_score") is not None else None,
        cve_ids=src.get("cve_ids") or [],
        published_at=src["published_at"],
    )


def _hit_to_detail(hit: dict) -> NewsDetail:
    src = hit["_source"]
    published_at = datetime.fromisoformat(src["published_at"])
    return NewsDetail(
        id=hit["_id"],
        slug=src.get("slug") or hit["_id"],
        tags=src.get("tags") or [],
        title=src["title"],
        desc=src.get("desc"),
        summary=src.get("summary"),
        keywords=src.get("keywords") or [],
        time=_time_ago(published_at),
        severity=src.get("severity"),
        type=src["type"],
        category=src["category"],
        author=src.get("author"),
        source_name=src.get("source_name"),
        source_url=src.get("source_url"),
        image_url=src.get("image_url"),
        cvss_score=Decimal(str(src["cvss_score"])) if src.get("cvss_score") is not None else None,
        cve_ids=src.get("cve_ids") or [],
        published_at=src["published_at"],
        content_html=src.get("content_html"),
        content_source=src.get("content_source"),
        raw_metadata=src.get("raw_metadata"),
    )


def _build_filters(
    *,
    category: Optional[str] = None,
    type: Optional[str] = None,
    severity: Optional[str] = None,
    source_name: Optional[str] = None,
    tag: Optional[str] = None,
    cve: Optional[str] = None,
    min_cvss: Optional[float] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[dict]:
    """Build OpenSearch bool filter clauses from query parameters."""
    filters: List[dict] = []
    if category:
        filters.append({"term": {"category": category}})
    if type:
        filters.append({"term": {"type": type}})
    if severity:
        filters.append({"term": {"severity": severity}})
    if source_name:
        filters.append({"term": {"source_name": source_name}})
    if tag:
        filters.append({"term": {"tags": tag}})
    if cve:
        filters.append({"term": {"cve_ids": cve}})
    if min_cvss is not None:
        filters.append({"range": {"cvss_score": {"gte": min_cvss}}})
    date_range: dict = {}
    if date_from:
        date_range["gte"] = date_from
    if date_to:
        date_range["lte"] = date_to
    if date_range:
        filters.append({"range": {"published_at": date_range}})
    return filters


def _build_sort(sort: str) -> List[dict]:
    """Build OpenSearch sort clause."""
    if sort == "oldest":
        return [{"published_at": {"order": "asc"}}]
    if sort == "cvss":
        return [{"cvss_score": {"order": "desc", "missing": "_last"}}, {"published_at": {"order": "desc"}}]
    # default: newest
    return [{"published_at": {"order": "desc"}}]


@router.get(
    "/",
    response_model=NewsListResponse,
    summary="List news articles",
    description="Returns a paginated list of news articles with optional filters for category, type, severity, source, tags, CVE IDs, CVSS score range, date range, and full-text search.",
)
async def get_news(
    category: Optional[str] = Query(None, description="Filter by category"),
    type: Optional[str] = Query(None, description="Filter by type (news|analysis|report|advisory)"),
    severity: Optional[str] = Query(None, description="Filter by severity"),
    source_name: Optional[str] = Query(None, description="Filter by source name"),
    tag: Optional[str] = Query(None, description="Filter by tag"),
    cve: Optional[str] = Query(None, description="Filter by CVE ID"),
    min_cvss: Optional[float] = Query(None, ge=0, le=10, description="Minimum CVSS score"),
    date_from: Optional[str] = Query(None, description="Start date (ISO-8601)"),
    date_to: Optional[str] = Query(None, description="End date (ISO-8601)"),
    sort: str = Query("newest", description="Sort order: newest|oldest|cvss"),
    q: Optional[str] = Query(None, description="Full-text search across title, desc, tags"),
    limit: int = Query(12, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    filters = _build_filters(
        category=category, type=type, severity=severity,
        source_name=source_name, tag=tag, cve=cve,
        min_cvss=min_cvss, date_from=date_from, date_to=date_to,
    )

    if q:
        must = [{"multi_match": {"query": q, "fields": ["title^3", "desc", "summary^1.5", "tags^2", "keywords"]}}]
    else:
        must = []

    query_clause = {"bool": {"must": must, "filter": filters}} if (must or filters) else {"match_all": {}}

    query_body = {
        "query": query_clause,
        "sort": _build_sort(sort),
        "from": offset,
        "size": limit,
        "_source": _LIST_SOURCE_FIELDS,
    }

    resp = await get_os_client().search(index=INDEX_NEWS, body=query_body)
    total = resp["hits"]["total"]["value"]
    items = [_hit_to_item(h) for h in resp["hits"]["hits"]]
    return NewsListResponse(items=items, total=total)


@router.get(
    "/{slug}",
    response_model=NewsDetail,
    summary="Get article detail",
    description="Returns full article details including HTML content and raw metadata.",
    responses={404: {"model": ErrorResponse, "description": "Article not found"}},
)
async def get_news_item(slug: str):
    try:
        resp = await get_os_client().get(index=INDEX_NEWS, id=slug)
        return _hit_to_detail(resp)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="News item not found")
