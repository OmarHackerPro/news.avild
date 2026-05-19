# Entity Intel Single Source of Truth — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `entity_intel` the single source of truth for all entity patterns; remove all hardcoded data dicts from `entity_extractor.py`; wire startup so patterns load from DB on every ingestion run.

**Architecture:** Phase 1 seeds missing vendors/products into `entity_intel`, adds product support to `_rebuild_patterns_from_db()`, and wires `refresh_entity_intel()` to `ingest_all_feeds()` startup. Phase 2 (separate commit, after log verification) deletes the now-redundant hardcoded dicts and `data/threat_keywords.json`.

**Tech Stack:** Python 3.12, SQLAlchemy async, Alembic, pytest-asyncio

---

## File Map

| File | Change |
|---|---|
| `tests/test_entity_extractor.py` | add tests for product pattern rebuild + fallback guard |
| `app/ingestion/entity_extractor.py` | add product rebuild + fallback guard to `_rebuild_patterns_from_db()` |
| `alembic/versions/d5e6f7a8b9c0_seed_curated_entities.py` | new — data migration seeds 13 vendors + 71 products |
| `app/ingestion/ingester.py` | call `refresh_entity_intel()` at top of `ingest_all_feeds()` |
| `tests/test_ingester.py` | add test that startup calls `refresh_entity_intel()` |
| `app/ingestion/entity_extractor.py` (phase 2) | delete all hardcoded dicts + JSON loader |
| `data/threat_keywords.json` (phase 2) | delete |

---

## Task 1: Tests for product rebuild and fallback guard

**Files:**
- Modify: `tests/test_entity_extractor.py`

- [ ] **Step 1: Add two tests at the bottom of `tests/test_entity_extractor.py`**

```python
# ---------------------------------------------------------------------------
# _rebuild_patterns_from_db — product type support
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rebuild_populates_product_patterns():
    """After refresh with product-type rows, _PRODUCT_PATTERNS must be non-empty."""
    import importlib
    import app.ingestion.entity_extractor as mod
    importlib.reload(mod)

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
```

- [ ] **Step 2: Run the tests to verify they fail**

```
docker compose exec ingestion pytest tests/test_entity_extractor.py::test_rebuild_populates_product_patterns tests/test_entity_extractor.py::test_rebuild_logs_warning_when_vendor_patterns_empty -v
```

Expected: both FAIL — `_PRODUCT_PATTERNS` never gets populated, no warning is logged.

---

## Task 2: Add product rebuild + fallback guard to `_rebuild_patterns_from_db()`

**Files:**
- Modify: `app/ingestion/entity_extractor.py`

- [ ] **Step 1: Replace `_rebuild_patterns_from_db()` in `entity_extractor.py`**

Find the existing function (starts at `def _rebuild_patterns_from_db() -> None:`) and replace the entire body:

```python
def _rebuild_patterns_from_db() -> None:
    """Rebuild _VENDOR_PATTERNS, _PRODUCT_PATTERNS, _THREAT_PATTERNS, _ALIAS_PATTERNS from _DB_ENTITY_MAP."""
    _VENDOR_PATTERNS.clear()
    _PRODUCT_PATTERNS.clear()
    _THREAT_PATTERNS.clear()
    _ALIAS_PATTERNS.clear()

    for key, (name, etype) in _DB_ENTITY_MAP.items():
        flags = 0 if len(name) <= 3 else re.IGNORECASE
        pattern = re.compile(r"\b" + re.escape(name) + r"\b", flags)
        if etype == "vendor":
            _VENDOR_PATTERNS.append((key, name, pattern))
        elif etype == "product":
            _PRODUCT_PATTERNS.append((key, name, pattern))
        elif etype in ("actor", "malware", "tool", "campaign", "vuln_alias"):
            _THREAT_PATTERNS.append((key, name, etype, pattern))

    for display_text, canonical_key in _DB_ALIAS_DISPLAY.items():
        _ALIAS_PATTERNS.append(
            (canonical_key, re.compile(r"\b" + re.escape(display_text) + r"\b", re.IGNORECASE))
        )

    if not _VENDOR_PATTERNS:
        logger.warning("_rebuild_patterns_from_db: no vendor patterns loaded — check entity_intel vendor rows")
    if not _PRODUCT_PATTERNS:
        logger.warning("_rebuild_patterns_from_db: no product patterns loaded — check entity_intel product rows")
    if not _THREAT_PATTERNS:
        logger.warning("_rebuild_patterns_from_db: no threat patterns loaded — check entity_intel actor/malware/tool rows")
```

