# CVSS Wiring + Write-Once API Cache — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire CVSS/severity from `cve_topics` into articles and clusters so the severity sort and severity badges work. Consolidate CVE intelligence in `cve_topics` (away from `entities`). Apply the `ner_cache` write-once pattern to API-fetched data project-wide.

**Architecture:** New module `app/ingestion/cve_intel.py` is the canonical CVE-lookup interface (reads from `cve_topics`, no API calls). New helper `app/db/os_write_once.py` does atomic "set-if-null" Painless upserts. Ingester calls `lookup_cve_intel` after entity extraction to set `article.cvss_score` + `severity`. Enrichers (`enrich_cve_nvd.py`, `sync_cisa_kev.py`) write to `cve_topics` using the immutable upsert helper. One-shot migration moves existing CVE data from `entities` → `cve_topics`. Full cluster `--reset` rebuild exercises the new wiring on all 2,146 articles.

**Tech Stack:** Python 3.12, FastAPI, OpenSearch (opensearch-py), SQLAlchemy + asyncpg, Alembic, pytest-asyncio.

**Spec:** [docs/superpowers/specs/2026-05-21-cvss-wiring-and-write-once-cache-design.md](../specs/2026-05-21-cvss-wiring-and-write-once-cache-design.md)

---

## File Map

