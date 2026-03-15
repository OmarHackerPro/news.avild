"""Persist extracted entities to OpenSearch.

Handles upserts for the entities index and tracks article linkage via
the article_ids field on each entity document.
"""
import logging
from datetime import datetime, timezone

from app.db.opensearch import INDEX_ENTITIES, get_os_client

logger = logging.getLogger(__name__)


async def store_article_entities(
    article_slug: str,
    entities: list[dict],
) -> None:
    """Upsert entities and link them to an article.

    Each entity dict must have keys: type, name, normalized_key.
    Optional key: cvss_score.
    """
    if not entities:
        return

    client = get_os_client()
    now = datetime.now(timezone.utc).isoformat()

    for ent in entities:
        doc_id = ent["normalized_key"]

        # Try to update existing entity doc (add article_id, bump last_seen)
        update_body: dict = {
            "script": {
                "source": (
                    "if (!ctx._source.article_ids.contains(params.slug)) {"
                    "  ctx._source.article_ids.add(params.slug);"
                    "  ctx._source.article_count = ctx._source.article_ids.length();"
                    "}"
                    "ctx._source.last_seen = params.now;"
                    "if (params.cvss != null && ctx._source.cvss_score == null) {"
                    "  ctx._source.cvss_score = params.cvss;"
                    "}"
                ),
                "params": {
                    "slug": article_slug,
                    "now": now,
                    "cvss": ent.get("cvss_score"),
                },
            },
            "upsert": {
                "type": ent["type"],
                "name": ent["name"],
                "normalized_key": ent["normalized_key"],
                "aliases": [],
                "description": None,
                "cvss_score": ent.get("cvss_score"),
                "article_ids": [article_slug],
                "article_count": 1,
                "first_seen": now,
                "last_seen": now,
            },
        }

        try:
            await client.update(
                index=INDEX_ENTITIES,
                id=doc_id,
                body=update_body,
                retry_on_conflict=3,
            )
        except Exception:
            logger.exception("Failed to upsert entity %s", doc_id)