- [ ] **Step 2: Run the new tests to verify they pass**

```
docker compose exec ingestion python -c "import app.ingestion.entity_extractor"  # smoke check import
docker compose exec ingestion pytest tests/test_entity_extractor.py::test_rebuild_populates_product_patterns tests/test_entity_extractor.py::test_rebuild_logs_warning_when_vendor_patterns_empty -v
```

Expected: both PASS.

- [ ] **Step 3: Run the full entity extractor test suite to check for regressions**

```
docker compose exec ingestion pytest tests/test_entity_extractor.py -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add app/ingestion/entity_extractor.py tests/test_entity_extractor.py
git commit -m "feat(entities): add product type support and fallback guard to _rebuild_patterns_from_db"
```

---

## Task 3: Alembic data migration — seed curated vendors and products

**Files:**
- Create: `alembic/versions/d5e6f7a8b9c0_seed_curated_entities.py`

- [ ] **Step 1: Create the migration file**

```python
"""Seed curated vendor and product entities into entity_intel

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-05-19
"""
from typing import Sequence, Union
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOW = datetime.now(timezone.utc)

_VENDORS = [
    ("amd", "AMD", ["AMD"]),
    ("aws", "AWS", ["Amazon Web Services", "AWS"]),
    ("cloudflare", "Cloudflare", ["Cloudflare"]),
    ("github", "GitHub", ["GitHub"]),
    ("hp", "HP", ["HP", "Hewlett-Packard"]),
    ("huawei", "Huawei", ["Huawei"]),
    ("lenovo", "Lenovo", ["Lenovo"]),
    ("nvidia", "NVIDIA", ["NVIDIA", "Nvidia"]),
    ("openai", "OpenAI", ["OpenAI"]),
    ("signal", "Signal", ["Signal"]),
    ("telegram", "Telegram", ["Telegram"]),
    ("whatsapp", "WhatsApp", ["WhatsApp"]),
    ("zoom", "Zoom", ["Zoom"]),
]

_PRODUCTS = [
    ("active-directory", "Active Directory", ["Active Directory", "AD"]),
    ("android", "Android", ["Android"]),
    ("ansible", "Ansible", ["Ansible"]),
    ("apache-http-server", "Apache HTTP Server", ["Apache HTTP Server", "Apache httpd"]),
    ("apparmor", "AppArmor", ["AppArmor"]),
    ("azure-ad", "Azure AD", ["Azure AD", "Azure Active Directory"]),
    ("bamboo", "Bamboo", ["Bamboo"]),
    ("bitbucket", "Bitbucket", ["Bitbucket"]),
    ("chrome", "Chrome", ["Google Chrome"]),
    ("chromium", "Chromium", ["Chromium"]),
    ("cisco-asa", "Cisco ASA", ["Cisco ASA"]),
    ("cisco-duo", "Cisco Duo", ["Cisco Duo", "Duo"]),
    ("citrix-adc", "Citrix ADC", ["Citrix ADC"]),
    ("citrix-workspace", "Citrix Workspace", ["Citrix Workspace"]),
    ("confluence", "Confluence", ["Confluence"]),
    ("cortex-xdr", "Cortex XDR", ["Cortex XDR"]),
    ("docker", "Docker", ["Docker"]),
    ("entra-id", "Entra ID", ["Entra ID", "Microsoft Entra ID"]),
    ("esxi", "ESXi", ["ESXi", "VMware ESXi"]),
    ("exchange", "Exchange", ["Microsoft Exchange", "Exchange Server"]),
    ("firepower", "Firepower", ["Firepower", "Cisco Firepower"]),
    ("fortiadc", "FortiADC", ["FortiADC"]),
    ("fortianalyzer", "FortiAnalyzer", ["FortiAnalyzer"]),
    ("forticlient", "FortiClient", ["FortiClient"]),
    ("fortigate", "FortiGate", ["FortiGate"]),
    ("fortimanager", "FortiManager", ["FortiManager"]),
    ("fortios", "FortiOS", ["FortiOS"]),
    ("fortiproxy", "FortiProxy", ["FortiProxy"]),
    ("fortisiem", "FortiSIEM", ["FortiSIEM"]),
    ("fortiswitch", "FortiSwitch", ["FortiSwitch"]),
    ("fortiweb", "FortiWeb", ["FortiWeb"]),
    ("globalprotect", "GlobalProtect", ["GlobalProtect"]),
    ("google-cloud", "Google Cloud", ["Google Cloud", "GCP"]),
    ("ios", "iOS", ["iOS"]),
    ("ios-xe", "IOS XE", ["IOS XE", "Cisco IOS XE"]),
    ("ios-xr", "IOS XR", ["IOS XR", "Cisco IOS XR"]),
    ("ipados", "iPadOS", ["iPadOS"]),
    ("ivanti-connect-secure", "Ivanti Connect Secure", ["Ivanti Connect Secure", "Pulse Connect Secure"]),
    ("ivanti-epmm", "Ivanti EPMM", ["Ivanti EPMM"]),
    ("jenkins", "Jenkins", ["Jenkins"]),
    ("jira", "Jira", ["Jira"]),
    ("juniper-srx", "Juniper SRX", ["Juniper SRX", "SRX Series"]),
    ("junos", "Junos", ["Junos", "Juniper Junos"]),
    ("kubernetes", "Kubernetes", ["Kubernetes", "K8s"]),
    ("macos", "macOS", ["macOS", "Mac OS X"]),
    ("meraki", "Meraki", ["Meraki", "Cisco Meraki"]),
    ("microsoft-365", "Microsoft 365", ["Microsoft 365", "M365"]),
    ("microsoft-defender", "Microsoft Defender", ["Microsoft Defender"]),
    ("microsoft-edge", "Microsoft Edge", ["Microsoft Edge"]),
    ("netscaler", "NetScaler", ["NetScaler", "Citrix NetScaler"]),
    ("nginx", "Nginx", ["Nginx", "NGINX"]),
    ("openssh", "OpenSSH", ["OpenSSH"]),
    ("openssl", "OpenSSL", ["OpenSSL"]),
    ("outlook", "Outlook", ["Outlook", "Microsoft Outlook"]),
    ("pan-os", "PAN-OS", ["PAN-OS"]),
    ("panorama", "Panorama", ["Panorama", "Palo Alto Panorama"]),
    ("pulse-connect-secure", "Pulse Connect Secure", ["Pulse Connect Secure"]),
    ("safari", "Safari", ["Safari"]),
    ("sharepoint", "SharePoint", ["SharePoint", "Microsoft SharePoint"]),
    ("sonicos", "SonicOS", ["SonicOS"]),
    ("terraform", "Terraform", ["Terraform"]),
    ("vcenter", "vCenter", ["vCenter", "VMware vCenter"]),
    ("vmware-workstation", "VMware Workstation", ["VMware Workstation"]),
    ("vsphere", "vSphere", ["vSphere", "VMware vSphere"]),
    ("watchos", "watchOS", ["watchOS"]),
    ("webex", "Webex", ["Webex", "Cisco Webex"]),
    ("webkit", "WebKit", ["WebKit"]),
    ("windows", "Windows", ["Microsoft Windows"]),
    ("windows-server", "Windows Server", ["Windows Server", "Microsoft Windows Server"]),
    ("wing-ftp", "Wing FTP", ["Wing FTP"]),
    ("xenserver", "XenServer", ["XenServer", "Citrix XenServer"]),
]


def upgrade() -> None:
    conn = op.get_bind()
    now = _NOW

    for normalized_key, display_name, aliases in _VENDORS:
        conn.execute(
            sa.text("""
                INSERT INTO entity_intel (normalized_key, display_name, entity_type, aliases, source, source_id, active, last_synced)
                VALUES (:key, :name, 'vendor', :aliases::jsonb, 'curated', NULL, true, :now)
                ON CONFLICT (normalized_key) DO NOTHING
            """),
            {"key": normalized_key, "name": display_name, "aliases": __import__("json").dumps(aliases), "now": now},
        )

    for normalized_key, display_name, aliases in _PRODUCTS:
        conn.execute(
            sa.text("""
                INSERT INTO entity_intel (normalized_key, display_name, entity_type, aliases, source, source_id, active, last_synced)
                VALUES (:key, :name, 'product', :aliases::jsonb, 'curated', NULL, true, :now)
                ON CONFLICT (normalized_key) DO NOTHING
            """),
            {"key": normalized_key, "name": display_name, "aliases": __import__("json").dumps(aliases), "now": now},
        )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM entity_intel WHERE source = 'curated'")
    )
```

