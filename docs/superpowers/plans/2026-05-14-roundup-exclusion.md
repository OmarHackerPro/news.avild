# Roundup Exclusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tag weekly/monthly digest clusters as `is_roundup: true` at creation time and exclude them from both the daily brief and the website cluster listing.

**Architecture:** A pure `_is_roundup(label, cve_ids)` helper in `clusterer.py` detects roundups by label keyword or CVE count > 10. The boolean is written into the cluster doc in `create_cluster()`. Two query sites — `briefing/selector.py` and `api/routes/clusters.py` — each add `must_not: [{term: {is_roundup: True}}]`. Existing clusters missing the field are unaffected by the filter until the next `--reset` rebuild.

**Tech Stack:** Python 3.12, FastAPI, opensearch-py (async), pytest + pytest-asyncio, unittest.mock

---

### Task 1: `_is_roundup()` helper — write test first, then implement

**Files:**
- Modify: `tests/test_clusterer.py`
- Modify: `app/ingestion/clusterer.py`

- [ ] **Step 1: Write failing tests for `_is_roundup()`**

In `tests/test_clusterer.py`, first extend the existing import at line 5 to include `_is_roundup`:

```python
from app.ingestion.clusterer import _build_event_signature, _updated_centroid, _is_roundup
```

Then append the following tests to the file:

```python
# ---------------------------------------------------------------------------
# _is_roundup — pure heuristic
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,cve_ids,expected", [
    # keyword matches
    ("Patch Tuesday May 2026: 80 fixes", [], True),
    ("March 2026 CVE Landscape: 31 High-Impact Vulnerabilities", [], True),
    ("Weekly Digest: Top Security Stories", [], True),
    ("Monthly Roundup: April Threats", [], True),
    ("Weekly Digest Cybersecurity News", [], True),
    # CVE count threshold
    ("FortiOS RCE", [f"CVE-2026-{i:04d}" for i in range(11)], True),
    # normal articles — not a roundup
    ("FortiOS RCE CVE-2026-1234 actively exploited", ["CVE-2026-1234"], False),
    ("Lazarus Group targets financial institutions", [], False),
    # exactly 10 CVEs — not a roundup (threshold is >10)
    ("Multiple CVEs fixed", [f"CVE-2026-{i:04d}" for i in range(10)], False),
])
def test_is_roundup(label, cve_ids, expected):
    assert _is_roundup(label, cve_ids) is expected
```

- [ ] **Step 2: Run to confirm they fail**

```
pytest tests/test_clusterer.py::test_is_roundup -v
```

Expected: `ImportError` or `AttributeError` — `_is_roundup` does not exist yet.

- [ ] **Step 3: Implement `_is_roundup()` in `app/ingestion/clusterer.py`**

Add after the existing `_MAX_ARTICLE_CVES_FOR_CVE_TOPIC` constant (line 82):

```python
_ROUNDUP_KEYWORDS = frozenset([
    "patch tuesday", "monthly", "landscape", "roundup", "weekly digest",
])
_ROUNDUP_CVE_THRESHOLD = 10


def _is_roundup(label: str, cve_ids: list[str]) -> bool:
    label_lower = label.lower()
    if any(kw in label_lower for kw in _ROUNDUP_KEYWORDS):
        return True
    return len(cve_ids) > _ROUNDUP_CVE_THRESHOLD
```

- [ ] **Step 4: Run tests to confirm they pass**

```
pytest tests/test_clusterer.py::test_is_roundup -v
```

Expected: 9 PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/clusterer.py tests/test_clusterer.py
git commit -m "feat(clustering): add _is_roundup() heuristic"
```

---

### Task 2: Add `is_roundup` field to mapping and set it in `create_cluster()`

**Files:**
- Modify: `app/db/opensearch.py`
- Modify: `app/ingestion/clusterer.py`
- Modify: `tests/test_clusterer.py`

- [ ] **Step 1: Write failing test — `create_cluster()` must set `is_roundup`**

Append to `tests/test_clusterer.py`:

```python
# ---------------------------------------------------------------------------
# create_cluster — sets is_roundup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_cluster_sets_is_roundup_true_for_roundup_label():
    os_mock = AsyncMock()
    os_mock.index.return_value = {"_id": "cluster-roundup-001"}
    os_mock.update.return_value = {}

    article = {
        "slug": "patch-tuesday-may-2026",
        "title": "Patch Tuesday May 2026: 80 fixes",
        "cve_ids": [f"CVE-2026-{i:04d}" for i in range(80)],
        "published_at": "2026-05-01T10:00:00Z",
        "source_name": "Microsoft",
    }

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer._rescore", new_callable=AsyncMock):
        from app.ingestion.clusterer import create_cluster
        await create_cluster(article, [], embedding=[0.1] * 1024)

    indexed = os_mock.index.call_args.kwargs["body"]
    assert indexed["is_roundup"] is True


@pytest.mark.asyncio
async def test_create_cluster_sets_is_roundup_false_for_normal_article():
    os_mock = AsyncMock()
    os_mock.index.return_value = {"_id": "cluster-normal-001"}
    os_mock.update.return_value = {}

    article = {
        "slug": "fortios-rce-001",
        "title": "FortiOS RCE CVE-2026-1234 actively exploited",
        "cve_ids": ["CVE-2026-1234"],
        "published_at": "2026-04-27T10:00:00Z",
        "source_name": "BleepingComputer",
    }

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer._rescore", new_callable=AsyncMock):
        from app.ingestion.clusterer import create_cluster
        await create_cluster(article, [], embedding=[0.1] * 1024)

    indexed = os_mock.index.call_args.kwargs["body"]
    assert indexed["is_roundup"] is False
