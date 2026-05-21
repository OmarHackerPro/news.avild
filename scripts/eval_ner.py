"""NER eval — diff local model output against cached Haiku output.

For each ner_cache row under model_version='haiku-4-5':
  1. Look up the article body in OpenSearch.
  2. Call the local sidecar to produce local entities (this also fills the
     'securebert-v1' cache row as a side effect — the backfill IS the eval).
  3. Compute per-entity diff vs. cached Haiku entities, classify by input_zone,
     write rows to ner_eval_judgments with verdict NULL (pending adjudication).

At the end, print a summary of agree / only-haiku / only-local counts per type.
"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio
import logging
from datetime import datetime
from typing import AsyncGenerator

from sqlalchemy import text

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.db.opensearch import INDEX_NEWS, get_os_client
from app.ingestion.ner_client import extract_entities_local

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

HAIKU_INPUT_CUTOFF = 500  # chars Haiku ever saw of the body


async def _iter_haiku_slugs() -> AsyncGenerator[tuple[str, list[dict]], None]:
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            text(
                "SELECT slug, entities_json FROM ner_cache "
                "WHERE model_version = 'haiku-4-5' "
                "ORDER BY slug"
            )
        )
        for slug, entities_json in rows.fetchall():
            yield slug, entities_json or []


async def _get_article_body(slug: str) -> tuple[str, str]:
    """Return (title, body_text) for the article. Empty strings if not found."""
    try:
        doc = await get_os_client().get(index=INDEX_NEWS, id=slug)
        src = doc.get("_source") or {}
        title = src.get("title") or ""
        body = src.get("content_extracted") or src.get("summary") or src.get("desc") or ""
        return title, body
    except Exception as exc:
        logger.warning("Skipping slug=%s — not in OpenSearch: %s", slug, exc)
        return "", ""


def _classify_zone(char_offset: int | None) -> str:
    if char_offset is None:
        return "shared"
    return "shared" if char_offset < HAIKU_INPUT_CUTOFF else "new-input"


def _diff(haiku: list[dict], local: list[dict]) -> list[tuple[dict, str, str | None]]:
    """Return list of (entity, source, input_zone) tuples to write as judgments."""
    # CVEs are handled by regex in production; exclude them from NER eval.
    haiku = [e for e in haiku if (e.get("type") or e.get("entity_type")) != "cve"]
    local = [e for e in local if e["type"] != "cve"]

    # Lowercase keys for comparison only — Haiku stores some keys differently-cased.
    haiku_cmp = {(e.get("type") or e.get("entity_type"), e["normalized_key"].lower()) for e in haiku}
    local_cmp = {(e["type"], e["normalized_key"].lower()) for e in local}
    out: list[tuple[dict, str, str | None]] = []

    agree = haiku_cmp & local_cmp
    only_haiku = haiku_cmp - local_cmp
    only_local = local_cmp - haiku_cmp

    for h in haiku:
        h_type = h.get("type") or h.get("entity_type")
        h_key = h["normalized_key"]
        cmp_key = (h_type, h_key.lower())
        if cmp_key in agree:
            out.append(({"type": h_type, "name": h.get("name", h_key), "normalized_key": h_key}, "both", "shared"))
        elif cmp_key in only_haiku:
            out.append(({"type": h_type, "name": h.get("name", h_key), "normalized_key": h_key}, "haiku", "shared"))

    for l in local:
        if (l["type"], l["normalized_key"].lower()) in only_local:
            zone = _classify_zone(l.get("char_offset"))
            out.append(({"type": l["type"], "name": l["name"], "normalized_key": l["normalized_key"]}, "local", zone))

    return out


async def _write_judgments(slug: str, judgments: list[tuple[dict, str, str | None]]) -> None:
    if not judgments:
        return
    async with AsyncSessionLocal() as session:
        for ent, source, zone in judgments:
            await session.execute(
                text(
                    "INSERT INTO ner_eval_judgments "
                    "(slug, entity_type, entity_normalized_key, source, input_zone) "
                    "VALUES (:slug, :etype, :ekey, :src, :zone) "
                    "ON CONFLICT (slug, entity_type, entity_normalized_key, source) DO NOTHING"
                ),
                {
                    "slug": slug,
                    "etype": ent["type"],
                    "ekey": ent["normalized_key"],
                    "src": source,
                    "zone": zone,
                },
            )
        await session.commit()


async def main() -> None:
    totals: dict[tuple[str, str], int] = {}  # (etype, status) -> count
    processed = 0
    async for slug, haiku_entities in _iter_haiku_slugs():
        title, body = await _get_article_body(slug)
        if not title and not body:
            continue
        async with AsyncSessionLocal() as session:
            local_entities = await extract_entities_local(
                slug=slug, title=title, body=body, db_session=session
            )

        judgments = _diff(haiku_entities, local_entities)
        await _write_judgments(slug, judgments)

        for ent, source, _ in judgments:
            key = (ent["type"], source)
            totals[key] = totals.get(key, 0) + 1
        processed += 1
        if processed % 50 == 0:
            logger.info("Processed %d articles", processed)

    print("\n=== EVAL SUMMARY ===")
    print(f"Articles processed: {processed}")
    by_type: dict[str, dict[str, int]] = {}
    for (etype, source), count in totals.items():
        by_type.setdefault(etype, {})[source] = count
    print(f"{'type':<14}{'agree':<10}{'only-haiku':<14}{'only-local':<14}")
    for etype, counts in sorted(by_type.items()):
        agree = counts.get("both", 0)
        only_h = counts.get("haiku", 0)
        only_l = counts.get("local", 0)
        print(f"{etype:<14}{agree:<10}{only_h:<14}{only_l:<14}")

    print("\nStopping criterion check (only-haiku rate vs haiku total per type):")
    for etype, counts in sorted(by_type.items()):
        haiku_total = counts.get("both", 0) + counts.get("haiku", 0)
        if haiku_total == 0:
            continue
        rate = counts.get("haiku", 0) / haiku_total
        threshold = 0.20 if etype == "campaign" else 0.10
        verdict = "PASS" if rate <= threshold else "FAIL"
        print(f"  {etype:<14}only-haiku rate={rate:.1%}  threshold={threshold:.0%}  {verdict}")

    print(f"\nFinished at {datetime.utcnow().isoformat()}Z")


if __name__ == "__main__":
    asyncio.run(main())
