# Clustering False Merge Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop unrelated articles from merging into the same cluster by filtering generic vendor entities from clustering signals and capping CVE-based matching for roundup articles.

**Architecture:** Two constants drive the fix — `_SIGNAL_TYPES` (excludes `vendor` from entity matching) and `_MAX_ARTICLE_CVES_FOR_MATCHING = 3` (skips CVE matching for roundup articles). `cluster_article()` changes signature to accept full entity dicts instead of string keys, deriving two lists internally: all keys for storage, signal keys for matching.

**Tech Stack:** Python 3.12, pytest, pytest-asyncio, unittest.mock

---

## File Map

| File | Change |
|---|---|
| `app/ingestion/clusterer.py` | Add constants + helper, update `cluster_article()` signature and logic |
| `app/ingestion/ingester.py` | Update one call site: pass `entities` list instead of `entity_keys` list |
| `tests/test_clusterer.py` | Add tests for signal key filtering and CVE cap; update existing `cluster_article` test |

---

### Task 1: Add `_signal_keys()` helper and tests

The filtering logic needs to live somewhere testable in isolation before wiring it into `cluster_article`.

**Files:**
- Modify: `app/ingestion/clusterer.py` (add after imports, before finders)
- Modify: `tests/test_clusterer.py` (add tests at top of file)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_clusterer.py` after the existing imports:

```python
# ---------------------------------------------------------------------------
# _signal_keys helper
# ---------------------------------------------------------------------------

def test_signal_keys_excludes_vendors():
    from app.ingestion.clusterer import _signal_keys

    entities = [
        {"type": "vendor",  "name": "Microsoft",  "normalized_key": "microsoft"},
        {"type": "vendor",  "name": "Google",      "normalized_key": "google"},
        {"type": "product", "name": "FortiGate",   "normalized_key": "fortigate"},
        {"type": "malware", "name": "LockBit",     "normalized_key": "lockbit"},
        {"type": "actor",   "name": "Lazarus Group","normalized_key": "lazarus-group"},
        {"type": "tool",    "name": "Mimikatz",    "normalized_key": "mimikatz"},
        {"type": "cve",     "name": "CVE-2026-1234","normalized_key": "cve-2026-1234"},
    ]

    result = _signal_keys(entities)

    assert "microsoft" not in result
    assert "google" not in result
    assert "fortigate" in result
    assert "lockbit" in result
    assert "lazarus-group" in result
    assert "mimikatz" in result
    assert "cve-2026-1234" in result


def test_signal_keys_empty_input():
    from app.ingestion.clusterer import _signal_keys
    assert _signal_keys([]) == []


def test_signal_keys_all_vendors_returns_empty():
    from app.ingestion.clusterer import _signal_keys

    entities = [
        {"type": "vendor", "name": "Apple",  "normalized_key": "apple"},
        {"type": "vendor", "name": "Signal", "normalized_key": "signal"},
    ]
    assert _signal_keys(entities) == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /path/to/kiber
pytest tests/test_clusterer.py::test_signal_keys_excludes_vendors tests/test_clusterer.py::test_signal_keys_empty_input tests/test_clusterer.py::test_signal_keys_all_vendors_returns_empty -v
```

Expected: `ImportError` or `AttributeError: module has no attribute '_signal_keys'`

- [ ] **Step 3: Add the constant and helper to `clusterer.py`**

In `app/ingestion/clusterer.py`, add after the `_MLT_SCORE_THRESHOLD` line (around line 22):

```python
# Entity types specific enough to use as cluster-matching signals.
# "vendor" is intentionally excluded — names like Microsoft, Apache, Google
# appear in almost every security article and cause false merges.
_SIGNAL_TYPES = frozenset({"cve", "product", "malware", "actor", "tool"})

# Articles with more than this many CVEs are roundups (Patch Tuesday, KEV batch).
# Skip CVE-based cluster matching for them to prevent sweep-in of unrelated articles.
_MAX_ARTICLE_CVES_FOR_MATCHING = 3


