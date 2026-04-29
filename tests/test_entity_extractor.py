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

@pytest.mark.asyncio
async def test_baseline_extracts_lockbit():
    """Fallback baseline must contain at minimum the original 35 entries."""
    article = _make_article(title="LockBit ransomware hits logistics company")
    entities = await extract_entities(article)
    keys = [e["normalized_key"] for e in entities]
    assert "lockbit" in keys


@pytest.mark.asyncio
async def test_baseline_extracts_apt29():
    article = _make_article(title="APT29 targets European embassies")
    entities = await extract_entities(article)
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


@pytest.mark.asyncio
async def test_file_loader_adds_new_entry(patched_data_file):
    mod = patched_data_file
    article = {"title": "RansomHub claims hospital attack", "desc": "", "summary": None, "cve_ids": [], "content_html": None}
    entities = await mod.extract_entities(article)
    keys = [e["normalized_key"] for e in entities]
    assert "ransomhub" in keys
    types = {e["normalized_key"]: e["type"] for e in entities}
    assert types["ransomhub"] == "malware"


# ---------------------------------------------------------------------------
# Alias normalization — different text maps to same normalized_key
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_alias_midnight_blizzard_maps_to_apt29(patched_data_file):
    """Both 'APT29' (via keywords) and 'Midnight Blizzard' (via alias table)
    should produce the same canonical normalized_key 'apt29'."""
    mod = patched_data_file
    a1 = {"title": "APT29 exfiltrates diplomatic cables", "desc": "", "summary": None, "cve_ids": [], "content_html": None}
    a2 = {"title": "Midnight Blizzard exfiltrates diplomatic cables", "desc": "", "summary": None, "cve_ids": [], "content_html": None}
    e1_keys = {e["normalized_key"] for e in await mod.extract_entities(a1)}
    e2_keys = {e["normalized_key"] for e in await mod.extract_entities(a2)}
    assert "apt29" in e1_keys
    assert "apt29" in e2_keys


@pytest.mark.asyncio
async def test_alias_does_not_produce_duplicate_keys(patched_data_file):
    """Article mentioning both APT29 and its alias must yield exactly one entity."""
    mod = patched_data_file
    article = {"title": "APT29, also known as Midnight Blizzard, targeted...", "desc": "", "summary": None, "cve_ids": [], "content_html": None}
    entities = await mod.extract_entities(article)
    keys = [e["normalized_key"] for e in entities]
    assert keys.count("apt29") == 1


@pytest.mark.asyncio
async def test_missing_data_file_falls_back_to_baseline(tmp_path):
    """extract_entities must still work when data/threat_keywords.json does not exist."""
    nonexistent = tmp_path / "no_file.json"
    with patch("app.ingestion.entity_extractor._DATA_FILE", nonexistent):
        import importlib
        import app.ingestion.entity_extractor as mod
        importlib.reload(mod)
        article = {"title": "LockBit attacks retailer", "desc": "", "summary": None, "cve_ids": [], "content_html": None}
        entities = await mod.extract_entities(article)
        keys = [e["normalized_key"] for e in entities]
        assert "lockbit" in keys
    importlib.reload(mod)


# ---------------------------------------------------------------------------
# LLM-first path tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_entities_llm_results_take_precedence():
    """When slug is provided, LLM entities come first; regex adds new keys only."""
    from unittest.mock import AsyncMock, patch

    llm_result = [
        {"type": "vuln_alias", "name": "CitrixBleed", "normalized_key": "citrixbleed"},
        {"type": "cve", "name": "CVE-2023-4966", "normalized_key": "CVE-2023-4966"},
    ]
    article = _make_article(title="CitrixBleed CVE-2023-4966 — LockBit exploiting NetScaler")

    with patch("app.ingestion.ner_llm.extract_entities_llm", new_callable=AsyncMock, return_value=llm_result):
        entities = await extract_entities(article, slug="test-slug")

    keys = [e["normalized_key"] for e in entities]
    # LLM entities present
    assert "citrixbleed" in keys
    assert "CVE-2023-4966" in keys
    # Regex-extracted entity (lockbit) also present as supplement
    assert "lockbit" in keys
    # No duplicates
    assert len(keys) == len(set(keys))


@pytest.mark.asyncio
async def test_extract_entities_falls_back_to_regex_when_llm_returns_empty():
    """When LLM returns no entities, regex results are used."""
    from unittest.mock import AsyncMock, patch

    article = _make_article(title="LockBit ransomware hits hospital")

    with patch("app.ingestion.ner_llm.extract_entities_llm", new_callable=AsyncMock, return_value=[]):
        entities = await extract_entities(article, slug="test-slug")

    keys = [e["normalized_key"] for e in entities]
    assert "lockbit" in keys
