"""Article clustering engine.

Assigns articles to clusters using a three-tier decision tree:
  1. CVE overlap  — exact match on shared CVE IDs (7-day window)
  2. Entity overlap — 2+ shared entity keys (48-hour window)
  3. Narrative similarity — more_like_this on title+summary (24-hour window)

If no existing cluster matches, a new one is created.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.db.opensearch import INDEX_CLUSTERS, INDEX_NEWS, get_os_client
from app.ingestion.normalizer import NormalizedArticle

logger = logging.getLogger(__name__)

# MLT score threshold — clusters below this are not considered matches.
# Tuned conservatively to avoid false merges; lower if clusters are too granular.
_MLT_SCORE_THRESHOLD = 10.0


# ---------------------------------------------------------------------------
# Finders — each returns the best matching cluster_id or None
# ---------------------------------------------------------------------------

async def find_cluster_by_cve(
    cve_ids: list[str], window_days: int = 7,
) -> Optional[str]:
    """Find an existing cluster that shares any CVE ID within the time window."""
    if not cve_ids:
        return None

    client = get_os_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()

    resp = await client.search(
        index=INDEX_CLUSTERS,
        body={
            "query": {
                "bool": {
                    "filter": [
                        {"terms": {"cve_ids": cve_ids}},
                        {"range": {"created_at": {"gte": cutoff}}},
                    ]
                }
            },
            "sort": [{"article_count": {"order": "desc"}}],
            "size": 1,
            "_source": False,
        },
    )

    hits = resp["hits"]["hits"]
    return hits[0]["_id"] if hits else None


async def find_cluster_by_entities(
    entity_keys: list[str], window_hours: int = 48,
) -> Optional[str]:
    """Find a cluster sharing 2+ entity keys within the time window."""
    if len(entity_keys) < 2:
        return None

    client = get_os_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()

    resp = await client.search(
        index=INDEX_CLUSTERS,
        body={
            "query": {
                "bool": {
                    "filter": [
                        {"range": {"created_at": {"gte": cutoff}}},
                    ],
                    "should": [
                        {"terms": {"entity_keys": entity_keys}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            "sort": [{"article_count": {"order": "desc"}}],
            "size": 10,
            "_source": ["entity_keys"],
        },
    )

    # Post-filter: require at least 2 overlapping keys
    for hit in resp["hits"]["hits"]:
        cluster_keys = set(hit["_source"].get("entity_keys") or [])
        overlap = cluster_keys & set(entity_keys)
        if len(overlap) >= 2:
            return hit["_id"]

    return None


async def find_cluster_by_mlt(
    title: str, summary: Optional[str], window_hours: int = 24,
) -> Optional[str]:
    """Find a narratively similar cluster via more_like_this."""
    like_text = title
    if summary:
        like_text = f"{title} {summary}"

    client = get_os_client()

    # Guard: MLT needs a minimum corpus to produce meaningful results
    count_resp = await client.count(index=INDEX_CLUSTERS)
    if count_resp.get("count", 0) < 20:
        return None

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()

    resp = await client.search(
        index=INDEX_CLUSTERS,
        body={
            "query": {
                "bool": {
                    "must": [
                        {
                            "more_like_this": {
                                "fields": ["label", "summary"],
                                "like": like_text,
                                "min_term_freq": 1,
                                "min_doc_freq": 1,
                                "minimum_should_match": "30%",
                            }
                        }
                    ],
                    "filter": [
                        {"range": {"created_at": {"gte": cutoff}}},
                    ],
                }
            },
            "size": 1,
            "_source": False,
        },
    )

    hits = resp["hits"]["hits"]
    if hits and hits[0]["_score"] >= _MLT_SCORE_THRESHOLD:
        return hits[0]["_id"]
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _tag_article(client, slug: str, cluster_id: str) -> None:
    """Set cluster_id on the article doc (denormalized back-reference)."""
    await client.update(
        index=INDEX_NEWS,
        id=slug,
        body={"doc": {"cluster_id": cluster_id}},
    )


# ---------------------------------------------------------------------------
# Mutators — create or merge
# ---------------------------------------------------------------------------

async def create_cluster(
    article: NormalizedArticle, entity_keys: list[str],
) -> str:
    """Create a new cluster seeded from a single article. Returns cluster _id."""
    now = datetime.now(timezone.utc).isoformat()
    slug = article["slug"]

    doc = {
        "label": article.get("title", ""),
        "state": "new",
        "summary": article.get("summary") or article.get("desc"),
        "why_it_matters": None,
        "score": None,
        "confidence": None,
        "article_ids": [slug],
        "article_count": 1,
        "cve_ids": article.get("cve_ids") or [],
        "entity_keys": entity_keys,
        "categories": [article["category"]] if article.get("category") else [],
        "tags": article.get("tags") or [],
        "timeline": [{
            "article_slug": slug,
            "source_name": article.get("source_name", ""),
            "title": article.get("title", ""),
            "published_at": article.get("published_at", now),
            "added_at": now,
        }],
        "latest_at": now,
        "created_at": now,
        "updated_at": now,
    }

    client = get_os_client()
    resp = await client.index(
        index=INDEX_CLUSTERS,
        body=doc,
        params={"refresh": "false"},
    )

    cluster_id = resp["_id"]
    await _tag_article(client, slug, cluster_id)
    logger.info("Created cluster %s for article '%s'", cluster_id, slug)
    return cluster_id


async def merge_into_cluster(
    cluster_id: str,
    article_slug: str,
    entity_keys: list[str],
    cve_ids: list[str],
    *,
    source_name: str = "",
    title: str = "",
    published_at: str = "",
) -> None:
    """Merge an article into an existing cluster via scripted update."""
    now = datetime.now(timezone.utc).isoformat()
    if not published_at:
        published_at = now

    script = """
        if (!ctx._source.article_ids.contains(params.slug)) {
            ctx._source.article_ids.add(params.slug);
            ctx._source.article_count = ctx._source.article_ids.size();
            // Append timeline entry (dedupe by slug)
            if (ctx._source.timeline == null) {
                ctx._source.timeline = new ArrayList();
            }
            boolean found = false;
            for (entry in ctx._source.timeline) {
                if (entry.article_slug.equals(params.slug)) { found = true; break; }
            }
            if (!found) {
                Map e = new HashMap();
                e.put('article_slug', params.slug);
                e.put('source_name', params.source_name);
                e.put('title', params.title);
                e.put('published_at', params.published_at);
                e.put('added_at', params.now);
                ctx._source.timeline.add(e);
            }
        }
        for (key in params.entity_keys) {
            if (!ctx._source.entity_keys.contains(key)) {
                ctx._source.entity_keys.add(key);
            }
        }
        for (cve in params.cve_ids) {
            if (!ctx._source.cve_ids.contains(cve)) {
                ctx._source.cve_ids.add(cve);
            }
        }
        ctx._source.latest_at = params.now;
        ctx._source.updated_at = params.now;
        if (ctx._source.article_count >= 3) {
            ctx._source.state = 'confirmed';
        } else if (ctx._source.article_count >= 2) {
            ctx._source.state = 'developing';
        }
    """

    client = get_os_client()
    await client.update(
        index=INDEX_CLUSTERS,
        id=cluster_id,
        body={
            "script": {
                "source": script,
                "params": {
                    "slug": article_slug,
                    "source_name": source_name,
                    "title": title,
                    "published_at": published_at,
                    "entity_keys": entity_keys,
                    "cve_ids": cve_ids,
                    "now": now,
                },
            },
        },
        retry_on_conflict=3,
    )

    await _tag_article(client, article_slug, cluster_id)
    logger.info("Merged article '%s' into cluster %s", article_slug, cluster_id)


# ---------------------------------------------------------------------------
# Orchestrator — the decision tree
# ---------------------------------------------------------------------------

async def cluster_article(
    article: NormalizedArticle,
    slug: str,
    entity_keys: list[str],
) -> None:
    """Assign an article to a cluster (existing or new).

    Decision priority:
      1. CVE overlap (strongest signal)
      2. Entity overlap (2+ shared keys)
      3. Narrative similarity (MLT fallback)
      4. Create new cluster
    """
    cve_ids = article.get("cve_ids") or []
    cluster_id: Optional[str] = None

    # 1. CVE overlap
    if cve_ids:
        cluster_id = await find_cluster_by_cve(cve_ids)
        if cluster_id:
            logger.debug("CVE match for '%s' → cluster %s", slug, cluster_id)

    # 2. Entity overlap (need 2+ keys to be meaningful)
    if not cluster_id and len(entity_keys) >= 2:
        cluster_id = await find_cluster_by_entities(entity_keys)
        if cluster_id:
            logger.debug("Entity match for '%s' → cluster %s", slug, cluster_id)

    # 3. Narrative similarity
    if not cluster_id:
        title = article.get("title") or ""
        summary = article.get("summary") or article.get("desc")
        cluster_id = await find_cluster_by_mlt(title, summary)
        if cluster_id:
            logger.debug("MLT match for '%s' → cluster %s", slug, cluster_id)

    # 4. Merge or create
    if cluster_id:
        await merge_into_cluster(
            cluster_id, slug, entity_keys, cve_ids,
            source_name=article.get("source_name", ""),
            title=article.get("title", ""),
            published_at=article.get("published_at", ""),
        )
    else:
        await create_cluster(article, entity_keys)