```

- [ ] **Step 2: Run to confirm they fail**

```
pytest tests/test_clusterer.py::test_create_cluster_sets_is_roundup_true_for_roundup_label tests/test_clusterer.py::test_create_cluster_sets_is_roundup_false_for_normal_article -v
```

Expected: `AssertionError` — `is_roundup` key not present in indexed doc.

- [ ] **Step 3: Add `is_roundup` to `_CLUSTERS_MAPPING` in `app/db/opensearch.py`**

In `_CLUSTERS_MAPPING["mappings"]["properties"]`, add after `"merged_into": {"type": "keyword"},` (before the closing `}`):

```python
"is_roundup":     {"type": "boolean"},
```

- [ ] **Step 4: Set `is_roundup` in `create_cluster()` in `app/ingestion/clusterer.py`**

In `create_cluster()`, the `doc` dict is built starting around line 318. Add `is_roundup` to it. The full `doc` dict should look like (show only the changed part — add this line alongside the existing `"state"` line):

```python
    doc = {
        "label": article.get("title", ""),
        "state": "new",
        "is_roundup": _is_roundup(article.get("title", ""), cve_ids),
        # ... rest unchanged
    }
```

Specifically, insert `"is_roundup": _is_roundup(article.get("title", ""), cve_ids),` as the third key in the `doc` dict, after `"state": "new",`.

- [ ] **Step 5: Run tests to confirm they pass**

```
pytest tests/test_clusterer.py -v
```

Expected: all tests PASSED (including the 2 new ones and all pre-existing ones).

- [ ] **Step 6: Commit**

```bash
git add app/db/opensearch.py app/ingestion/clusterer.py tests/test_clusterer.py
git commit -m "feat(clustering): set is_roundup on cluster creation"
```

---

### Task 3: Exclude roundups from the daily brief selector

**Files:**
- Modify: `tests/briefing/test_selector.py`
- Modify: `app/briefing/selector.py`

- [ ] **Step 1: Write failing test — query must contain `must_not: is_roundup: True`**

Append to `tests/briefing/test_selector.py`:

```python
@pytest.mark.asyncio
async def test_fetch_top_clusters_excludes_roundups():
    mock_client = MagicMock()
    mock_client.search = AsyncMock(return_value={
        "hits": {"hits": [], "total": {"value": 0}}
    })

    await fetch_top_clusters(mock_client, top_n=7, hours=24)

    call_body = mock_client.search.call_args.kwargs["body"]
    must_not = call_body["query"]["bool"]["must_not"]
    assert {"term": {"is_roundup": True}} in must_not
```

- [ ] **Step 2: Run to confirm it fails**

```
pytest tests/briefing/test_selector.py::test_fetch_top_clusters_excludes_roundups -v
```

Expected: `KeyError` or `AssertionError` — query has no `bool` with `must_not`.

- [ ] **Step 3: Update `fetch_top_clusters()` in `app/briefing/selector.py`**

Replace the current `body` dict in `fetch_top_clusters()`:

```python
    body = {
        "size": top_n,
        "_source": _SOURCE_FIELDS,
        "query": {
            "bool": {
                "filter": [{"range": {"latest_at": {"gte": f"now-{hours}h"}}}],
                "must_not": [{"term": {"is_roundup": True}}],
            }
        },
        "sort": [{"score": {"order": "desc"}}],
    }
```

- [ ] **Step 4: Run all selector tests**

```
pytest tests/briefing/test_selector.py -v
```

Expected: all 3 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add app/briefing/selector.py tests/briefing/test_selector.py
git commit -m "feat(brief): exclude roundup clusters from brief selection"
```

---

### Task 4: Exclude roundups from the website cluster listing

**Files:**
- Modify: `app/api/routes/clusters.py`

- [ ] **Step 1: Update `list_clusters()` in `app/api/routes/clusters.py`**

The query is built at line 83. Currently it's:

```python
    body: dict = {
        "query": {"bool": {"filter": filters}} if filters else {"match_all": {}},
        "sort": [{"created_at": {"order": "desc"}}],
        "from": offset,
        "size": limit,
    }
```

Replace with:

```python
    body: dict = {
        "query": {
            "bool": {
                "filter": filters,
                "must_not": [{"term": {"is_roundup": True}}],
            }
        },
        "sort": [{"created_at": {"order": "desc"}}],
        "from": offset,
        "size": limit,
    }
```

Note: `filter: []` (empty list) is valid in OpenSearch `bool` query — it matches all documents, equivalent to the previous `match_all`. No conditional needed.

- [ ] **Step 2: Run the full test suite to check for regressions**

```
pytest tests/ -v --tb=short
```

Expected: all existing tests PASSED, no new failures.

- [ ] **Step 3: Commit**

```bash
git add app/api/routes/clusters.py
git commit -m "feat(api): exclude roundup clusters from /api/clusters/ listing"
```

---

## After All Tasks

The `is_roundup` field is live in the mapping (applied by `ensure_indexes()` on next container restart). New clusters created after the restart will have `is_roundup` set. Existing roundup clusters remain visible until the next `--reset` rebuild — that is expected behavior per the design.

To apply to existing data, run the full rebuild (requires explicit permission):
```bash
docker compose exec ingestion python scripts/cluster_articles.py --reset
```
