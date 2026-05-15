# Advisory Content Type Taxonomy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `content_type` field to articles so that ICS advisories, KEV catalog entries, and product advisories (Cisco, MSRC) are routed through the clustering pipeline differently — ICS advisories get their own hidden clusters, KEV entries annotate existing clusters, and product advisories merge without seeding new clusters.

**Architecture:** `_infer_content_type()` in `normalizer.py` sets `content_type` on each article based on source/title heuristics. The ingester writes it to OpenSearch. `cluster_article()` in `clusterer.py` branches on `content_type` before calling `create_cluster()` or `merge_into_cluster()`. A new `is_advisory: bool` field on cluster docs mirrors the `is_roundup` pattern to hide ICS clusters from the main API listing. JPCERT/CC (Japanese-language source) is disabled separately via a cleanup script.

**Tech Stack:** Python 3.12, FastAPI, opensearch-py (async), PostgreSQL/SQLAlchemy (async), pytest + pytest-asyncio, unittest.mock

**Spec:** `docs/superpowers/specs/2026-05-15-advisory-taxonomy-design.md`

---

## File map

| File | Change |
|---|---|
| `scripts/cleanup_jpcert.py` | New — disables JPCERT in Postgres, deletes articles + solo clusters from OpenSearch |
| `app/ingestion/sources.py` | Add comment to JPCERT entry noting it is intentionally disabled in DB |
| `app/db/opensearch.py` | Add `content_type: keyword` to `NEWS_MAPPING`; add `is_advisory: boolean` to `_CLUSTERS_MAPPING` |
| `app/ingestion/normalizer.py` | Add `_KEV_TITLE_RE` regex and `_infer_content_type()` function |
| `app/ingestion/ingester.py` | Import `_infer_content_type`; set `article["content_type"]` after normalization |
| `app/ingestion/clusterer.py` | Add `_mark_kev_clusters()`; update `cluster_article()` routing; set `is_advisory` in `create_cluster()` |
| `app/api/routes/clusters.py` | Add `must_not: [{term: {is_advisory: True}}]` to `list_clusters()` query |
| `tests/test_normalizer.py` | Add `TestInferContentType` class |
| `tests/test_clusterer.py` | Add tests for `kev_catalog` routing, `product_advisory` routing, `is_advisory` flag in `create_cluster()` |
| `tests/briefing/test_selector.py` | No change needed — `is_roundup` filter already present; `is_advisory` only affects `/api/clusters/` |

---

## Task 1: JPCERT cleanup script

**Files:**
- Create: `scripts/cleanup_jpcert.py`
- Modify: `app/ingestion/sources.py`

- [ ] **Step 1: Write `scripts/cleanup_jpcert.py`**

