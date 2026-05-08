"""Send the WhatsApp brief.

Stub mode (default): writes brief to a file and logs to stdout.
Live mode: sends via Twilio when TWILIO_ACCOUNT_SID is set.
"""
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_OUTPUT_DIR = "/app/briefs"


async def send_brief(
    text: str,
    output_dir: str = _DEFAULT_OUTPUT_DIR,
    date_str: str | None = None,
) -> bool:
    """Send or stub-send the brief. Returns True on success."""
    twilio_sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()

    if twilio_sid:
        return await _send_twilio(text)

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


async def _send_twilio(text: str) -> bool:
    import asyncio

    try:
        from twilio.rest import Client  # noqa: PLC0415
    except ImportError:
        logger.error("twilio package not installed; cannot send live message")
        return False

    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ.get("TWILIO_FROM_NUMBER", "")
    to_number = os.environ.get("WHATSAPP_PHONE_NUMBER", "")

    if not to_number:
        logger.error("WHATSAPP_PHONE_NUMBER not set; cannot send")
        return False

    def _sync_send():
        client = Client(sid, token)
        client.messages.create(
            from_=f"whatsapp:{from_number}" if not from_number.startswith("whatsapp:") else from_number,
            to=f"whatsapp:{to_number}" if not to_number.startswith("whatsapp:") else to_number,
            body=text,
        )

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _sync_send)
        logger.info("WhatsApp brief sent to %s", to_number)
        return True
    except Exception as exc:
        logger.error("Twilio send failed: %s", exc)
        return False
