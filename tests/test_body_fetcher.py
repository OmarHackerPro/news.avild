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


from urllib.robotparser import RobotFileParser
from app.ingestion.body_fetcher import RobotsCache


def test_robots_cache_allows_when_no_robots(monkeypatch):
    cache = RobotsCache()
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
    cache = RobotsCache()
    assert cache.is_url_allowed("https://newsite.example.com/x", default_on_unknown=True) is True