```python
#!/usr/bin/env python
"""Disable JPCERT/CC source and remove its articles and solo clusters.

Usage (inside container):
    python scripts/cleanup_jpcert.py --dry-run   # preview counts only
    python scripts/cleanup_jpcert.py             # apply changes

Safe to re-run: Postgres UPDATE is idempotent; OpenSearch deletes are no-ops
if documents are already gone.
"""
import asyncio
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
if Path(".env").exists():
    from dotenv import load_dotenv
    load_dotenv()

from sqlalchemy import update as sa_update

from app.db.models.feed_source import FeedSource as FeedSourceModel
from app.db.session import AsyncSessionLocal
from app.db.opensearch import INDEX_NEWS, INDEX_CLUSTERS, get_os_client

SOURCE_NAME = "JPCERT/CC"
logger = logging.getLogger(__name__)


async def run(*, dry_run: bool) -> None:
    os_client = get_os_client()

    # --- 1. Find all JPCERT article slugs ---
    resp = await os_client.search(
        index=INDEX_NEWS,
        body={
            "query": {"term": {"source_name": SOURCE_NAME}},
            "_source": False,
            "size": 10000,
        },
    )
    jpcert_slugs = {h["_id"] for h in resp["hits"]["hits"]}
    logger.info("Found %d JPCERT articles", len(jpcert_slugs))

    # --- 2. Find solo clusters whose only article is a JPCERT slug ---
    cluster_resp = await os_client.search(
        index=INDEX_CLUSTERS,
        body={
            "query": {"term": {"article_count": 1}},
            "_source": ["article_ids"],
            "size": 10000,
        },
    )
    solo_cluster_ids = [
        h["_id"]
        for h in cluster_resp["hits"]["hits"]
        if h["_source"].get("article_ids", []) and
           h["_source"]["article_ids"][0] in jpcert_slugs
    ]
    logger.info("Found %d solo clusters to delete", len(solo_cluster_ids))

    if dry_run:
        logger.info("[DRY RUN] Would delete %d articles and %d clusters", len(jpcert_slugs), len(solo_cluster_ids))
        logger.info("[DRY RUN] Would set is_active=False for '%s' in Postgres", SOURCE_NAME)
        return

    # --- 3. Delete solo clusters ---
    for cid in solo_cluster_ids:
        try:
            await os_client.delete(index=INDEX_CLUSTERS, id=cid)
        except Exception as e:
            logger.warning("Could not delete cluster %s: %s", cid, e)
    logger.info("Deleted %d clusters", len(solo_cluster_ids))

    # --- 4. Delete articles ---
    if jpcert_slugs:
        await os_client.delete_by_query(
            index=INDEX_NEWS,
            body={"query": {"term": {"source_name": SOURCE_NAME}}},
        )
    logger.info("Deleted %d articles", len(jpcert_slugs))

    # --- 5. Disable source in Postgres ---
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(
                sa_update(FeedSourceModel)
                .where(FeedSourceModel.name == SOURCE_NAME)
                .values(is_active=False)
            )
    logger.info("Set is_active=False for '%s' in Postgres", SOURCE_NAME)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run))
```

- [ ] **Step 2: Add comment to JPCERT entry in `app/ingestion/sources.py`**

Find the JPCERT entry in `SEED_SOURCES` (around line 281). Replace the existing comment with a more specific one:

```python
    # JPCERT/CC: disabled 2026-05-15 — content is primarily Japanese.
    # Source remains here for historical record. is_active=False in DB (set by scripts/cleanup_jpcert.py).
    # Do NOT re-enable without adding translation support.
    FeedSource(
        name="JPCERT/CC",
        url="https://www.jpcert.or.jp/rss/jpcert.rdf",
        default_type="advisory",
        default_category="breaking",
        default_severity=None,
        normalizer="generic",
        credibility_weight=1.3,
        extract_cves=True,
    ),
```

- [ ] **Step 3: Run dry-run to confirm counts (run inside ingestion container)**

```bash
docker compose exec ingestion python scripts/cleanup_jpcert.py --dry-run
```

Expected output: something like:
```
Found 37 JPCERT articles
Found 37 solo clusters to delete
[DRY RUN] Would delete 37 articles and 37 clusters
[DRY RUN] Would set is_active=False for 'JPCERT/CC' in Postgres
```

- [ ] **Step 4: Run the actual cleanup**

```bash
docker compose exec ingestion python scripts/cleanup_jpcert.py
```

Expected: no errors, counts match dry-run.

- [ ] **Step 5: Commit**

```bash
git add scripts/cleanup_jpcert.py app/ingestion/sources.py
git commit -m "feat(sources): disable JPCERT/CC and remove Japanese articles"
```

---

## Task 2: Add `content_type` to article mapping and write `_infer_content_type()`

**Files:**
- Modify: `app/db/opensearch.py`
- Modify: `app/ingestion/normalizer.py`
- Modify: `tests/test_normalizer.py`

- [ ] **Step 1: Write failing tests for `_infer_content_type()`**

Append to `tests/test_normalizer.py`:

