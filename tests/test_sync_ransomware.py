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
