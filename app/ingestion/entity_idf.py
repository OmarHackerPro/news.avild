"""Entity document-frequency IDF map.

Built from the `entities` index. Common entities get a near-zero weight,
rare entities a high weight. Read synchronously by unified_scorer._compute_score
via idf(); when the map is empty, idf() returns _DEFAULT_IDF for every key so
scoring degrades to plain unweighted Jaccard.
"""
import logging
import math

from app.db.opensearch import INDEX_ENTITIES, INDEX_NEWS, get_os_client

logger = logging.getLogger(__name__)

_MIN_IDF = 0.01
_DEFAULT_IDF = 1.0

_IDF_MAP: dict[str, float] = {}


def _compute_idf(n_articles: int, df: int) -> float:
    n = max(n_articles, 1)
    d = max(df, 1)
    return max(_MIN_IDF, math.log(n / d))


def idf(key: str) -> float:
    """Synchronous IDF lookup. Returns _DEFAULT_IDF for unseen / unbuilt keys."""
    return _IDF_MAP.get(key, _DEFAULT_IDF)


async def build_idf_map() -> dict[str, float]:
    """Scan the entities index, return {normalized_key: idf}. Does not mutate cache."""
    client = get_os_client()
    count_resp = await client.count(index=INDEX_NEWS)
    n_articles = count_resp.get("count", 0)

    result: dict[str, float] = {}
    resp = await client.search(
        index=INDEX_ENTITIES,
        body={"query": {"match_all": {}}, "_source": ["normalized_key", "article_count"]},
        scroll="2m",
        size=1000,
    )
    scroll_id = resp.get("_scroll_id")
    hits = resp["hits"]["hits"]
    while hits:
        for hit in hits:
            src = hit["_source"]
            key = src.get("normalized_key")
            if not key:
                continue
            df = src.get("article_count") or 1
            result[key] = _compute_idf(n_articles, df)
        resp = await client.scroll(scroll_id=scroll_id, scroll="2m")
        scroll_id = resp.get("_scroll_id")
        hits = resp["hits"]["hits"]
    if scroll_id:
        try:
            await client.clear_scroll(scroll_id=scroll_id)
        except Exception:
            pass
    return result


async def refresh_idf_map() -> int:
    """Rebuild the cached IDF map. Returns entity count. Swallows errors (leaves
    the map as-is so scoring still degrades gracefully)."""
    try:
        new_map = await build_idf_map()
    except Exception:
        logger.warning("IDF map build failed — entity scoring falls back to plain Jaccard", exc_info=True)
        return len(_IDF_MAP)
    _IDF_MAP.clear()
    _IDF_MAP.update(new_map)
    logger.info("IDF map built: %d entities", len(_IDF_MAP))
    return len(_IDF_MAP)


async def ensure_idf_map() -> None:
    """Build the map on first use if it has not been built yet."""
    if not _IDF_MAP:
        await refresh_idf_map()
