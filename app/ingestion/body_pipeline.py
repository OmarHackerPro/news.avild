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
