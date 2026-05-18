"""Tests for the STIX parser in scripts/sync_mitre_attack.py."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.sync_mitre_attack import _normalize_key, _parse_stix_bundle, _SKIP_NAMES


def _make_obj(type_, name, aliases=None, x_mitre_aliases=None,
              revoked=False, deprecated=False):
    obj = {
        "type": type_,
        "name": name,
        "revoked": revoked,
        "x_mitre_deprecated": deprecated,
    }
    if aliases is not None:
        obj["aliases"] = aliases
    if x_mitre_aliases is not None:
        obj["x_mitre_aliases"] = x_mitre_aliases
    return obj


# --- normalize_key ---

def test_normalize_key_lowercases():
    assert _normalize_key("APT29") == "apt29"


def test_normalize_key_replaces_spaces_with_hyphens():
    assert _normalize_key("Cozy Bear") == "cozy-bear"


def test_normalize_key_strips_leading_trailing_hyphens():
    assert _normalize_key("@dmin") == "dmin"


def test_normalize_key_collapses_multiple_separators():
    assert _normalize_key("admin@338") == "admin-338"


# --- _parse_stix_bundle: actors ---

def test_actor_extracted():
    bundle = {"objects": [_make_obj("intrusion-set", "APT29", aliases=["APT29", "Cozy Bear"])]}
    kw, al, _ = _parse_stix_bundle(bundle)
    assert "apt29" in kw
    assert kw["apt29"] == ["APT29", "actor"]


def test_actor_alias_maps_to_canonical():
    bundle = {"objects": [_make_obj("intrusion-set", "APT29", aliases=["APT29", "Cozy Bear", "Midnight Blizzard"])]}
    _, al, _si = _parse_stix_bundle(bundle)
    assert al["Cozy Bear"] == "apt29"
    assert al["Midnight Blizzard"] == "apt29"


def test_actor_primary_name_not_in_aliases():
    """The primary name should NOT appear as an alias pointing to itself."""
    bundle = {"objects": [_make_obj("intrusion-set", "APT29", aliases=["APT29", "Cozy Bear"])]}
    _, al, _si = _parse_stix_bundle(bundle)
    assert "APT29" not in al


# --- _parse_stix_bundle: malware ---

def test_malware_extracted():
    bundle = {"objects": [_make_obj("malware", "LockBit", x_mitre_aliases=["LockBit 3.0"])]}
    kw, al, _ = _parse_stix_bundle(bundle)
    assert "lockbit" in kw
    assert kw["lockbit"] == ["LockBit", "malware"]


def test_malware_x_mitre_aliases_captured():
    bundle = {"objects": [_make_obj("malware", "BlackCat", x_mitre_aliases=["ALPHV", "Noberus"])]}
    _, al, _si = _parse_stix_bundle(bundle)
    assert al["ALPHV"] == "blackcat"
    assert al["Noberus"] == "blackcat"


# --- _parse_stix_bundle: tools ---

def test_tool_extracted():
    bundle = {"objects": [_make_obj("tool", "Cobalt Strike")]}
    kw, al, _ = _parse_stix_bundle(bundle)
    assert "cobalt-strike" in kw
    assert kw["cobalt-strike"] == ["Cobalt Strike", "tool"]


# --- _parse_stix_bundle: campaigns ---

def test_campaign_extracted():
    bundle = {"objects": [_make_obj("campaign", "SolarWinds Compromise",
                                    aliases=["SolarWinds Compromise"])]}
    kw, al, _ = _parse_stix_bundle(bundle)
    assert "solarwinds-compromise" in kw
    assert kw["solarwinds-compromise"] == ["SolarWinds Compromise", "campaign"]


def test_campaign_alias_captured():
    bundle = {"objects": [_make_obj("campaign", "SolarWinds Compromise",
                                    aliases=["SolarWinds Compromise", "UNC2452 Campaign"])]}
    _, al, _si = _parse_stix_bundle(bundle)
    assert al["UNC2452 Campaign"] == "solarwinds-compromise"


def test_campaign_primary_not_in_aliases():
    bundle = {"objects": [_make_obj("campaign", "Operation Honeybee",
                                    aliases=["Operation Honeybee"])]}
    _, al, _si = _parse_stix_bundle(bundle)
    assert "Operation Honeybee" not in al


# --- skip revoked / deprecated ---

def test_revoked_objects_skipped():
    bundle = {"objects": [_make_obj("malware", "OldMalware", revoked=True)]}
    kw, _al, _si = _parse_stix_bundle(bundle)
    assert "oldmalware" not in kw


def test_deprecated_objects_skipped():
    bundle = {"objects": [_make_obj("tool", "OldTool", deprecated=True)]}
    kw, _al, _si = _parse_stix_bundle(bundle)
    assert "oldtool" not in kw


def test_unknown_stix_types_ignored():
    bundle = {"objects": [{"type": "attack-pattern", "name": "Spearphishing"}]}
    kw, al, _ = _parse_stix_bundle(bundle)
    assert kw == {}
    assert al == {}


# --- no duplicates ---

def test_no_duplicate_keywords_on_name_collision():
    """Two objects with same normalized key — last one wins (deterministic)."""
    bundle = {"objects": [
        _make_obj("malware", "LockBit"),
        _make_obj("malware", "lockbit"),  # same key
    ]}
    kw, _al, _si = _parse_stix_bundle(bundle)
    assert list(kw.keys()).count("lockbit") == 1


# --- _SKIP_NAMES noise filter ---

def test_skip_names_excludes_common_words():
    """Objects whose name is in _SKIP_NAMES must be excluded from output."""
    # "at" and "net" are in _SKIP_NAMES — they should never appear in keywords
    for noise_name in _SKIP_NAMES:
        bundle = {"objects": [_make_obj("tool", noise_name)]}
        kw, _al, _si = _parse_stix_bundle(bundle)
        assert _normalize_key(noise_name) not in kw, (
            f"'{noise_name}' from _SKIP_NAMES should be excluded but was found in keywords"
        )


def test_skip_names_case_insensitive():
    """_SKIP_NAMES check is case-insensitive (names are lowercased before comparison)."""
    bundle = {"objects": [_make_obj("tool", "AT")]}
    kw, _al, _si = _parse_stix_bundle(bundle)
    assert "at" not in kw


# --- _upsert_to_db unit tests (mocked DB, no real connection) ---

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_upsert_inserts_new_row():
    """A key not yet in DB gets inserted."""
    from scripts.sync_mitre_attack import _upsert_to_db

    mock_result_none = MagicMock()
    mock_result_none.fetchone.return_value = None

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result_none)
    mock_db.commit = AsyncMock()

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.db.session.AsyncSessionLocal", return_value=mock_session_ctx):
        ins, upd = await _upsert_to_db(
            {"apt29": ["APT29", "actor"]},
            {"Cozy Bear": "apt29"},
        )

    assert ins == 1
    assert upd == 0


@pytest.mark.asyncio
async def test_upsert_updates_existing_attack_row():
    """An existing attack-source row is updated (same priority = allowed)."""
    from scripts.sync_mitre_attack import _upsert_to_db

    existing_row = MagicMock()
    existing_row.__getitem__ = lambda self, i: "attack"

    mock_result_existing = MagicMock()
    mock_result_existing.fetchone.return_value = existing_row

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result_existing)
    mock_db.commit = AsyncMock()

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.db.session.AsyncSessionLocal", return_value=mock_session_ctx):
        ins, upd = await _upsert_to_db({"lockbit": ["LockBit", "malware"]}, {})

    assert ins == 0
    assert upd == 1


@pytest.mark.asyncio
async def test_upsert_attack_overwrites_lower_priority_source():
    """attack source overwrites a ransomware.live row (attack has higher priority)."""
    from scripts.sync_mitre_attack import _upsert_to_db

    existing_row = MagicMock()
    existing_row.__getitem__ = lambda self, i: "ransomware.live"

    mock_result = MagicMock()
    mock_result.fetchone.return_value = existing_row

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.db.session.AsyncSessionLocal", return_value=mock_session_ctx):
        ins, upd = await _upsert_to_db(
            {"lockbit": ["LockBit", "actor"]},
            {},
            {},  # source_ids
        )

    assert ins == 0
    assert upd == 1  # attack should overwrite ransomware.live
