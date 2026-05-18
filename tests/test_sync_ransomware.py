"""Tests for scripts/sync_ransomware.py — group parsing."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.sync_ransomware import _parse_groups


def test_parse_groups_active():
    raw = [
        {"name": "LockBit", "aliases": ["LockBit 3.0", "LockBit Black"], "status": "active"},
    ]
    rows = _parse_groups(raw)
    assert len(rows) == 1
    row = rows[0]
    assert row["normalized_key"] == "lockbit"
    assert row["display_name"] == "LockBit"
    assert row["entity_type"] == "actor"
    assert row["active"] is True
    assert "LockBit 3.0" in row["aliases"]


def test_parse_groups_inactive():
    raw = [{"name": "REvil", "aliases": [], "status": "inactive"}]
    rows = _parse_groups(raw)
    assert rows[0]["active"] is False


def test_parse_groups_skips_empty_name():
    raw = [{"name": "", "aliases": [], "status": "active"}]
    rows = _parse_groups(raw)
    assert rows == []


def test_parse_groups_deduplicates_by_key():
    """Two entries normalizing to the same key — last one wins (seen dict behavior)."""
    raw = [
        {"name": "BlackCat", "aliases": [], "status": "active"},
        {"name": "blackcat", "aliases": [], "status": "inactive"},
    ]
    rows = _parse_groups(raw)
    keys = [r["normalized_key"] for r in rows]
    assert keys.count("blackcat") == 1


def test_parse_groups_excludes_name_from_aliases():
    """The group's own display_name should not appear in the aliases list."""
    raw = [{"name": "LockBit", "aliases": ["LockBit", "LockBit 3.0"], "status": "active"}]
    rows = _parse_groups(raw)
    assert "LockBit" not in rows[0]["aliases"]
    assert "LockBit 3.0" in rows[0]["aliases"]


import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_sync_inserts_new_row():
    from scripts.sync_ransomware import _sync_to_db

    mock_result = MagicMock()
    mock_result.fetchone.return_value = None  # no existing row

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.db.session.AsyncSessionLocal", return_value=mock_ctx):
        ins, upd = await _sync_to_db([{
            "normalized_key": "lockbit",
            "display_name": "LockBit",
            "entity_type": "actor",
            "aliases": ["LockBit 3.0"],
            "source": "ransomware.live",
            "active": True,
        }])

    assert ins == 1
    assert upd == 0


@pytest.mark.asyncio
async def test_sync_attack_row_preserves_display_name():
    """Existing attack row (higher priority) — ransomware.live should NOT overwrite display_name."""
    from scripts.sync_ransomware import _sync_to_db

    existing_row = MagicMock()
    existing_row.__getitem__ = lambda self, i: "attack"  # source = attack

    mock_result = MagicMock()
    mock_result.fetchone.return_value = existing_row

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.db.session.AsyncSessionLocal", return_value=mock_ctx):
        ins, upd = await _sync_to_db([{
            "normalized_key": "lockbit",
            "display_name": "LockBit",
            "entity_type": "actor",
            "aliases": [],
            "source": "ransomware.live",
            "active": True,
        }])

    # Should not count as an update of the priority fields — attack row is preserved
    assert ins == 0
    assert upd == 0  # only last_synced updated, not counted as "upd"


@pytest.mark.asyncio
async def test_sync_cisa_kev_row_is_overwritten():
    """Existing cisa_kev row (lower priority) — ransomware.live should fully overwrite."""
    from scripts.sync_ransomware import _sync_to_db

    existing_row = MagicMock()
    existing_row.__getitem__ = lambda self, i: "cisa_kev"  # source = cisa_kev

    mock_result = MagicMock()
    mock_result.fetchone.return_value = existing_row

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.db.session.AsyncSessionLocal", return_value=mock_ctx):
        ins, upd = await _sync_to_db([{
            "normalized_key": "somedomain-vendor",
            "display_name": "SomeDomain Vendor",
            "entity_type": "actor",
            "aliases": ["Alias A"],
            "source": "ransomware.live",
            "active": True,
        }])

    assert ins == 0
    assert upd == 1  # full overwrite counts as update
