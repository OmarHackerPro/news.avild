from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Optional
from xml.sax.saxutils import escape

from fastapi import APIRouter, Query, Response

from app.db.opensearch import INDEX_NEWS, get_os_client

router = APIRouter(tags=["rss"])


def _to_rfc2822(iso_str: str) -> str:
    """Convert ISO-8601 string to RFC 2822 for RSS."""
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return format_datetime(dt, usegmt=True)


@router.get("/rss", response_class=Response)
async def rss_feed(
    category: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """RSS 2.0 feed of latest articles."""
    filters = []
    if category:
        filters.append({"term": {"category": category}})
    if severity:
        filters.append({"term": {"severity": severity}})

    body = {
        "query": {"bool": {"filter": filters}} if filters else {"match_all": {}},
        "sort": [{"published_at": {"order": "desc"}}],
        "size": limit,
        "_source": [
            "slug", "title", "desc", "source_url", "published_at",
            "author", "category", "tags",
        ],
    }

    resp = await get_os_client().search(index=INDEX_NEWS, body=body)
    hits = resp["hits"]["hits"]

    items_xml = []
    for hit in hits:
        src = hit["_source"]
        slug = src.get("slug") or hit["_id"]
        title = escape(src.get("title", ""))
        desc = escape(src.get("desc") or "")
        link = src.get("source_url") or ""
        pub_date = _to_rfc2822(src["published_at"]) if src.get("published_at") else ""
        author = escape(src.get("author") or "")
        categories = "".join(
            f"      <category>{escape(t)}</category>\n" for t in (src.get("tags") or [])
        )
        items_xml.append(
            f"    <item>\n"
            f"      <title>{title}</title>\n"
            f"      <link>{escape(link)}</link>\n"
            f"      <guid isPermaLink=\"false\">{escape(slug)}</guid>\n"
            f"      <description>{desc}</description>\n"
            f"      <pubDate>{pub_date}</pubDate>\n"
            f"      <author>{author}</author>\n"
            f"{categories}"
            f"    </item>\n"
        )

    now_rfc = format_datetime(datetime.now(timezone.utc), usegmt=True)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "  <channel>\n"
        "    <title>news.avild.com — Security News</title>\n"
        "    <link>https://news.avild.com</link>\n"
        "    <description>Cybersecurity news and threat intelligence</description>\n"
        "    <language>en</language>\n"
        f"    <lastBuildDate>{now_rfc}</lastBuildDate>\n"
        + "".join(items_xml)
        + "  </channel>\n"
        "</rss>\n"
    )

    return Response(content=xml, media_type="application/rss+xml")