```python
import pytest
from app.ingestion.normalizer import _infer_content_type


class TestInferContentType:
    @pytest.mark.parametrize("title,normalizer_key,source_name,expected", [
        # KEV catalog — title pattern beats everything
        (
            "CISA Adds 2 Known Exploited Vulnerabilities to Catalog",
            "cisa_news", "CISA News", "kev_catalog",
        ),
        (
            "CISA Adds One Known Exploited Vulnerability to Catalog",
            "cisa_news", "CISA News", "kev_catalog",
        ),
        (
            "CISA Adds 12 Known Exploited Vulnerabilities to Catalog",
            "generic", "SomeSource", "kev_catalog",
        ),
        # ICS advisory (cisa_advisory normalizer)
        (
            "Siemens SCALANCE Vulnerabilities (ICSA-26-099-01)",
            "cisa_advisory", "CISA Advisories", "ics_advisory",
        ),
        # Product advisory
        (
            "CVE-2026-1234 | Windows Kernel Elevation of Privilege Vulnerability",
            "generic", "Microsoft MSRC", "product_advisory",
        ),
        (
            "Cisco IOS XE Software Web UI Privilege Escalation",
            "generic", "Cisco Security Advisories", "product_advisory",
        ),
        # Threat advisory (CISA News non-KEV)
        (
            "CISA and FBI Release Advisory on LockBit Ransomware",
            "cisa_news", "CISA News", "threat_advisory",
        ),
        # NCSC UK
        (
            "Weekly Threat Report 9th May 2025",
            "generic", "NCSC UK", "threat_advisory",
        ),
        # Default news
        (
            "New ransomware campaign targets healthcare",
            "bleepingcomputer", "BleepingComputer", "news",
        ),
        (
            "Critical RCE in Apache Log4j",
            "generic", "SecurityWeek", "news",
        ),
    ])
    def test_infer_content_type(self, title, normalizer_key, source_name, expected):
        article = {"title": title, "source_name": source_name}
        assert _infer_content_type(article, normalizer_key) == expected
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_normalizer.py::TestInferContentType -v
```

Expected: `ImportError` — `_infer_content_type` does not exist yet.

- [ ] **Step 3: Add `content_type` to `NEWS_MAPPING` in `app/db/opensearch.py`**

In `NEWS_MAPPING["mappings"]["properties"]`, add after `"is_teaser": {"type": "boolean"},` (line 50):

```python
            "content_type":  {"type": "keyword"},
```

- [ ] **Step 4: Implement `_infer_content_type()` in `app/ingestion/normalizer.py`**

After the `_extract_cve_ids` function (around line 201), add:

```python
_KEV_TITLE_RE = re.compile(
    r"(?i)adds\s+(?:\d+|one|two|three)\s+known\s+exploited",
)

_PRODUCT_ADVISORY_SOURCES = frozenset([
    "Cisco Security Advisories",
    "Microsoft MSRC",
])


def _infer_content_type(article: dict, normalizer_key: str) -> str:
    """Infer content_type from article title and source metadata.

    Priority order:
    1. KEV catalog title pattern (beats everything — any source can publish KEV updates)
    2. ICS advisory normalizer
    3. Product advisory source name
    4. Threat advisory normalizer/source
    5. Default: news
    """
    title = article.get("title", "")
    source_name = article.get("source_name", "")

    if _KEV_TITLE_RE.search(title):
        return "kev_catalog"

    if normalizer_key == "cisa_advisory":
        return "ics_advisory"

    if source_name in _PRODUCT_ADVISORY_SOURCES:
        return "product_advisory"

    if normalizer_key == "cisa_news" or source_name == "NCSC UK":
        return "threat_advisory"

    return "news"
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
pytest tests/test_normalizer.py::TestInferContentType -v
```

Expected: 10 PASSED.

- [ ] **Step 6: Commit**

```bash
git add app/db/opensearch.py app/ingestion/normalizer.py tests/test_normalizer.py
git commit -m "feat(ingestion): add content_type field and inference function"
```

---

## Task 3: Wire `content_type` into the ingester

**Files:**
- Modify: `app/ingestion/ingester.py`
- Modify: `tests/test_ingester.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_ingester.py`:

