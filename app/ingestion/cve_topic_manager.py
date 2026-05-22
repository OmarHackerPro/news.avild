"""Manages cve_topics index documents.

Two public functions:
  upsert_cve_topics()       — attach an article to CVE topics (creates if missing)
  create_cve_topic_stubs()  — create empty CVE topic docs for roundup articles

When a CVE topic is created for the first time, EPSS scores are fetched inline
from FIRST.org so the topic is never born without exploit-prediction data.
Existing topics are left untouched here — scripts/refresh_epss.py owns refresh.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from app.db.opensearch import INDEX_CVE_TOPICS, get_os_client
from app.ingestion.epss_client import fetch_epss

logger = logging.getLogger(__name__)


async def _epss_for_new_cves(os_client, cve_ids: list[str]) -> dict[str, dict]:
    """Return EPSS data keyed by uppercase CVE ID, only for CVEs with no topic doc.

    Existing topics are excluded — refresh_epss.py keeps those current. On any
    failure returns an empty dict so topic creation still proceeds.
    """
    ids = list({c.upper() for c in cve_ids if c})
    if not ids:
        return {}
    try:
        resp = await os_client.search(
            index=INDEX_CVE_TOPICS,
            body={"query": {"ids": {"values": ids}}, "size": len(ids), "_source": False},
        )
        existing = {hit["_id"] for hit in resp["hits"]["hits"]}
    except Exception as exc:
        logger.warning("EPSS existence check failed: %s", exc)
        return {}
    new_ids = [i for i in ids if i not in existing]
    if not new_ids:
        return {}
    try:
        return await fetch_epss(new_ids)
    except Exception as exc:
        logger.warning("EPSS fetch failed for %d new CVEs: %s", len(new_ids), exc)
        return {}


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
    epss_map = await _epss_for_new_cves(os_client, cve_ids)

    for cve_id in cve_ids:
        try:
            await _upsert_one(
                os_client, cve_id, article_slug, aliases, embedding, now,
                epss=epss_map.get(cve_id.upper()),
            )
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

    missing: list[str] = []
    for cve_id in cve_ids:
        try:
            if not await os_client.exists(index=INDEX_CVE_TOPICS, id=cve_id):
                missing.append(cve_id)
        except Exception as exc:
            logger.warning("cve_topic existence check failed for %s: %s", cve_id, exc)
    if not missing:
        return

    epss_map: dict[str, dict] = {}
    try:
        epss_map = await fetch_epss([c.upper() for c in missing])
    except Exception as exc:
        logger.warning("EPSS fetch failed for %d stub CVEs: %s", len(missing), exc)

    for cve_id in missing:
        try:
            epss = epss_map.get(cve_id.upper())
            doc = {
                "cve_id": cve_id,
                "aliases": [],
                "cvss_score": None,
                "cvss_severity": None,
                "cvss_vector": None,
                "cisa_kev": False,
                "epss_score": epss["epss_score"] if epss else None,
                "epss_percentile": epss["epss_percentile"] if epss else None,
                "epss_updated_at": epss["epss_updated_at"] if epss else None,
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
    epss: Optional[dict] = None,
) -> None:
    doc_on_create: dict = {
        "cve_id": cve_id,
        "aliases": aliases,
        "cvss_score": None,
        "cvss_severity": None,
        "cvss_vector": None,
        "cisa_kev": False,
        "epss_score": epss["epss_score"] if epss else None,
        "epss_percentile": epss["epss_percentile"] if epss else None,
        "epss_updated_at": epss["epss_updated_at"] if epss else None,
        "article_ids": [article_slug],
        "article_count": 1,
        "linked_event_ids": [],
        "created_at": now,
        "updated_at": now,
    }
    if embedding is not None:
        doc_on_create["cve_embedding"] = embedding

    # cve_topics has multiple producers: this module creates docs with the
    # incident-tracking fields, but the NVD/KEV enrichers (upsert_immutable)
    # create docs without them. Null-init before use, or the script throws
    # "failed to execute script" on every enricher-created topic.
    script_source = """
        if (ctx._source.article_ids == null) { ctx._source.article_ids = []; }
        if (ctx._source.article_count == null) { ctx._source.article_count = 0; }
        if (ctx._source.aliases == null) { ctx._source.aliases = []; }
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