| File | Change |
|---|---|
| `app/db/opensearch.py` | Add 4 fields to `_CVE_TOPICS_MAPPING` (`cwe_ids`, `vuln_status`, `nvd_raw`, `enriched_at`) |
| `app/db/os_write_once.py` | **NEW** — `upsert_immutable()` helper |
| `tests/test_os_write_once.py` | **NEW** — unit tests |
| `app/ingestion/cve_intel.py` | **NEW** — `severity_from_cvss()` + `lookup_cve_intel()` |
| `tests/test_cve_intel.py` | **NEW** — unit tests |
| `scripts/enrich_cve_nvd.py` | Rewrite write path: target `cve_topics` via `upsert_immutable`; scroll `cve_topics` not `entities`; remove `--force` |
| `app/api/routes/entities.py` | Join `cve_topics` for `type=cve` entries in list + detail responses |
| `app/ingestion/ingester.py` | Call `lookup_cve_intel` after entity extraction; set `cvss_score` + `severity` on article |
| `tests/test_ingester.py` | Add test for CVSS/severity population from cve_intel lookup |
| `app/ingestion/normalizer.py` | Remove `_extract_cvss_score` calls from `normalize_article` and `normalize_cisa_advisory` |
| `tests/test_normalizer.py` | Update tests that exercised CVSS extraction (assert it's absent now) |
| `scripts/sync_cisa_kev.py` | Add second write: set `cisa_kev=true` + `kev_added_at` (immutable) on `cve_topics` per CVE |
| `alembic/versions/<hash>_protect_api_sourced_rows.py` | **NEW** — Postgres triggers for `cisa_kev` and `entity_intel` (mitre_attack source) |
| `scripts/migrate_cve_intel_to_topics.py` | **NEW** — one-shot data migration `entities` → `cve_topics` |

---

## Task 1: Add CVE-intel fields to `cve_topics` mapping

**Files:**
- Modify: `app/db/opensearch.py` (within `_CVE_TOPICS_MAPPING`)

- [ ] **Step 1: Add the four new fields in `_CVE_TOPICS_MAPPING["mappings"]["properties"]`**

Insert after `nvd_last_modified`:

```python
"cwe_ids":          {"type": "keyword"},
"vuln_status":      {"type": "keyword"},
"nvd_raw":          {"type": "object", "enabled": False},
"enriched_at":      {"type": "date", "format": "date_optional_time||epoch_millis"},
```

- [ ] **Step 2: Restart the ingestion container to run `ensure_indexes()`**

```bash
docker compose restart ingestion
docker compose logs ingestion --tail 50 | grep -E "Updated mapping|Created OpenSearch|cve_topics"
```

Expected output: `INFO Updated mapping for index: cve_topics`. No errors.

- [ ] **Step 3: Verify the new fields are live**

```bash
docker compose exec opensearch curl -s -u "admin:$(grep OPENSEARCH_ADMIN_PASSWORD .env | cut -d= -f2)" -k https://localhost:9200/cve_topics/_mapping | python -m json.tool | grep -E "cwe_ids|vuln_status|nvd_raw|enriched_at"
```

Expected: all four field names appear in the output.

- [ ] **Step 4: Commit**

```bash
git add app/db/opensearch.py
git commit -m "cve_topics: add cwe_ids, vuln_status, nvd_raw, enriched_at fields"
```

---

## Task 2: `os_write_once.upsert_immutable` helper + tests

**Files:**
- Create: `app/db/os_write_once.py`
- Create: `tests/test_os_write_once.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_os_write_once.py`:

```python
"""Tests for app.db.os_write_once."""
import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_upsert_immutable_passes_immutable_and_mutable_params():
    from app.db.os_write_once import upsert_immutable

    mock_client = AsyncMock()
    await upsert_immutable(
        client=mock_client,
        index="cve_topics",
        doc_id="CVE-2026-1234",
        immutable_fields={"cvss_score": 9.8, "cvss_severity": "CRITICAL"},
        mutable_fields={"updated_at": "2026-05-21T00:00:00Z"},
    )

    assert mock_client.update.called
    kwargs = mock_client.update.call_args.kwargs
    body = kwargs["body"]
    assert body["script"]["params"]["immutable"] == {"cvss_score": 9.8, "cvss_severity": "CRITICAL"}
    assert body["script"]["params"]["mutable"] == {"updated_at": "2026-05-21T00:00:00Z"}
    assert "if (!ctx._source.containsKey" in body["script"]["source"]
    assert body["upsert"]["cvss_score"] == 9.8
    assert body["upsert"]["updated_at"] == "2026-05-21T00:00:00Z"


@pytest.mark.asyncio
async def test_upsert_immutable_accepts_only_immutable_fields():
    from app.db.os_write_once import upsert_immutable

    mock_client = AsyncMock()
    await upsert_immutable(
        client=mock_client,
        index="cve_topics",
        doc_id="CVE-2026-5678",
        immutable_fields={"cvss_score": 7.5},
    )

    body = mock_client.update.call_args.kwargs["body"]
    assert body["script"]["params"]["immutable"] == {"cvss_score": 7.5}
    assert body["script"]["params"]["mutable"] == {}


@pytest.mark.asyncio
async def test_upsert_immutable_noop_when_both_empty():
    from app.db.os_write_once import upsert_immutable

    mock_client = AsyncMock()
    await upsert_immutable(
        client=mock_client,
        index="cve_topics",
        doc_id="CVE-2026-9999",
        immutable_fields={},
    )

    mock_client.update.assert_not_called()
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
docker compose exec ingestion pytest tests/test_os_write_once.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.db.os_write_once'`.

- [ ] **Step 3: Implement the helper**

Create `app/db/os_write_once.py`:

```python
"""Atomic write-once upsert for API-fetched data.

Immutable fields are set only on first write (if currently null/missing).
Mutable fields are always overwritten. Implemented as a Painless update_with_upsert
so concurrent writers can't race.

Used by enrichers (NVD, KEV) so API-paid data is never silently overwritten.
"""
from typing import Optional


_SCRIPT_SOURCE = """
    for (entry in params.immutable.entrySet()) {
        if (!ctx._source.containsKey(entry.getKey()) || ctx._source[entry.getKey()] == null) {
            ctx._source[entry.getKey()] = entry.getValue();
        }
    }
    for (entry in params.mutable.entrySet()) {
        ctx._source[entry.getKey()] = entry.getValue();
    }
"""


async def upsert_immutable(
    *,
    client,
    index: str,
    doc_id: str,
    immutable_fields: dict,
    mutable_fields: Optional[dict] = None,
) -> None:
    """Upsert a doc: write immutable fields only if currently null.

    Args:
      client: AsyncOpenSearch instance
      index: target index name
      doc_id: document id
      immutable_fields: fields that are written once and never updated
      mutable_fields: fields that are always written (e.g. timestamps)
    """
    mutable_fields = mutable_fields or {}
    if not immutable_fields and not mutable_fields:
        return

    await client.update(
        index=index,
        id=doc_id,
        body={
            "script": {
                "source": _SCRIPT_SOURCE,
                "lang": "painless",
                "params": {
                    "immutable": immutable_fields,
                    "mutable": mutable_fields,
                },
            },
            "upsert": {**immutable_fields, **mutable_fields},
        },
        retry_on_conflict=3,
    )
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
docker compose exec ingestion pytest tests/test_os_write_once.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/db/os_write_once.py tests/test_os_write_once.py
git commit -m "add os_write_once.upsert_immutable helper"
```

---

## Task 3: `cve_intel` module + tests

**Files:**
- Create: `app/ingestion/cve_intel.py`
- Create: `tests/test_cve_intel.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_cve_intel.py`:

```python
"""Tests for app.ingestion.cve_intel."""
import pytest
from unittest.mock import AsyncMock, patch


# ---------------------------------------------------------------------------
# severity_from_cvss
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("score, expected", [
    (10.0, "critical"),
    (9.0, "critical"),
    (8.9, "high"),
    (7.0, "high"),
    (6.9, "medium"),
    (4.0, "medium"),
    (3.9, "low"),
    (0.1, "low"),
])
def test_severity_from_cvss_thresholds(score, expected):
    from app.ingestion.cve_intel import severity_from_cvss
    assert severity_from_cvss(score) == expected


@pytest.mark.parametrize("score", [None, 0.0, -1.0])
def test_severity_from_cvss_returns_none_for_zero_or_none(score):
    from app.ingestion.cve_intel import severity_from_cvss
    assert severity_from_cvss(score) is None


# ---------------------------------------------------------------------------
# lookup_cve_intel
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lookup_cve_intel_returns_empty_when_no_cves():
    from app.ingestion.cve_intel import lookup_cve_intel
    result = await lookup_cve_intel([])
    assert result == {}


@pytest.mark.asyncio
async def test_lookup_cve_intel_keys_results_by_upper_cve_id():
    from app.ingestion.cve_intel import lookup_cve_intel

    mock_client = AsyncMock()
    mock_client.search = AsyncMock(return_value={
        "hits": {"hits": [
            {"_id": "CVE-2026-1234", "_source": {"cvss_score": 9.8, "cvss_severity": "CRITICAL", "cisa_kev": True}},
            {"_id": "CVE-2026-5678", "_source": {"cvss_score": 7.2, "cvss_severity": "HIGH", "cisa_kev": False}},
        ]}
    })
    with patch("app.ingestion.cve_intel.get_os_client", return_value=mock_client):
        result = await lookup_cve_intel(["cve-2026-1234", "CVE-2026-5678"])

    assert result["CVE-2026-1234"]["cvss_score"] == 9.8
    assert result["CVE-2026-1234"]["cisa_kev"] is True
    assert result["CVE-2026-5678"]["cvss_severity"] == "HIGH"


@pytest.mark.asyncio
async def test_lookup_cve_intel_omits_missing_cves():
    from app.ingestion.cve_intel import lookup_cve_intel

    mock_client = AsyncMock()
    mock_client.search = AsyncMock(return_value={"hits": {"hits": []}})
    with patch("app.ingestion.cve_intel.get_os_client", return_value=mock_client):
        result = await lookup_cve_intel(["CVE-9999-9999"])
    assert result == {}
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
docker compose exec ingestion pytest tests/test_cve_intel.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the module**

Create `app/ingestion/cve_intel.py`:

```python
"""CVE intelligence lookups against cve_topics. Read-only.

This module is the single read-side interface for "what do we know about this CVE?".
Used at ingest time to populate article.cvss_score and article.severity from
already-enriched data in cve_topics. Never calls external APIs — if a CVE isn't
in cve_topics, the caller gets nothing back, which is the correct signal that
NVD enrichment hasn't reached this CVE yet.
"""
from typing import Optional

from app.db.opensearch import INDEX_CVE_TOPICS, get_os_client


# CVSS v3 severity bands (per FIRST CVSS spec):
#   9.0-10.0 critical, 7.0-8.9 high, 4.0-6.9 medium, 0.1-3.9 low
_SEVERITY_THRESHOLDS = [
    (9.0, "critical"),
    (7.0, "high"),
    (4.0, "medium"),
    (0.0, "low"),  # >0 because we treat 0.0/negative/None as "no severity"
]


def severity_from_cvss(score: Optional[float]) -> Optional[str]:
    """Map CVSS base score → severity label.

    Returns None for None, 0.0, or negative scores (treated as "no severity").
    """
    if score is None or score <= 0:
        return None
    for threshold, label in _SEVERITY_THRESHOLDS:
        if score >= threshold:
            return label
    return None


async def lookup_cve_intel(cve_ids: list[str]) -> dict[str, dict]:
    """Look up CVE intelligence in cve_topics. Read-only, no API calls.

    Returns {cve_id_upper: {cvss_score, cvss_severity, cvss_vector, cisa_kev,
                            epss_score, epss_percentile, cwe_ids, ...}}.
    Missing CVEs are absent from the result (not returned as null).
    Caller decides how to handle absence.
    """
    if not cve_ids:
        return {}

    # Normalize and dedupe — cve_topics doc IDs are uppercase
    ids = list({cid.upper() for cid in cve_ids if cid})
    if not ids:
        return {}

    resp = await get_os_client().search(
        index=INDEX_CVE_TOPICS,
        body={
            "query": {"ids": {"values": ids}},
            "size": len(ids),
            "_source": [
                "cvss_score", "cvss_severity", "cvss_vector",
                "cwe_ids", "cisa_kev", "kev_added_at",
                "epss_score", "epss_percentile", "epss_updated_at",
                "vuln_status", "nvd_last_modified",
            ],
        },
    )
    return {hit["_id"]: hit["_source"] for hit in resp["hits"]["hits"]}
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
docker compose exec ingestion pytest tests/test_cve_intel.py -v
```

Expected: 11 passed (8 parametrized severity + 3 lookup).

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/cve_intel.py tests/test_cve_intel.py
git commit -m "cve_intel: severity_from_cvss + lookup_cve_intel from cve_topics"
```

---

## Task 4: Rewrite `enrich_cve_nvd.py` to target `cve_topics`

**Files:**
- Modify: `scripts/enrich_cve_nvd.py`

The current script writes NVD fields to the `entities` index. After this change it writes to `cve_topics` via `upsert_immutable`, scrolls `cve_topics` for unenriched CVEs, and drops `--force` (per spec Phase 3.5, re-enrichment becomes an explicit two-step admin path).

- [ ] **Step 1: Replace the index scroll function**

Locate `_scroll_cve_entities` (around line 234) and replace with:

```python
async def _scroll_unenriched_cve_topics(client: AsyncOpenSearch) -> list[dict]:
    """Scan cve_topics for CVEs that haven't been NVD-enriched yet.

    Returns hits where nvd_last_modified is null. Each hit has _id = CVE ID.
    """
    from app.db.opensearch import INDEX_CVE_TOPICS

    query = {
        "bool": {
            "must": [{"match_all": {}}],
            "must_not": [{"exists": {"field": "nvd_last_modified"}}],
        }
    }

    results = []
    resp = await client.search(
        index=INDEX_CVE_TOPICS,
        body={"query": query, "size": _SCROLL_SIZE},
        scroll="2m",
    )
    scroll_id = resp["_scroll_id"]

    while True:
        hits = resp["hits"]["hits"]
        if not hits:
            break
        results.extend(hits)
        resp = await client.scroll(scroll_id=scroll_id, scroll="2m")

    try:
        await client.clear_scroll(scroll_id=scroll_id)
    except Exception:
        pass

    return results
```

- [ ] **Step 2: Replace the entity-update helper with cve_topics upsert**

Locate `_update_entity` (around line 271) and replace with:

```python
async def _update_cve_topic(client: AsyncOpenSearch, cve_id: str, immutable: dict, mutable: dict) -> None:
    """Write NVD fields to cve_topics via write-once helper."""
    from app.db.opensearch import INDEX_CVE_TOPICS
    from app.db.os_write_once import upsert_immutable

    await upsert_immutable(
        client=client,
        index=INDEX_CVE_TOPICS,
        doc_id=cve_id,
        immutable_fields=immutable,
        mutable_fields=mutable,
    )
```

- [ ] **Step 3: Update `_apply_mappings` to only touch `cve_topics`**

Replace the body of `_apply_mappings` (around line 188-231) with:

```python
async def _apply_mappings(client: AsyncOpenSearch) -> None:
    """Push new fields into cve_topics mapping (idempotent, no admin needed)."""
    from app.db.opensearch import INDEX_CVE_TOPICS, _CVE_TOPICS_MAPPING

    await client.indices.put_mapping(
        index=INDEX_CVE_TOPICS,
        body={"properties": _CVE_TOPICS_MAPPING["mappings"]["properties"]},
    )
    logger.info("Mapping updated: %s", INDEX_CVE_TOPICS)
```

- [ ] **Step 4: Update the main loop to use the new path**

In `run()` (around line 368), replace the body after the API-key/sleep logging with:

```python
    docs = await _scroll_unenriched_cve_topics(client)
    total = len(docs)
    logger.info("Unenriched CVEs in cve_topics: %d", total)

    if total == 0:
        logger.info("Nothing to do. All CVE topics already enriched.")
        await client.close()
        return

    if args.dry_run:
        logger.info("Dry run — no NVD requests made.")
        await client.close()
        return

    done = not_found = failed = 0
    enriched_cves: list[str] = []
    t_start = time.monotonic()

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    for i, hit in enumerate(docs, 1):
        cve_id = hit["_id"]

        elapsed = time.monotonic() - t_start
        eta_s = (elapsed / i) * (total - i) if i > 1 else total * sleep_s
        logger.info("[%d/%d] %s  (ETA ~%dm%02ds)", i, total, cve_id, int(eta_s // 60), int(eta_s % 60))

        cve_data = _nvd_fetch(cve_id, api_key, sleep_s)

        if cve_data is None:
            mitre_state = _mitre_check(cve_id)
            if mitre_state == "reserved":
                mutable = {"vuln_status": "Reserved"}
                logger.info("  → Reserved")
            elif mitre_state == "rejected":
                mutable = {"vuln_status": "Rejected", "nvd_last_modified": now_iso}
                logger.info("  → Rejected")
            elif mitre_state == "published":
                mutable = {"vuln_status": "Pending NVD"}
                logger.info("  → MITRE Published, NVD missed it — retry next run")
            else:
                mutable = {"vuln_status": "Invalid", "nvd_last_modified": now_iso}
                logger.info("  → Invalid CVE ID")
            try:
                await _update_cve_topic(client, cve_id, immutable={}, mutable=mutable)
            except Exception:
                logger.exception("Failed to update status for %s", cve_id)
            not_found += 1
        else:
            try:
                immutable = _parse_entity_fields(cve_data)  # cvss_score, cvss_severity, cvss_vector, cwe_ids, vuln_status, nvd_last_modified
                immutable["nvd_raw"] = cve_data
                immutable["enriched_at"] = now_iso
                await _update_cve_topic(client, cve_id, immutable=immutable, mutable={"updated_at": now_iso})
                enriched_cves.append(cve_id)
                done += 1
            except Exception:
                logger.exception("Failed to enrich %s", cve_id)
                failed += 1

        if i < total:
            time.sleep(sleep_s)

    await client.close()
    logger.info("Done. enriched=%d  pending=%d  failed=%d", done, not_found, failed)

    await _rescore_clusters_for_cves(enriched_cves)
```

- [ ] **Step 5: Update `_rescore_clusters_for_cves` to read CVSS from `cve_topics`**

Replace the `ent_resp` block in `_rescore_clusters_for_cves` (around line 312) with:

```python
        # Fetch enriched CVSS scores from cve_topics
        topic_resp = await client.search(
            index=INDEX_CVE_TOPICS,
            body={
                "query": {"ids": {"values": enriched_cve_names}},
                "size": len(enriched_cve_names),
                "_source": ["cvss_score"],
            },
        )
        cve_cvss: dict[str, float] = {}
        for hit in topic_resp["hits"]["hits"]:
            score = hit["_source"].get("cvss_score")
            if score is not None:
                cve_cvss[hit["_id"].upper()] = float(score)
```

And replace `from app.db.opensearch import INDEX_CLUSTERS, INDEX_ENTITIES` with:

```python
        from app.db.opensearch import INDEX_CLUSTERS, INDEX_CVE_TOPICS
```

- [ ] **Step 6: Remove `--force` flag and `--api-key`/`--sleep` defaults that depended on it**

In the argparse setup at the bottom (around line 472), delete the `--force` line:

```python
    parser.add_argument("--force", action="store_true", help="Re-enrich CVEs that already have NVD data")
```

Re-enrichment now requires manual deletion of the cve_topics doc first. (Documented in Phase 3.5 of the spec.)

- [ ] **Step 7: Dry-run the script to confirm it loads and finds work**

```bash
docker compose exec ingestion python scripts/enrich_cve_nvd.py --dry-run
```

Expected: logs `Unenriched CVEs in cve_topics: N` where N is the count of CVE topics without `nvd_last_modified` set. Exits cleanly.

- [ ] **Step 8: Commit**

```bash
git add scripts/enrich_cve_nvd.py
git commit -m "enrich_cve_nvd: write to cve_topics via upsert_immutable; drop --force"
```

---

## Task 5: Update `/api/entities/` to JOIN `cve_topics` for CVE entries

**Files:**
- Modify: `app/api/routes/entities.py`

The entity list and detail endpoints currently read `cvss_score` from the `entities` index. Once Task 4 ships, that field stops being updated on entities. We need to fetch CVSS from `cve_topics` for `type=cve` results so the entity page keeps working.

- [ ] **Step 1: Add a helper to enrich CVE entity items with cve_topics data**

In `app/api/routes/entities.py`, near the top after imports add:

```python
from app.db.opensearch import INDEX_CVE_TOPICS


async def _join_cve_topics(items_or_hits: list, get_name) -> dict[str, dict]:
    """Look up CVSS for the CVE-type items. Returns {cve_id_upper: {cvss_score, ...}}."""
    cve_names = [get_name(it) for it in items_or_hits if get_name(it)]
    if not cve_names:
        return {}
    ids = list({n.upper() for n in cve_names})
    resp = await get_os_client().search(
        index=INDEX_CVE_TOPICS,
        body={
            "query": {"ids": {"values": ids}},
            "size": len(ids),
            "_source": ["cvss_score", "cvss_severity"],
        },
    )
    return {h["_id"]: h["_source"] for h in resp["hits"]["hits"]}
```

- [ ] **Step 2: Use the join in `list_entities`**

Replace the `items = [...]` block in `list_entities` (currently around line 53) with:

```python
    cve_topic_data = {}
    if type == "cve" or type is None:
        cve_hits = [(h["_id"], h["_source"]) for h in hits if h["_source"].get("type") == "cve"]
        cve_topic_data = await _join_cve_topics(cve_hits, lambda x: x[1].get("name"))

    items = []
    for h in hits:
        src = h["_source"]
        topic = cve_topic_data.get((src.get("name") or "").upper(), {})
        items.append(EntityItem(
            id=h["_id"],
            type=src["type"],
            name=src["name"],
            normalized_key=src["normalized_key"],
            cvss_score=topic.get("cvss_score") if src["type"] == "cve" else src.get("cvss_score"),
            first_seen=src["first_seen"],
            last_seen=src["last_seen"],
            article_count=src.get("article_count", 0),
        ))
```

- [ ] **Step 3: Use the join in `get_entity`**

In `get_entity`, after the `src = resp["_source"]` line, add:

```python
    cvss_score = src.get("cvss_score")
    if src["type"] == "cve":
        topic_resp = await get_os_client().search(
            index=INDEX_CVE_TOPICS,
            body={"query": {"ids": {"values": [src["name"].upper()]}}, "_source": ["cvss_score"]},
        )
        topic_hits = topic_resp["hits"]["hits"]
        if topic_hits:
            cvss_score = topic_hits[0]["_source"].get("cvss_score")
```

Then change the `cvss_score=src.get("cvss_score")` line in the `EntityDetail(...)` return to:

```python
        cvss_score=cvss_score,
```

- [ ] **Step 4: Manual verification via curl**

```bash
docker compose exec ingestion curl -s http://localhost:8000/api/entities/?type=cve\&limit=3 | python -m json.tool | grep -E "name|cvss_score"
```

Expected: CVE entries have non-null `cvss_score` (matching what's in cve_topics).

- [ ] **Step 5: Commit**

```bash
git add app/api/routes/entities.py
git commit -m "entities API: join cve_topics for CVE-type CVSS data"
```

---

## Task 6: Wire `lookup_cve_intel` into the ingester

**Files:**
- Modify: `app/ingestion/ingester.py`
- Modify: `tests/test_ingester.py`

- [ ] **Step 1: Write a failing test in `tests/test_ingester.py`**

Add at the bottom of `tests/test_ingester.py`:

```python
@pytest.mark.asyncio
async def test_ingest_sets_cvss_and_severity_from_cve_intel(monkeypatch):
    """When article has CVEs known to cve_topics, ingest sets cvss_score + severity."""
    import app.ingestion.ingester as ingester
    from unittest.mock import AsyncMock, patch

    article = {
        "slug": "test-article",
        "title": "Test",
        "desc": "Test",
        "cve_ids": ["CVE-2026-9999"],
        "tags": [],
        "raw_tags": [],
        "normalized_topics": [],
        "credibility_weight": 1.0,
        "source_name": "Test",
        "source_url": "https://example.com/x",
        "published_at": "2026-05-21T00:00:00Z",
        "content_type": "news",
    }

    captured = {}
    async def fake_lookup(cve_ids):
        captured["called_with"] = cve_ids
        return {"CVE-2026-9999": {"cvss_score": 9.8, "cvss_severity": "CRITICAL"}}

    monkeypatch.setattr(ingester, "lookup_cve_intel", fake_lookup)

    await ingester._apply_cve_intel(article)

    assert captured["called_with"] == ["CVE-2026-9999"]
    assert article["cvss_score"] == 9.8
    assert article["severity"] == "critical"


@pytest.mark.asyncio
async def test_apply_cve_intel_noop_when_no_cves(monkeypatch):
    import app.ingestion.ingester as ingester

    called = []
    async def fake_lookup(cve_ids):
        called.append(cve_ids)
        return {}

    monkeypatch.setattr(ingester, "lookup_cve_intel", fake_lookup)

    article = {"slug": "x", "cve_ids": []}
    await ingester._apply_cve_intel(article)

    assert called == []
    assert "cvss_score" not in article


@pytest.mark.asyncio
async def test_apply_cve_intel_respects_existing_value(monkeypatch):
    """Write-once: if cvss_score already set, do not overwrite."""
    import app.ingestion.ingester as ingester

    async def fake_lookup(cve_ids):
        return {"CVE-2026-1111": {"cvss_score": 9.8}}

    monkeypatch.setattr(ingester, "lookup_cve_intel", fake_lookup)

    article = {"slug": "x", "cve_ids": ["CVE-2026-1111"], "cvss_score": 5.0, "severity": "medium"}
    await ingester._apply_cve_intel(article)

    assert article["cvss_score"] == 5.0  # unchanged
    assert article["severity"] == "medium"
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
docker compose exec ingestion pytest tests/test_ingester.py::test_ingest_sets_cvss_and_severity_from_cve_intel tests/test_ingester.py::test_apply_cve_intel_noop_when_no_cves tests/test_ingester.py::test_apply_cve_intel_respects_existing_value -v
```

Expected: `AttributeError: module 'app.ingestion.ingester' has no attribute '_apply_cve_intel'`.

- [ ] **Step 3: Add `_apply_cve_intel` and wire it into `ingest_source`**

In `app/ingestion/ingester.py`, add to the imports near the top:

```python
from app.ingestion.cve_intel import lookup_cve_intel, severity_from_cvss
```

Add a helper function just above `ingest_source`:

```python
async def _apply_cve_intel(article: dict) -> None:
    """Look up article's CVEs in cve_topics; set cvss_score + severity if known.

    Write-once: respects existing non-null values. Articles whose CVEs aren't in
    cve_topics yet (NVD enrichment hasn't reached them) get nothing — the nightly
    rebuild or backfill picks them up later.
    """
    cve_ids = article.get("cve_ids") or []
    if not cve_ids:
        return
    intel = await lookup_cve_intel(cve_ids)
    if not intel:
        return
    scores = [v["cvss_score"] for v in intel.values() if v.get("cvss_score") is not None]
    if not scores:
        return
    max_score = max(scores)
    if article.get("cvss_score") is None:
        article["cvss_score"] = max_score
    if article.get("severity") is None:
        article["severity"] = severity_from_cvss(max_score)
```

In `ingest_source`, after the `tag_result = await asyncio.to_thread(...)` block (around line 388, just before `article["raw_tags"] = ...`), add:

```python
            # Populate cvss_score + severity from cve_topics if known
            await _apply_cve_intel(article)
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
docker compose exec ingestion pytest tests/test_ingester.py::test_ingest_sets_cvss_and_severity_from_cve_intel tests/test_ingester.py::test_apply_cve_intel_noop_when_no_cves tests/test_ingester.py::test_apply_cve_intel_respects_existing_value -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/ingester.py tests/test_ingester.py
git commit -m "ingester: populate article cvss_score + severity from cve_topics"
```

---

## Task 7: Drop `_extract_cvss_score` regex from normalizer

**Files:**
- Modify: `app/ingestion/normalizer.py`
- Modify: `tests/test_normalizer.py`

The DB lookup now supersedes the regex path. The regex is only kept for the `cvss_vector` extraction (no NVD equivalent).

- [ ] **Step 1: In `normalize_cisa_advisory`, remove the `_extract_cvss_score` call**

Find the block in `normalize_cisa_advisory` (around line 401):

```python
    cvss_score = _extract_cvss_score(content_html)
    cve_ids    = _extract_cve_ids(content_html)
```

Change to:

```python
    cve_ids = _extract_cve_ids(content_html)
```

Then in the `return NormalizedArticle(...)` block of the same function (around line 429), delete the line:

```python
        cvss_score=cvss_score,
```

- [ ] **Step 2: In `normalize_article`, remove the conditional CVSS extraction**

Delete this block in `normalize_article` (around lines 513-517):

```python
    # Conditional: extract CVSS score + advisory metadata
    if source.get("extract_cvss"):
        cvss = _extract_cvss_score(content_html or "")
        if cvss is not None:
            article["cvss_score"] = cvss
        raw_metadata: dict = {}
```

Replace with:

```python
    # CVSS now comes from cve_topics at ingest time, not regex extraction.
    # Keep CVSS vector extraction — it has no NVD equivalent.
    if source.get("extract_cvss"):
        raw_metadata: dict = {}
```

- [ ] **Step 3: Delete the `_extract_cvss_score` function**

It's now unreferenced. Delete lines 181-196 (the `_extract_cvss_score` definition).

- [ ] **Step 4: Update normalizer tests**

In `tests/test_normalizer.py`, find any test that imports or asserts on `_extract_cvss_score` or `cvss_score` from a normalize call. Either delete the test or rewrite it to assert the field is absent:

```bash
docker compose exec ingestion grep -n "cvss_score\|_extract_cvss_score" tests/test_normalizer.py
```

For each match, decide:
- Test of `_extract_cvss_score` directly → delete the test
- Test that calls `normalize_cisa_advisory` and asserts `cvss_score` → change to `assert "cvss_score" not in result` (or just delete the assertion)

- [ ] **Step 5: Run normalizer tests, verify they pass**

```bash
docker compose exec ingestion pytest tests/test_normalizer.py -v
```

Expected: all pass (or pass after the test updates above).

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/normalizer.py tests/test_normalizer.py
git commit -m "normalizer: drop _extract_cvss_score; cve_topics is authoritative"
```

---

## Task 8: `sync_cisa_kev.py` writes KEV flag to `cve_topics`

**Files:**
- Modify: `scripts/sync_cisa_kev.py`

Right now the KEV sync only writes to Postgres `cisa_kev` table. Add a second write so `cve_topics.cisa_kev` and `cve_topics.kev_added_at` are populated from the same source.

- [ ] **Step 1: Add OpenSearch import at top of `scripts/sync_cisa_kev.py`**

After the existing imports:

```python
from app.db.opensearch import INDEX_CVE_TOPICS, get_os_client
from app.db.os_write_once import upsert_immutable
```

- [ ] **Step 2: Add a function that writes KEV flags to cve_topics**

Add near the bottom of the module, just above `main`:

```python
async def _sync_to_cve_topics(rows: list[dict]) -> int:
    """Mark cisa_kev=true + kev_added_at on cve_topics. Write-once for kev_added_at."""
    client = get_os_client()
    written = 0
    try:
        for row in rows:
            cve_id = row["cve_id"]
            kev_added_at = row["date_added"].isoformat() if row["date_added"] else None
            try:
                await upsert_immutable(
                    client=client,
                    index=INDEX_CVE_TOPICS,
                    doc_id=cve_id,
                    immutable_fields={"kev_added_at": kev_added_at} if kev_added_at else {},
                    mutable_fields={"cisa_kev": True},
                )
                written += 1
            except Exception:
                logger.exception("Failed to set KEV on cve_topics for %s", cve_id)
    finally:
        await client.close()
    return written
```

- [ ] **Step 3: Call it from `main` after the Postgres sync**

In `main()`, after the `kev_ins, kev_upd, vendor_ups = asyncio.run(_sync_to_db(rows))` line, add:

```python
    cve_topic_updates = asyncio.run(_sync_to_cve_topics(rows))
    logger.info("cve_topics KEV flags: %d updated", cve_topic_updates)
```

- [ ] **Step 4: Dry-run smoke test**

```bash
docker compose exec ingestion python scripts/sync_cisa_kev.py --dry-run
```

Expected: prints unique vendor / CVE counts, exits 0 without touching OpenSearch.

- [ ] **Step 5: Commit**

```bash
git add scripts/sync_cisa_kev.py
git commit -m "sync_cisa_kev: propagate kev flag to cve_topics via write-once helper"
```

---

## Task 9: Alembic migration — protect API-sourced Postgres rows

**Files:**
- Create: `alembic/versions/<hash>_protect_api_sourced_rows.py`

Mirror the existing `ner_cache_protect_api_rows` trigger pattern (see `c4d5e6f7a8b9_protect_ner_cache_api_rows.py`) for `cisa_kev` and `entity_intel`.

- [ ] **Step 1: Generate the migration scaffold**

```bash
docker compose exec ingestion alembic revision -m "protect API-sourced rows"
```

Note the revision id printed (e.g., `f1a2b3c4d5e6`). The file appears at `alembic/versions/<id>_protect_api_sourced_rows.py`.

- [ ] **Step 2: Replace its contents**

Open the new file and replace with:

```python
"""protect API-sourced rows in cisa_kev and entity_intel

Revision ID: <PASTE_REVISION_ID>
Revises: c4d5e6f7a8b9
Create Date: 2026-05-21

cisa_kev: vulnerability_name, date_added, cwes are write-once after first sync.
entity_intel rows with source='mitre_attack': display_name, entity_type write-once.
Same hard-stop pattern as ner_cache_protect_api_rows_trg.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "<PASTE_REVISION_ID>"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # cisa_kev: protect immutable columns after first sync
    op.execute("""
        CREATE OR REPLACE FUNCTION cisa_kev_protect_immutable_cols()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.vulnerability_name IS DISTINCT FROM OLD.vulnerability_name
               OR NEW.date_added IS DISTINCT FROM OLD.date_added
               OR NEW.cwes::text IS DISTINCT FROM OLD.cwes::text THEN
                RAISE EXCEPTION
                    'cisa_kev row (cve_id=%) has API-immutable columns; mutate via DELETE + INSERT only',
                    OLD.cve_id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER cisa_kev_protect_immutable_cols_trg
        BEFORE UPDATE ON cisa_kev
        FOR EACH ROW
        EXECUTE FUNCTION cisa_kev_protect_immutable_cols();
    """)

    # entity_intel: protect mitre_attack-sourced rows
    op.execute("""
        CREATE OR REPLACE FUNCTION entity_intel_protect_mitre_rows()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.source = 'mitre_attack' AND (
                NEW.display_name IS DISTINCT FROM OLD.display_name
                OR NEW.entity_type IS DISTINCT FROM OLD.entity_type
            ) THEN
                RAISE EXCEPTION
                    'entity_intel row (key=%) is mitre_attack-sourced; display_name and entity_type are immutable',
                    OLD.normalized_key
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER entity_intel_protect_mitre_rows_trg
        BEFORE UPDATE ON entity_intel
        FOR EACH ROW
        EXECUTE FUNCTION entity_intel_protect_mitre_rows();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS cisa_kev_protect_immutable_cols_trg ON cisa_kev")
    op.execute("DROP FUNCTION IF EXISTS cisa_kev_protect_immutable_cols()")
    op.execute("DROP TRIGGER IF EXISTS entity_intel_protect_mitre_rows_trg ON entity_intel")
    op.execute("DROP FUNCTION IF EXISTS entity_intel_protect_mitre_rows()")
```

Replace `<PASTE_REVISION_ID>` (in two places) with the id from Step 1.

- [ ] **Step 3: Run the migration**

```bash
docker compose exec ingestion alembic upgrade head
```

Expected: logs `Running upgrade c4d5e6f7a8b9 -> <new_id>, protect API-sourced rows`. No errors.

- [ ] **Step 4: Smoke test the cisa_kev trigger**

```bash
docker compose exec postgres psql -U kiber -d kiber -c "UPDATE cisa_kev SET vulnerability_name = 'TEST' WHERE cve_id = (SELECT cve_id FROM cisa_kev LIMIT 1);"
```

Expected: `ERROR: cisa_kev row (cve_id=...) has API-immutable columns; mutate via DELETE + INSERT only`.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/*_protect_api_sourced_rows.py
git commit -m "alembic: write-once triggers for cisa_kev and entity_intel mitre rows"
```

---

## Task 10: One-shot migration `entities` + `nvd_cache` → `cve_topics`

**Files:**
- Create: `scripts/migrate_cve_intel_to_topics.py`

- [ ] **Step 1: Write the migration script**

Create `scripts/migrate_cve_intel_to_topics.py`:

```python
#!/usr/bin/env python
"""One-shot: migrate CVE intelligence from `entities` (+ `nvd_cache`) into `cve_topics`.

For every CVE-type entity, copies cvss_score/severity/vector/cwe_ids/cisa_kev/
nvd_last_modified/vuln_status into cve_topics. Pulls the raw NVD blob from
nvd_cache and embeds as cve_topics.nvd_raw. Write-once: never overwrites existing
cve_topics fields.

Idempotent — safe to re-run. Counts before/after.

Usage:
    docker compose exec ingestion python scripts/migrate_cve_intel_to_topics.py
    docker compose exec ingestion python scripts/migrate_cve_intel_to_topics.py --dry-run
"""
import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.db.opensearch import INDEX_CVE_TOPICS, INDEX_ENTITIES, INDEX_NVD_CACHE, get_os_client, close_os_client
from app.db.os_write_once import upsert_immutable

logger = logging.getLogger(__name__)

_FIELDS = ["cvss_score", "cvss_severity", "cvss_vector", "cwe_ids", "cisa_kev", "vuln_status", "nvd_last_modified"]


async def _scroll_cve_entities(client):
    resp = await client.search(
        index=INDEX_ENTITIES,
        body={
            "query": {"term": {"type": "cve"}},
            "size": 200,
            "_source": ["name", *_FIELDS],
        },
        scroll="2m",
    )
    scroll_id = resp["_scroll_id"]
    results = []
    while True:
        hits = resp["hits"]["hits"]
        if not hits:
            break
        results.extend(hits)
        resp = await client.scroll(scroll_id=scroll_id, scroll="2m")
    try:
        await client.clear_scroll(scroll_id=scroll_id)
    except Exception:
        pass
    return results


async def _get_nvd_raw(client, cve_id: str):
    try:
        resp = await client.get(index=INDEX_NVD_CACHE, id=cve_id, _source=["nvd_raw"])
        return resp["_source"].get("nvd_raw")
    except Exception:
        return None


async def run(args: argparse.Namespace) -> None:
    client = get_os_client()
    hits = await _scroll_cve_entities(client)
    logger.info("Found %d CVE entities to migrate", len(hits))

    if args.dry_run:
        sample = [h["_source"].get("name") for h in hits[:5]]
        logger.info("Dry run — first 5 CVEs: %s", sample)
        return

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    migrated = nvd_raw_attached = skipped = 0

    for h in hits:
        src = h["_source"]
        cve_id = (src.get("name") or "").upper()
        if not cve_id.startswith("CVE-"):
            skipped += 1
            continue
        immutable = {k: src[k] for k in _FIELDS if src.get(k) is not None}
        nvd_raw = await _get_nvd_raw(client, cve_id)
        if nvd_raw is not None:
            immutable["nvd_raw"] = nvd_raw
            nvd_raw_attached += 1
        immutable["enriched_at"] = now_iso
        try:
            await upsert_immutable(
                client=client,
                index=INDEX_CVE_TOPICS,
                doc_id=cve_id,
                immutable_fields=immutable,
                mutable_fields={"updated_at": now_iso},
            )
            migrated += 1
        except Exception:
            logger.exception("Failed to migrate %s", cve_id)
            skipped += 1

    logger.info("Done. migrated=%d  nvd_raw_attached=%d  skipped=%d", migrated, nvd_raw_attached, skipped)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Migrate CVE intel from entities + nvd_cache into cve_topics")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    finally:
        asyncio.run(close_os_client())


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Dry-run to confirm script loads and finds entities**

```bash
docker compose exec ingestion python scripts/migrate_cve_intel_to_topics.py --dry-run
```

Expected: logs `Found N CVE entities to migrate` and a sample of 5 CVE IDs.

- [ ] **Step 3: Commit**

```bash
git add scripts/migrate_cve_intel_to_topics.py
git commit -m "add migrate_cve_intel_to_topics.py one-shot"
```

---

## Task 11: Run the pipeline (Phase 5)

This task is **execution**, not code. Each step is a command that must complete cleanly before the next.

- [ ] **Step 1: Run the entities → cve_topics migration for real**

```bash
docker compose exec ingestion python scripts/migrate_cve_intel_to_topics.py
```

Expected: logs `Done. migrated=N  nvd_raw_attached=M  skipped=0`. N should match the count of CVE entities with NVD data (verified earlier: 600-1000 range).

- [ ] **Step 2: Spot-check that cve_topics has CVSS data**

```bash
docker compose exec ingestion python -c "
import asyncio
from app.db.opensearch import INDEX_CVE_TOPICS, get_os_client, close_os_client

async def go():
    client = get_os_client()
    resp = await client.search(index=INDEX_CVE_TOPICS, body={
        'query': {'exists': {'field': 'cvss_score'}},
        'size': 3,
        '_source': ['cvss_score', 'cvss_severity']
    })
    for h in resp['hits']['hits']:
        print(h['_id'], h['_source'])
    await close_os_client()

asyncio.run(go())
"
```

Expected: 3 lines like `CVE-2026-20223 {'cvss_score': 10.0, 'cvss_severity': 'CRITICAL'}`.

- [ ] **Step 3: Rebuild the ingestion image with the new code**

```bash
docker compose build ingestion && docker compose up -d ingestion
```

- [ ] **Step 4: Copy the cluster_articles.py into the running container (per CLAUDE.md workflow)**

```bash
docker cp scripts/cluster_articles.py kiber-ingestion-1:/app/scripts/cluster_articles.py
```

- [ ] **Step 5: Full cluster `--reset` rebuild**

```bash
docker compose exec ingestion python scripts/cluster_articles.py --reset
```

Expected runtime: ~13 minutes (per CLAUDE.md baseline). Final log line: `Done. Clustered N articles into M clusters.`

- [ ] **Step 6: API sanity-check — clusters now have real `max_cvss`**

```bash
docker compose exec ingestion curl -s "http://localhost:8000/api/feed?sort=severity&limit=5" | python -m json.tool | grep -E "id|max_cvss"
```

Expected: top 5 clusters have `max_cvss` values like 10.0, 9.8, 9.1 — not 0.

- [ ] **Step 7: API sanity-check — articles have `cvss_score` + `severity`**

```bash
docker compose exec ingestion curl -s "http://localhost:8000/api/search/?q=CVE&limit=5" | python -m json.tool | grep -E "slug|cvss_score|severity"
```

Expected: a meaningful fraction of articles have non-null `cvss_score` and `severity`.

- [ ] **Step 8: Browser verification**

Open `http://localhost/` and:

1. Click the **Severity** sort pill.
2. Expect top cards to show `CVSS 9.x` or `10.0` badges (not 0.0). Empty CVSS clusters no longer show a CVSS badge at all.
3. Expect colored severity tags (critical/high/medium/low) on cards where the top article has a known CVE.

Take a screenshot.

- [ ] **Step 9: If verification passes, no commit needed (no code changes in this task). If it fails, diagnose before merging.**

---

## Task 12: Final review + PR

- [ ] **Step 1: Confirm all tests pass**

```bash
docker compose exec ingestion pytest tests/ -x --tb=short
```

Expected: full suite green.

- [ ] **Step 2: Run a quick lint pass on new files**

```bash
docker compose exec ingestion ruff check app/db/os_write_once.py app/ingestion/cve_intel.py scripts/migrate_cve_intel_to_topics.py
```

Fix any issues found.

- [ ] **Step 3: Skim the git log**

```bash
git log --oneline main..HEAD
```

Expected: ~10 commits, each scoped to one task.

- [ ] **Step 4: Open PR**

Title: `feat: wire CVSS through to articles and clusters; write-once API cache`

Body: link to the spec, list the 12 tasks, paste browser screenshot from Task 11 Step 8.

---

## Self-Review Notes

- **Spec coverage** — All five phases covered: Phase 1 (Tasks 1, 4, 5, 10); Phase 2 (Tasks 3, 6, 7); Phase 3 (Tasks 2, 8, 9); Phase 4 (already shipped); Phase 5 (Task 11). Verified.
- **Placeholder scan** — One intentional `<PASTE_REVISION_ID>` in Task 9 (Alembic generates this at scaffold time). All other steps have concrete code.
- **Type consistency** — `lookup_cve_intel` returns `dict[str, dict]` keyed by uppercase CVE ID throughout. `upsert_immutable` signature matches its callers in `enrich_cve_nvd.py`, `sync_cisa_kev.py`, and `migrate_cve_intel_to_topics.py`.
- **`_apply_cve_intel` placement** — Inserted after tag classification but before `upsert_article` so `cvss_score` and `severity` land in the doc on first index.
