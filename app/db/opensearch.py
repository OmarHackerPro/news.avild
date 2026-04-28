from opensearchpy import AsyncOpenSearch

from app.core.config import settings

INDEX_NEWS = "news_articles"
INDEX_SNAPSHOTS = "raw_feed_snapshots"
INDEX_CLUSTERS = "clusters"
INDEX_ENTITIES = "entities"
INDEX_NVD_CACHE = "nvd_cache"

_client: AsyncOpenSearch | None = None

NEWS_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "refresh_interval": "10s",
        "index.knn": True,
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
            "summary":      {"type": "text", "analyzer": "english"},
            "content_source": {"type": "keyword"},
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
            "credibility_weight": {"type": "half_float"},
            "cluster_id":   {"type": "keyword"},
            "raw_metadata": {"type": "object", "dynamic": True},
            "created_at":   {
                "type": "date",
                "format": "strict_date_time||strict_date_time_no_millis",
            },
            "updated_at":   {
                "type": "date",
                "format": "strict_date_time||strict_date_time_no_millis",
            },
            "article_embedding": {
                "type": "knn_vector",
                "dimension": 1024,
                "method": {
                    "name": "hnsw",
                    "space_type": "cosinesimil",
                    "engine": "nmslib",
                },
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


_CLUSTERS_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "refresh_interval": "10s",
        "index.knn": True,
    },
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "label": {
                "type": "text",
                "analyzer": "english",
                "fields": {"raw": {"type": "keyword", "ignore_above": 512}},
            },
            "state":          {"type": "keyword"},
            "summary":        {"type": "text", "analyzer": "english"},
            "why_it_matters": {"type": "text", "analyzer": "english"},
            "score":          {"type": "half_float"},
            "confidence":     {"type": "keyword"},
            "max_cvss":       {"type": "half_float"},
            "cisa_kev":       {"type": "boolean"},
            "max_credibility_weight": {"type": "half_float"},
            "top_factors": {
                "type": "nested",
                "properties": {
                    "factor": {"type": "keyword"},
                    "label":  {"type": "keyword"},
                    "points": {"type": "half_float"},
                },
            },
            "article_ids":    {"type": "keyword"},
            "categories":     {"type": "keyword"},
            "tags":           {"type": "keyword"},
            "article_count":  {"type": "integer"},
            "cve_ids":        {"type": "keyword"},
            "seed_cve_ids":   {"type": "keyword"},
            "entity_keys":    {"type": "keyword"},
            "timeline": {
                "type": "nested",
                "properties": {
                    "article_slug": {"type": "keyword"},
                    "source_name":  {"type": "keyword"},
                    "title":        {"type": "text", "analyzer": "english"},
                    "published_at": {
                        "type": "date",
                        "format": "strict_date_time||strict_date_time_no_millis",
                    },
                    "added_at": {
                        "type": "date",
                        "format": "strict_date_time||strict_date_time_no_millis",
                    },
                },
            },
            "latest_at": {
                "type": "date",
                "format": "strict_date_time||strict_date_time_no_millis",
            },
            "created_at": {
                "type": "date",
                "format": "strict_date_time||strict_date_time_no_millis",
            },
            "updated_at": {
                "type": "date",
                "format": "strict_date_time||strict_date_time_no_millis",
            },
            "centroid_embedding": {
                "type": "knn_vector",
                "dimension": 1024,
                "method": {
                    "name": "hnsw",
                    "space_type": "cosinesimil",
                    "engine": "nmslib",
                },
            },
            "event_signature": {
                "type": "object",
                "properties": {
                    "cve_ids":           {"type": "keyword"},
                    "vuln_aliases":      {"type": "keyword"},
                    "campaign_names":    {"type": "keyword"},
                    "affected_products": {"type": "keyword"},
                    "primary_actors":    {"type": "keyword"},
                    "confidence":        {"type": "keyword"},
                },
            },
            "merged_into": {"type": "keyword"},
        },
    },
}


_ENTITIES_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "refresh_interval": "10s",
    },
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "type":           {"type": "keyword"},
            "name": {
                "type": "text",
                "analyzer": "english",
                "fields": {"raw": {"type": "keyword", "ignore_above": 512}},
            },
            "normalized_key": {"type": "keyword"},
            "aliases":        {"type": "keyword"},
            "description":    {"type": "text", "analyzer": "english"},
            "cvss_score":     {"type": "half_float"},
            "cvss_severity":  {"type": "keyword"},
            "cvss_vector":    {"type": "keyword"},
            "cwe_ids":        {"type": "keyword"},
            "vuln_status":    {"type": "keyword"},
            "cisa_kev":       {"type": "boolean"},
            "nvd_last_modified": {
                "type": "date",
                "format": "date_optional_time||epoch_millis",
            },
            "article_ids":    {"type": "keyword"},
            "article_count":  {"type": "integer"},
            "first_seen": {
                "type": "date",
                "format": "strict_date_time||strict_date_time_no_millis",
            },
            "last_seen": {
                "type": "date",
                "format": "strict_date_time||strict_date_time_no_millis",
            },
        },
    },
}


_NVD_CACHE_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "refresh_interval": "30s",
    },
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "cve_id":           {"type": "keyword"},
            "fetched_at":       {"type": "date", "format": "date_optional_time||epoch_millis"},
            "nvd_last_modified": {"type": "date", "format": "date_optional_time||epoch_millis"},
            "nvd_raw":          {"type": "object", "enabled": False},
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
    """Create or update OpenSearch indexes. Recreates clusters index if k-NN not enabled."""
    import logging
    log = logging.getLogger(__name__)
    client = get_os_client()

    for index, mapping in [
        (INDEX_NEWS, NEWS_MAPPING),
        (INDEX_SNAPSHOTS, _SNAPSHOTS_MAPPING),
        (INDEX_CLUSTERS, _CLUSTERS_MAPPING),
        (INDEX_ENTITIES, _ENTITIES_MAPPING),
        (INDEX_NVD_CACHE, _NVD_CACHE_MAPPING),
    ]:
        try:
            exists = await client.indices.exists(index=index)
            needs_knn = mapping["settings"].get("index.knn", False)

            if exists and needs_knn:
                settings_resp = await client.indices.get_settings(index=index)
                knn_on = settings_resp[index]["settings"].get("index", {}).get("knn") == "true"
                if not knn_on:
                    if index == INDEX_CLUSTERS:
                        log.warning("Recreating %s index to enable k-NN", index)
                        await client.indices.delete(index=index)
                        await client.indices.create(index=index, body=mapping)
                    else:
                        log.warning(
                            "Index '%s' exists without k-NN. article_embedding will not be "
                            "k-NN searchable until the index is manually reindexed.",
                            index,
                        )
                        try:
                            await client.indices.put_mapping(
                                index=index,
                                body={"properties": mapping["mappings"]["properties"]},
                            )
                        except Exception:
                            pass
                else:
                    await client.indices.put_mapping(
                        index=index,
                        body={"properties": mapping["mappings"]["properties"]},
                    )
                    log.info("Updated mapping for index: %s", index)
            elif not exists:
                await client.indices.create(index=index, body=mapping)
                log.info("Created OpenSearch index: %s", index)
            else:
                await client.indices.put_mapping(
                    index=index,
                    body={"properties": mapping["mappings"]["properties"]},
                )
                log.info("Updated mapping for index: %s", index)
        except Exception as exc:
            log.warning("Could not ensure index '%s': %s", index, exc)
