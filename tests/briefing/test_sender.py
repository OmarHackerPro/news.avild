import pytest
from pathlib import Path
from app.briefing.sender import send_brief


@pytest.mark.asyncio
async def test_stub_mode_writes_file(tmp_path, monkeypatch):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER", "+1234567890")

    ok = await send_brief("Hello brief", output_dir=str(tmp_path), date_str="2026-05-08")
    assert ok is True

    files = list(tmp_path.glob("*.txt"))
    assert len(files) == 1
    assert files[0].read_text() == "Hello brief"


@pytest.mark.asyncio
async def test_stub_mode_no_phone_number_still_returns_true(tmp_path, monkeypatch):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("WHATSAPP_PHONE_NUMBER", raising=False)

    ok = await send_brief("Hello brief", output_dir=str(tmp_path), date_str="2026-05-08")
    assert ok is True