def _signal_keys(entities: list[dict]) -> list[str]:
    """Return normalized keys for entities whose type is a high-signal cluster indicator."""
    return [e["normalized_key"] for e in entities if e["type"] in _SIGNAL_TYPES]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_clusterer.py::test_signal_keys_excludes_vendors tests/test_clusterer.py::test_signal_keys_empty_input tests/test_clusterer.py::test_signal_keys_all_vendors_returns_empty -v
```

Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/clusterer.py tests/test_clusterer.py
git commit -m "Add _signal_keys helper and _SIGNAL_TYPES, _MAX_ARTICLE_CVES_FOR_MATCHING constants"
```

---

### Task 2: Update `cluster_article()` — new signature, signal key filtering, CVE cap

**Files:**
- Modify: `app/ingestion/clusterer.py` — `cluster_article()` function only
- Modify: `tests/test_clusterer.py` — update existing test + add two new tests

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_clusterer.py` after the existing `test_cluster_article_cve_match_merges` test. Also update the existing test (the signature change will break it):

First, update the **existing** `test_cluster_article_cve_match_merges` — change the call from passing a list of strings to a list of entity dicts:

```python
@pytest.mark.asyncio
async def test_cluster_article_cve_match_merges():
    from app.ingestion.clusterer import cluster_article

    article = {
        "slug": "test-article-001",
        "title": "Critical FortiOS RCE",
        "source_name": "BleepingComputer",
        "published_at": "2026-03-19T10:00:00+00:00",
        "cve_ids": ["CVE-2026-1234"],
        "summary": "A critical vulnerability...",
        "desc": "A critical vulnerability...",
        "category": "vulnerability",
    }
    entities = [
        {"type": "product", "name": "FortiOS",  "normalized_key": "fortios"},
        {"type": "vendor",  "name": "Fortinet", "normalized_key": "fortinet"},
    ]

    with patch("app.ingestion.clusterer.find_cluster_by_cve", new_callable=AsyncMock) as mock_cve, \
         patch("app.ingestion.clusterer.merge_into_cluster", new_callable=AsyncMock) as mock_merge, \
         patch("app.ingestion.clusterer.create_cluster", new_callable=AsyncMock) as mock_create:

        mock_cve.return_value = "cluster-existing"

        await cluster_article(article, "test-article-001", entities)

        mock_cve.assert_awaited_once_with(["CVE-2026-1234"])
        mock_merge.assert_awaited_once()
        merge_args = mock_merge.call_args
        assert merge_args[0][0] == "cluster-existing"
        assert merge_args[0][1] == "test-article-001"
        mock_create.assert_not_awaited()
```

Then add these two **new** tests after it:

```python
@pytest.mark.asyncio
async def test_cluster_article_skips_cve_match_for_roundup():
    """Articles with >3 CVEs (roundups) must not trigger CVE-based cluster lookup."""
    from app.ingestion.clusterer import cluster_article

    article = {
        "slug": "patch-tuesday-001",
        "title": "Patch Tuesday, April 2026 Edition",
        "source_name": "Krebs on Security",
        "published_at": "2026-04-08T10:00:00+00:00",
        "cve_ids": ["CVE-2026-001", "CVE-2026-002", "CVE-2026-003", "CVE-2026-004"],
        "summary": "Microsoft patched 80 vulnerabilities...",
        "desc": "Microsoft patched 80 vulnerabilities...",
        "category": "vulnerability",
    }
    entities = [
        {"type": "vendor", "name": "Microsoft", "normalized_key": "microsoft"},
    ]

    with patch("app.ingestion.clusterer.find_cluster_by_cve", new_callable=AsyncMock) as mock_cve, \
         patch("app.ingestion.clusterer.find_cluster_by_entities", new_callable=AsyncMock) as mock_ent, \
         patch("app.ingestion.clusterer.find_cluster_by_mlt", new_callable=AsyncMock) as mock_mlt, \
         patch("app.ingestion.clusterer.create_cluster", new_callable=AsyncMock), \
         patch("app.ingestion.clusterer.merge_into_cluster", new_callable=AsyncMock):

        mock_cve.return_value = None
        mock_ent.return_value = None
        mock_mlt.return_value = None

        await cluster_article(article, "patch-tuesday-001", entities)

        # CVE lookup must be skipped entirely for a 4-CVE article
        mock_cve.assert_not_awaited()


