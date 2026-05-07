# Article Body Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the article body extraction pipeline per [the design spec](../specs/2026-05-07-body-extraction-design.md): Trafilatura extraction with curl-cffi anti-bot fetching, hybrid inline + one-shot backfill, per-source quality thresholds, exponential-backoff retries.

**Architecture:** Two new modules — `body_fetcher.py` (HTTP fetching with anti-bot, robots, cookies, retries) and `body_extractor.py` (Trafilatura wrapper + classification). One orchestration function `maybe_extract_body()`. Inline path runs after `upsert_article()`; backfill is a one-shot script `scripts/backfill_body_extraction.py`. New OpenSearch fields for failure metadata. New Postgres column `feed_sources.min_body_chars` for per-source thresholds.

**Tech Stack:** Python 3.12, asyncio, `trafilatura`, `curl-cffi` (with httpx fallback), opensearch-py, SQLAlchemy + Alembic, pytest.

---

## File Structure

| File | Purpose |
|---|---|
| `app/ingestion/body_extractor.py` (NEW) | Trafilatura wrapper, length classifier, error-tagging logic. Pure compute, no I/O. |
| `app/ingestion/body_fetcher.py` (NEW) | Async HTTP fetcher: curl-cffi client, browser headers, per-host cookie jar, robots.txt cache, error classification, 429/503 retry-within-fetch. |
| `app/ingestion/body_pipeline.py` (NEW) | Orchestration: `maybe_extract_body(article_doc, source_dict) -> dict`. Combines fetcher + extractor + skip-when-RSS-is-good logic. |
| `app/ingestion/ingester.py` (MODIFY) | Call `maybe_extract_body()` from inside the article ingest flow. Add new fields to `_prepare_article_doc` defaults. |
| `app/db/opensearch.py` (MODIFY) | Add `body_fetch_error`, `last_fetch_attempt_at`, `fetch_attempt_count` to `NEWS_MAPPING`. |
| `app/db/models/feed_source.py` (MODIFY) | Add `min_body_chars: Mapped[int \| None]` column. |
| `alembic/versions/0a1b2c3d4e5f_add_min_body_chars.py` (NEW) | Migration: add column + seed per-source values. |
| `scripts/backfill_body_extraction.py` (NEW) | One-shot backfill script with `--pilot` mode, concurrency caps, bulk writes. |
| `tests/test_body_extractor.py` (NEW) | Unit tests for length classification + Trafilatura wrapper. |
| `tests/test_body_fetcher.py` (NEW) | Unit tests for fetcher (mocked HTTP), error classification, robots, cookies. |
| `tests/test_body_pipeline.py` (NEW) | Unit tests for orchestration: skip-RSS-is-good, fetch-then-extract, failure paths. |
| `requirements.txt` (MODIFY) | Add `trafilatura`, `curl-cffi`. |

---

## Task 1: Add dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add dependencies**

Append to `requirements.txt` (alphabetically among existing entries — find an appropriate spot):

```
curl-cffi==0.7.4
trafilatura==1.12.2
```

- [ ] **Step 2: Verify install in ingestion container**

Run:
```bash
cd c:/Users/xb_admin/Desktop/Omar/Projects/kiber.info/kiber
docker compose build ingestion
docker compose run --rm ingestion python -c "import trafilatura, curl_cffi; print(trafilatura.__version__, curl_cffi.__version__)"
```

Expected: prints `1.12.2 0.7.4` (or compatible versions).

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore(deps): add trafilatura + curl-cffi for body extraction"
```

---

## Task 2: Add new fields to OpenSearch NEWS_MAPPING

**Files:**
- Modify: `app/db/opensearch.py:14-83` (add fields to `NEWS_MAPPING.mappings.properties`)

The schema is `dynamic: "strict"`, so any new field must be in the mapping before it can be written.

- [ ] **Step 1: Add fields to NEWS_MAPPING**

In `app/db/opensearch.py`, inside `NEWS_MAPPING["mappings"]["properties"]`, add three fields. Place them right after the existing `body_source` line:

```python
            "body_quality": {"type": "keyword"},
            "body_source": {"type": "keyword"},
            "body_fetch_error": {"type": "keyword"},
            "last_fetch_attempt_at": {
                "type": "date",
                "format": "strict_date_time||strict_date_time_no_millis",
            },
            "fetch_attempt_count": {"type": "integer"},
            "is_teaser": {"type": "boolean"},
```

- [ ] **Step 2: Apply mapping update to running OpenSearch**

The mapping is applied by `ensure_indexes()` on app startup, but for an existing index OpenSearch only adds new fields if they don't conflict. Run a one-shot update:

```bash
docker exec -e OPENSEARCH_URL=https://81.17.98.185:9200 -e OPENSEARCH_USER=kiber_app -e OPENSEARCH_PASSWORD=$OS_PASS kiber-ingestion-1 python - <<'PY'
import asyncio, os
from opensearchpy import AsyncOpenSearch
async def main():
    c = AsyncOpenSearch(
        hosts=[os.environ["OPENSEARCH_URL"]],
        http_auth=(os.environ["OPENSEARCH_USER"], os.environ["OPENSEARCH_PASSWORD"]),
        use_ssl=True, verify_certs=False, ssl_show_warn=False,
    )
    await c.indices.put_mapping(index="news_articles", body={
        "properties": {
            "body_fetch_error": {"type": "keyword"},
            "last_fetch_attempt_at": {"type": "date",
                "format": "strict_date_time||strict_date_time_no_millis"},
            "fetch_attempt_count": {"type": "integer"},
        }
    })
    print("mapping updated")
    await c.close()
