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


# ---------------------------------------------------------------------------
# merge_entities() tests
# ---------------------------------------------------------------------------

from app.ingestion.entity_extractor import merge_entities


def test_merge_entities_no_overlap():
    text = [{"type": "vendor", "name": "Ivanti", "normalized_key": "ivanti"}]
    tag = [{"type": "malware", "name": "LockBit", "normalized_key": "lockbit", "source": "tag", "sources": ["tag"]}]
    result = merge_entities(text, tag)
    keys = [e["normalized_key"] for e in result]
    assert "ivanti" in keys
    assert "lockbit" in keys
    assert len(result) == 2


def test_merge_entities_overlap_merges_sources():
    text = [{"type": "vendor", "name": "Ivanti", "normalized_key": "ivanti"}]
    tag = [{"type": "vendor", "name": "Ivanti", "normalized_key": "ivanti", "source": "tag", "sources": ["tag"]}]
    result = merge_entities(text, tag)
    assert len(result) == 1
    ivanti = result[0]
    assert set(ivanti["sources"]) == {"text", "tag"}


def test_merge_entities_text_entity_gets_sources_field():
    text = [{"type": "vendor", "name": "Cisco", "normalized_key": "cisco"}]
    result = merge_entities(text, [])
    assert result[0]["sources"] == ["text"]


def test_merge_entities_empty_inputs():
    assert merge_entities([], []) == []


# ---------------------------------------------------------------------------
# refresh_entity_intel — DB-backed startup loader
# ---------------------------------------------------------------------------

from collections import namedtuple
from unittest.mock import AsyncMock, MagicMock

_EntityRow = namedtuple("_EntityRow", ["normalized_key", "display_name", "entity_type", "aliases"])


def _mock_db_with_rows(rows):
    """Return an AsyncMock db session that yields rows on execute."""
    mock_result = MagicMock()
    mock_result.fetchall.return_value = rows
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    return mock_db


@pytest.mark.asyncio
async def test_refresh_entity_intel_loads_vendor():
    import importlib
    import app.ingestion.entity_extractor as mod
    importlib.reload(mod)

    rows = [_EntityRow("fortinetsec", "FortinetSec", "vendor", ["FortinetSec"])]
    db = _mock_db_with_rows(rows)
    count = await mod.refresh_entity_intel(db)

    assert count == 1
    assert "fortinetsec" in mod._DB_ENTITY_MAP
    assert mod._DB_ENTITY_MAP["fortinetsec"] == ("FortinetSec", "vendor")


@pytest.mark.asyncio
async def test_refresh_entity_intel_builds_alias_index():
    import importlib
    import app.ingestion.entity_extractor as mod
    importlib.reload(mod)

    rows = [_EntityRow("apt29", "APT29", "actor", ["Cozy Bear", "Midnight Blizzard"])]
    db = _mock_db_with_rows(rows)
    await mod.refresh_entity_intel(db)

    assert mod._DB_ALIAS_INDEX.get("cozy-bear") == "apt29"
    assert mod._DB_ALIAS_INDEX.get("midnight-blizzard") == "apt29"


@pytest.mark.asyncio
async def test_refresh_entity_intel_returns_zero_on_empty_db():
    import importlib
    import app.ingestion.entity_extractor as mod
    importlib.reload(mod)

    db = _mock_db_with_rows([])
    count = await mod.refresh_entity_intel(db)
    assert count == 0


# ---------------------------------------------------------------------------
# _resolve_aliases — Stage 4 alias resolution for NER output
# ---------------------------------------------------------------------------

from app.ingestion.entity_extractor import _resolve_aliases


def test_resolve_aliases_rewrites_to_canonical():
    """NER entity whose normalized_key is a known alias gets rewritten."""
    import app.ingestion.entity_extractor as mod
    mod._DB_ENTITY_MAP["apt29"] = ("APT29", "actor")
    mod._DB_ALIAS_INDEX["midnight-blizzard"] = "apt29"

    entities = [{"type": "actor", "name": "Midnight Blizzard", "normalized_key": "midnight-blizzard", "mentions": 1}]
    resolved = _resolve_aliases(entities)

    assert len(resolved) == 1
    assert resolved[0]["normalized_key"] == "apt29"
    assert resolved[0]["name"] == "APT29"


