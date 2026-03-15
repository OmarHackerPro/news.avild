import csv
import io
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.api.routes.news import _build_filters
from app.db.opensearch import INDEX_ENTITIES, INDEX_NEWS, get_os_client

router = APIRouter(prefix="/exports", tags=["exports"])

_ALL_CSV_COLUMNS = [
    "id", "title", "source_name", "source_url", "category", "type",
    "severity", "tags", "cve_ids", "cvss_score", "published_at", "author",
]


async def _fetch_articles(
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
    max_items: int = 1000,
) -> list:
    """Fetch articles from OpenSearch for export."""
    filters = _build_filters(
        category=category, type=type, severity=severity,
        source_name=source_name, tag=tag, cve=cve,
        min_cvss=min_cvss, date_from=date_from, date_to=date_to,
    )
    body = {
        "query": {"bool": {"filter": filters}} if filters else {"match_all": {}},
        "sort": [{"published_at": {"order": "desc"}}],
        "size": max_items,
        "_source": [
            "slug", "title", "desc", "author", "source_name", "source_url",
            "image_url", "tags", "keywords", "severity", "type", "category",
            "cvss_score", "cve_ids", "published_at", "content_html", "raw_metadata",
        ],
    }
    resp = await get_os_client().search(index=INDEX_NEWS, body=body)
    return resp["hits"]["hits"]


@router.get(
    "/csv",
    summary="Export articles as CSV",
    description="Downloads a CSV file of articles matching the given filters. Optionally specify which columns to include.",
)
async def export_csv(
    category: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    source_name: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    cve: Optional[str] = Query(None),
    min_cvss: Optional[float] = Query(None, ge=0, le=10),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    columns: Optional[str] = Query(None, description="Comma-separated column names"),
):
    """Export articles as CSV."""
    hits = await _fetch_articles(
        category=category, type=type, severity=severity,
        source_name=source_name, tag=tag, cve=cve,
        min_cvss=min_cvss, date_from=date_from, date_to=date_to,
    )

    cols = [c.strip() for c in columns.split(",")] if columns else _ALL_CSV_COLUMNS

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()

    for hit in hits:
        src = hit["_source"]
        row = {
            "id": hit["_id"],
            "title": src.get("title", ""),
            "source_name": src.get("source_name", ""),
            "source_url": src.get("source_url", ""),
            "category": src.get("category", ""),
            "type": src.get("type", ""),
            "severity": src.get("severity", ""),
            "tags": ";".join(src.get("tags") or []),
            "cve_ids": ";".join(src.get("cve_ids") or []),
            "cvss_score": src.get("cvss_score", ""),
            "published_at": src.get("published_at", ""),
            "author": src.get("author", ""),
        }
        writer.writerow(row)

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=articles.csv"},
    )


@router.get(
    "/json",
    summary="Export articles as JSON",
    description="Downloads a JSON file containing an array of articles matching the given filters.",
)
async def export_json(
    category: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    source_name: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    cve: Optional[str] = Query(None),
    min_cvss: Optional[float] = Query(None, ge=0, le=10),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    """Export articles as JSON array."""
    hits = await _fetch_articles(
        category=category, type=type, severity=severity,
        source_name=source_name, tag=tag, cve=cve,
        min_cvss=min_cvss, date_from=date_from, date_to=date_to,
    )

    articles = []
    for hit in hits:
        src = hit["_source"]
        articles.append({
            "id": hit["_id"],
            "slug": src.get("slug") or hit["_id"],
            "title": src.get("title"),
            "desc": src.get("desc"),
            "content_html": src.get("content_html"),
            "author": src.get("author"),
            "source_name": src.get("source_name"),
            "source_url": src.get("source_url"),
            "image_url": src.get("image_url"),
            "tags": src.get("tags") or [],
            "keywords": src.get("keywords") or [],
            "severity": src.get("severity"),
            "type": src.get("type"),
            "category": src.get("category"),
            "cvss_score": src.get("cvss_score"),
            "cve_ids": src.get("cve_ids") or [],
            "raw_metadata": src.get("raw_metadata"),
            "published_at": src.get("published_at"),
        })

    content = json.dumps(articles, indent=2, default=str)
    return StreamingResponse(
        io.BytesIO(content.encode()),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=articles.json"},
    )


@router.get(
    "/stix",
    summary="Export entities as STIX 2.1",
    description="Downloads a STIX 2.1 bundle containing CVEs, threat actors, malware, and tools as structured threat intelligence objects.",
)
async def export_stix(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
):
    """Export IOCs as STIX 2.1 bundle."""
    filters: list[dict] = []
    if date_from:
        filters.append({"range": {"last_seen": {"gte": date_from}}})
    if date_to:
        filters.append({"range": {"first_seen": {"lte": date_to}}})

    body: dict = {
        "query": {"bool": {"filter": filters}} if filters else {"match_all": {}},
        "sort": [{"last_seen": {"order": "desc"}}],
        "size": 500,
    }

    resp = await get_os_client().search(index=INDEX_ENTITIES, body=body)

    _TYPE_MAP = {
        "cve": ("vulnerability", lambda n: {"external_references": [{"source_name": "cve", "external_id": n}]}),
        "actor": ("threat-actor", lambda n: {"threat_actor_types": ["unknown"]}),
        "malware": ("malware", lambda n: {"is_family": True}),
        "tool": ("tool", lambda n: {}),
    }

    objects = []
    now = datetime.now(timezone.utc).isoformat()

    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        ent_type = src["type"]
        if ent_type not in _TYPE_MAP:
            continue
        stix_type, extra_fn = _TYPE_MAP[ent_type]
        obj = {
            "type": stix_type,
            "spec_version": "2.1",
            "id": f"{stix_type}--{hit['_id']}",
            "created": now,
            "modified": now,
            "name": src["name"],
            **extra_fn(src["name"]),
        }
        objects.append(obj)

    bundle = {
        "type": "bundle",
        "id": f"bundle--{uuid.uuid4()}",
        "objects": objects,
    }

    content = json.dumps(bundle, indent=2)
    return StreamingResponse(
        io.BytesIO(content.encode()),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=stix-bundle.json"},
    )