asyncio.run(main())
PY
```

Expected output: `mapping updated`.

- [ ] **Step 3: Commit**

```bash
git add app/db/opensearch.py
git commit -m "feat(schema): add body_fetch_error, last_fetch_attempt_at, fetch_attempt_count to news_articles"
```

---

## Task 3: Alembic migration — feed_sources.min_body_chars

**Files:**
- Create: `alembic/versions/0a1b2c3d4e5f_add_min_body_chars.py`
- Modify: `app/db/models/feed_source.py` (add column to ORM)

- [ ] **Step 1: Create the migration file**

Create `alembic/versions/0a1b2c3d4e5f_add_min_body_chars.py`:

```python
"""Add min_body_chars to feed_sources

Revision ID: 0a1b2c3d4e5f
Revises: f6a7b8c9d0e1
Create Date: 2026-05-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0a1b2c3d4e5f"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "feed_sources",
        sa.Column("min_body_chars", sa.Integer(), nullable=True),
    )
    # Sources whose articles are systematically short by nature
    op.execute("UPDATE feed_sources SET min_body_chars = 200 WHERE name ILIKE '%MSRC%' OR name ILIKE '%Microsoft Security%'")
    op.execute("UPDATE feed_sources SET min_body_chars = 200 WHERE name ILIKE '%NVD%' OR url LIKE '%nist.gov%'")
    op.execute("UPDATE feed_sources SET min_body_chars = 400 WHERE name ILIKE '%CISA%'")
    op.execute("UPDATE feed_sources SET min_body_chars = 800 WHERE name = 'Krebs on Security'")


def downgrade() -> None:
    op.drop_column("feed_sources", "min_body_chars")
```

- [ ] **Step 2: Add ORM column to FeedSource model**

In `app/db/models/feed_source.py`, after the `junk_tags` column declaration, add:

```python
    min_body_chars: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
```

Then update `to_source_dict()` (search for it in the file) to include `min_body_chars` in the returned dict.

- [ ] **Step 3: Run migration**

```bash
docker compose run --rm backend alembic upgrade head
```

Expected output (last lines):
```
INFO  [alembic.runtime.migration] Running upgrade f6a7b8c9d0e1 -> 0a1b2c3d4e5f, Add min_body_chars to feed_sources
```

- [ ] **Step 4: Verify column + seed values**

```bash
docker exec kiber-db-1 psql -U postgres -d kiber -c "SELECT name, min_body_chars FROM feed_sources WHERE min_body_chars IS NOT NULL;"
```

Expected: rows for MSRC, NVD, CISA, Krebs with the seeded values (200/200/400/800).

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/0a1b2c3d4e5f_add_min_body_chars.py app/db/models/feed_source.py
git commit -m "feat(db): add feed_sources.min_body_chars with per-source seeds"
```

---

## Task 4: body_extractor — quality classifier

Pure function, no I/O — easy to unit test.

**Files:**
- Create: `app/ingestion/body_extractor.py`
- Create: `tests/test_body_extractor.py`

- [ ] **Step 1: Write the failing test for classify_length**

Create `tests/test_body_extractor.py`:

```python
import pytest
from app.ingestion.body_extractor import classify_length


@pytest.mark.parametrize("length,threshold,expected", [
    (3000, 1500, "ok"),
    (1500, 1500, "ok"),
    (1499, 1500, "weak"),
    (500, 1500, "weak"),
    (499, 1500, "empty"),
    (0, 1500, "empty"),
    # Per-source override (NVD = 200)
    (250, 200, "ok"),
    (200, 200, "ok"),
    (199, 200, "weak"),  # falls into [threshold/3, threshold) bucket
    (66, 200, "empty"),  # below threshold/3 = 66
])
def test_classify_length(length, threshold, expected):
    assert classify_length(length, threshold) == expected
```

- [ ] **Step 2: Run test, expect ImportError**

```bash
docker compose run --rm ingestion pytest tests/test_body_extractor.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.ingestion.body_extractor'`.

- [ ] **Step 3: Implement classify_length**

Create `app/ingestion/body_extractor.py`:

```python
"""Body extraction + quality classification.

Pure compute layer. No I/O. Wraps Trafilatura.
"""
from typing import Optional

import trafilatura


def classify_length(length: int, threshold: int) -> str:
    """Classify body length into a quality tier.

    - ok:    length >= threshold
    - weak:  threshold/3 <= length < threshold
    - empty: length < threshold/3

    The weak/empty boundary scales with the per-source threshold so a 200-char
    threshold for NVD doesn't auto-classify everything below 500 as empty.
    """
    weak_floor = max(threshold // 3, 1)
    if length >= threshold:
        return "ok"
    if length >= weak_floor:
        return "weak"
    return "empty"
```

- [ ] **Step 4: Run tests, expect pass**

```bash
docker compose run --rm ingestion pytest tests/test_body_extractor.py -v
```

Expected: 10 passed.

- [ ] **Step 5: Add Trafilatura wrapper test**

Append to `tests/test_body_extractor.py`:

```python
from app.ingestion.body_extractor import extract_text


SAMPLE_HTML = """
<html><body>
<nav>Site nav junk we don't want</nav>
<article>
<h1>Linux LPE in copy_file_range</h1>
<p>A vulnerability in the Linux kernel affecting all distributions
with kernels newer than 2017 was disclosed today. The flaw enables
local privilege escalation and has been confirmed across Ubuntu,
RHEL, SUSE, and Amazon Linux.</p>
<p>Mitigation: apply kernel patches as soon as your distribution
publishes them. CISA is expected to add CVE-2026-31431 to KEV.</p>
</article>
<footer>Copyright 2026</footer>
</body></html>
"""


def test_extract_text_returns_main_content():
    result = extract_text(SAMPLE_HTML)
    assert result is not None
    # Body content present
    assert "Linux kernel" in result
    assert "CVE-2026-31431" in result
    # Junk sections gone
    assert "Site nav junk" not in result
    assert "Copyright 2026" not in result


def test_extract_text_returns_none_on_empty_input():
    assert extract_text("") is None
    assert extract_text(None) is None  # type: ignore[arg-type]


def test_extract_text_returns_none_on_garbage():
    assert extract_text("not html at all") in (None, "")
```

- [ ] **Step 6: Run tests, expect failure**

```bash
docker compose run --rm ingestion pytest tests/test_body_extractor.py -v
```

Expected: 3 errors / failures on the new tests (`extract_text` not defined).

- [ ] **Step 7: Implement extract_text**

Append to `app/ingestion/body_extractor.py`:

```python
def extract_text(html: Optional[str]) -> Optional[str]:
    """Run Trafilatura on HTML, return clean main-content text.

    Returns None if input is empty or extraction fails.
    """
    if not html:
        return None
    try:
        result = trafilatura.extract(
            html,
            favor_recall=True,        # tolerate sites with thin metadata
            include_comments=False,
            include_tables=False,
            no_fallback=False,        # let trafilatura try its readability fallback
        )
        return result
    except Exception:
        return None
```

- [ ] **Step 8: Run tests, expect all pass**

```bash
docker compose run --rm ingestion pytest tests/test_body_extractor.py -v
```

Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add app/ingestion/body_extractor.py tests/test_body_extractor.py
git commit -m "feat(extractor): add classify_length + Trafilatura extract_text"
```

---

## Task 5: body_fetcher — error classifier

Start with the smallest piece: a pure function that classifies an HTTP response or exception into one of the `body_fetch_error` codes.

**Files:**
- Create: `app/ingestion/body_fetcher.py`
- Create: `tests/test_body_fetcher.py`

- [ ] **Step 1: Write failing test for classify_fetch_error**

Create `tests/test_body_fetcher.py`:

```python
import pytest
from app.ingestion.body_fetcher import classify_fetch_error


def test_classify_status_403():
    assert classify_fetch_error(status=403, body=None, exc=None) == "403"


def test_classify_status_404():
    assert classify_fetch_error(status=404, body=None, exc=None) == "404"


def test_classify_status_500():
    assert classify_fetch_error(status=500, body=None, exc=None) == "500"


def test_classify_cloudflare_challenge():
    body = '<html><head><title>Just a moment...</title></head>'
    assert classify_fetch_error(status=200, body=body, exc=None) == "cloudflare-challenge"


def test_classify_cloudflare_chl_script():
    body = '<html><body><script src="/cdn-cgi/challenge-platform/cf-chl-bypass.js"></script></body></html>'
    assert classify_fetch_error(status=200, body=body, exc=None) == "cloudflare-challenge"


def test_classify_timeout():
    import asyncio
    assert classify_fetch_error(status=None, body=None, exc=asyncio.TimeoutError()) == "timeout"


def test_classify_connection_error():
    assert classify_fetch_error(status=None, body=None, exc=ConnectionError("dns")) == "connection-error"


def test_classify_no_error_returns_none():
    body = '<html><body>Real article content here</body></html>'
    assert classify_fetch_error(status=200, body=body, exc=None) is None
```

- [ ] **Step 2: Run tests, expect ImportError**

```bash
docker compose run --rm ingestion pytest tests/test_body_fetcher.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.ingestion.body_fetcher'`.

- [ ] **Step 3: Implement classify_fetch_error**

Create `app/ingestion/body_fetcher.py`:

```python
"""HTTP body fetcher with anti-bot tier (curl-cffi + headers + cookies + robots).