- [ ] **Step 2: Run the migration**

```
docker compose exec ingestion alembic upgrade head
```

Expected output ends with: `Running upgrade c4d5e6f7a8b9 -> d5e6f7a8b9c0, Seed curated vendor and product entities into entity_intel`

- [ ] **Step 3: Verify row counts**

```
docker compose exec ingestion python -c "
import asyncio, sys
sys.path.insert(0, '/app')
from dotenv import load_dotenv; load_dotenv()
from app.db.session import AsyncSessionLocal
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as db:
        r = await db.execute(text('SELECT entity_type, count(*) FROM entity_intel GROUP BY entity_type ORDER BY count(*) DESC'))
        for row in r.fetchall():
            print(row[0], row[1])
asyncio.run(main())
"
```

Expected: `vendor` ≥ 272 (259 cisa_kev + 13 new), `product` ≥ 67, `malware` 725, `actor` 492.

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/d5e6f7a8b9c0_seed_curated_entities.py
git commit -m "feat(entities): seed curated vendors and products into entity_intel"
```

---

## Task 4: Wire `refresh_entity_intel()` to `ingest_all_feeds()` startup

**Files:**
- Modify: `app/ingestion/ingester.py`
- Modify: `tests/test_ingester.py`

- [ ] **Step 1: Add the test first**

Add to `tests/test_ingester.py`:

```python
@pytest.mark.asyncio
async def test_ingest_all_feeds_calls_refresh_entity_intel():
    """ingest_all_feeds() must call refresh_entity_intel() before processing sources."""
    from unittest.mock import AsyncMock, patch, MagicMock

    mock_refresh = AsyncMock(return_value=1625)
    mock_sources = []  # no sources — just checking startup call happens

    with patch("app.ingestion.ingester.refresh_entity_intel", mock_refresh), \
         patch("app.ingestion.ingester.AsyncSessionLocal") as mock_session_cls:

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=mock_sources)))))
        mock_session_cls.return_value = mock_session

        from app.ingestion.ingester import ingest_all_feeds
        await ingest_all_feeds()

    mock_refresh.assert_called_once()
