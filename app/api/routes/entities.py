from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from opensearchpy import NotFoundError

from app.api.routes.news import _hit_to_item
from app.db.opensearch import INDEX_ENTITIES, INDEX_NEWS, get_os_client
from app.models.entity import EntityDetail, EntityItem, EntityListResponse
from app.models.errors import ErrorResponse

router = APIRouter(prefix="/entities", tags=["entities"])


@router.get(
    "/",
    response_model=EntityListResponse,
    summary="List entities",
    description="Returns a paginated list of entities (CVEs, vendors, products, actors, malware, tools) with optional type filter and name prefix search.",
)
async def list_entities(
    type: Optional[str] = Query(None, description="Filter by entity type (cve|vendor|product|actor|malware|tool)"),
    q: Optional[str] = Query(None, description="Prefix search on entity name"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    filters: list[dict] = []
    if type:
        filters.append({"term": {"type": type}})

    if q:
        query_clause: dict = {
            "bool": {
                "must": [{"prefix": {"name.raw": {"value": q, "case_insensitive": True}}}],
                "filter": filters,
            }
        }
    elif filters:
        query_clause = {"bool": {"filter": filters}}
    else:
        query_clause = {"match_all": {}}

    body: dict = {
        "query": query_clause,
        "sort": [{"last_seen": {"order": "desc"}}],
        "from": offset,
        "size": limit,
    }

    resp = await get_os_client().search(index=INDEX_ENTITIES, body=body)
    total = resp["hits"]["total"]["value"]
    hits = resp["hits"]["hits"]

    items = [
        EntityItem(
            id=h["_id"],
            type=src["type"],
            name=src["name"],
            normalized_key=src["normalized_key"],
            cvss_score=src.get("cvss_score"),
            first_seen=src["first_seen"],
            last_seen=src["last_seen"],
            article_count=src.get("article_count", 0),
        )
        for h in hits
        for src in [h["_source"]]
    ]

    return EntityListResponse(items=items, total=total)


@router.get(
    "/{entity_id}",
    response_model=EntityDetail,
    summary="Get entity detail",
    description="Returns full entity details including aliases, description, and linked articles.",
    responses={
        404: {"model": ErrorResponse, "description": "Entity not found"},
    },
)
async def get_entity(entity_id: str):
    try:
        resp = await get_os_client().get(index=INDEX_ENTITIES, id=entity_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Entity not found")

    src = resp["_source"]
    article_ids = src.get("article_ids", [])

    # Fetch linked articles from OpenSearch
    articles = []
    if article_ids:
        art_resp = await get_os_client().search(
            index=INDEX_NEWS,
            body={
                "query": {"ids": {"values": article_ids}},
                "size": len(article_ids),
                "sort": [{"published_at": {"order": "desc"}}],
                "_source": [
                    "slug", "title", "desc", "summary", "tags", "keywords",
                    "published_at", "severity", "type", "category", "author",
                    "source_name", "source_url", "image_url", "cvss_score",
                    "cve_ids",
                ],
            },
        )
        articles = [_hit_to_item(h) for h in art_resp["hits"]["hits"]]

    return EntityDetail(
        id=resp["_id"],
        type=src["type"],
        name=src["name"],
        normalized_key=src["normalized_key"],
        aliases=src.get("aliases", []),
        description=src.get("description"),
        cvss_score=src.get("cvss_score"),
        first_seen=src["first_seen"],
        last_seen=src["last_seen"],
        article_count=src.get("article_count", 0),
        articles=articles,
    )
