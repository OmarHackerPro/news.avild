from typing import Optional

from fastapi import APIRouter, Query

from app.api.routes.news import _build_filters, _build_sort, _hit_to_item
from app.db.opensearch import INDEX_NEWS, get_os_client
from app.models.search import FacetBucket, SearchResponse

router = APIRouter(prefix="/search", tags=["search"])


@router.get(
    "/",
    response_model=SearchResponse,
    summary="Search articles",
    description="Full-text search across articles with fuzzy matching, optional filters, and faceted results for categories, sources, and severity.",
)
async def search_articles(
    q: str = Query(..., min_length=1, description="Search query (required)"),
    category: Optional[str] = Query(None, description="Filter by category"),
    type: Optional[str] = Query(None, description="Filter by type"),
    severity: Optional[str] = Query(None, description="Filter by severity"),
    source_name: Optional[str] = Query(None, description="Filter by source name"),
    tag: Optional[str] = Query(None, description="Filter by tag"),
    date_from: Optional[str] = Query(None, description="Start date (ISO-8601)"),
    date_to: Optional[str] = Query(None, description="End date (ISO-8601)"),
    sort: str = Query("relevance", description="Sort: relevance|newest"),
    limit: int = Query(12, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    filters = _build_filters(
        category=category, type=type, severity=severity,
        source_name=source_name, tag=tag, date_from=date_from, date_to=date_to,
    )

    must = [
        {
            "multi_match": {
                "query": q,
                "fields": ["title^3", "desc^2", "summary^1.5", "tags^2", "keywords", "content_html"],
                "type": "best_fields",
                "fuzziness": "AUTO",
            }
        }
    ]

    sort_clause = [{"_score": {"order": "desc"}}] if sort == "relevance" else _build_sort(sort)

    body = {
        "query": {"bool": {"must": must, "filter": filters}},
        "sort": sort_clause,
        "from": offset,
        "size": limit,
        "_source": [
            "slug", "title", "desc", "summary", "tags", "keywords",
            "published_at", "severity", "type", "category", "author",
            "source_name", "source_url", "image_url", "cvss_score", "cve_ids",
        ],
        "aggs": {
            "categories": {"terms": {"field": "category", "size": 20}},
            "sources": {"terms": {"field": "source_name", "size": 20}},
            "severity": {"terms": {"field": "severity", "size": 10}},
        },
    }

    resp = await get_os_client().search(index=INDEX_NEWS, body=body)
    total = resp["hits"]["total"]["value"]
    items = [_hit_to_item(h) for h in resp["hits"]["hits"]]

    facets = {}
    for facet_key in ("categories", "sources", "severity"):
        buckets = resp.get("aggregations", {}).get(facet_key, {}).get("buckets", [])
        facets[facet_key] = [FacetBucket(name=b["key"], count=b["doc_count"]) for b in buckets]

    return SearchResponse(items=items, total=total, query=q, facets=facets)
