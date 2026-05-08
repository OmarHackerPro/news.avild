"""Generate per-cluster WhatsApp summaries via Claude Haiku."""
import logging
import os
from typing import Any, Optional

import anthropic
import httpx

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 256

_SYSTEM = (
    "You are writing a WhatsApp security brief for a security professional. "
    "In exactly 2-3 punchy sentences, summarise this story. "
    "Cover: what happened, what/who is affected, and the severity or urgency. "
    "No bullet points. No markdown. Plain text only."
)

_client: Optional[anthropic.AsyncAnthropic] = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"],
            timeout=httpx.Timeout(30.0),
        )
    return _client


async def generate_cluster_summary(
    cluster: dict,
    client: Optional[Any] = None,
) -> str:
    """Return 2-3 sentence plain-text summary. Falls back to cluster['summary'] on error."""
    c = client or _get_client()
    cves = ", ".join(cluster.get("cve_ids") or [])
    cvss = cluster.get("max_cvss")
    user_msg = (
        f"Story: {cluster['label']}\n"
        f"Summary: {cluster.get('summary', '')}\n"
        f"Why it matters: {cluster.get('why_it_matters', '')}\n"
    )
    if cves:
        user_msg += f"CVEs: {cves}\n"
    if cvss:
        user_msg += f"Max CVSS: {cvss}\n"

    try:
        resp = await c.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        text_block = next((b for b in resp.content if b.type == "text"), None)
        if text_block and text_block.text.strip():
            return text_block.text.strip()
    except Exception as exc:
        logger.warning("LLM summary failed for cluster %s: %s", cluster.get("id"), exc)

    return cluster.get("summary") or ""
