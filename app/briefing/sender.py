"""Send the WhatsApp brief.

Stub mode (default): writes brief to a file and logs to stdout.
Live mode: sends via WAHA when WAHA_API_KEY is set.
"""
import logging
import os
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_OUTPUT_DIR = "/app/briefs"


async def send_brief(
    text: str,
    output_dir: str = _DEFAULT_OUTPUT_DIR,
    date_str: str | None = None,
) -> bool:
    """Send or stub-send the brief. Returns True on success."""
    if os.environ.get("WAHA_API_KEY", "").strip():
        return await _send_waha(text)

    return _send_stub(text, output_dir=output_dir, date_str=date_str)


def _send_stub(text: str, output_dir: str, date_str: str | None) -> bool:
    logger.info("=== WHATSAPP BRIEF (stub) ===\n%s\n=== END BRIEF ===", text)
    try:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        fname = f"{date_str or 'brief'}.txt"
        (Path(output_dir) / fname).write_text(text)
    except Exception as exc:
        logger.warning("Could not write brief file: %s", exc)
    return True


async def _send_waha(text: str) -> bool:
    url = os.environ.get("WAHA_URL", "http://waha:3000").rstrip("/")
    api_key = os.environ["WAHA_API_KEY"]
    chat_id = os.environ.get("WAHA_CHAT_ID", "")

    if not chat_id:
        logger.error("WAHA_CHAT_ID not set; cannot send")
        return False

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{url}/api/sendText",
                headers={"X-Api-Key": api_key},
                json={"chatId": chat_id, "text": text, "session": "default"},
            )
            resp.raise_for_status()
        logger.info("WhatsApp brief sent to %s via WAHA", chat_id)
        return True
    except Exception as exc:
        logger.error("WAHA send failed: %s", exc)
        return False
