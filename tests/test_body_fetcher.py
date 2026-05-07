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