```

- [ ] **Step 2: Run the test to confirm it fails**

```
docker compose exec ingestion pytest tests/test_ingester.py::test_ingest_all_feeds_calls_refresh_entity_intel -v
```

Expected: FAIL — `refresh_entity_intel` not called yet.

- [ ] **Step 3: Add the startup call to `ingest_all_feeds()` in `app/ingestion/ingester.py`**

Add this import at the top of the file with other ingestion imports:

```python
from app.ingestion.entity_extractor import refresh_entity_intel
```

Then at the top of `ingest_all_feeds()`, immediately after the `AsyncSessionLocal is None` guard and before the sources query:

```python
async def ingest_all_feeds(*, update: bool = False) -> None:
    if AsyncSessionLocal is None:
        logger.error("Database not configured (DATABASE_URL missing).")
        return

    async with AsyncSessionLocal() as db:
        count = await refresh_entity_intel(db)
        logger.info("Entity intel loaded: %d entries", count)

    async with AsyncSessionLocal() as session:
        sources = await get_active_sources(session)
    # ... rest unchanged
```

- [ ] **Step 4: Run the new test to confirm it passes**

```
docker compose exec ingestion pytest tests/test_ingester.py::test_ingest_all_feeds_calls_refresh_entity_intel -v
```

Expected: PASS.

- [ ] **Step 5: Run the full ingester test suite**

```
docker compose exec ingestion pytest tests/test_ingester.py -v
```

Expected: all PASS.

- [ ] **Step 6: Smoke-test the startup wiring live**

```
docker compose exec ingestion python -c "
import asyncio, sys
sys.path.insert(0, '/app')
from dotenv import load_dotenv; load_dotenv()
import logging
logging.basicConfig(level=logging.INFO)
from app.ingestion.entity_extractor import _DB_ENTITY_MAP, _VENDOR_PATTERNS, _PRODUCT_PATTERNS, _THREAT_PATTERNS
from app.ingestion.ingester import refresh_entity_intel
from app.db.session import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as db:
        count = await refresh_entity_intel(db)
    print(f'Loaded: {count} entities')
    print(f'Vendor patterns: {len(_VENDOR_PATTERNS)}')
    print(f'Product patterns: {len(_PRODUCT_PATTERNS)}')
    print(f'Threat patterns: {len(_THREAT_PATTERNS)}')
