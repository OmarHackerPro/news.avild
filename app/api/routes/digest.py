from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query

from app.api.routes.news import _hit_to_item
from app.db.opensearch import INDEX_NEWS, get_os_client
from app.models.digest import (
    CategoryDigest,
    DailyDigest,
    TrendingResponse,
    TrendingSource,
    TrendingTag,
    WeeklyDigest,
)

router = APIRouter(prefix="/digest", tags=["digest"])

_LIST_FIELDS = [
    "slug", "title", "desc", "tags", "keywords", "published_at",
    "severity", "type", "category", "author", "source_name",
    "source_url", "image_url", "cvss_score", "cve_ids",
]


async def _digest_for_range(gte: str, lte: str) -> dict:
    """Fetch aggregated digest data for a date range."""
    body = {
        "query": {"range": {"published_at": {"gte": gte, "lte": lte}}},
        "size": 0,
        "aggs": {
            "by_category": {
                "terms": {"field": "category", "size": 20},
                "aggs": {
                    "top_articles": {
                        "top_hits": {
                            "size": 5,
                            "sort": [{"published_at": {"order": "desc"}}],
                            "_source": _LIST_FIELDS,
                        }
                    }
                },
            },
            "top_tags": {"terms": {"field": "tags", "size": 10}},
            "top_cves": {"terms": {"field": "cve_ids", "size": 10}},
        },
    }
    resp = await get_os_client().search(index=INDEX_NEWS, body=body)
    total = resp["hits"]["total"]["value"]
    aggs = resp.get("aggregations", {})

    by_category = {}
    for bucket in aggs.get("by_category", {}).get("buckets", []):
        cat = bucket["key"]
        top_hits = bucket.get("top_articles", {}).get("hits", {}).get("hits", [])
        by_category[cat] = CategoryDigest(
            count=bucket["doc_count"],
            top=[_hit_to_item(h) for h in top_hits],
        )

    top_cves = [b["key"] for b in aggs.get("top_cves", {}).get("buckets", [])]
    top_tags = [{"tag": b["key"], "count": b["doc_count"]} for b in aggs.get("top_tags", {}).get("buckets", [])]

    return {
        "total_articles": total,
        "by_category": by_category,
        "top_cves": top_cves,
        "top_tags": top_tags,
    }


@router.get("/daily", response_model=DailyDigest)
async def daily_digest():
    """Today's top articles grouped by category."""
    today = datetime.now(timezone.utc).date()
    gte = f"{today}T00:00:00Z"
    lte = f"{today}T23:59:59Z"
    data = await _digest_for_range(gte, lte)
    return DailyDigest(date=today.isoformat(), **data)


@router.get("/weekly", response_model=WeeklyDigest)
async def weekly_digest(
    week: str = Query(None, description="ISO week e.g. 2026-W11 (default: current)"),
):
    """This week's summary."""
    now = datetime.now(timezone.utc)
    if week:
        # Parse ISO week string like "2026-W11"
        year, w = week.split("-W")
        monday = datetime.strptime(f"{year} {w} 1", "%Y %W %w").replace(tzinfo=timezone.utc)
    else:
        monday = now - timedelta(days=now.weekday())
        monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        week = f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"

    sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)
    data = await _digest_for_range(monday.isoformat(), sunday.isoformat())
    return WeeklyDigest(week=week, **data)


@router.get("/trending", response_model=TrendingResponse)
async def trending(
    hours: int = Query(24, ge=1, le=168, description="Look-back period in hours"),
):
    """Trending tags/topics in the given period."""
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=hours)).isoformat()

    body = {
        "query": {"range": {"published_at": {"gte": since}}},
        "size": 5,
        "sort": [{"published_at": {"order": "desc"}}],
        "_source": _LIST_FIELDS,
        "aggs": {
            "tags": {"terms": {"field": "tags", "size": 20}},
            "sources": {"terms": {"field": "source_name", "size": 20}},
        },
    }

    resp = await get_os_client().search(index=INDEX_NEWS, body=body)
    aggs = resp.get("aggregations", {})

    tags = [
        TrendingTag(tag=b["key"], count=b["doc_count"])
        for b in aggs.get("tags", {}).get("buckets", [])
    ]
    sources = [
        TrendingSource(name=b["key"], article_count=b["doc_count"])
        for b in aggs.get("sources", {}).get("buckets", [])
    ]
    top_articles = [_hit_to_item(h) for h in resp["hits"]["hits"]]

    return TrendingResponse(
        period_hours=hours,
        tags=tags,
        sources=sources,
        top_articles=top_articles,
    )
