"""Tests for entity_extractor — file-based loader and alias normalization."""
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from app.ingestion.entity_extractor import extract_entities


def _make_article(title="", desc=""):
    return {"title": title, "desc": desc, "summary": None, "cve_ids": [], "content_html": None}


# ---------------------------------------------------------------------------
# Fallback baseline — must work even without data file
# ---------------------------------------------------------------------------

def test_baseline_extracts_lockbit():
    """Fallback baseline must contain at minimum the original 35 entries."""
    article = _make_article(title="LockBit ransomware hits logistics company")
    entities = extract_entities(article)
    keys = [e["normalized_key"] for e in entities]
    assert "lockbit" in keys


def test_baseline_extracts_apt29():
    article = _make_article(title="APT29 targets European embassies")
    entities = extract_entities(article)
    keys = [e["normalized_key"] for e in entities]
    assert "apt29" in keys


# ---------------------------------------------------------------------------
# File-based loader — injects a minimal test fixture
# ---------------------------------------------------------------------------

_MINIMAL_DATA = {
    "keywords": {
        "ransomhub": ["RansomHub", "malware"],
        "apt29": ["APT29", "actor"],
        "havoc": ["Havoc", "tool"],
    },
    "aliases": {
        "Midnight Blizzard": "apt29",
        "Nobelium": "apt29",
        "Forest Blizzard": "apt28",
    },
}


@pytest.fixture
def patched_data_file(tmp_path):
    """Write minimal test data to a temp file and patch _DATA_FILE."""
    p = tmp_path / "threat_keywords.json"
    p.write_text(json.dumps(_MINIMAL_DATA))
    with patch("app.ingestion.entity_extractor._DATA_FILE", p):
        # Force reload
        import importlib
        import app.ingestion.entity_extractor as mod
        importlib.reload(mod)
        yield mod
    # Reload back to normal state
    import importlib
    import app.ingestion.entity_extractor as mod
    importlib.reload(mod)


def test_file_loader_adds_new_entry(patched_data_file):
    mod = patched_data_file
    article = {"title": "RansomHub claims hospital attack", "desc": "", "summary": None, "cve_ids": [], "content_html": None}
    entities = mod.extract_entities(article)
    keys = [e["normalized_key"] for e in entities]
    assert "ransomhub" in keys
    types = {e["normalized_key"]: e["type"] for e in entities}
    assert types["ransomhub"] == "malware"


# ---------------------------------------------------------------------------
# Alias normalization — different text maps to same normalized_key
# ---------------------------------------------------------------------------

def test_alias_midnight_blizzard_maps_to_apt29(patched_data_file):
    """Both 'APT29' (via keywords) and 'Midnight Blizzard' (via alias table)
    should produce the same canonical normalized_key 'apt29'."""
    mod = patched_data_file
    a1 = {"title": "APT29 exfiltrates diplomatic cables", "desc": "", "summary": None, "cve_ids": [], "content_html": None}
    a2 = {"title": "Midnight Blizzard exfiltrates diplomatic cables", "desc": "", "summary": None, "cve_ids": [], "content_html": None}
    e1_keys = {e["normalized_key"] for e in mod.extract_entities(a1)}
    e2_keys = {e["normalized_key"] for e in mod.extract_entities(a2)}
    assert "apt29" in e1_keys
    assert "apt29" in e2_keys


def test_alias_does_not_produce_duplicate_keys(patched_data_file):
    """Article mentioning both APT29 and its alias must yield exactly one entity."""
    mod = patched_data_file
    article = {"title": "APT29, also known as Midnight Blizzard, targeted...", "desc": "", "summary": None, "cve_ids": [], "content_html": None}
    entities = mod.extract_entities(article)
    keys = [e["normalized_key"] for e in entities]
    assert keys.count("apt29") == 1


def test_missing_data_file_falls_back_to_baseline(tmp_path):
    """extract_entities must still work when data/threat_keywords.json does not exist."""
    nonexistent = tmp_path / "no_file.json"
    with patch("app.ingestion.entity_extractor._DATA_FILE", nonexistent):
        import importlib
        import app.ingestion.entity_extractor as mod
        importlib.reload(mod)
        article = {"title": "LockBit attacks retailer", "desc": "", "summary": None, "cve_ids": [], "content_html": None}
        entities = mod.extract_entities(article)
        keys = [e["normalized_key"] for e in entities]
        assert "lockbit" in keys
    importlib.reload(mod)
