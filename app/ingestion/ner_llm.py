"""Claude Haiku NER — extracts security entities from article text.

Caches results in Postgres ner_cache table. Pass db_session=None to skip cache
(useful for unit tests and one-off calls).
"""
import logging
import os
from typing import Literal, Optional

import anthropic
import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_async_client: anthropic.AsyncAnthropic | None = None

_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 1024


class _EntityResult(BaseModel):
    type: Literal["cve", "product", "malware", "actor", "tool", "vuln_alias", "campaign"]
    name: str
    normalized_key: str


def _get_client() -> anthropic.AsyncAnthropic:
    global _async_client
    if _async_client is None:
        _async_client = anthropic.AsyncAnthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"],
            timeout=httpx.Timeout(30.0),
        )
    return _async_client


_SYSTEM_PROMPT = """You are a cybersecurity named entity extractor. Extract security-relevant entities from the article title and summary.

Entity types:
- cve: CVE identifiers. Keep format (e.g. "CVE-2021-44228"). normalized_key = same as name.
- product: software/hardware, include version if present. "FortiGate 7.4.2" → "fortigate-7.4.2". Skip bare names without version (skip "Windows", include "Windows 11 23H2" → "windows-11-23h2").
- malware: malware families. "LockBit 3.0" → "lockbit-3.0". "BlackCat" → "blackcat".
- actor: threat actor groups. "Lazarus Group" → "lazarus-group". "APT29" → "apt29".
- tool: attack tools/frameworks. "Cobalt Strike" → "cobalt-strike". "Mimikatz" → "mimikatz".
- vuln_alias: vulnerability nicknames. "Log4Shell" → "log4shell". "Heartbleed" → "heartbleed". "CitrixBleed" → "citrixbleed". "PrintNightmare" → "printnightmare".
- campaign: named incidents or campaigns. "MOVEit Transfer campaign" → "moveit-transfer-campaign". "SolarWinds breach" → "solarwinds-breach".

Normalization: lowercase everything, spaces and special chars → hyphens. Only extract entities you are confident about."""

_TOOL = {
    "name": "extract_entities",
    "description": "Return extracted security entities from the article.",
    "input_schema": {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["cve", "product", "malware", "actor", "tool", "vuln_alias", "campaign"],
                        },
                        "name": {"type": "string"},
                        "normalized_key": {"type": "string"},
                    },
                    "required": ["type", "name", "normalized_key"],
                },
            }
        },
        "required": ["entities"],
    },
}


async def _get_cached(slug: str, session: AsyncSession) -> Optional[list[dict]]:
    result = await session.execute(
        text("SELECT entities_json FROM ner_cache WHERE slug = :slug"),
        {"slug": slug},
    )
    row = result.fetchone()
    return row[0] if row else None


async def _write_cache(slug: str, entities: list[dict], session: AsyncSession) -> None:
    await session.execute(
        text(
            "INSERT INTO ner_cache (slug, entities_json, extracted_at) "
            "VALUES (:slug, :entities, NOW()) "
            "ON CONFLICT (slug) DO NOTHING"
        ),
        {"slug": slug, "entities": entities},
    )
    await session.commit()


async def extract_entities_llm(
    slug: str,
    title: str,
    summary: str,
    db_session: Optional[AsyncSession],
) -> list[dict]:
    """Extract entities via Claude Haiku. Returns list of entity dicts.

    Falls back to [] if the LLM call fails. Results are cached in Postgres by slug.
    Pass db_session=None to skip cache (testing / one-off use).
    """
    if db_session is not None:
        cached = await _get_cached(slug, db_session)
        if cached is not None:
            return cached

    text_input = f"Title: {title}\nSummary: {(summary or '')[:500]}"
    entities: list[dict] = []
    try:
        response = await _get_client().messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "extract_entities"},
            messages=[{"role": "user", "content": text_input}],
        )
        tool_block = next((b for b in response.content if b.type == "tool_use"), None)
        raw = tool_block.input.get("entities", []) if tool_block else []
        for item in raw:
            if isinstance(item, dict):
                try:
                    entities.append(_EntityResult.model_validate(item).model_dump())
                except ValidationError:
                    logger.warning("Skipping invalid entity from LLM: %s", item)
        if db_session is not None:
            await _write_cache(slug, entities, db_session)
    except Exception as exc:
        logger.warning("LLM NER failed for slug=%s: %s", slug, exc)

    return entities