Public surface:
    fetch_body(url, source_dict) -> FetchResult
    classify_fetch_error(status, body, exc) -> Optional[str]
"""
import asyncio
from typing import Optional


_CF_CHALLENGE_TITLE = "<title>Just a moment...</title>"
_CF_CHL_SCRIPT_TOKEN = "cf-chl-"


def classify_fetch_error(
    *,
    status: Optional[int],
    body: Optional[str],
    exc: Optional[BaseException],
) -> Optional[str]:
    """Map a fetch outcome to a body_fetch_error string, or None on success.

    Order matters: exceptions first (no response), then HTTP status, then
    body-content checks for Cloudflare challenge pages (which return 200).
    """
    if exc is not None:
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return "timeout"
        if isinstance(exc, ConnectionError):
            return "connection-error"
        return "connection-error"

    if status is None:
        return "connection-error"

    if status >= 400:
        return str(status)

    if body and (_CF_CHALLENGE_TITLE in body or _CF_CHL_SCRIPT_TOKEN in body):
        return "cloudflare-challenge"

    return None
```

- [ ] **Step 4: Run tests, expect all pass**

```bash
docker compose run --rm ingestion pytest tests/test_body_fetcher.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/body_fetcher.py tests/test_body_fetcher.py
git commit -m "feat(fetcher): add classify_fetch_error for response/exception triage"
```

---

## Task 6: body_fetcher — robots.txt cache

Per-host robots.txt fetched once, cached 24h, used to gate URL fetches.

**Files:**
- Modify: `app/ingestion/body_fetcher.py`
- Modify: `tests/test_body_fetcher.py`

- [ ] **Step 1: Write failing test for is_url_allowed**

Append to `tests/test_body_fetcher.py`:

```python
from urllib.robotparser import RobotFileParser
from app.ingestion.body_fetcher import RobotsCache


def test_robots_cache_allows_when_no_robots(monkeypatch):
    cache = RobotsCache()
    # No fetch happened — empty robots means allow everything
    rp = RobotFileParser()
    rp.parse([])
    cache._cache["example.com"] = (rp, 9999999999)
    assert cache.is_url_allowed("https://example.com/article") is True


def test_robots_cache_disallows_per_robots(monkeypatch):
    cache = RobotsCache()
    rp = RobotFileParser()
    rp.parse(["User-agent: *", "Disallow: /private/"])
    cache._cache["example.com"] = (rp, 9999999999)
    assert cache.is_url_allowed("https://example.com/private/secret") is False
    assert cache.is_url_allowed("https://example.com/public/article") is True


def test_robots_cache_unknown_host_returns_true_default():
    """When robots can't be fetched, default to allow (cache is empty)."""
    cache = RobotsCache()
    # No prefetch — should default to allow
    assert cache.is_url_allowed("https://newsite.example.com/x", default_on_unknown=True) is True
```

- [ ] **Step 2: Run tests, expect ImportError**

```bash
docker compose run --rm ingestion pytest tests/test_body_fetcher.py -v
```

Expected: ImportError on `RobotsCache`.

- [ ] **Step 3: Implement RobotsCache**

Append to `app/ingestion/body_fetcher.py`:

```python
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

_ROBOTS_TTL_SECONDS = 24 * 60 * 60  # 24h


class RobotsCache:
    """Per-host robots.txt cache with 24h TTL. Default-allow on fetch failure."""

    def __init__(self) -> None:
        # host -> (parser, expires_at_unix)
        self._cache: dict[str, tuple[RobotFileParser, float]] = {}

    def is_url_allowed(self, url: str, *, default_on_unknown: bool = True) -> bool:
        host = urlparse(url).hostname or ""
        cached = self._cache.get(host)
        if cached is None:
            return default_on_unknown
        rp, expires = cached
        if time.time() > expires:
            return default_on_unknown
        return rp.can_fetch("*", url)

    async def prefetch(self, url: str, fetcher) -> None:
        """Fetch and cache robots.txt for the host of `url`.

        `fetcher` is a callable: async (robots_url) -> Optional[str]
        """
        host = urlparse(url).hostname or ""
        if not host:
            return
        cached = self._cache.get(host)
        if cached and time.time() <= cached[1]:
            return
        scheme = urlparse(url).scheme or "https"
        robots_url = f"{scheme}://{host}/robots.txt"
        rp = RobotFileParser()
        try:
            text = await fetcher(robots_url)
            if text:
                rp.parse(text.splitlines())
            else:
                rp.parse([])
        except Exception:
            rp.parse([])
        self._cache[host] = (rp, time.time() + _ROBOTS_TTL_SECONDS)
```

- [ ] **Step 4: Run tests, expect all pass**

```bash
docker compose run --rm ingestion pytest tests/test_body_fetcher.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/body_fetcher.py tests/test_body_fetcher.py
git commit -m "feat(fetcher): add RobotsCache with 24h TTL + default-allow"
```

---

## Task 7: body_fetcher — HTTP fetch with curl-cffi + browser headers

The actual fetcher. Uses curl-cffi for TLS fingerprinting; falls back to httpx if curl-cffi import fails.

**Files:**
- Modify: `app/ingestion/body_fetcher.py`
- Modify: `tests/test_body_fetcher.py`

- [ ] **Step 1: Write failing test for fetch_body**

Append to `tests/test_body_fetcher.py`:

```python
import pytest
from app.ingestion.body_fetcher import fetch_body, FetchResult


@pytest.mark.asyncio
async def test_fetch_body_success(monkeypatch):
    """fetch_body returns a FetchResult on success."""

    class FakeResp:
        status_code = 200
        text = "<html><body><article>Real content</article></body></html>"
        headers = {"content-type": "text/html"}

    class FakeSession:
        async def get(self, url, **kwargs):
            return FakeResp()
        async def close(self):
            pass

    monkeypatch.setattr("app.ingestion.body_fetcher._make_session", lambda: FakeSession())

    result = await fetch_body("https://example.com/article")
    assert result.error is None
    assert result.status == 200
    assert "Real content" in result.body


@pytest.mark.asyncio
async def test_fetch_body_404(monkeypatch):
    class FakeResp:
        status_code = 404
        text = ""
        headers = {}

    class FakeSession:
        async def get(self, url, **kwargs):
            return FakeResp()
        async def close(self):
            pass

    monkeypatch.setattr("app.ingestion.body_fetcher._make_session", lambda: FakeSession())

    result = await fetch_body("https://example.com/missing")
    assert result.error == "404"
    assert result.status == 404
    assert result.body is None


@pytest.mark.asyncio
async def test_fetch_body_timeout(monkeypatch):
    import asyncio

    class FakeSession:
        async def get(self, url, **kwargs):
            raise asyncio.TimeoutError()
        async def close(self):
            pass

    monkeypatch.setattr("app.ingestion.body_fetcher._make_session", lambda: FakeSession())

    result = await fetch_body("https://slow.example.com/x")
    assert result.error == "timeout"
    assert result.body is None
