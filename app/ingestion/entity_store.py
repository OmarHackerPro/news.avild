"""Persist extracted entities to PostgreSQL.

Handles upserts for both the entities table and the article_entities junction table.
"""
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.entity import ArticleEntity, Entity

logger = logging.getLogger(__name__)


async def store_article_entities(
    article_slug: str,
    entities: list[dict],
    session: AsyncSession,
) -> None:
    """Upsert entities and link them to an article.

    Each entity dict must have keys: type, name, normalized_key.
    Optional key: cvss_score.
    """
    if not entities:
        return

    entity_ids: list[uuid.UUID] = []
    now = datetime.now(timezone.utc)

    for ent in entities:
        stmt = pg_insert(Entity).values(
            id=uuid.uuid4(),
            type=ent["type"],
            name=ent["name"],
            normalized_key=ent["normalized_key"],
            cvss_score=ent.get("cvss_score"),
            first_seen=now,
            last_seen=now,
        )

        # Always update last_seen on conflict.
        # For cvss_score: use COALESCE(existing, new) so we only fill NULLs.
        update_set: dict = {"last_seen": now}
        if ent.get("cvss_score") is not None:
            update_set["cvss_score"] = func.coalesce(
                Entity.cvss_score, stmt.excluded.cvss_score
            )

        stmt = stmt.on_conflict_do_update(
            index_elements=["normalized_key"],
            set_=update_set,
        ).returning(Entity.id)

        result = await session.execute(stmt)
        entity_id = result.scalar_one()
        entity_ids.append(entity_id)

    # Bulk link article ↔ entities
    if entity_ids:
        link_stmt = pg_insert(ArticleEntity).values(
            [
                {"article_id": article_slug, "entity_id": eid}
                for eid in entity_ids
            ]
        ).on_conflict_do_nothing()
        await session.execute(link_stmt)