```python
class TestContentTypeIsSet:
    """content_type must be set on every normalized article before upsert."""

    @pytest.mark.asyncio
    async def test_cisa_news_kev_article_gets_kev_catalog_type(self):
        """An article from CISA News with KEV title → content_type = kev_catalog."""
        from unittest.mock import patch
        import feedparser

        entry = feedparser.FeedParserDict({
            "title": "CISA Adds 3 Known Exploited Vulnerabilities to Catalog",
            "link": "https://www.cisa.gov/news/2026/05/cisa-adds-3-vuln",
            "id": "https://www.cisa.gov/news/2026/05/cisa-adds-3-vuln",
            "published_parsed": None,
        })
        source = {
            "id": 3,
            "name": "CISA News",
            "url": "https://www.cisa.gov/news.xml",
            "default_type": "news",
            "default_category": "breaking",
            "default_severity": None,
            "normalizer": "cisa_news",
            "credibility_weight": 1.5,
            "extract_cves": False,
            "extract_cvss": False,
            "junk_tags": [],
            "min_body_chars": None,
        }

        captured = {}

        async def fake_upsert(article):
            captured["content_type"] = article.get("content_type")
            return False  # skip actual OS write

        # FeedParserDict supports both .bozo attribute access and .get("entries", [])
        mock_feed = feedparser.FeedParserDict({"bozo": False, "entries": [entry]})

        with patch("app.ingestion.ingester.fetch_feed_content", return_value="<rss/>"), \
             patch("app.ingestion.ingester.feedparser.parse", return_value=mock_feed), \
             patch("app.ingestion.ingester.upsert_article", side_effect=fake_upsert), \
             patch("app.ingestion.ingester.store_raw_snapshot", return_value=None), \
             patch("app.ingestion.ingester.classify_tags", return_value={
                 "clean_tags": [], "normalized_topics": [], "tag_entities": []
             }):
            import httpx
            async with httpx.AsyncClient() as client:
                from app.ingestion.ingester import ingest_source
                await ingest_source(source, client)

        assert captured.get("content_type") == "kev_catalog"
```

- [ ] **Step 2: Run to confirm it fails**

```bash
pytest tests/test_ingester.py::TestContentTypeIsSet -v
```

Expected: `AssertionError` — `content_type` key not in article dict (or is `None`).

- [ ] **Step 3: Import `_infer_content_type` in `app/ingestion/ingester.py`**

Find the import line for `normalize_article` (line 22):
```python
from app.ingestion.normalizer import NORMALIZER_REGISTRY, NormalizedArticle, normalize_article
```

Replace with:
```python
from app.ingestion.normalizer import NORMALIZER_REGISTRY, NormalizedArticle, normalize_article, _infer_content_type
```

- [ ] **Step 4: Set `content_type` after normalization in `app/ingestion/ingester.py`**

In `ingest_source()`, find the block where `article` is set (handler or normalize_article call, around lines 347–351). Right after the `if article is None` check and continue block, and before the category filter block, add:

```python
            article["content_type"] = _infer_content_type(article, source["normalizer"])
```

The full block should look like this after the change:

```python
        for entry in entries:
            try:
                handler = flags.get("_handler")
                if handler is not None:
                    article = handler(entry, source)
                else:
                    article = normalize_article(entry, {**source, **flags})
                if article is None:
                    logger.debug(
                        "[%s] Skipped entry (normalizer returned None): %s",
                        name, entry.get("title", "<no title>"),
                    )
                    stats["errors"] += 1
                    continue

                article["content_type"] = _infer_content_type(article, source["normalizer"])

                # Category filter: check article tags against source_categories
                if _cat_map:
                    ...
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
pytest tests/test_ingester.py -v
```

Expected: all tests PASSED, including `TestContentTypeIsSet`.

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/ingester.py tests/test_ingester.py
git commit -m "feat(ingestion): set content_type on every article during ingestion"
```

---

## Task 4: Add `is_advisory` to cluster mapping and filter from API listing

**Files:**
- Modify: `app/db/opensearch.py`
- Modify: `app/api/routes/clusters.py`
- Modify: `tests/briefing/test_selector.py` (no change — only `/api/clusters/` needs the filter)
- Create: `tests/test_clusters_api.py` (new test file for the route)

Actually, add the test to the existing file if one exists; otherwise inline in this task.

- [ ] **Step 1: Write failing test**

Create `tests/test_clusters_api.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_list_clusters_excludes_advisory_clusters():
    """list_clusters() query must contain must_not: is_advisory: True."""
    from app.api.routes.clusters import list_clusters

    os_mock = AsyncMock()
    os_mock.search.return_value = {
        "hits": {"hits": [], "total": {"value": 0}}
    }

    with patch("app.api.routes.clusters.get_os_client", return_value=os_mock):
        await list_clusters()

    call_body = os_mock.search.call_args.kwargs["body"]
    must_not = call_body["query"]["bool"]["must_not"]
    assert {"term": {"is_advisory": True}} in must_not
