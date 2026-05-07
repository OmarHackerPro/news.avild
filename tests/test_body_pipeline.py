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
    assert "content_html" not in result


@pytest.mark.asyncio
async def test_uses_default_threshold_when_source_missing_override():
    rss_body = "x" * 2000
    article = {"slug": "t", "source_url": "https://example.com", "content_html": rss_body}
    source = {}  # no min_body_chars
    result = await maybe_extract_body(article, source, fetch_fn=None, extract_fn=None)
    assert result["body_source"] == "rss-full"
    assert result["body_quality"] == "ok"


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
    assert len(fetch_called) == 0


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
    assert result["body_source"] == "trafilatura"
    assert result["body_quality"] == "empty"