```

- [ ] **Step 2: Add pytest-asyncio if not already there**

Verify in `requirements.txt` or `requirements-dev.txt`. If missing, add `pytest-asyncio==0.24.0` to dev requirements and rebuild.

- [ ] **Step 3: Run tests, expect failure**

```bash
docker compose run --rm ingestion pytest tests/test_body_fetcher.py -v
```

Expected: ImportError on `fetch_body` / `FetchResult`.

- [ ] **Step 4: Implement fetch_body + FetchResult + module-level session**

Append to `app/ingestion/body_fetcher.py`:

```python
from dataclasses import dataclass


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_DEFAULT_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}
_DEFAULT_TIMEOUT_SECONDS = 8

# Module-level session for cookie + connection persistence per process.
# Tests monkeypatch _SESSION directly (or _make_session) to inject fakes.
_SESSION = None


@dataclass
class FetchResult:
    status: Optional[int]
    body: Optional[str]
    error: Optional[str]  # body_fetch_error code; None on success


def _make_session():
    """Build the HTTP session. curl-cffi preferred; httpx fallback.

    Called lazily on first fetch_body() invocation. Reused for the rest
    of the process so cookies persist per host.
    """
    try:
        from curl_cffi.requests import AsyncSession
        return AsyncSession(impersonate="chrome120")
    except Exception:
        import httpx
        return httpx.AsyncClient(http2=True, follow_redirects=True)


def _get_session():
    global _SESSION
    if _SESSION is None:
        _SESSION = _make_session()
    return _SESSION


async def fetch_body(
    url: str,
    *,
    timeout: int = _DEFAULT_TIMEOUT_SECONDS,
    extra_headers: Optional[dict] = None,
) -> FetchResult:
    """Fetch a single URL with anti-bot headers + 8s timeout.

    On 429/503: respect Retry-After (capped at 4s), retry once.
    Caller is responsible for robots.txt gating and r3 retry scheduling.
    """
    headers = {**_DEFAULT_HEADERS, **(extra_headers or {})}
    session = _get_session()

    for attempt in range(2):  # original + at most 1 retry on 429/503
        try:
            resp = await asyncio.wait_for(
                session.get(url, headers=headers, timeout=timeout),
                timeout=timeout + 2,
            )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            return FetchResult(status=None, body=None, error=classify_fetch_error(
                status=None, body=None, exc=exc
            ))
        except Exception as exc:
            return FetchResult(status=None, body=None, error=classify_fetch_error(
                status=None, body=None, exc=exc
            ))

        status = getattr(resp, "status_code", None)
        body = getattr(resp, "text", None)

        # 429/503 retry-within-fetch (one shot, respect Retry-After up to 4s)
        if status in (429, 503) and attempt == 0:
            retry_after = 1
            try:
                ra_header = resp.headers.get("Retry-After")
                if ra_header:
                    retry_after = min(int(ra_header), 4)
            except Exception:
                pass
            await asyncio.sleep(retry_after)
            continue

        err = classify_fetch_error(status=status, body=body, exc=None)
        return FetchResult(
            status=status,
            body=None if err else body,
            error=err,
        )

    # Both attempts exhausted on 429/503
    return FetchResult(status=503, body=None, error="503")
```

- [ ] **Step 5: Run tests, expect all pass**

```bash
docker compose run --rm ingestion pytest tests/test_body_fetcher.py -v
```

Expected: 14 passed.

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/body_fetcher.py tests/test_body_fetcher.py
git commit -m "feat(fetcher): add fetch_body with curl-cffi + browser headers + 8s timeout"
```

---

## Task 8: body_pipeline — orchestration

Combines fetcher + extractor + skip-when-RSS-is-good logic. This is what `ingester.py` and the backfill script will call.

**Files:**
- Create: `app/ingestion/body_pipeline.py`
- Create: `tests/test_body_pipeline.py`
- Modify: `app/ingestion/body_fetcher.py` (add `fetch_text` for robots.txt prefetch)

- [ ] **Step 0: Add fetch_text to body_fetcher.py**

`RobotsCache.prefetch` needs a plain text fetcher. Append to `app/ingestion/body_fetcher.py`:

```python
async def fetch_text(url: str, *, timeout: int = 10) -> Optional[str]:
    """Simple text fetch used by RobotsCache to retrieve robots.txt.

    Returns None on any error so robots check defaults to allow.
    """
    session = _get_session()
    try:
        resp = await asyncio.wait_for(
            session.get(url, headers=_DEFAULT_HEADERS, timeout=timeout),
            timeout=timeout + 2,
        )
        if getattr(resp, "status_code", 200) < 400:
            return getattr(resp, "text", None)
        return None
    except Exception:
        return None
```

- [ ] **Step 1: Write failing test for skip-when-RSS-is-good**

Create `tests/test_body_pipeline.py`:

```python
import pytest
from app.ingestion.body_pipeline import maybe_extract_body


@pytest.mark.asyncio
async def test_skip_when_rss_already_good():
    """If content_html from RSS is already >= threshold, don't fetch."""
    rss_body = "x" * 2000
    article = {
        "slug": "test",
        "source_url": "https://example.com/article",
        "content_html": rss_body,
    }
    source = {"min_body_chars": 1500}
    result = await maybe_extract_body(article, source, fetch_fn=None, extract_fn=None)
    assert result["body_source"] == "rss-full"
    assert result["body_quality"] == "ok"
    # No content_html update — it's already correct
    assert "content_html" not in result


@pytest.mark.asyncio
async def test_uses_default_threshold_when_source_missing_override():
    rss_body = "x" * 2000
    article = {"slug": "t", "source_url": "https://example.com", "content_html": rss_body}
    source = {}  # no min_body_chars
    result = await maybe_extract_body(article, source, fetch_fn=None, extract_fn=None)
    assert result["body_source"] == "rss-full"
    assert result["body_quality"] == "ok"
```

- [ ] **Step 2: Run tests, expect ImportError**

```bash
docker compose run --rm ingestion pytest tests/test_body_pipeline.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement maybe_extract_body skeleton**

Create `app/ingestion/body_pipeline.py`:

```python
"""Body extraction orchestration.

Combines fetch (HTTP) + extract (Trafilatura) + skip-when-RSS-is-good logic.

Called from:
- app/ingestion/ingester.py (inline path, new articles)
- scripts/backfill_body_extraction.py (one-shot backfill)

Returns a dict of fields to merge into the article doc:
  {
    "body_quality": "ok" | "weak" | "empty" | "failed",
    "body_source":  "rss-full" | "trafilatura" | "failed",
    "body_fetch_error": Optional[str],
    "last_fetch_attempt_at": iso timestamp,
    "fetch_attempt_count": int (incremented),
    "content_html": Optional[str],   # only present if changed
  }
"""
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from app.ingestion.body_extractor import classify_length, extract_text
from app.ingestion.body_fetcher import fetch_body, fetch_text, FetchResult, RobotsCache

_DEFAULT_THRESHOLD = 1500
_robots_cache = RobotsCache()


