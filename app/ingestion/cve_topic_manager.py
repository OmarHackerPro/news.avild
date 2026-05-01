"""Manages cve_topics index documents.

Two public functions:
  upsert_cve_topics()       — attach an article to CVE topics (creates if missing)
  create_cve_topic_stubs()  — create empty CVE topic docs for roundup articles
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from app.db.opensearch import INDEX_CVE_TOPICS, get_os_client

logger = logging.getLogger(__name__)


async def upsert_cve_topics(
    cve_ids: list[str],
    article_slug: str,
    entities: list[dict],
    embedding: Optional[list[float]],
) -> None:
    """Create or update cve_topic documents and attach the article to each."""
    if not cve_ids:
        return
    os_client = get_os_client()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    aliases = [e["normalized_key"] for e in entities if e["type"] == "vuln_alias"]

    for cve_id in cve_ids:
        try:
            await _upsert_one(os_client, cve_id, article_slug, aliases, embedding, now)
        except Exception as exc:
            logger.warning("cve_topic upsert failed for %s: %s", cve_id, exc)


async def create_cve_topic_stubs(cve_ids: list[str]) -> None:
    """Create empty cve_topic documents for roundup articles.

    Does not attach the roundup article — just ensures the CVE topic exists
    so it is discoverable. Skips CVEs that already have a topic document.
    """
    if not cve_ids:
        return
    os_client = get_os_client()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for cve_id in cve_ids:
        try:
            exists = await os_client.exists(index=INDEX_CVE_TOPICS, id=cve_id)
            if not exists:
                doc = {
                    "cve_id": cve_id,
                    "aliases": [],
                    "cvss_score": None,
                    "cvss_severity": None,
                    "cvss_vector": None,
                    "cisa_kev": False,
                    "epss_score": None,
                    "epss_percentile": None,
                    "article_ids": [],
                    "article_count": 0,
                    "linked_event_ids": [],
                    "created_at": now,
                    "updated_at": now,
                }
                await os_client.index(index=INDEX_CVE_TOPICS, id=cve_id, body=doc)
        except Exception as exc:
            logger.warning("cve_topic stub creation failed for %s: %s", cve_id, exc)


async def _upsert_one(
    os_client,
    cve_id: str,
    article_slug: str,
    aliases: list[str],
    embedding: Optional[list[float]],
    now: str,
) -> None:
    doc_on_create: dict = {
        "cve_id": cve_id,
        "aliases": aliases,
        "cvss_score": None,
        "cvss_severity": None,
        "cvss_vector": None,
        "cisa_kev": False,
        "epss_score": None,
        "epss_percentile": None,
        "article_ids": [article_slug],
        "article_count": 1,
        "linked_event_ids": [],
        "created_at": now,
        "updated_at": now,
    }
    if embedding is not None:
        doc_on_create["cve_embedding"] = embedding

    script_source = """
        if (!ctx._source.article_ids.contains(params.slug)) {
            ctx._source.article_ids.add(params.slug);
            ctx._source.article_count += 1;
        }
        for (alias in params.aliases) {
            if (!ctx._source.aliases.contains(alias)) {
                ctx._source.aliases.add(alias);
            }
        }
        if (params.embedding != null) {
            ctx._source.cve_embedding = params.embedding;
        }
        ctx._source.updated_at = params.now;
    """

    await os_client.update(
        index=INDEX_CVE_TOPICS,
        id=cve_id,
        body={
            "script": {
                "source": script_source,
                "lang": "painless",
                "params": {
                    "slug": article_slug,
                    "aliases": aliases,
                    "embedding": embedding,
                    "now": now,
                },
            },
            "upsert": doc_on_create,
        },
        retry_on_conflict=3,
    )