```

- [ ] **Step 2: Run to confirm it fails**

```bash
pytest tests/test_clusters_api.py::test_list_clusters_excludes_advisory_clusters -v
```

Expected: `AssertionError` — `is_advisory` not in `must_not`.

- [ ] **Step 3: Add `is_advisory` to `_CLUSTERS_MAPPING` in `app/db/opensearch.py`**

In `_CLUSTERS_MAPPING["mappings"]["properties"]`, find the last line `"is_roundup": {"type": "boolean"},` (around line 206). Add `is_advisory` on the next line:

```python
            "is_roundup":     {"type": "boolean"},
            "is_advisory":    {"type": "boolean"},
```

- [ ] **Step 4: Add `is_advisory` filter to `list_clusters()` in `app/api/routes/clusters.py`**

Find the `body` dict in `list_clusters()` (around line 83). The current `must_not` only has `is_roundup`. Add `is_advisory`:

```python
    body: dict = {
        "query": {
            "bool": {
                "filter": filters,
                "must_not": [
                    {"term": {"is_roundup": True}},
                    {"term": {"is_advisory": True}},
                ],
            }
        },
        "sort": [{"created_at": {"order": "desc"}}],
        "from": offset,
        "size": limit,
    }
```

- [ ] **Step 5: Run all tests**

```bash
pytest tests/test_clusters_api.py tests/test_clusterer.py -v
```

Expected: all PASSED.

- [ ] **Step 6: Commit**

```bash
git add app/db/opensearch.py app/api/routes/clusters.py tests/test_clusters_api.py
git commit -m "feat(clusters): add is_advisory field and filter from API listing"
```

---

## Task 5: Clusterer routing by `content_type`

**Files:**
- Modify: `app/ingestion/clusterer.py`
- Modify: `tests/test_clusterer.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_clusterer.py`:

```python
# ---------------------------------------------------------------------------
# content_type routing in cluster_article()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kev_catalog_article_does_not_create_cluster():
    """kev_catalog articles annotate clusters but never create one."""
    from app.ingestion.clusterer import cluster_article

    article = {
        "slug": "cisa-adds-3-cve-2026-abc12345",
        "title": "CISA Adds 3 Known Exploited Vulnerabilities to Catalog",
        "content_type": "kev_catalog",
        "cve_ids": ["CVE-2026-1111", "CVE-2026-2222", "CVE-2026-3333"],
        "published_at": "2026-05-15T10:00:00Z",
        "source_name": "CISA News",
    }

    os_mock = AsyncMock()
    os_mock.update_by_query = AsyncMock(return_value={})

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer.embed_text", return_value=[0.1] * 1024), \
         patch("app.ingestion.clusterer.find_best_cluster", new_callable=AsyncMock) as mock_find, \
         patch("app.ingestion.clusterer.upsert_cve_topics", new_callable=AsyncMock), \
         patch("app.ingestion.clusterer.create_cve_topic_stubs", new_callable=AsyncMock):
        mock_find.return_value = None  # no existing cluster match
        await cluster_article(article, "cisa-adds-3-cve-2026-abc12345", [])

    # create_cluster / index was NOT called
    os_mock.index.assert_not_called()
    # kev annotation WAS attempted
    os_mock.update_by_query.assert_awaited_once()
    call_body = os_mock.update_by_query.call_args.kwargs["body"]
    assert call_body["query"]["terms"]["cve_ids"] == ["CVE-2026-1111", "CVE-2026-2222", "CVE-2026-3333"]


