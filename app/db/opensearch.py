from opensearchpy import AsyncOpenSearch

from app.core.config import settings

INDEX_NEWS = "news_articles"
INDEX_SNAPSHOTS = "raw_feed_snapshots"

_client: AsyncOpenSearch | None = None

_NEWS_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "refresh_interval": "10s",
    },
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "slug":         {"type": "keyword"},
            "guid":         {"type": "keyword", "index": False},
            "source_id":    {"type": "integer"},
            "source_name":  {"type": "keyword"},
            "title": {
                "type": "text",
                "analyzer": "english",
                "fields": {"raw": {"type": "keyword", "ignore_above": 512}},
            },
            "author":       {"type": "keyword"},
            "desc":         {"type": "text", "analyzer": "english"},
            "content_html": {
                "type": "text",
                "analyzer": "english",
                "index_options": "offsets",
            },
            "image_url":    {"type": "keyword", "index": False},
            "tags":         {"type": "keyword"},
            "keywords":     {"type": "keyword"},
            "published_at": {
                "type": "date",
                "format": "strict_date_time||strict_date_time_no_millis",
            },
            "severity":     {"type": "keyword"},
            "type":         {"type": "keyword"},
            "category":     {"type": "keyword"},
            "source_url":   {"type": "keyword", "index": False},
            "cvss_score":   {"type": "half_float"},
            "cve_ids":      {"type": "keyword"},
            "raw_metadata": {"type": "object", "dynamic": True},
            "created_at":   {
                "type": "date",
                "format": "strict_date_time||strict_date_time_no_millis",
            },
            "updated_at":   {
                "type": "date",
                "format": "strict_date_time||strict_date_time_no_millis",
            },
        },
    },
}

_SNAPSHOTS_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "refresh_interval": "30s",
    },
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "content_hash": {"type": "keyword"},
            "source_name":  {"type": "keyword"},
            "source_url":   {"type": "keyword", "index": False},
            "raw_content":  {"type": "keyword", "index": False, "doc_values": False},
            "http_status":  {"type": "short"},
            "fetched_at":   {
                "type": "date",
                "format": "strict_date_time||strict_date_time_no_millis",
            },
            "entry_count":  {"type": "integer"},
            "created_at":   {
                "type": "date",
                "format": "strict_date_time||strict_date_time_no_millis",
            },
        },
    },
}


def get_os_client() -> AsyncOpenSearch:
    global _client
    if _client is None:
        if not settings.OPENSEARCH_URL:
            raise RuntimeError("OPENSEARCH_URL not configured")
        _client = AsyncOpenSearch(
            hosts=[settings.OPENSEARCH_URL],
            use_ssl=settings.OPENSEARCH_URL.startswith("https"),
            verify_certs=False,
            http_auth=(settings.OPENSEARCH_USER, settings.OPENSEARCH_PASSWORD)
            if settings.OPENSEARCH_USER
            else None,
        )
    return _client


async def close_os_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


async def ensure_indexes() -> None:
    """Create OpenSearch indexes if they don't exist. Safe to call on every startup."""
    client = get_os_client()
    for index, mapping in [
        (INDEX_NEWS, _NEWS_MAPPING),
        (INDEX_SNAPSHOTS, _SNAPSHOTS_MAPPING),
    ]:
        if not await client.indices.exists(index=index):
            await client.indices.create(index=index, body=mapping)
