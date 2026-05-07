"""HTTP body fetcher with anti-bot tier (curl-cffi + headers + cookies + robots).

Public surface:
    fetch_body(url, *, timeout, extra_headers) -> FetchResult
    fetch_text(url, *, timeout) -> Optional[str]
    classify_fetch_error(*, status, body, exc) -> Optional[str]
    RobotsCache
"""
import asyncio
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser


_CF_CHALLENGE_TITLE = "<title>Just a moment...</title>"
_CF_CHL_SCRIPT_TOKEN = "cf-chl-"
_ROBOTS_TTL_SECONDS = 24 * 60 * 60  # 24h
_DEFAULT_TIMEOUT_SECONDS = 8

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

# Module-level session for cookie + connection persistence per process.
# Tests monkeypatch _SESSION=None + _make_session to inject fakes.
_SESSION = None


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
        return "connection-error"

    if status is None:
        return "connection-error"

    if status >= 400:
        return str(status)

    if body and (_CF_CHALLENGE_TITLE in body or _CF_CHL_SCRIPT_TOKEN in body):
        return "cloudflare-challenge"

    return None


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

        `fetcher` is callable: async (robots_url) -> Optional[str]
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


@dataclass
class FetchResult:
    status: Optional[int]
    body: Optional[str]
    error: Optional[str]  # body_fetch_error code; None on success


def _make_session():
    """Build the HTTP session. curl-cffi preferred; httpx fallback."""
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
    status = None

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

    # Both attempts exhausted on 429/503 — return the last seen status
    return FetchResult(status=status, body=None, error=str(status))


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
