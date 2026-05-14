"""HTTP client wrapper around the local NER sidecar.

Caches results in Postgres ner_cache keyed by (slug, model_version). On HTTP
failure returns [] and does not write to cache.

Same input/output shape as ner_llm.extract_entities_llm to minimize churn in
entity_extractor.extract_entities().
"""
import json
import logging
from typing import Optional

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

logger = logging.getLogger(__name__)

_http_client: httpx.AsyncClient | None = None


def _get_http() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            base_url=settings.NER_SIDECAR_URL,
            timeout=httpx.Timeout(settings.NER_REQUEST_TIMEOUT_S),
        )
    return _http_client


async def _get_cached(
    slug: str, model_version: str, session: AsyncSession
) -> Optional[list[dict]]:
    result = await session.execute(
        text(
            "SELECT entities_json FROM ner_cache "
            "WHERE slug = :slug AND model_version = :version"
        ),
        {"slug": slug, "version": model_version},
    )
    row = result.fetchone()
    return row[0] if row else None


async def _write_cache(
    slug: str, model_version: str, entities: list[dict], session: AsyncSession
) -> None:
    await session.execute(
        text(
            "INSERT INTO ner_cache (slug, model_version, entities_json, extracted_at) "
            "VALUES (:slug, :version, :entities, NOW()) "
            "ON CONFLICT (slug, model_version) DO NOTHING"
        ),
        {"slug": slug, "version": model_version, "entities": json.dumps(entities)},
    )
    await session.commit()


async def extract_entities_local(
    slug: str,
    title: str,
    body: str,
    db_session: Optional[AsyncSession],
) -> list[dict]:
    """Extract entities via the local NER sidecar.

    Cache key: (slug, NER_ACTIVE_MODEL). Failures return [] and do not cache.
    """
    model_version = settings.NER_ACTIVE_MODEL
    if db_session is not None:
        cached = await _get_cached(slug, model_version, db_session)
        if cached is not None:
            return cached

    entities: list[dict] = []
    try:
        resp = await _get_http().post(
            "/extract",
            json={"slug": slug, "title": title, "body": body or ""},
        )
        resp.raise_for_status()
        data = resp.json()
        entities = data.get("entities", []) or []
        if db_session is not None:
            await _write_cache(slug, model_version, entities, db_session)
    except Exception as exc:
        logger.warning("Local NER failed for slug=%s: %s", slug, exc)

    return entities