@pytest.mark.asyncio
async def test_cluster_article_vendor_entities_not_used_for_matching():
    """Vendor-type entities must be excluded from entity overlap matching."""
    from app.ingestion.clusterer import cluster_article

    article = {
        "slug": "generic-article-001",
        "title": "Security Roundup",
        "source_name": "Dark Reading",
        "published_at": "2026-04-08T10:00:00+00:00",
        "cve_ids": [],
        "summary": "This week in security...",
        "desc": "This week in security...",
        "category": "research",
    }
    # Only vendor-type entities — signal_keys will be empty, entity match must not fire
    entities = [
        {"type": "vendor", "name": "Microsoft", "normalized_key": "microsoft"},
        {"type": "vendor", "name": "Google",    "normalized_key": "google"},
        {"type": "vendor", "name": "Apple",     "normalized_key": "apple"},
    ]

    with patch("app.ingestion.clusterer.find_cluster_by_entities", new_callable=AsyncMock) as mock_ent, \
         patch("app.ingestion.clusterer.find_cluster_by_cve", new_callable=AsyncMock) as mock_cve, \
         patch("app.ingestion.clusterer.find_cluster_by_mlt", new_callable=AsyncMock) as mock_mlt, \
         patch("app.ingestion.clusterer.create_cluster", new_callable=AsyncMock), \
         patch("app.ingestion.clusterer.merge_into_cluster", new_callable=AsyncMock):

        mock_cve.return_value = None
        mock_ent.return_value = None
        mock_mlt.return_value = None

        await cluster_article(article, "generic-article-001", entities)

        # Entity lookup must be skipped — no signal keys available
        mock_ent.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_clusterer.py::test_cluster_article_cve_match_merges tests/test_clusterer.py::test_cluster_article_skips_cve_match_for_roundup tests/test_clusterer.py::test_cluster_article_vendor_entities_not_used_for_matching -v
```

Expected: all 3 FAIL — `test_cluster_article_cve_match_merges` fails with `TypeError` (wrong signature), the two new tests fail because the logic doesn't exist yet.

- [ ] **Step 3: Update `cluster_article()` in `clusterer.py`**

Replace the entire `cluster_article` function (lines 323–370) with:

```python
async def cluster_article(
    article: NormalizedArticle,
    slug: str,
    entities: list[dict],
) -> None:
    """Assign an article to a cluster (existing or new).

    Decision priority:
      1. CVE overlap (strongest signal) — skipped for roundup articles (>3 CVEs)
      2. Entity overlap on high-signal entity types only (no generic vendors)
      3. Narrative similarity (MLT fallback)
      4. Create new cluster
    """
    cve_ids = article.get("cve_ids") or []

    # All keys stored on the cluster document (for display, search, entity pages)
    entity_keys = [e["normalized_key"] for e in entities]
    # High-signal keys used for matching only (vendor type excluded)
    signal_keys = _signal_keys(entities)

    cluster_id: Optional[str] = None

    # 1. CVE overlap — only for focused articles, not roundups
    if cve_ids and len(cve_ids) <= _MAX_ARTICLE_CVES_FOR_MATCHING:
        cluster_id = await find_cluster_by_cve(cve_ids)
        if cluster_id:
            logger.debug("CVE match for '%s' → cluster %s", slug, cluster_id)

    # 2. Entity overlap — use signal_keys only (vendors excluded)
    if not cluster_id and len(signal_keys) >= 2:
        cluster_id = await find_cluster_by_entities(signal_keys)
        if cluster_id:
            logger.debug("Entity match for '%s' → cluster %s", slug, cluster_id)

    # 3. Narrative similarity
    if not cluster_id:
        title = article.get("title") or ""
        summary = article.get("summary") or article.get("desc")
        cluster_id = await find_cluster_by_mlt(title, summary)
        if cluster_id:
            logger.debug("MLT match for '%s' → cluster %s", slug, cluster_id)

    # 4. Merge or create
    if cluster_id:
        raw_cvss = article.get("cvss_score")
        await merge_into_cluster(
            cluster_id, slug, entity_keys, cve_ids,
            source_name=article.get("source_name", ""),
            title=article.get("title", ""),
            published_at=article.get("published_at", ""),
            cvss_score=float(raw_cvss) if raw_cvss is not None else None,
        )
    else:
        await create_cluster(article, entity_keys)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_clusterer.py::test_cluster_article_cve_match_merges tests/test_clusterer.py::test_cluster_article_skips_cve_match_for_roundup tests/test_clusterer.py::test_cluster_article_vendor_entities_not_used_for_matching -v