@pytest.mark.asyncio
async def test_product_advisory_does_not_create_cluster_when_no_match():
    """product_advisory articles merge if a cluster matches, but never seed a new one."""
    from app.ingestion.clusterer import cluster_article

    article = {
        "slug": "cisco-ios-xe-rce-abc12345",
        "title": "Cisco IOS XE RCE Vulnerability",
        "content_type": "product_advisory",
        "cve_ids": ["CVE-2026-9999"],
        "published_at": "2026-05-15T10:00:00Z",
        "source_name": "Cisco Security Advisories",
    }

    os_mock = AsyncMock()

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer.embed_text", return_value=[0.1] * 1024), \
         patch("app.ingestion.clusterer.find_best_cluster", new_callable=AsyncMock) as mock_find, \
         patch("app.ingestion.clusterer.upsert_cve_topics", new_callable=AsyncMock), \
         patch("app.ingestion.clusterer.create_cve_topic_stubs", new_callable=AsyncMock):
        mock_find.return_value = None  # no match
        await cluster_article(article, "cisco-ios-xe-rce-abc12345", [])

    # cluster index (create_cluster) was NOT called
    os_mock.index.assert_not_called()


@pytest.mark.asyncio
async def test_ics_advisory_creates_cluster_with_is_advisory_true():
    """ics_advisory articles create a cluster with is_advisory=True."""
    from app.ingestion.clusterer import create_cluster

    article = {
        "slug": "icsa-26-099-01-siemens-abc12345",
        "title": "Siemens SCALANCE Vulnerabilities (ICSA-26-099-01)",
        "content_type": "ics_advisory",
        "cve_ids": ["CVE-2026-5555"],
        "published_at": "2026-05-15T10:00:00Z",
        "source_name": "CISA Advisories",
    }

    os_mock = AsyncMock()
    os_mock.index.return_value = {"_id": "cluster-ics-001"}
    os_mock.update.return_value = {}

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer._rescore", new_callable=AsyncMock):
        await create_cluster(article, [], embedding=[0.1] * 1024)

    indexed = os_mock.index.call_args.kwargs["body"]
    assert indexed["is_advisory"] is True


@pytest.mark.asyncio
async def test_news_article_creates_cluster_with_is_advisory_false():
    """Regular news articles create a cluster with is_advisory=False."""
    from app.ingestion.clusterer import create_cluster

    article = {
        "slug": "fortios-rce-abc12345",
        "title": "FortiOS RCE CVE-2026-1234 exploited in the wild",
        "content_type": "news",
        "cve_ids": ["CVE-2026-1234"],
        "published_at": "2026-05-15T09:00:00Z",
        "source_name": "BleepingComputer",
    }

    os_mock = AsyncMock()
    os_mock.index.return_value = {"_id": "cluster-news-001"}
    os_mock.update.return_value = {}

    with patch("app.ingestion.clusterer.get_os_client", return_value=os_mock), \
         patch("app.ingestion.clusterer._rescore", new_callable=AsyncMock):
        await create_cluster(article, [], embedding=[0.1] * 1024)

    indexed = os_mock.index.call_args.kwargs["body"]
    assert indexed["is_advisory"] is False
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_clusterer.py::test_kev_catalog_article_does_not_create_cluster tests/test_clusterer.py::test_product_advisory_does_not_create_cluster_when_no_match tests/test_clusterer.py::test_ics_advisory_creates_cluster_with_is_advisory_true tests/test_clusterer.py::test_news_article_creates_cluster_with_is_advisory_false -v
```

Expected: various failures — `update_by_query` not called, `is_advisory` not in indexed doc.

- [ ] **Step 3: Add `_mark_kev_clusters()` to `app/ingestion/clusterer.py`**

After the `_is_roundup` function (around line 94), add:

```python
async def _mark_kev_clusters(cve_ids: list[str]) -> None:
    """Set cisa_kev=True on all clusters that share any of the given CVE IDs."""
    if not cve_ids:
        return
    os_client = get_os_client()
    try:
        await os_client.update_by_query(
            index=INDEX_CLUSTERS,
            body={
                "query": {"terms": {"cve_ids": cve_ids}},
                "script": {"source": "ctx._source.cisa_kev = true", "lang": "painless"},
            },
            params={"conflicts": "proceed"},
        )
    except Exception as exc:
        logger.warning("KEV cluster annotation failed for %s CVEs: %s", len(cve_ids), exc)