asyncio.run(main())
"
```

Expected:
```
Loaded: ~1800 entities
Vendor patterns: ~272
Product patterns: ~67
Threat patterns: ~1366
```

- [ ] **Step 7: Commit**

```bash
git add app/ingestion/ingester.py tests/test_ingester.py
git commit -m "feat(entities): wire refresh_entity_intel to ingest_all_feeds startup"
```

---

## Task 5: Phase 2 — delete hardcoded dicts and JSON file

> **Trigger:** Only proceed after confirming production/container logs show `"Entity intel loaded: N entries"` where N ≥ 1800 after at least one ingestion run.

**Files:**
- Modify: `app/ingestion/entity_extractor.py`
- Delete: `data/threat_keywords.json`

- [ ] **Step 1: Verify all JSON keys are in entity_intel (must pass before deleting)**

```
docker compose exec ingestion python -c "
import asyncio, sys, json
sys.path.insert(0, '/app')
from dotenv import load_dotenv; load_dotenv()
from app.db.session import AsyncSessionLocal
from sqlalchemy import text
from pathlib import Path

async def main():
    with open('/app/data/threat_keywords.json') as f:
        json_keys = set(json.load(f)['keywords'].keys())
    async with AsyncSessionLocal() as db:
        r = await db.execute(text('SELECT normalized_key FROM entity_intel'))
        db_keys = {row[0] for row in r.fetchall()}
    missing = json_keys - db_keys
    print(f'JSON keys missing from DB: {len(missing)}')
    if missing:
        print('ABORT — do not delete JSON file')
        for k in sorted(missing):
            print(f'  {k}')
    else:
        print('SAFE TO DELETE threat_keywords.json')
asyncio.run(main())
"
```

Expected: `JSON keys missing from DB: 0` and `SAFE TO DELETE threat_keywords.json`

- [ ] **Step 2: Delete the JSON file**

```bash
git rm data/threat_keywords.json
```

- [ ] **Step 3: Remove hardcoded dicts and JSON loader from `entity_extractor.py`**

Delete these sections entirely from `app/ingestion/entity_extractor.py`:

1. `VENDOR_KEYWORDS: dict[str, str] = { ... }` (lines ~23–66)
2. `PRODUCT_KEYWORDS: dict[str, str] = { ... }` (lines ~69–155)
3. `_BASELINE_KEYWORDS: dict[str, tuple[str, str]] = { ... }` (lines ~162–211)
4. `_BASELINE_ALIASES: dict[str, str] = {}` (line ~213)
5. `_DATA_FILE = Path(...)` (line ~219)
6. `def _load_threat_data() -> ...` (the entire function, lines ~222–245)
7. `THREAT_KEYWORDS, _THREAT_ALIASES = _load_threat_data()` (line ~249)

Also delete the module-level pattern-building loops that use these dicts:

```python
# DELETE these three blocks:
_VENDOR_PATTERNS: list[...] = []
for _key, _name in VENDOR_KEYWORDS.items():
    ...

