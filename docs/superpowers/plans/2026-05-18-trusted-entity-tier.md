# Trusted Entity Tier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all hardcoded entity lists with DB-backed tables synced from MITRE ATT&CK, CISA KEV, and ransomware.live — and wire the entity extractor to load from DB at startup and resolve NER output against canonical aliases.

**Architecture:** Two new PostgreSQL tables (`entity_intel`, `cisa_kev`) hold the ground-truth entity registry. Three sync scripts populate them from external sources. `entity_extractor.py` gains a startup loader that rebuilds regex patterns from DB, an alias resolution step for NER output (Stage 4), and a KEV enrichment path that emits deterministic vendor+product entities when a CVE is found. All DB access uses `text()` SQL — no ORM models.

**Tech Stack:** Python 3.12, SQLAlchemy async + asyncpg, Alembic, `requests` (sync HTTP for scripts), `pytest-asyncio`, `unittest.mock`

**Spec:** `docs/superpowers/specs/2026-05-18-trusted-entity-tier-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `alembic/versions/<hash>_add_entity_intel_tables.py` | Create | Migration: entity_intel + cisa_kev tables |
| `scripts/sync_mitre_attack.py` | Modify | Add DB upsert alongside existing JSON write |
| `scripts/sync_cisa_kev.py` | Create | Fetch KEV JSON, populate cisa_kev + vendor rows in entity_intel |
| `scripts/sync_ransomware.py` | Create | Fetch ransomware.live groups, populate entity_intel |
| `app/ingestion/entity_extractor.py` | Modify | refresh_entity_intel(), alias resolution, KEV enrichment, CWE/TTP regex |
| `app/main.py` | Modify | Call refresh_entity_intel() at startup |
| `.env.example` | Modify | Add RANSOMWARE_LIVE_API_KEY |
| `tests/test_entity_extractor.py` | Modify | Tests for new extractor behaviour |
| `tests/test_sync_cisa_kev.py` | Create | Tests for vendor normalization and KEV parsing |
| `tests/test_sync_ransomware.py` | Create | Tests for ransomware sync parsing |

---

## Task 1: Alembic migration — entity_intel + cisa_kev

**Files:**
- Create: `alembic/versions/<hash>_add_entity_intel_tables.py`

- [ ] **Step 1: Generate blank migration**

```bash
docker compose exec ingestion alembic revision -m "add_entity_intel_tables"
```

Note the generated filename (e.g. `a1b2c3d4e5f6_add_entity_intel_tables.py`).

- [ ] **Step 2: Write migration body**

Open the generated file and replace the `upgrade()` / `downgrade()` bodies:

```python
def upgrade() -> None:
    op.create_table(
        "entity_intel",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("normalized_key", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("aliases", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "last_synced",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_key"),
    )
    op.create_index("entity_intel_type_idx", "entity_intel", ["entity_type"])
    op.create_index("entity_intel_source_idx", "entity_intel", ["source"])

    op.create_table(
        "cisa_kev",
        sa.Column("cve_id", sa.String(), nullable=False),
        sa.Column("vendor", sa.String(), nullable=False),
        sa.Column("product", sa.String(), nullable=False),
        sa.Column("vulnerability_name", sa.String(), nullable=False),
        sa.Column("date_added", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("known_ransomware_use", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("cwes", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "last_synced",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("cve_id"),
    )
    op.create_index("cisa_kev_vendor_idx", "cisa_kev", ["vendor"])


def downgrade() -> None:
    op.drop_table("cisa_kev")
    op.drop_index("entity_intel_source_idx", "entity_intel")
    op.drop_index("entity_intel_type_idx", "entity_intel")
    op.drop_table("entity_intel")
```

Add the missing imports at the top of the file:

```python
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
```

- [ ] **Step 3: Run migration**

```bash
docker compose exec ingestion alembic upgrade head
```

Expected output ends with: `Running upgrade <prev> -> <hash>, add_entity_intel_tables`

- [ ] **Step 4: Verify tables exist**

```bash
docker compose exec ingestion python -c "
import asyncio
from app.db.session import AsyncSessionLocal
from sqlalchemy import text

async def check():
    async with AsyncSessionLocal() as db:
        r = await db.execute(text(\"SELECT COUNT(*) FROM entity_intel\"))
        print('entity_intel rows:', r.scalar())
        r = await db.execute(text(\"SELECT COUNT(*) FROM cisa_kev\"))
        print('cisa_kev rows:', r.scalar())

asyncio.run(check())
"
```

Expected: `entity_intel rows: 0` / `cisa_kev rows: 0`

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/
git commit -m "feat(db): add entity_intel and cisa_kev tables"
```

---

## Task 2: Upgrade sync_mitre_attack.py to write to DB

The existing script downloads ATT&CK and writes `data/threat_keywords.json`. We add DB upsert as a second target. The JSON write is preserved for backward compatibility.

**Files:**
- Modify: `scripts/sync_mitre_attack.py`

- [ ] **Step 1: Run existing tests to confirm baseline**

```bash
docker compose exec ingestion python -m pytest tests/test_fetch_mitre_attack.py -v
```

Expected: all pass. These tests cover `_parse_stix_bundle` which we are NOT changing.

- [ ] **Step 2: Add upsert function and async main**

Add to `scripts/sync_mitre_attack.py` after the existing `_parse_stix_bundle` function:

```python
import asyncio
from datetime import datetime, timezone

# Source priority — lower number = higher priority.
# A higher-priority source already owns a row → skip the update.
_SOURCE_PRIORITY = {"attack": 0, "ransomware.live": 1, "cisa_kev": 2, "manual": 3}


async def _upsert_to_db(keywords: dict, aliases: dict) -> tuple[int, int]:
    """Upsert parsed ATT&CK data into entity_intel. Returns (inserted, updated)."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    from app.db.session import AsyncSessionLocal
    from sqlalchemy import text

    inserted = updated = 0
    now = datetime.now(timezone.utc)

    # Build key → [alias_display_texts] mapping
    alias_map: dict[str, list[str]] = {key: [] for key in keywords}
    for display_text, canonical_key in aliases.items():
        if canonical_key in alias_map:
            alias_map[canonical_key].append(display_text)

    async with AsyncSessionLocal() as db:
        for key, (display_name, entity_type) in keywords.items():
            alias_list = alias_map.get(key, [])
            # Check if row exists
            row = await db.execute(
                text("SELECT source FROM entity_intel WHERE normalized_key = :key"),
                {"key": key},
            )
            existing = row.fetchone()

            if existing is None:
                await db.execute(
                    text("""
                        INSERT INTO entity_intel
                            (normalized_key, display_name, entity_type, aliases, source, active, last_synced)
                        VALUES
                            (:key, :name, :etype, :aliases::jsonb, 'attack', true, :now)
                    """),
                    {"key": key, "name": display_name, "etype": entity_type,
                     "aliases": __import__("json").dumps(alias_list), "now": now},
                )
                inserted += 1
            else:
                existing_source = existing[0]
                # Only update if we have equal or higher priority
                if _SOURCE_PRIORITY.get(existing_source, 99) >= _SOURCE_PRIORITY["attack"]:
                    await db.execute(
                        text("""
                            UPDATE entity_intel
                            SET display_name = :name,
                                entity_type  = :etype,
                                aliases      = :aliases::jsonb,
                                source       = 'attack',
                                last_synced  = :now
                            WHERE normalized_key = :key
                        """),
                        {"key": key, "name": display_name, "etype": entity_type,
                         "aliases": __import__("json").dumps(alias_list), "now": now},
                    )
                    updated += 1
        await db.commit()

    return inserted, updated
```

- [ ] **Step 3: Add `--db` flag and call from main()**

In `main()`, after `keywords, aliases = _parse_stix_bundle(stix)`, add:

```python
    if not args.dry_run and args.db:
        ins, upd = asyncio.run(_upsert_to_db(keywords, aliases))
        logger.info("DB upsert: %d inserted, %d updated", ins, upd)
```

Add the argument parser flag (after existing `--output`):

```python
    parser.add_argument(
        "--db", action="store_true",
        help="Upsert into entity_intel PostgreSQL table (in addition to JSON output)",
    )
```

- [ ] **Step 4: Add a test for upsert logic**

Add to `tests/test_fetch_mitre_attack.py`:

```python
# --- _upsert_to_db priority logic (unit test, no real DB) ---

import json as _json
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.mark.asyncio
async def test_upsert_skips_existing_higher_priority_row():
    """An attack-source row is not downgraded by a later attack run with same key."""
    from scripts.sync_mitre_attack import _upsert_to_db

    # Simulate existing row with source='attack'
    existing_row = MagicMock()
    existing_row.__getitem__ = lambda self, i: "attack"  # existing_source

    mock_result_existing = MagicMock()
    mock_result_existing.fetchone.return_value = existing_row

    mock_execute = AsyncMock(return_value=mock_result_existing)
    mock_db = AsyncMock()
    mock_db.execute = mock_execute
    mock_db.commit = AsyncMock()

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.db.session.AsyncSessionLocal", return_value=mock_session_ctx):
        ins, upd = await _upsert_to_db({"lockbit": ["LockBit", "malware"]}, {})

    # Should have updated (attack → attack is same priority = allowed)
    assert ins == 0
    assert upd == 1


@pytest.mark.asyncio
async def test_upsert_inserts_new_row():
    """A key not yet in DB gets inserted."""
    from scripts.sync_mitre_attack import _upsert_to_db

    mock_result_none = MagicMock()
    mock_result_none.fetchone.return_value = None  # row does not exist

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result_none)
    mock_db.commit = AsyncMock()

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.db.session.AsyncSessionLocal", return_value=mock_session_ctx):
        ins, upd = await _upsert_to_db({"apt29": ["APT29", "actor"]}, {"Cozy Bear": "apt29"})

    assert ins == 1
    assert upd == 0
```

- [ ] **Step 5: Run all ATT&CK tests**

```bash
docker compose exec ingestion python -m pytest tests/test_fetch_mitre_attack.py -v
```

Expected: all pass.

- [ ] **Step 6: Run the sync against the live DB**

```bash
docker compose exec ingestion python scripts/sync_mitre_attack.py --db
```

Expected output includes: `DB upsert: N inserted, 0 updated`

- [ ] **Step 7: Verify DB populated**

```bash
docker compose exec ingestion python -c "
import asyncio
from app.db.session import AsyncSessionLocal
from sqlalchemy import text

async def check():
    async with AsyncSessionLocal() as db:
        r = await db.execute(text(\"SELECT entity_type, COUNT(*) FROM entity_intel GROUP BY entity_type ORDER BY entity_type\"))
        for row in r.fetchall():
            print(f'  {row[0]}: {row[1]}')

asyncio.run(check())
"
```

Expected: actor, malware, tool, campaign rows.

- [ ] **Step 8: Commit**

```bash
git add scripts/sync_mitre_attack.py tests/test_fetch_mitre_attack.py
git commit -m "feat(sync): add DB upsert to sync_mitre_attack"
```

---

## Task 3: scripts/sync_cisa_kev.py

**Files:**
- Create: `scripts/sync_cisa_kev.py`
- Create: `tests/test_sync_cisa_kev.py`

- [ ] **Step 1: Write the failing tests first**

Create `tests/test_sync_cisa_kev.py`:

```python
"""Tests for scripts/sync_cisa_kev.py — vendor normalization and KEV parsing."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.sync_cisa_kev import _normalize_vendor, _parse_kev


def test_normalize_vendor_strips_corp_suffix():
    assert _normalize_vendor("Microsoft Corp.") == "Microsoft"


def test_normalize_vendor_strips_llc():
    assert _normalize_vendor("Google LLC") == "Google"


def test_normalize_vendor_strips_inc():
    assert _normalize_vendor("Apple Inc.") == "Apple"


def test_normalize_vendor_strips_parenthetical():
    assert _normalize_vendor("Ivanti (formerly Pulse Secure)") == "Ivanti"


def test_normalize_vendor_passes_clean_name():
    assert _normalize_vendor("Fortinet") == "Fortinet"


def test_normalize_vendor_strips_whitespace():
    assert _normalize_vendor("  Cisco  ") == "Cisco"


def test_parse_kev_returns_cve_rows():
    raw = {
        "vulnerabilities": [
            {
                "cveID": "CVE-2024-1234",
                "vendorProject": "Microsoft Corp.",
                "product": "Windows",
                "vulnerabilityName": "Windows RCE",
                "dateAdded": "2024-01-15",
                "dueDate": "2024-02-05",
                "knownRansomwareCampaignUse": "Known",
                "cwes": ["CWE-79"],
            }
        ]
    }
    rows = _parse_kev(raw)
    assert len(rows) == 1
    row = rows[0]
    assert row["cve_id"] == "CVE-2024-1234"
    assert row["vendor"] == "Microsoft"          # normalized
    assert row["product"] == "Windows"
    assert row["known_ransomware_use"] is True
    assert row["cwes"] == ["CWE-79"]


def test_parse_kev_unknown_ransomware_is_false():
    raw = {
        "vulnerabilities": [
            {
                "cveID": "CVE-2024-9999",
                "vendorProject": "Cisco",
                "product": "IOS XE",
                "vulnerabilityName": "Test",
                "dateAdded": "2024-03-01",
                "dueDate": None,
                "knownRansomwareCampaignUse": "Unknown",
                "cwes": [],
            }
        ]
    }
    rows = _parse_kev(raw)
    assert rows[0]["known_ransomware_use"] is False


def test_parse_kev_deduplicates_vendors():
    """Two KEV entries from same vendor (after normalization) yield one vendor key."""
    raw = {
        "vulnerabilities": [
            {
                "cveID": "CVE-2024-0001",
                "vendorProject": "Microsoft Corp.",
                "product": "Windows",
                "vulnerabilityName": "A",
                "dateAdded": "2024-01-01",
                "dueDate": None,
                "knownRansomwareCampaignUse": "Unknown",
                "cwes": [],
            },
            {
                "cveID": "CVE-2024-0002",
                "vendorProject": "Microsoft",
                "product": "Exchange",
                "vulnerabilityName": "B",
                "dateAdded": "2024-01-02",
                "dueDate": None,
                "knownRansomwareCampaignUse": "Unknown",
                "cwes": [],
            },
        ]
    }
    rows = _parse_kev(raw)
    vendor_names = {r["vendor"] for r in rows}
    assert vendor_names == {"Microsoft"}  # deduped at parse level (check unique_vendors)
    # Both CVE rows exist
    assert len(rows) == 2
```

- [ ] **Step 2: Run to confirm they fail**

```bash
docker compose exec ingestion python -m pytest tests/test_sync_cisa_kev.py -v
```

Expected: `ModuleNotFoundError: No module named 'scripts.sync_cisa_kev'`

- [ ] **Step 3: Create scripts/sync_cisa_kev.py**

```python
#!/usr/bin/env python
"""Sync CISA Known Exploited Vulnerabilities into cisa_kev + entity_intel tables.

Usage:
    python scripts/sync_cisa_kev.py
    python scripts/sync_cisa_kev.py --dry-run
"""
import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

KEV_URL = (
    "https://raw.githubusercontent.com/cisagov/kev-data/develop/"
    "known_exploited_vulnerabilities.json"
)

_LEGAL_SUFFIX_RE = re.compile(
    r"\s+(corp\.?|inc\.?|ltd\.?|llc\.?|gmbh|co\.|limited|incorporated)\s*$",
    re.IGNORECASE,
)
_PARENTHETICAL_RE = re.compile(r"\s*\(.*?\)\s*$")


def _normalize_vendor(raw: str) -> str:
    """Strip legal suffixes and parentheticals from a KEV vendorProject string."""
    name = raw.strip()
    name = _PARENTHETICAL_RE.sub("", name)
    name = _LEGAL_SUFFIX_RE.sub("", name)
    return name.strip()


def _parse_kev(data: dict) -> list[dict]:
    """Parse KEV JSON into a list of row dicts ready for DB insert."""
    rows = []
    for v in data.get("vulnerabilities", []):
        vendor = _normalize_vendor(v.get("vendorProject", ""))
        if not vendor:
            continue
        rows.append({
            "cve_id": v["cveID"],
            "vendor": vendor,
            "product": v.get("product", ""),
            "vulnerability_name": v.get("vulnerabilityName", ""),
            "date_added": v.get("dateAdded"),
            "due_date": v.get("dueDate") or None,
            "known_ransomware_use": v.get("knownRansomwareCampaignUse", "Unknown") == "Known",
            "cwes": v.get("cwes") or [],
        })
    return rows


async def _sync_to_db(rows: list[dict]) -> tuple[int, int, int]:
    """Upsert KEV rows and vendor entries. Returns (kev_inserted, kev_updated, vendors_upserted)."""
    from app.db.session import AsyncSessionLocal
    from sqlalchemy import text

    now = datetime.now(timezone.utc)
    kev_ins = kev_upd = vendor_ups = 0

    # Collect unique normalized vendors
    unique_vendors: dict[str, str] = {}  # normalized_key → display_name
    for row in rows:
        import re as _re
        key = _re.sub(r"[^a-z0-9]+", "-", row["vendor"].lower()).strip("-")
        if key and key not in unique_vendors:
            unique_vendors[key] = row["vendor"]

    async with AsyncSessionLocal() as db:
        # Upsert cisa_kev rows
        for row in rows:
            existing = (await db.execute(
                text("SELECT 1 FROM cisa_kev WHERE cve_id = :cve_id"),
                {"cve_id": row["cve_id"]},
            )).fetchone()

            if existing is None:
                await db.execute(
                    text("""
                        INSERT INTO cisa_kev
                            (cve_id, vendor, product, vulnerability_name,
                             date_added, due_date, known_ransomware_use, cwes, last_synced)
                        VALUES
                            (:cve_id, :vendor, :product, :vulnerability_name,
                             :date_added, :due_date, :known_ransomware_use, :cwes::jsonb, :now)
                    """),
                    {**row, "cwes": json.dumps(row["cwes"]), "now": now},
                )
                kev_ins += 1
            else:
                await db.execute(
                    text("""
                        UPDATE cisa_kev SET
                            vendor = :vendor, product = :product,
                            vulnerability_name = :vulnerability_name,
                            known_ransomware_use = :known_ransomware_use,
                            cwes = :cwes::jsonb, last_synced = :now
                        WHERE cve_id = :cve_id
                    """),
                    {**row, "cwes": json.dumps(row["cwes"]), "now": now},
                )
                kev_upd += 1

        # Upsert vendor entries in entity_intel
        for norm_key, display_name in unique_vendors.items():
            existing = (await db.execute(
                text("SELECT source FROM entity_intel WHERE normalized_key = :key"),
                {"key": norm_key},
            )).fetchone()

            if existing is None:
                await db.execute(
                    text("""
                        INSERT INTO entity_intel
                            (normalized_key, display_name, entity_type, aliases,
                             source, active, last_synced)
                        VALUES
                            (:key, :name, 'vendor', :aliases::jsonb,
                             'cisa_kev', true, :now)
                    """),
                    {"key": norm_key, "name": display_name,
                     "aliases": json.dumps([display_name]), "now": now},
                )
                vendor_ups += 1
            else:
                # cisa_kev is lowest priority — only update last_synced
                await db.execute(
                    text("UPDATE entity_intel SET last_synced = :now WHERE normalized_key = :key"),
                    {"key": norm_key, "now": now},
                )

        await db.commit()

    return kev_ins, kev_upd, vendor_ups


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Sync CISA KEV to DB")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--url", default=KEV_URL)
    args = parser.parse_args(argv)

    logger.info("Fetching KEV from %s ...", args.url)
    resp = requests.get(args.url, timeout=60)
    resp.raise_for_status()
    rows = _parse_kev(resp.json())
    logger.info("Parsed %d KEV entries", len(rows))

    if args.dry_run:
        vendors = {r["vendor"] for r in rows}
        logger.info("--dry-run: %d unique vendors, %d CVEs", len(vendors), len(rows))
        return

    kev_ins, kev_upd, vendor_ups = asyncio.run(_sync_to_db(rows))
    logger.info("cisa_kev: %d inserted, %d updated", kev_ins, kev_upd)
    logger.info("entity_intel vendors: %d upserted", vendor_ups)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

```bash
docker compose exec ingestion python -m pytest tests/test_sync_cisa_kev.py -v
```

Expected: all pass.

- [ ] **Step 5: Run sync against live DB**

```bash
docker compose exec ingestion python scripts/sync_cisa_kev.py
```

Expected: `cisa_kev: N inserted, 0 updated` / `entity_intel vendors: N upserted`

- [ ] **Step 6: Verify**

```bash
docker compose exec ingestion python -c "
import asyncio
from app.db.session import AsyncSessionLocal
from sqlalchemy import text

async def check():
    async with AsyncSessionLocal() as db:
        r = await db.execute(text('SELECT COUNT(*) FROM cisa_kev'))
        print('KEV rows:', r.scalar())
        r = await db.execute(text(\"SELECT COUNT(*) FROM entity_intel WHERE entity_type = 'vendor'\"))
        print('Vendor entities:', r.scalar())

asyncio.run(check())
"
```

Expected: KEV rows ~900+, Vendor entities ~200-300.

- [ ] **Step 7: Commit**

```bash
git add scripts/sync_cisa_kev.py tests/test_sync_cisa_kev.py
git commit -m "feat(sync): add sync_cisa_kev script"
```

---

## Task 4: scripts/sync_ransomware.py

**Files:**
- Create: `scripts/sync_ransomware.py`
- Create: `tests/test_sync_ransomware.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_sync_ransomware.py`:

```python
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
    """Two entries normalizing to the same key — last one wins."""
    raw = [
        {"name": "BlackCat", "aliases": [], "status": "active"},
        {"name": "blackcat", "aliases": [], "status": "inactive"},
    ]
    rows = _parse_groups(raw)
    keys = [r["normalized_key"] for r in rows]
    assert keys.count("blackcat") == 1
```

- [ ] **Step 2: Run to confirm they fail**

```bash
docker compose exec ingestion python -m pytest tests/test_sync_ransomware.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create scripts/sync_ransomware.py**

```python
#!/usr/bin/env python
"""Sync ransomware groups from ransomware.live into entity_intel.

Usage:
    python scripts/sync_ransomware.py
    python scripts/sync_ransomware.py --dry-run

Requires RANSOMWARE_LIVE_API_KEY env var (free Pro key from ransomware.live).
"""
import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

API_URL = "https://api.ransomware.live/v2/groups"

_SOURCE = "ransomware.live"
_SOURCE_PRIORITY = {"attack": 0, "ransomware.live": 1, "cisa_kev": 2, "manual": 3}


def _normalize_key(name: str) -> str:
    key = name.lower()
    key = re.sub(r"[^a-z0-9]+", "-", key)
    return key.strip("-")


def _parse_groups(raw: list[dict]) -> list[dict]:
    """Parse ransomware.live group list into entity_intel row dicts."""
    seen: dict[str, dict] = {}
    for g in raw:
        name = (g.get("name") or "").strip()
        if not name:
            continue
        key = _normalize_key(name)
        aliases = [a.strip() for a in (g.get("aliases") or []) if a.strip() and a.strip() != name]
        seen[key] = {
            "normalized_key": key,
            "display_name": name,
            "entity_type": "actor",
            "aliases": aliases,
            "source": _SOURCE,
            "active": (g.get("status") or "").lower() == "active",
        }
    return list(seen.values())


async def _sync_to_db(rows: list[dict]) -> tuple[int, int]:
    """Upsert rows into entity_intel. Returns (inserted, updated)."""
    from app.db.session import AsyncSessionLocal
    from sqlalchemy import text

    now = datetime.now(timezone.utc)
    ins = upd = 0

    async with AsyncSessionLocal() as db:
        for row in rows:
            existing = (await db.execute(
                text("SELECT source FROM entity_intel WHERE normalized_key = :key"),
                {"key": row["normalized_key"]},
            )).fetchone()

            if existing is None:
                await db.execute(
                    text("""
                        INSERT INTO entity_intel
                            (normalized_key, display_name, entity_type, aliases,
                             source, active, last_synced)
                        VALUES
                            (:key, :name, :etype, :aliases::jsonb,
                             :source, :active, :now)
                    """),
                    {
                        "key": row["normalized_key"],
                        "name": row["display_name"],
                        "etype": row["entity_type"],
                        "aliases": json.dumps(row["aliases"]),
                        "source": row["source"],
                        "active": row["active"],
                        "now": now,
                    },
                )
                ins += 1
            else:
                existing_source = existing[0]
                if _SOURCE_PRIORITY.get(existing_source, 99) >= _SOURCE_PRIORITY[_SOURCE]:
                    await db.execute(
                        text("""
                            UPDATE entity_intel SET
                                active = :active,
                                last_synced = :now
                            WHERE normalized_key = :key
                        """),
                        {"key": row["normalized_key"], "active": row["active"], "now": now},
                    )
                    upd += 1
        await db.commit()

    return ins, upd


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Sync ransomware.live groups to DB")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    api_key = os.environ.get("RANSOMWARE_LIVE_API_KEY", "")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    if not api_key:
        logger.warning("RANSOMWARE_LIVE_API_KEY not set — using unauthenticated (rate-limited)")

    logger.info("Fetching groups from ransomware.live ...")
    resp = requests.get(API_URL, headers=headers, timeout=30)
    resp.raise_for_status()
    rows = _parse_groups(resp.json())
    logger.info("Parsed %d groups", len(rows))

    if args.dry_run:
        active = sum(1 for r in rows if r["active"])
        logger.info("--dry-run: %d active, %d inactive", active, len(rows) - active)
        return

    ins, upd = asyncio.run(_sync_to_db(rows))
    logger.info("entity_intel: %d inserted, %d updated", ins, upd)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

```bash
docker compose exec ingestion python -m pytest tests/test_sync_ransomware.py -v
```

Expected: all pass.

- [ ] **Step 5: Add RANSOMWARE_LIVE_API_KEY to .env.example**

Open `.env.example` and add:

```
RANSOMWARE_LIVE_API_KEY=   # Free Pro key from https://ransomware.live — 500K calls/month
```

- [ ] **Step 6: Run sync (unauthenticated is fine for initial run)**

```bash
docker compose exec ingestion python scripts/sync_ransomware.py
```

Expected: `entity_intel: N inserted, 0 updated`

- [ ] **Step 7: Commit**

```bash
git add scripts/sync_ransomware.py tests/test_sync_ransomware.py .env.example
git commit -m "feat(sync): add sync_ransomware script"
```

---

## Task 5: entity_extractor.py — startup loader

This task adds `refresh_entity_intel()` which loads entity_intel rows from DB into module-level dicts, then rebuilds the regex pattern lists.

**Files:**
- Modify: `app/ingestion/entity_extractor.py`
- Modify: `app/main.py`
- Modify: `tests/test_entity_extractor.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_entity_extractor.py`:

```python
# ---------------------------------------------------------------------------
# refresh_entity_intel — DB-backed startup loader
# ---------------------------------------------------------------------------

from collections import namedtuple
from unittest.mock import AsyncMock, MagicMock

_EntityRow = namedtuple("_EntityRow", ["normalized_key", "display_name", "entity_type", "aliases"])


def _mock_db(rows):
    """Return an AsyncMock db session that yields the given rows on execute."""
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
    db = _mock_db(rows)
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
    db = _mock_db(rows)
    await mod.refresh_entity_intel(db)

    # Aliases are normalized and indexed
    assert mod._DB_ALIAS_INDEX.get("cozy-bear") == "apt29"
    assert mod._DB_ALIAS_INDEX.get("midnight-blizzard") == "apt29"


@pytest.mark.asyncio
async def test_refresh_entity_intel_returns_zero_on_empty_db():
    import importlib
    import app.ingestion.entity_extractor as mod
    importlib.reload(mod)

    db = _mock_db([])
    count = await mod.refresh_entity_intel(db)
    assert count == 0
```

- [ ] **Step 2: Run to confirm they fail**

```bash
docker compose exec ingestion python -m pytest tests/test_entity_extractor.py::test_refresh_entity_intel_loads_vendor -v
```

Expected: `AttributeError: module 'app.ingestion.entity_extractor' has no attribute 'refresh_entity_intel'`

- [ ] **Step 3: Add module-level dicts and refresh function to entity_extractor.py**

Find the section after `_ALIAS_PATTERNS` is built (around line 270) and add:

```python
# ---------------------------------------------------------------------------
# DB-backed entity registry (populated at startup via refresh_entity_intel)
# ---------------------------------------------------------------------------

# normalized_key → (display_name, entity_type)
_DB_ENTITY_MAP: dict[str, tuple[str, str]] = {}
# raw alias display text → canonical normalized_key  (used to rebuild _ALIAS_PATTERNS)
_DB_ALIAS_DISPLAY: dict[str, str] = {}
# normalized alias key → canonical normalized_key  (used by _resolve_aliases for NER output)
_DB_ALIAS_INDEX: dict[str, str] = {}


def _rebuild_patterns_from_db() -> None:
    """Rebuild _VENDOR_PATTERNS, _THREAT_PATTERNS, _ALIAS_PATTERNS from _DB_ENTITY_MAP."""
    _VENDOR_PATTERNS.clear()
    for key, (name, etype) in _DB_ENTITY_MAP.items():
        if etype == "vendor":
            flags = 0 if len(name) <= 3 else re.IGNORECASE
            _VENDOR_PATTERNS.append(
                (key, name, re.compile(r"\b" + re.escape(name) + r"\b", flags))
            )

    _THREAT_PATTERNS.clear()
    for key, (name, etype) in _DB_ENTITY_MAP.items():
        if etype in ("actor", "malware", "tool", "campaign", "vuln_alias"):
            flags = 0 if len(name) <= 3 else re.IGNORECASE
            _THREAT_PATTERNS.append(
                (key, name, etype, re.compile(r"\b" + re.escape(name) + r"\b", flags))
            )

    _ALIAS_PATTERNS.clear()
    for display_text, canonical_key in _DB_ALIAS_DISPLAY.items():
        _ALIAS_PATTERNS.append(
            (canonical_key, re.compile(r"\b" + re.escape(display_text) + r"\b", re.IGNORECASE))
        )


async def refresh_entity_intel(db_session) -> int:
    """Load entity_intel from DB into module-level dicts. Returns count of rows loaded.

    Call once at app startup. Falls back to hardcoded lists if table is empty.
    """
    result = await db_session.execute(
        text("SELECT normalized_key, display_name, entity_type, aliases FROM entity_intel")
    )
    rows = result.fetchall()

    if not rows:
        return 0

    new_entity_map: dict[str, tuple[str, str]] = {}
    new_alias_display: dict[str, str] = {}
    new_alias_index: dict[str, str] = {}

    for row in rows:
        norm_key, display_name, entity_type, aliases = row
        new_entity_map[norm_key] = (display_name, entity_type)
        # Index all aliases
        for alias in (aliases or []):
            alias_display = alias.strip()
            if not alias_display:
                continue
            alias_norm = _normalize_key(alias_display)
            # Don't shadow the canonical key itself
            if alias_norm != norm_key:
                new_alias_display[alias_display] = norm_key
                new_alias_index[alias_norm] = norm_key

    _DB_ENTITY_MAP.clear()
    _DB_ENTITY_MAP.update(new_entity_map)
    _DB_ALIAS_DISPLAY.clear()
    _DB_ALIAS_DISPLAY.update(new_alias_display)
    _DB_ALIAS_INDEX.clear()
    _DB_ALIAS_INDEX.update(new_alias_index)

    _rebuild_patterns_from_db()
    logger.info(
        "Entity intel: loaded %d entities, %d aliases from DB",
        len(_DB_ENTITY_MAP), len(_DB_ALIAS_INDEX),
    )
    return len(_DB_ENTITY_MAP)
```

**Important:** You also need to add `from sqlalchemy import text` at the top of `entity_extractor.py` if it's not already imported. Check the existing imports first.

- [ ] **Step 4: Run tests**

```bash
docker compose exec ingestion python -m pytest tests/test_entity_extractor.py -v
```

Expected: all pass (including new tests and all pre-existing ones).

- [ ] **Step 5: Add startup hook to main.py**

Find the `startup` event handler (or `lifespan` function) in `app/main.py`. Add the entity intel load after `ensure_indexes()`:

```python
from app.ingestion.entity_extractor import refresh_entity_intel
from app.db.session import AsyncSessionLocal

# Inside the startup event / lifespan startup block:
async with AsyncSessionLocal() as db:
    try:
        count = await refresh_entity_intel(db)
        if count > 0:
            logger.info("Entity intel: %d entities loaded from DB", count)
        else:
            logger.info("Entity intel: DB table empty — using hardcoded fallback")
    except Exception:
        logger.warning(
            "entity_intel table not available — using hardcoded fallback",
            exc_info=True,
        )
```

- [ ] **Step 6: Smoke test the app starts cleanly**

```bash
docker compose restart backend
docker compose logs backend --tail=20
```

Expected: no errors, log line with `Entity intel: N entities loaded from DB`.

- [ ] **Step 7: Commit**

```bash
git add app/ingestion/entity_extractor.py app/main.py tests/test_entity_extractor.py
git commit -m "feat(entities): add refresh_entity_intel startup loader"
```

---

## Task 6: entity_extractor.py — alias resolution (Stage 4)

NER output whose name or normalized_key matches a known alias gets rewritten to the canonical key/name before the trusted-tier merge.

**Files:**
- Modify: `app/ingestion/entity_extractor.py`
- Modify: `tests/test_entity_extractor.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_entity_extractor.py`:

```python
# ---------------------------------------------------------------------------
# _resolve_aliases — Stage 4 alias resolution for NER output
# ---------------------------------------------------------------------------

from app.ingestion.entity_extractor import _resolve_aliases


@pytest.mark.asyncio
async def test_resolve_aliases_rewrites_to_canonical():
    """NER entity whose normalized_key is a known alias gets rewritten."""
    import app.ingestion.entity_extractor as mod
    # Inject test alias
    mod._DB_ENTITY_MAP["apt29"] = ("APT29", "actor")
    mod._DB_ALIAS_INDEX["midnight-blizzard"] = "apt29"

    entities = [{"type": "actor", "name": "Midnight Blizzard", "normalized_key": "midnight-blizzard"}]
    resolved = _resolve_aliases(entities)

    assert len(resolved) == 1
    assert resolved[0]["normalized_key"] == "apt29"
    assert resolved[0]["name"] == "APT29"


@pytest.mark.asyncio
async def test_resolve_aliases_deduplicates_to_higher_mentions():
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

    entities = [{"type": "malware", "name": "SomeNewThing", "normalized_key": "some-new-thing"}]
    resolved = _resolve_aliases(entities)

    assert resolved[0]["normalized_key"] == "some-new-thing"
```

- [ ] **Step 2: Run to confirm they fail**

```bash
docker compose exec ingestion python -m pytest tests/test_entity_extractor.py::test_resolve_aliases_rewrites_to_canonical -v
```

Expected: `ImportError` or `AttributeError`

- [ ] **Step 3: Add _resolve_aliases() to entity_extractor.py**

Add after `refresh_entity_intel()`:

```python
def _resolve_aliases(entities: list[dict]) -> list[dict]:
    """Stage 4: rewrite NER entity keys/names to canonical using _DB_ALIAS_INDEX.

    If two entities resolve to the same canonical key, the one with higher mentions
    wins; the other is dropped.
    """
    if not _DB_ALIAS_INDEX:
        return entities

    canonical_winner: dict[str, dict] = {}  # canonical_key → winning entity dict

    for e in entities:
        # Check by normalized_key, then by name (lowercased + normalized)
        canonical = (
            _DB_ALIAS_INDEX.get(e["normalized_key"])
            or _DB_ALIAS_INDEX.get(_normalize_key(e.get("name", "")))
        )
        if canonical and canonical in _DB_ENTITY_MAP:
            display, etype = _DB_ENTITY_MAP[canonical]
            e = {**e, "normalized_key": canonical, "name": display, "type": etype}

        key = e["normalized_key"]
        existing = canonical_winner.get(key)
        if existing is None:
            canonical_winner[key] = e
        elif e.get("mentions", 1) > existing.get("mentions", 1):
            canonical_winner[key] = e

    return list(canonical_winner.values())
```

- [ ] **Step 4: Wire _resolve_aliases() into extract_entities()**

In `extract_entities()`, add the call after the CVE-drop line and before `regex_entities`:

```python
    # CVEs handled by regex — drop any CVE entities the NER model emits
    model_entities = [e for e in model_entities if e.get("type") != "cve"]

    # Stage 4: resolve NER output to canonical keys via DB alias index
    model_entities = _resolve_aliases(model_entities)

    regex_entities = _extract_regex(article)
```

- [ ] **Step 5: Run all entity extractor tests**

```bash
docker compose exec ingestion python -m pytest tests/test_entity_extractor.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/entity_extractor.py tests/test_entity_extractor.py
git commit -m "feat(entities): add Stage 4 alias resolution for NER output"
```

---

## Task 7: entity_extractor.py — KEV enrichment + CWE/TTP regex

**Files:**
- Modify: `app/ingestion/entity_extractor.py`
- Modify: `tests/test_entity_extractor.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_entity_extractor.py`:

```python
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
    types = {e["normalized_key"]: e["type"] for e in entities}
    assert types["cwe-79"] == "cwe"


@pytest.mark.asyncio
async def test_extract_ttp_from_text():
    article = _make_article(title="Attacker uses T1059 command execution technique")
    entities = await extract_entities(article)
    keys = [e["normalized_key"] for e in entities]
    assert "t1059" in keys
    types = {e["normalized_key"]: e["type"] for e in entities}
    assert types["t1059"] == "ttp"


@pytest.mark.asyncio
async def test_ttp_regex_does_not_match_out_of_range():
    """T9999 is not a valid ATT&CK TTP — must not match."""
    article = _make_article(title="Model T9999 device was vulnerable")
    entities = await extract_entities(article)
    keys = [e["normalized_key"] for e in entities]
    assert "t9999" not in keys
```

- [ ] **Step 2: Run to confirm they fail**

```bash
docker compose exec ingestion python -m pytest tests/test_entity_extractor.py::test_enrich_from_kev_emits_vendor_and_product tests/test_entity_extractor.py::test_extract_cwe_from_text tests/test_entity_extractor.py::test_extract_ttp_from_text -v
```

Expected: AttributeError / assertion failures.

- [ ] **Step 3: Add CWE + TTP regex to _extract_regex()**

Near the top of `entity_extractor.py` where other patterns are defined, add:

```python
_CWE_RE = re.compile(r"\bCWE-\d+\b", re.IGNORECASE)
# ATT&CK technique IDs: T1xxx range only to avoid serial/model number false positives
_TTP_RE = re.compile(r"\bT1[0-6]\d{2}(?:\.\d{3})?\b")
```

In `_extract_regex()`, after the alias loop and before `return list(seen.values())`, add:

```python
    # --- CWE IDs ---
    for match in _CWE_RE.finditer(combined):
        cwe = match.group(0).upper()  # normalise case: cwe-79 → CWE-79
        key = cwe.lower()
        if key not in seen:
            seen[key] = {"type": "cwe", "name": cwe, "normalized_key": key}

    # --- ATT&CK TTP IDs ---
    for match in _TTP_RE.finditer(combined):
        ttp = match.group(0).upper()
        key = ttp.lower()
        if key not in seen:
            seen[key] = {"type": "ttp", "name": ttp, "normalized_key": key}
```

- [ ] **Step 4: Add _enrich_from_kev() to entity_extractor.py**

Add after `_resolve_aliases()`:

```python
async def _enrich_from_kev(cve_ids: list[str], db_session) -> list[dict]:
    """Look up CVE IDs in cisa_kev, emit trusted vendor+product entities for each hit."""
    if not cve_ids or db_session is None:
        return []

    result = await db_session.execute(
        text("SELECT vendor, product FROM cisa_kev WHERE cve_id = ANY(:ids)"),
        {"ids": cve_ids},
    )
    rows = result.fetchall()

    seen: set[str] = set()
    entities: list[dict] = []
    for vendor, product in rows:
        vendor_key = _normalize_key(vendor)
        if vendor_key and vendor_key not in seen:
            entities.append({"type": "vendor", "name": vendor, "normalized_key": vendor_key})
            seen.add(vendor_key)
        product_key = _normalize_key(product)
        if product_key and product_key not in seen:
            entities.append({"type": "product", "name": product, "normalized_key": product_key})
            seen.add(product_key)

    return entities
```

- [ ] **Step 5: Wire _enrich_from_kev() into extract_entities()**

In `extract_entities()`, after `regex_entities = _extract_regex(article)`, add:

```python
    # KEV enrichment: deterministic vendor+product for CVEs found in text
    cve_ids = [e["name"] for e in regex_entities if e.get("type") == "cve"]
    kev_entities = await _enrich_from_kev(cve_ids, db_session)
```

Then update the merge loop to include `kev_entities` before `regex_entities`:

```python
    seen_keys = {e["normalized_key"] for e in model_entities}
    merged = list(model_entities)
    for e in kev_entities + regex_entities:
        key = e["normalized_key"]
        if key not in seen_keys and not any(k.startswith(key + "-") for k in seen_keys):
            merged.append(e)
            seen_keys.add(key)
    return merged
```

- [ ] **Step 6: Run all entity extractor tests**

```bash
docker compose exec ingestion python -m pytest tests/test_entity_extractor.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add app/ingestion/entity_extractor.py tests/test_entity_extractor.py
git commit -m "feat(entities): add KEV enrichment, CWE/TTP regex, Stage 4 complete"
```

---

## Task 8: Full test suite + end-to-end verification

- [ ] **Step 1: Run full test suite**

```bash
docker compose exec ingestion python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all pass. Fix any failures before proceeding.

- [ ] **Step 2: Restart backend to exercise startup loader with live DB**

```bash
docker compose restart backend
docker compose logs backend --tail=20
```

Expected log line: `Entity intel: loaded N entities, N aliases from DB`

- [ ] **Step 3: Run full backfill to re-extract entities for all articles**

```bash
docker compose exec ingestion python scripts/backfill_ner_sidecar.py --force
```

This re-runs entity extraction for all 2,146 articles with the new alias resolution and KEV enrichment active. Takes ~13 min.

- [ ] **Step 4: Verify vendor coverage improvement**

```bash
docker compose exec ingestion python -c "
import asyncio
from app.db.opensearch import get_os_client, INDEX_ENTITIES

async def check():
    client = get_os_client()
    r = await client.count(index=INDEX_ENTITIES, body={'query': {'term': {'type': 'vendor'}}})
    print('Vendor entity docs:', r['count'])
    r2 = await client.count(index=INDEX_ENTITIES, body={'query': {'term': {'type': 'cwe'}}})
    print('CWE entity docs:', r2['count'])
    r3 = await client.count(index=INDEX_ENTITIES, body={'query': {'term': {'type': 'ttp'}}})
    print('TTP entity docs:', r3['count'])
    await client.close()

asyncio.run(check())
"
```

Expected: vendor docs >> 42 (was 42 before this feature), CWE > 0, TTP > 0.

- [ ] **Step 5: Spot-check alias resolution**

```bash
docker compose exec ingestion python -c "
import asyncio
from app.db.opensearch import get_os_client, INDEX_ENTITIES

async def check():
    client = get_os_client()
    # Confirm HIDDEN COBRA resolves to lazarus-group
    r = await client.search(index=INDEX_ENTITIES, body={
        'query': {'term': {'normalized_key': 'hidden-cobra'}},
        'size': 1
    })
    print('hidden-cobra docs (should be 0 — merged into lazarus-group):', r['hits']['total']['value'])
    r2 = await client.search(index=INDEX_ENTITIES, body={
        'query': {'term': {'normalized_key': 'lazarus-group'}},
        'size': 1
    })
    print('lazarus-group docs:', r2['hits']['total']['value'])
    await client.close()

asyncio.run(check())
"
```

Expected: `hidden-cobra docs: 0`, `lazarus-group docs: 1` (or more if multiple articles).

- [ ] **Step 6: Final commit**

```bash
git add .
git commit -m "feat(entities): trusted entity tier complete — DB-backed registry, alias resolution, KEV enrichment"
```
