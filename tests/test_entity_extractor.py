"""Tests for entity_extractor — DB-backed loader and alias normalization."""
from collections import namedtuple
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.ingestion.entity_extractor import extract_entities

_EntityRow = namedtuple("_EntityRow", ["normalized_key", "display_name", "entity_type", "aliases"])


def _mock_db_with_rows(rows):
    """Return an AsyncMock db session that yields rows on execute."""
    mock_result = MagicMock()
    mock_result.fetchall.return_value = rows
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    return mock_db


def _make_article(title="", desc=""):
    return {"title": title, "desc": desc, "summary": None, "cve_ids": [], "content_html": None}


# ---------------------------------------------------------------------------
# Alias resolution via DB
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_alias_via_db_maps_to_canonical():
    """Alias loaded from entity_intel maps text mention to canonical key."""
    import importlib
    import app.ingestion.entity_extractor as mod
    importlib.reload(mod)

    rows = [_EntityRow("apt29", "APT29", "actor", ["Midnight Blizzard", "Cozy Bear"])]
    db = _mock_db_with_rows(rows)
    await mod.refresh_entity_intel(db)

    article = {"title": "Midnight Blizzard exfiltrates diplomatic cables", "desc": "", "summary": None, "cve_ids": [], "content_html": None}
    entities = await mod.extract_entities(article)
    keys = {e["normalized_key"] for e in entities}
    assert "apt29" in keys
    assert "midnight-blizzard" not in keys


# ---------------------------------------------------------------------------
# LLM-first path tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_entities_ner_results_take_precedence():
    """When slug is provided, NER entities come first; regex adds new keys only."""
    import importlib
    import app.ingestion.entity_extractor as mod
    importlib.reload(mod)

    # Prime DB with lockbit so regex can find it
    db_rows = [_EntityRow("lockbit", "LockBit", "malware", [])]
    db = _mock_db_with_rows(db_rows)
    await mod.refresh_entity_intel(db)

    from unittest.mock import AsyncMock, patch

    ner_result = [
        {"type": "vuln_alias", "name": "CitrixBleed", "normalized_key": "citrixbleed"},
    ]
    article = _make_article(title="CitrixBleed CVE-2023-4966 — LockBit exploiting NetScaler")

    with patch("app.ingestion.ner_client.extract_entities_local", new_callable=AsyncMock, return_value=ner_result):
        entities = await mod.extract_entities(article, slug="test-slug")

    keys = [e["normalized_key"] for e in entities]
    # NER entity present
    assert "citrixbleed" in keys
    # CVE extracted by regex
    assert "cve-2023-4966" in keys
    # Regex-extracted entity (lockbit) also present as supplement
    assert "lockbit" in keys
    # No duplicates
    assert len(keys) == len(set(keys))


@pytest.mark.asyncio
async def test_extract_entities_falls_back_to_regex_when_ner_returns_empty():
    """When NER returns no entities, regex results are used."""
    import importlib
    import app.ingestion.entity_extractor as mod
    importlib.reload(mod)

    db_rows = [_EntityRow("lockbit", "LockBit", "malware", [])]
    db = _mock_db_with_rows(db_rows)
    await mod.refresh_entity_intel(db)

    from unittest.mock import AsyncMock, patch

    article = _make_article(title="LockBit ransomware hits hospital")

    with patch("app.ingestion.ner_client.extract_entities_local", new_callable=AsyncMock, return_value=[]):
        entities = await mod.extract_entities(article, slug="test-slug")

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


# ---------------------------------------------------------------------------
# _rebuild_patterns_from_db — product type support
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rebuild_populates_product_patterns():
    """After refresh with product-type rows, _PRODUCT_PATTERNS must be non-empty."""
    import importlib
    import app.ingestion.entity_extractor as mod
    importlib.reload(mod)
    mod._PRODUCT_PATTERNS.clear()  # clear hardcoded init — test only DB-driven rebuild

    rows = [
        _EntityRow("esxi", "ESXi", "product", []),
        _EntityRow("confluence", "Confluence", "product", []),
        _EntityRow("apt29", "APT29", "actor", []),
    ]
    db = _mock_db_with_rows(rows)
    await mod.refresh_entity_intel(db)

    product_keys = {k for k, _, _ in mod._PRODUCT_PATTERNS}
    assert "esxi" in product_keys
    assert "confluence" in product_keys


@pytest.mark.asyncio
async def test_rebuild_logs_warning_when_vendor_patterns_empty(caplog):
    """If DB has no vendor rows, a WARNING is logged after rebuild."""
    import importlib
    import logging
    import app.ingestion.entity_extractor as mod
    importlib.reload(mod)

    rows = [_EntityRow("apt29", "APT29", "actor", [])]
    db = _mock_db_with_rows(rows)
    with caplog.at_level(logging.WARNING, logger="app.ingestion.entity_extractor"):
        await mod.refresh_entity_intel(db)

    assert any("vendor" in r.message.lower() for r in caplog.records)