```

Expected: all 3 PASS

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
pytest tests/test_clusterer.py -v
```

Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/clusterer.py tests/test_clusterer.py
git commit -m "Fix clustering false merges: filter vendor entities, cap CVE matching at 3"
```

---

### Task 3: Update `ingester.py` call site

**Files:**
- Modify: `app/ingestion/ingester.py` lines ~310–312

- [ ] **Step 1: Find and update the call site**

In `app/ingestion/ingester.py`, find this block (around line 310):

```python
# Clustering — always attempt, even without entities
try:
    entity_keys = [e["normalized_key"] for e in entities]
    await cluster_article(article, article["slug"], entity_keys)
```

Replace with:

```python
# Clustering — always attempt, even without entities
try:
    await cluster_article(article, article["slug"], entities)
```

- [ ] **Step 2: Run the ingester tests**

```bash
pytest tests/test_ingester.py -v
```

Expected: all tests PASS (the ingester tests mock `cluster_article`, so the signature change is transparent to them — but verify anyway)

- [ ] **Step 3: Run the full test suite**

```bash
pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
git add app/ingestion/ingester.py
git commit -m "Update ingester to pass entity dicts to cluster_article"
```

---

### Task 4: Manual smoke test

Verify the fix works end-to-end by re-ingesting a sample and checking cluster separation.

- [ ] **Step 1: Check current mega-cluster sizes for baseline**

```bash
docker exec kiber-backend-1 python3 -c "
import asyncio
from app.db.opensearch import get_os_client, INDEX_CLUSTERS

async def main():
    client = get_os_client()
    resp = await client.search(index=INDEX_CLUSTERS, body={
        'query': {'match_all': {}},
        'size': 5,
        'sort': [{'article_count': 'desc'}],
        '_source': ['label', 'article_count', 'entity_keys']
    })
    for h in resp['hits']['hits']:
        s = h['_source']
        print(f'[{s[\"article_count\"]}src] {s.get(\"label\",\"\")[:60]}')
        print(f'  keys: {s.get(\"entity_keys\",[])[:6]}')
asyncio.run(main())
"
```

- [ ] **Step 2: Rebuild and restart backend**

```bash
docker compose build --no-cache backend && docker compose up -d backend
```

- [ ] **Step 3: Trigger a fresh ingestion run**

```bash
docker exec kiber-backend-1 python3 -c "
import asyncio
from app.ingestion.ingester import ingest_all_feeds
asyncio.run(ingest_all_feeds())
"
```

- [ ] **Step 4: Verify new articles land in separate clusters**

After ingestion, check that new articles about unrelated topics are not being merged. A quick sanity check — confirm no new clusters with >15 sources that have only generic vendor entity keys:

```bash
docker exec kiber-backend-1 python3 -c "
import asyncio
from app.db.opensearch import get_os_client, INDEX_CLUSTERS

async def main():
    client = get_os_client()
    resp = await client.search(index=INDEX_CLUSTERS, body={
        'query': {'range': {'article_count': {'gte': 10}}},
        'size': 20,
        'sort': [{'article_count': 'desc'}],
        '_source': ['label', 'article_count', 'entity_keys', 'created_at']
    })
    for h in resp['hits']['hits']:
        s = h['_source']
        print(f'[{s[\"article_count\"]}src] {s.get(\"label\",\"\")[:65]}')
        print(f'  keys: {s.get(\"entity_keys\",[])[:5]}')
asyncio.run(main())
"
```

Expected: large clusters should be legitimately related stories (Patch Tuesday, major incidents). Clusters with only generic vendor keys and unrelated article titles should be gone for new articles.