```

- [ ] **Step 4: Update `cluster_article()` routing in `app/ingestion/clusterer.py`**

Replace the body of `cluster_article()` with the following (keep the docstring, replace everything after it):

```python
async def cluster_article(
    article: dict,
    slug: str,
    entities: list[dict],
) -> None:
    """Assign article to an incident cluster and optionally to CVE topics.

    Routing by content_type:
    - kev_catalog: annotate matching clusters with cisa_kev=True, then return.
    - product_advisory: participate in CVE topics and merge if matched, but never seed.
    - ics_advisory: full participation; create_cluster() sets is_advisory=True.
    - threat_advisory / news: full participation (default).
    """
    from app.ingestion.cve_topic_manager import upsert_cve_topics, create_cve_topic_stubs

    content_type = article.get("content_type", "news")
    cve_ids: list[str] = article.get("cve_ids") or []
    embedding = await embed_text(_build_embed_input(article))
    ref_time = _parse_published_at(article.get("published_at"))

    # KEV catalog: annotate existing clusters, then exit — no incident clustering
    if content_type == "kev_catalog":
        await _mark_kev_clusters(cve_ids)
        return

    # CVE topic flow (all non-kev types participate)
    if cve_ids:
        if len(cve_ids) > _MAX_ARTICLE_CVES_FOR_CVE_TOPIC:
            await create_cve_topic_stubs(cve_ids)
        else:
            await upsert_cve_topics(cve_ids, slug, entities, embedding)

    # Incident cluster flow
    cluster_id = await find_best_cluster(entities, embedding, reference_time=ref_time)

    if cluster_id:
        await merge_into_cluster(
            cluster_id,
            slug,
            [e["normalized_key"] for e in entities],
            cve_ids,
            source_name=article.get("source_name", ""),
            title=article.get("title", ""),
            published_at=article.get("published_at", ""),
            cvss_score=article.get("cvss_score"),
            credibility_weight=float(article.get("credibility_weight") or 1.0),
            new_entities=entities,
            new_embedding=embedding,
        )
    elif content_type != "product_advisory":
        # product_advisory with no matching cluster: article is stored but unclustered
        await create_cluster(article, entities, embedding=embedding)
```

- [ ] **Step 5: Set `is_advisory` in `create_cluster()` in `app/ingestion/clusterer.py`**

In `create_cluster()`, find the `doc` dict (around line 330). Add `"is_advisory"` after `"is_roundup"`:

```python
    doc = {
        "label": article.get("title", ""),
        "state": "new",
        "is_roundup": _is_roundup(article.get("title", ""), cve_ids),
        "is_advisory": article.get("content_type") == "ics_advisory",
        "summary": "",
        ...
    }
```

- [ ] **Step 6: Run all tests to confirm they pass**

```bash
pytest tests/test_clusterer.py -v
```

Expected: all tests PASSED (new 4 + all pre-existing).

- [ ] **Step 7: Run the full test suite**

```bash
pytest tests/ -v --tb=short
```

Expected: all PASSED, no regressions.

- [ ] **Step 8: Commit**

```bash
git add app/ingestion/clusterer.py tests/test_clusterer.py
git commit -m "feat(clustering): route articles by content_type (kev/product/ics/news)"
```

---

## After All Tasks

**`content_type` takes effect immediately** for new articles — the field is set during ingestion. Existing articles in OpenSearch do not have `content_type` set (they'll have it as `null`). In `cluster_article()`, `article.get("content_type", "news")` defaults to `"news"` so the backfill script (if re-run) will also handle them correctly.

**`is_advisory` on clusters** takes effect on container restart via `ensure_indexes()` → `put_mapping`. Existing clusters do not have `is_advisory` set — they pass through the `must_not: [{term: {is_advisory: True}}]` filter (missing field ≠ `True`). New ICS advisory clusters created after restart will have `is_advisory: True` and be hidden.

**To apply to existing CISA Advisory clusters**, a full rebuild is needed:
```bash
# Only run after explicit permission
docker compose exec ingestion python scripts/cluster_articles.py --reset
```

**MSRC articles**: Microsoft MSRC was previously deleted from OpenSearch. The source remains in `feed_sources` (active). When the ingester next runs, MSRC articles will be re-ingested with `content_type = "product_advisory"` — they will only merge into existing clusters, never seed new ones.