def test_resolve_aliases_deduplicates_to_higher_mentions():
    """Two NER entities resolving to same canonical key — keep higher mentions."""
    import app.ingestion.entity_extractor as mod
    mod._DB_ENTITY_MAP["apt29"] = ("APT29", "actor")
    mod._DB_ALIAS_INDEX["cozy-bear"] = "apt29"
    mod._DB_ALIAS_INDEX["midnight-blizzard"] = "apt29"

    entities = [
        {"type": "actor", "name": "Cozy Bear", "normalized_key": "cozy-bear", "mentions": 2},
        {"type": "actor", "name": "Midnight Blizzard", "normalized_key": "midnight-blizzard", "mentions": 5},
    ]
    resolved = _resolve_aliases(entities)

    assert len(resolved) == 1
    assert resolved[0]["mentions"] == 5


def test_resolve_aliases_passthrough_when_no_match():
    """Entity with no alias entry passes through unchanged."""
    import app.ingestion.entity_extractor as mod
    mod._DB_ALIAS_INDEX.clear()

    entities = [{"type": "malware", "name": "SomeNewThing", "normalized_key": "some-new-thing", "mentions": 1}]
    resolved = _resolve_aliases(entities)

    assert resolved[0]["normalized_key"] == "some-new-thing"


def test_resolve_aliases_returns_unchanged_when_index_empty():
    """When _DB_ALIAS_INDEX is empty (no DB loaded), pass through unchanged."""
    import app.ingestion.entity_extractor as mod
    mod._DB_ALIAS_INDEX.clear()
    mod._DB_ENTITY_MAP.clear()

    entities = [{"type": "actor", "name": "Lazarus", "normalized_key": "lazarus", "mentions": 3}]
    resolved = _resolve_aliases(entities)

    assert len(resolved) == 1
    assert resolved[0]["normalized_key"] == "lazarus"


# ---------------------------------------------------------------------------
# _enrich_from_kev — deterministic vendor+product from CVE lookup
# ---------------------------------------------------------------------------

from app.ingestion.entity_extractor import _enrich_from_kev

_KevRow = namedtuple("_KevRow", ["vendor", "product"])


@pytest.mark.asyncio
async def test_enrich_from_kev_emits_vendor_and_product():
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [_KevRow("Microsoft", "Windows")]
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    entities = await _enrich_from_kev(["CVE-2024-1234"], mock_db)
    types = {e["type"] for e in entities}
    keys = {e["normalized_key"] for e in entities}

    assert "vendor" in types
    assert "product" in types
    assert "microsoft" in keys
    assert "windows" in keys


@pytest.mark.asyncio
async def test_enrich_from_kev_returns_empty_when_no_session():
    entities = await _enrich_from_kev(["CVE-2024-1234"], None)
    assert entities == []


@pytest.mark.asyncio
async def test_enrich_from_kev_deduplicates_same_vendor():
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [
        _KevRow("Microsoft", "Windows"),
        _KevRow("Microsoft", "Exchange"),
    ]
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    entities = await _enrich_from_kev(["CVE-2024-0001", "CVE-2024-0002"], mock_db)
    vendor_entities = [e for e in entities if e["type"] == "vendor"]
    assert len(vendor_entities) == 1  # deduplicated


# ---------------------------------------------------------------------------
# CWE + TTP regex
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_cwe_from_text():
    article = _make_article(title="CWE-79 cross-site scripting vulnerability found")
    entities = await extract_entities(article)
    keys = [e["normalized_key"] for e in entities]
    assert "cwe-79" in keys
    type_map = {e["normalized_key"]: e["type"] for e in entities}
    assert type_map["cwe-79"] == "cwe"


@pytest.mark.asyncio
async def test_extract_ttp_from_text():
    article = _make_article(title="Attacker uses T1059 command execution technique")
    entities = await extract_entities(article)
    keys = [e["normalized_key"] for e in entities]
    assert "t1059" in keys
    type_map = {e["normalized_key"]: e["type"] for e in entities}
    assert type_map["t1059"] == "ttp"


@pytest.mark.asyncio
async def test_ttp_regex_does_not_match_out_of_range():
    """T9999 is not a valid ATT&CK TTP — must not match."""
    article = _make_article(title="Model T9999 device was vulnerable")
    entities = await extract_entities(article)
    keys = [e["normalized_key"] for e in entities]
    assert "t9999" not in keys