_PRODUCT_PATTERNS: list[...] = []
for _key, _name in PRODUCT_KEYWORDS.items():
    ...

_THREAT_PATTERNS: list[...] = []
for _key, (_name, _etype) in THREAT_KEYWORDS.items():
    ...
```

Replace those three blocks with empty list declarations:

```python
_VENDOR_PATTERNS: list[tuple[str, str, re.Pattern]] = []
_PRODUCT_PATTERNS: list[tuple[str, str, re.Pattern]] = []
_THREAT_PATTERNS: list[tuple[str, str, str, re.Pattern]] = []
```

Also delete the `_ALIAS_PATTERNS` initialization loop that reads from `_THREAT_ALIASES`:

```python
# DELETE:
_ALIAS_PATTERNS: list[tuple[str, re.Pattern]] = [
    (canonical_key, re.compile(...))
    for display_text, canonical_key in _THREAT_ALIASES.items()
]
```

Replace with:

```python
_ALIAS_PATTERNS: list[tuple[str, re.Pattern]] = []
```

Also update the alias loop in `_extract_regex` — remove the fallback branch that references `THREAT_KEYWORDS` (now deleted):

```python
# BEFORE:
for canonical_key, pattern in _ALIAS_PATTERNS:
    if canonical_key not in seen and pattern.search(combined):
        if canonical_key in THREAT_KEYWORDS:
            name, etype = THREAT_KEYWORDS[canonical_key]
            seen[canonical_key] = {"type": etype, "name": name, "normalized_key": canonical_key}
        elif canonical_key in _DB_ENTITY_MAP:
            name, etype = _DB_ENTITY_MAP[canonical_key]
            seen[canonical_key] = {"type": etype, "name": name, "normalized_key": canonical_key}

# AFTER:
for canonical_key, pattern in _ALIAS_PATTERNS:
    if canonical_key not in seen and pattern.search(combined):
        if canonical_key in _DB_ENTITY_MAP:
            name, etype = _DB_ENTITY_MAP[canonical_key]
            seen[canonical_key] = {"type": etype, "name": name, "normalized_key": canonical_key}
```

- [ ] **Step 4: Remove now-broken tests that rely on the JSON file**

In `tests/test_entity_extractor.py`, delete or rewrite these tests that depend on `_DATA_FILE` or `_load_threat_data`:

- `test_file_loader_adds_new_entry` — delete (JSON loader gone)
- `test_alias_midnight_blizzard_maps_to_apt29` — rewrite to use `refresh_entity_intel` mock instead of patching `_DATA_FILE`
- `test_alias_does_not_produce_duplicate_keys` — rewrite similarly
- `test_missing_data_file_falls_back_to_baseline` — delete (no longer applicable)
- `patched_data_file` fixture — delete

Add a replacement test for alias resolution via DB:

```python
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
```

- [ ] **Step 5: Run the full test suite**

```
docker compose exec ingestion pytest tests/test_entity_extractor.py tests/test_ingester.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/entity_extractor.py tests/test_entity_extractor.py
git commit -m "feat(entities): remove hardcoded keyword dicts and threat_keywords.json"
```

---

## Verification Checklist (after Phase 1 deploy)

After running `docker compose up -d ingestion` and triggering one ingestion run:

- [ ] Log line `Entity intel loaded: N entries` present with N ≥ 1800
- [ ] Log line `_rebuild_patterns_from_db: no ... patterns` is NOT present
- [ ] OpenSearch entities index shows product-type entities in recent articles
- [ ] No new errors in ingestion logs related to entity extraction