async def maybe_extract_body(
    article_doc: dict,
    source_dict: dict,
    *,
    fetch_fn: Optional[Callable[..., Awaitable[FetchResult]]] = None,
    extract_fn: Optional[Callable[[Optional[str]], Optional[str]]] = None,
    robots_cache: Optional[RobotsCache] = None,
) -> dict:
    """Decide between RSS-full / fetch+extract / failed.

    fetch_fn, extract_fn, and robots_cache are injectable for tests;
    default to module-level singletons.
    """
    fetch = fetch_fn or fetch_body
    extract = extract_fn or extract_text
    cache = robots_cache if robots_cache is not None else _robots_cache

    threshold = source_dict.get("min_body_chars") or _DEFAULT_THRESHOLD
    rss_body = article_doc.get("content_html") or ""

    if len(rss_body) >= threshold:
        return {
            "body_source": "rss-full",
            "body_quality": classify_length(len(rss_body), threshold),
        }

    # Fetch + extract path implemented in next task.
    raise NotImplementedError("fetch path not yet implemented")
```

- [ ] **Step 4: Run tests, expect first 2 pass**

```bash
docker compose run --rm ingestion pytest tests/test_body_pipeline.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Add fetch+extract path tests (including robots-disallowed)**

Append to `tests/test_body_pipeline.py`:

```python
from app.ingestion.body_fetcher import FetchResult


@pytest.mark.asyncio
async def test_fetch_and_extract_success():
    """RSS too short, fetch succeeds, extracted body >= threshold."""
    article = {
        "slug": "test",
        "source_url": "https://example.com/article",
        "content_html": "short rss",
    }
    source = {"min_body_chars": 1500}

    long_html = "<html><body><article>" + ("a" * 2000) + "</article></body></html>"

    async def fake_fetch(url, **kwargs):
        return FetchResult(status=200, body=long_html, error=None)

    extracted_text = "a" * 2000
    def fake_extract(html):
        return extracted_text

    result = await maybe_extract_body(article, source, fetch_fn=fake_fetch, extract_fn=fake_extract)
    assert result["body_source"] == "trafilatura"
    assert result["body_quality"] == "ok"
    assert result["content_html"] == extracted_text
    assert "fetch_attempt_count" in result
    assert "last_fetch_attempt_at" in result


@pytest.mark.asyncio
async def test_fetch_404_marks_failed():
    article = {
        "slug": "test",
        "source_url": "https://example.com/missing",
        "content_html": "short rss",
    }
    source = {"min_body_chars": 1500}

    async def fake_fetch(url, **kwargs):
        return FetchResult(status=404, body=None, error="404")

    result = await maybe_extract_body(article, source, fetch_fn=fake_fetch, extract_fn=None)
    assert result["body_source"] == "failed"
    assert result["body_quality"] == "failed"
    assert result["body_fetch_error"] == "404"


@pytest.mark.asyncio
async def test_robots_disallowed_skips_fetch():
    """If robots.txt disallows the URL, return failed without calling fetch_fn."""
    from app.ingestion.body_fetcher import RobotsCache
    from urllib.robotparser import RobotFileParser

    article = {
        "slug": "test",
        "source_url": "https://blocked.example.com/private/article",
        "content_html": "short rss",
        "fetch_attempt_count": 0,
    }
    source = {"min_body_chars": 1500}

    # Pre-load the robots cache with a disallow rule
    cache = RobotsCache()
    rp = RobotFileParser()
    rp.parse(["User-agent: *", "Disallow: /private/"])
    cache._cache["blocked.example.com"] = (rp, 9999999999)

    fetch_called = []

    async def fake_fetch(url, **kwargs):
        fetch_called.append(url)
        return FetchResult(status=200, body="should not be called", error=None)

    result = await maybe_extract_body(
        article, source,
        fetch_fn=fake_fetch, extract_fn=None,
        robots_cache=cache,
    )
    assert result["body_quality"] == "failed"
    assert result["body_fetch_error"] == "robots-disallowed"
    assert len(fetch_called) == 0  # fetch was never called


@pytest.mark.asyncio
async def test_fetch_succeeds_but_extracted_too_short():
    article = {
        "slug": "test",
        "source_url": "https://example.com/x",
        "content_html": "short rss",
    }
    source = {"min_body_chars": 1500}

    async def fake_fetch(url, **kwargs):
        return FetchResult(status=200, body="<html>fine</html>", error=None)
    def fake_extract(html):
        return "tiny"

    result = await maybe_extract_body(article, source, fetch_fn=fake_fetch, extract_fn=fake_extract)
    # tiny < threshold/3 = 500 → empty
    assert result["body_source"] == "trafilatura"
    assert result["body_quality"] == "empty"
```

- [ ] **Step 6: Run tests, expect 4 failures (NotImplementedError)**

```bash
docker compose run --rm ingestion pytest tests/test_body_pipeline.py -v
```

Expected: 2 passed, 4 failed.

- [ ] **Step 7: Implement fetch+extract path with robots check**

Replace the `raise NotImplementedError` line at the bottom of `maybe_extract_body` with:

```python
    # Fetch path
    url = article_doc["source_url"]
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fetch_count = (article_doc.get("fetch_attempt_count") or 0) + 1
    base_meta = {
        "fetch_attempt_count": fetch_count,
        "last_fetch_attempt_at": now_iso,
    }

    # Robots.txt gate: prefetch (cached 24h), then check
    await cache.prefetch(url, fetch_text)
    if not cache.is_url_allowed(url):
        return {
            **base_meta,
            "body_source": "failed",
            "body_quality": "failed",
            "body_fetch_error": "robots-disallowed",
        }

    result = await fetch(url)

    if result.error is not None:
        return {
            **base_meta,
            "body_source": "failed",
            "body_quality": "failed",
            "body_fetch_error": result.error,
        }

    extracted = extract(result.body) or ""
    return {
        **base_meta,
        "body_source": "trafilatura",
        "body_quality": classify_length(len(extracted), threshold),
        "content_html": extracted,
    }
```

- [ ] **Step 8: Run tests, all pass**

```bash
docker compose run --rm ingestion pytest tests/test_body_pipeline.py -v
```

Expected: 6 passed.

- [ ] **Step 9: Commit**

```bash
git add app/ingestion/body_pipeline.py tests/test_body_pipeline.py
git commit -m "feat(pipeline): add maybe_extract_body orchestration"
```

---

## Task 9: Inline integration in ingester.py

Wire `maybe_extract_body()` into the ingest flow. Best path: extend `_prepare_article_doc` defaults to include the new fields (so `dynamic: strict` doesn't reject them), and add an extraction step in the per-article ingest path.

**Files:**
- Modify: `app/ingestion/ingester.py`

- [ ] **Step 1: Update _prepare_article_doc defaults**

In `app/ingestion/ingester.py:80-95` (the `_prepare_article_doc` function), in the `setdefault` block, add after `body_source`:

```python
    doc.setdefault("body_quality", "empty")
    doc.setdefault("body_source", "none")
    doc.setdefault("body_fetch_error", None)
    doc.setdefault("last_fetch_attempt_at", None)
    doc.setdefault("fetch_attempt_count", 0)
    doc.setdefault("is_teaser", False)
```

- [ ] **Step 2: Find the per-article ingest call site**

Run:

```bash
grep -n "upsert_article\|cluster_article\|extract_entities" app/ingestion/ingester.py
```

Identify the loop that processes each entry within a feed (likely in `process_source()` or similar). Note the line numbers.

- [ ] **Step 3: Add body extraction call after upsert**

After the call to `upsert_article(article)` succeeds (and likely before `cluster_article`), insert:

```python
            from app.ingestion.body_pipeline import maybe_extract_body
            try:
                body_updates = await maybe_extract_body(
                    article_doc=dict(article),
                    source_dict={"min_body_chars": getattr(source, "min_body_chars", None)},
                )
                if body_updates:
                    await get_os_client().update(
                        index=INDEX_NEWS,
                        id=article["slug"],
                        body={"doc": body_updates},
                    )
            except Exception as exc:
                logger.warning("body extraction failed for %s: %s", article["slug"], exc)
```

(Place this in the ingest loop right after the article is successfully indexed.)

- [ ] **Step 4: Restart ingestion container with new code**

```bash
docker compose build ingestion
docker compose up -d ingestion
docker compose logs -f ingestion --tail 20
```

Watch logs for the next ingest cycle. Expect to see body extraction running on new articles (look for `INFO  ingester.body_extraction` lines, or for warnings on failure).

- [ ] **Step 5: Verify a recently-ingested article has body fields**

```bash
docker exec -e OPENSEARCH_URL=https://81.17.98.185:9200 -e OPENSEARCH_USER=kiber_app -e OPENSEARCH_PASSWORD=$OS_PASS kiber-ingestion-1 python - <<'PY'
import asyncio, os, json
from opensearchpy import AsyncOpenSearch
async def main():
    c = AsyncOpenSearch(
        hosts=[os.environ["OPENSEARCH_URL"]],
        http_auth=(os.environ["OPENSEARCH_USER"], os.environ["OPENSEARCH_PASSWORD"]),
        use_ssl=True, verify_certs=False, ssl_show_warn=False,
    )
    r = await c.search(index="news_articles", body={
        "size": 3,
        "_source": ["title", "body_quality", "body_source", "fetch_attempt_count"],
        "sort": [{"published_at": {"order": "desc"}}],
    })
    for hit in r["hits"]["hits"]:
        print(json.dumps(hit["_source"], indent=2))
    await c.close()
asyncio.run(main())
PY
```

Expected: at least one of the most-recent articles has `body_source != "none"` or `fetch_attempt_count > 0`.

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/ingester.py
git commit -m "feat(ingester): wire maybe_extract_body into article ingest flow"
```

---

## Task 10: Backfill script — skeleton + query

Build the backfill script in pieces. Start with the query that finds eligible articles.

**Files:**
- Create: `scripts/backfill_body_extraction.py`

- [ ] **Step 1: Create script skeleton**

Create `scripts/backfill_body_extraction.py`:

```python
#!/usr/bin/env python
"""One-shot backfill of article body extraction.

Usage:
    python scripts/backfill_body_extraction.py            # full backfill
    python scripts/backfill_body_extraction.py --pilot    # 3-5 articles per top-25 source
    python scripts/backfill_body_extraction.py --dry-run  # log what would happen, no writes

Idempotent: reruns pick up where left off (queries body_quality IS missing or "failed").
"""
import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select

from app.db.models.feed_source import FeedSource as FeedSourceModel
from app.db.opensearch import INDEX_NEWS, get_os_client
from app.db.session import AsyncSessionLocal
from app.ingestion.body_pipeline import maybe_extract_body

logger = logging.getLogger("backfill_body")

GLOBAL_CONCURRENCY = 10
PER_HOST_CONCURRENCY = 2
BULK_BATCH_SIZE = 50
PROGRESS_LOG_EVERY = 100


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--pilot", action="store_true", help="3-5 articles per top-25 source")
    p.add_argument("--dry-run", action="store_true", help="log without writing")
    p.add_argument("--limit", type=int, default=None, help="cap total processed articles")
    return p.parse_args()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args()
    logger.info("backfill starting | pilot=%s dry_run=%s limit=%s", args.pilot, args.dry_run, args.limit)
    # Implementation in subsequent tasks


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Verify it runs**

```bash
docker compose run --rm ingestion python scripts/backfill_body_extraction.py --dry-run
```

Expected output:
```
... INFO backfill_body: backfill starting | pilot=False dry_run=True limit=None
```

- [ ] **Step 3: Add eligible-articles query**

Replace the comment `# Implementation in subsequent tasks` in `main()` with:

```python
    client = get_os_client()
    sources_by_name = await _load_sources()

    if args.pilot:
        article_iter = _pilot_articles(client, sources_by_name)
    else:
        article_iter = _eligible_articles(client, args.limit)

    processed = 0
    successes = 0
    failures = 0
    weak = 0
    started = time.time()

    async for batch in article_iter:
        if args.dry_run:
            for doc in batch:
                logger.info("[DRY RUN] would process %s (source=%s)",
                    doc["_id"], doc["_source"].get("source_name"))
            processed += len(batch)
            continue
        # Real processing implemented in Task 11

    elapsed = time.time() - started
    logger.info("backfill done | processed=%d ok=%d weak=%d failed=%d elapsed=%.1fs",
                processed, successes, weak, failures, elapsed)
```

And add helper functions above `main()`:

```python
async def _load_sources() -> dict[str, dict]:
    """Return {source_name: {min_body_chars: int|None, ...}} from Postgres."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(FeedSourceModel))
        sources = result.scalars().all()
    return {s.name: {"min_body_chars": s.min_body_chars} for s in sources}


async def _eligible_articles(client, limit: int | None):
    """Yield batches of 50 articles with body_quality missing or 'failed'."""
    body = {
        "size": 50,
        "_source": ["slug", "source_url", "source_name", "content_html",
                    "body_quality", "body_source", "fetch_attempt_count",
                    "last_fetch_attempt_at"],
        "query": {
            "bool": {
                "should": [
                    {"bool": {"must_not": {"exists": {"field": "body_quality"}}}},
                    {"term": {"body_quality": "failed"}},
                    {"term": {"body_quality": "empty"}},
                ],
                "minimum_should_match": 1,
            }
        },
        "sort": [{"published_at": {"order": "desc"}}],
    }
    yielded = 0
    response = await client.search(
        index=INDEX_NEWS, body=body, scroll="2m"
    )
    scroll_id = response.get("_scroll_id")
    try:
        while True:
            hits = response["hits"]["hits"]
            if not hits:
                break
            if limit is not None and yielded + len(hits) > limit:
                hits = hits[: limit - yielded]
            yield hits
            yielded += len(hits)
            if limit is not None and yielded >= limit:
                break
            response = await client.scroll(scroll_id=scroll_id, scroll="2m")
            scroll_id = response.get("_scroll_id")
    finally:
        if scroll_id:
            try:
                await client.clear_scroll(scroll_id=scroll_id)
            except Exception:
                pass


async def _pilot_articles(client, sources_by_name: dict):
    """Yield ~3-5 articles per top-25 source, single batch."""
    # Use a top_hits aggregation grouped by source_name
    body = {
        "size": 0,
        "aggs": {
            "by_source": {
                "terms": {"field": "source_name.keyword", "size": 25},
                "aggs": {
                    "samples": {
                        "top_hits": {
                            "size": 5,
                            "_source": ["slug", "source_url", "source_name",
                                        "content_html", "body_quality"],
                            "sort": [{"published_at": {"order": "desc"}}],
                        }
                    }
                }
            }
        },
    }
    response = await client.search(index=INDEX_NEWS, body=body)
    hits = []
    for bucket in response["aggregations"]["by_source"]["buckets"]:
        for hit in bucket["samples"]["hits"]["hits"]:
            hits.append(hit)
    yield hits
```

- [ ] **Step 4: Dry-run test**

```bash
docker compose run --rm ingestion python scripts/backfill_body_extraction.py --pilot --dry-run
```

Expected: ~50–125 lines of `[DRY RUN] would process ...` followed by a `backfill done` summary with `processed > 0`.

- [ ] **Step 5: Commit**

```bash
git add scripts/backfill_body_extraction.py
git commit -m "feat(scripts): backfill_body_extraction skeleton + eligibility query + pilot mode"
```

---

## Task 11: Backfill script — concurrent processing + bulk writes

Add the actual fetch-and-update path with concurrency caps and bulk OpenSearch writes.

**Files:**
- Modify: `scripts/backfill_body_extraction.py`

- [ ] **Step 1: Add concurrent processing helpers**

Above `main()`, add:

```python
from urllib.parse import urlparse


class _PerHostSemaphore:
    """asyncio.Semaphore per hostname, lazily created."""
    def __init__(self, per_host: int):
        self._per_host = per_host
        self._locks: dict[str, asyncio.Semaphore] = {}

    def get(self, url: str) -> asyncio.Semaphore:
        host = urlparse(url).hostname or "unknown"
        if host not in self._locks:
            self._locks[host] = asyncio.Semaphore(self._per_host)
        return self._locks[host]


async def _process_one(
    hit: dict,
    sources_by_name: dict,
    global_sem: asyncio.Semaphore,
    host_sem: _PerHostSemaphore,
) -> dict | None:
    """Run maybe_extract_body for a single article, return bulk-update doc or None."""
    src = hit["_source"]
    article_doc = {
        "slug": hit["_id"],
        "source_url": src.get("source_url", ""),
        "content_html": src.get("content_html", ""),
        "fetch_attempt_count": src.get("fetch_attempt_count") or 0,
    }
    source_dict = sources_by_name.get(src.get("source_name", ""), {})

    # Check r3 retry eligibility
    if not _is_retry_eligible(src):
        return None

    async with global_sem, host_sem.get(article_doc["source_url"]):
        try:
            updates = await maybe_extract_body(article_doc, source_dict)
        except Exception as exc:
            logger.warning("extraction crashed for %s: %s", hit["_id"], exc)
            return None

    if not updates:
        return None
    return {
        "_op_type": "update",
        "_index": INDEX_NEWS,
        "_id": hit["_id"],
        "doc": updates,
    }
```

- [ ] **Step 2: Add r3 retry eligibility helper**

```python
from datetime import datetime, timedelta, timezone


_RETRY_DELAYS = {1: timedelta(0), 2: timedelta(hours=1), 3: timedelta(hours=24)}


def _is_retry_eligible(src: dict) -> bool:
    """Return True if this article should be (re-)attempted now."""
    body_quality = src.get("body_quality")
    if body_quality not in ("failed", "empty", None):
        return False  # already ok / weak — don't reprocess
    attempts = src.get("fetch_attempt_count") or 0
    if attempts == 0:
        return True
    if attempts >= 3:
        return False
    last_str = src.get("last_fetch_attempt_at")
    if not last_str:
        return True
    last = datetime.fromisoformat(last_str.replace("Z", "+00:00"))
    next_eligible = last + _RETRY_DELAYS[min(attempts + 1, 3)]
    return datetime.now(timezone.utc) >= next_eligible
```

- [ ] **Step 3: Replace dry-run loop in main() with real processing**

Replace the contents of `async for batch in article_iter:` block in `main()` with:

```python
    global_sem = asyncio.Semaphore(GLOBAL_CONCURRENCY)
    host_sem = _PerHostSemaphore(PER_HOST_CONCURRENCY)
    pending_updates: list[dict] = []

    async for batch in article_iter:
        tasks = [
            _process_one(hit, sources_by_name, global_sem, host_sem)
            for hit in batch
        ]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        for r in results:
            if r is None:
                continue
            doc = r["doc"]
            quality = doc.get("body_quality")
            if quality == "ok":
                successes += 1
            elif quality == "weak":
                weak += 1
            else:
                failures += 1

            if args.dry_run:
                logger.info("[DRY RUN] would update %s body_quality=%s", r["_id"], quality)
            else:
                pending_updates.append(r)

        processed += len(batch)

        # Flush bulk batch
        if not args.dry_run and len(pending_updates) >= BULK_BATCH_SIZE:
            await _flush_bulk(client, pending_updates)
            pending_updates.clear()

        if processed % PROGRESS_LOG_EVERY < len(batch):
            elapsed = time.time() - started
            rate = processed / max(elapsed / 60, 0.001)
            logger.info(
                "PROGRESS %d processed | ok=%d weak=%d failed=%d | %.1f articles/min",
                processed, successes, weak, failures, rate
            )

    if not args.dry_run and pending_updates:
        await _flush_bulk(client, pending_updates)
```

- [ ] **Step 4: Add bulk-flush helper**

```python
from opensearchpy.helpers import async_bulk


async def _flush_bulk(client, actions: list[dict]) -> None:
    success, errors = await async_bulk(client, actions, raise_on_error=False)
    if errors:
        logger.warning("bulk write had %d errors", len(errors))
```

- [ ] **Step 5: Run pilot dry-run**

```bash
docker compose run --rm ingestion python scripts/backfill_body_extraction.py --pilot --dry-run
```

Expected: ~50–125 articles processed, `backfill done` summary with non-zero counts. No OpenSearch writes.

- [ ] **Step 6: Commit**

```bash
git add scripts/backfill_body_extraction.py
git commit -m "feat(scripts): backfill_body_extraction concurrent processing + bulk writes + r3 retry"
```

---

## Task 12: Pilot run + measurement

Real run on a small sample to validate the pipeline before full backfill.

- [ ] **Step 1: Run pilot for real (writes to OpenSearch)**

```bash
docker compose run --rm ingestion python scripts/backfill_body_extraction.py --pilot 2>&1 | tee /tmp/pilot.log
```

Expected: ~30–60 seconds to complete. Final line: `backfill done | processed=N ok=X weak=Y failed=Z`.

- [ ] **Step 2: Measure per-source result distribution**

Run the existing `scripts/measure_body_coverage.py` to confirm:

```bash
docker cp scripts/measure_body_coverage.py kiber-ingestion-1:/tmp/measure_body_coverage.py
docker exec -e OPENSEARCH_URL=https://81.17.98.185:9200 -e OPENSEARCH_USER=kiber_app -e OPENSEARCH_PASSWORD=$OS_PASS kiber-ingestion-1 python /tmp/measure_body_coverage.py
```

Compare to pre-pilot baseline. Coverage on the 100 pilot articles should improve significantly.

- [ ] **Step 3: Inspect failures by category**

```bash
docker exec -e OPENSEARCH_URL=https://81.17.98.185:9200 -e OPENSEARCH_USER=kiber_app -e OPENSEARCH_PASSWORD=$OS_PASS kiber-ingestion-1 python - <<'PY'
import asyncio, os, json
from collections import Counter
from opensearchpy import AsyncOpenSearch
async def main():
    c = AsyncOpenSearch(
        hosts=[os.environ["OPENSEARCH_URL"]],
        http_auth=(os.environ["OPENSEARCH_USER"], os.environ["OPENSEARCH_PASSWORD"]),
        use_ssl=True, verify_certs=False, ssl_show_warn=False,
    )
    r = await c.search(index="news_articles", body={
        "size": 0,
        "query": {"term": {"body_quality": "failed"}},
        "aggs": {
            "errors": {"terms": {"field": "body_fetch_error", "size": 30}},
            "sources": {"terms": {"field": "source_name.keyword", "size": 30}},
        }
    })
    a = r["aggregations"]
    print("Failure modes:")
    for b in a["errors"]["buckets"]:
        print(f"  {b['key']}: {b['doc_count']}")
    print("\nSources with failures:")
    for b in a["sources"]["buckets"]:
        print(f"  {b['key']}: {b['doc_count']}")
    await c.close()
asyncio.run(main())
PY
```

- [ ] **Step 4: Decide and document**

Based on the pilot results:
- If a source has >50% failures → flag for future Playwright opt-in (note in the spec's Open Items section)
- If a source has systematically short bodies that classify as `weak` → consider a per-source `min_body_chars` override (update the migration or run a manual UPDATE)
- If the global timeout (8s) is too aggressive → consider per-source override (out of scope for v1)

Document findings in a comment block at the top of the pilot log or as a brief note in the spec.

- [ ] **Step 5: No commit needed** — this is observational.

---

## Task 13: Full backfill

After pilot fixes, run the full backfill.

- [ ] **Step 1: Snapshot count before**

```bash
docker exec -e OPENSEARCH_URL=https://81.17.98.185:9200 -e OPENSEARCH_USER=kiber_app -e OPENSEARCH_PASSWORD=$OS_PASS kiber-ingestion-1 python - <<'PY'
import asyncio, os
from opensearchpy import AsyncOpenSearch
async def main():
    c = AsyncOpenSearch(
        hosts=[os.environ["OPENSEARCH_URL"]],
        http_auth=(os.environ["OPENSEARCH_USER"], os.environ["OPENSEARCH_PASSWORD"]),
        use_ssl=True, verify_certs=False, ssl_show_warn=False,
    )
    eligible = await c.count(index="news_articles", body={
        "query": {"bool": {"should": [
            {"bool": {"must_not": {"exists": {"field": "body_quality"}}}},
            {"term": {"body_quality": "failed"}},
            {"term": {"body_quality": "empty"}},
        ], "minimum_should_match": 1}}
    })
    print("Eligible articles:", eligible["count"])
    await c.close()
asyncio.run(main())
PY
```

Note the count.

- [ ] **Step 2: Run full backfill**

```bash
docker compose run --rm ingestion python scripts/backfill_body_extraction.py 2>&1 | tee /tmp/backfill_full.log
```

Expected: 30–90 minutes for ~1,500 articles. Progress logs every 100 articles. Final summary line.

- [ ] **Step 3: Verify resumability**

If the run was interrupted, simply re-run the same command — it picks up where it left off (queries on `body_quality IS NULL OR 'failed'`).

- [ ] **Step 4: No commit needed** — operational.

---

## Task 14: Verify launch criteria

Confirm we hit the t2 launch threshold and unblock the brief sub-project.

- [ ] **Step 1: Measure last-7-day coverage globally**

```bash
docker exec -e OPENSEARCH_URL=https://81.17.98.185:9200 -e OPENSEARCH_USER=kiber_app -e OPENSEARCH_PASSWORD=$OS_PASS kiber-ingestion-1 python - <<'PY'
import asyncio, os
from datetime import datetime, timedelta, timezone
from opensearchpy import AsyncOpenSearch
async def main():
    c = AsyncOpenSearch(
        hosts=[os.environ["OPENSEARCH_URL"]],
        http_auth=(os.environ["OPENSEARCH_USER"], os.environ["OPENSEARCH_PASSWORD"]),
        use_ssl=True, verify_certs=False, ssl_show_warn=False,
    )
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    base = {"range": {"published_at": {"gte": since}}}

    total = await c.count(index="news_articles", body={"query": base})
    ok = await c.count(index="news_articles", body={
        "query": {"bool": {"must": [base, {"term": {"body_quality": "ok"}}]}}
    })
    pct = 100 * ok["count"] // total["count"] if total["count"] else 0
    print(f"Last 7d total: {total['count']} | body_quality=ok: {ok['count']} ({pct}%)")
    print(f"Launch criterion (1) ≥50% global: {'PASS' if pct >= 50 else 'FAIL'}")
    await c.close()
asyncio.run(main())
PY
```

- [ ] **Step 2: Measure last-7-day coverage for top-5 credibility sources**

```bash
docker exec -e OPENSEARCH_URL=https://81.17.98.185:9200 -e OPENSEARCH_USER=kiber_app -e OPENSEARCH_PASSWORD=$OS_PASS kiber-ingestion-1 python - <<'PY'
import asyncio, os
from datetime import datetime, timedelta, timezone
from opensearchpy import AsyncOpenSearch
async def main():
    c = AsyncOpenSearch(
        hosts=[os.environ["OPENSEARCH_URL"]],
        http_auth=(os.environ["OPENSEARCH_USER"], os.environ["OPENSEARCH_PASSWORD"]),
        use_ssl=True, verify_certs=False, ssl_show_warn=False,
    )
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Top 5 sources by max credibility_weight in last 7d
    r = await c.search(index="news_articles", body={
        "size": 0,
        "query": {"range": {"published_at": {"gte": since}}},
        "aggs": {
            "by_source": {
                "terms": {"field": "source_name.keyword", "size": 5,
                          "order": {"max_cred": "desc"}},
                "aggs": {
                    "max_cred": {"max": {"field": "credibility_weight"}},
                    "ok_count": {"filter": {"term": {"body_quality": "ok"}}},
                }
            }
        }
    })
    print("Top-5 high-credibility sources, last 7d:")
    all_pass = True
    for b in r["aggregations"]["by_source"]["buckets"]:
        total = b["doc_count"]
        ok = b["ok_count"]["doc_count"]
        pct = 100 * ok // total if total else 0
        verdict = "PASS" if pct >= 80 else "FAIL"
        if pct < 80:
            all_pass = False
        print(f"  {b['key']:30} | n={total:4} | ok={ok:4} ({pct:3}%) [{verdict}]")
    print(f"Launch criterion (2) all top-5 ≥80%: {'PASS' if all_pass else 'FAIL'}")
    await c.close()
asyncio.run(main())
PY
```

- [ ] **Step 3: Decide**

If both criteria PASS → body extraction is done; brief sub-project is unblocked. Move on to writing its design spec.

If either FAILs → identify which sources are dragging coverage down. For each: check failure modes (Cloudflare? 403? short bodies?), apply per-source fix (Playwright flag for spec, `min_body_chars` override, etc.), re-run backfill or change source list. Repeat measurement.

- [ ] **Step 4: No commit needed** — observational.

---

## Done

The body extraction pipeline is ready when:

- ✅ Inline extraction live in `ingester.py`; new articles get body extraction
- ✅ Backfill complete; existing articles processed
- ✅ Launch criteria met (≥50% global, ≥80% top-5 credibility)
- ✅ Failure modes observable in logs
- ✅ Robots.txt respected (verifiable on a robots-disallowed test URL)

Next: Daily WhatsApp Brief design spec (separate brainstorming session).
