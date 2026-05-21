from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.ingestion.ingester import _prepare_article_doc, upsert_article


class TestPrepareArticleDoc:
    def test_known_fields_pass_through(self):
        article = {
            "slug": "test-article-abc12345",
            "guid": "https://example.com/1",
            "source_name": "TestFeed",
            "title": "Test Article",
            "desc": "Description",
            "published_at": datetime(2026, 3, 19, tzinfo=timezone.utc),
            "tags": ["security"],
            "keywords": [],
            "cve_ids": [],
        }
        slug, doc = _prepare_article_doc(article)
        assert slug == "test-article-abc12345"
        assert doc["title"] == "Test Article"
        assert doc["tags"] == ["security"]

    def test_unknown_fields_stripped(self):
        article = {
            "slug": "test-article-abc12345",
            "guid": "https://example.com/1",
            "source_name": "TestFeed",
            "title": "Test Article",
            "desc": "Description",
            "published_at": datetime(2026, 3, 19, tzinfo=timezone.utc),
            "tags": [],
            "keywords": [],
            "cve_ids": [],
            "bogus_field": "should be removed",
            "another_unknown": 42,
        }
        slug, doc = _prepare_article_doc(article)
        assert "bogus_field" not in doc
        assert "another_unknown" not in doc

    def test_datetime_serialized_to_iso(self):
        article = {
            "slug": "test-abc12345",
            "guid": "guid",
            "source_name": "Feed",
            "title": "T",
            "published_at": datetime(2026, 3, 19, 12, 0, 0, tzinfo=timezone.utc),
            "tags": [],
            "keywords": [],
            "cve_ids": [],
        }
        _, doc = _prepare_article_doc(article)
        assert doc["published_at"] == "2026-03-19T12:00:00+00:00"

    def test_defaults_set_for_missing_fields(self):
        article = {
            "slug": "test-abc12345",
            "guid": "guid",
            "source_name": "Feed",
            "title": "T",
            "published_at": "2026-03-19T12:00:00+00:00",
        }
        _, doc = _prepare_article_doc(article)
        assert doc["raw_tags"] == []
        assert doc["keywords"] == []
        assert doc["cve_ids"] == []
        assert doc["content_html"] is None
        assert doc["summary"] is None
        assert doc["content_source"] is None
        assert doc["body_quality"] == "empty"
        assert doc["body_source"] == "none"
        assert doc["body_fetch_error"] is None
        assert doc["last_fetch_attempt_at"] is None
        assert doc["fetch_attempt_count"] == 0
        assert doc["is_teaser"] is False



class TestUpsertArticleSponsored:
    @pytest.mark.asyncio
    async def test_sponsored_author_skipped_without_touching_opensearch(self):
        article = {
            "slug": "sponsored-post-abc12345",
            "author": "Sponsored by Acme Corp",
            "title": "Why you need our product",
            "source_name": "BleepingComputer",
        }
        with patch("app.ingestion.ingester.get_os_client") as mock_client:
            result = await upsert_article(article)
        assert result is False
        mock_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_sponsored_lowercase_variant_also_skipped(self):
        article = {
            "slug": "sponsored-post-def12345",
            "author": "sponsored by Keep Aware",
            "title": "Browser security whitepaper",
            "source_name": "BleepingComputer",
        }
        with patch("app.ingestion.ingester.get_os_client") as mock_client:
            result = await upsert_article(article)
        assert result is False
        mock_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_sponsored_author_proceeds_to_opensearch(self):
        article = {
            "slug": "real-article-ghi12345",
            "author": "Lawrence Abrams",
            "title": "New ransomware campaign targets hospitals",
            "source_name": "BleepingComputer",
            "published_at": "2026-05-14T10:00:00+00:00",
            "guid": "https://bc.com/1",
        }
        os_mock = AsyncMock()
        os_mock.count.return_value = {"count": 0}
        os_mock.index = AsyncMock()
        with patch("app.ingestion.ingester.get_os_client", return_value=os_mock):
            await upsert_article(article)
        os_mock.index.assert_awaited()


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


@pytest.mark.asyncio
async def test_ingest_all_feeds_calls_refresh_entity_intel():
    """ingest_all_feeds() must call refresh_entity_intel() before processing sources."""
    from unittest.mock import AsyncMock, patch, MagicMock

    mock_refresh = AsyncMock(return_value=1625)
    mock_sources = []  # no sources — just checking startup call happens

    with patch("app.ingestion.ingester.refresh_entity_intel", mock_refresh), \
         patch("app.ingestion.ingester.AsyncSessionLocal") as mock_session_cls:

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=mock_sources)))))
        mock_session_cls.return_value = mock_session

        from app.ingestion.ingester import ingest_all_feeds
        await ingest_all_feeds()

    mock_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_ingest_sets_cvss_and_severity_from_cve_intel(monkeypatch):
    """When article has CVEs known to cve_topics, ingest sets cvss_score + severity."""
    import app.ingestion.ingester as ingester

    article = {
        "slug": "test-article",
        "cve_ids": ["CVE-2026-9999"],
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


class TestMergeEntityCves:
    def test_adds_body_only_cve_from_ner(self):
        from app.ingestion.ingester import _merge_entity_cves
        result = _merge_entity_cves(
            ["CVE-2026-1111"],
            [
                {"type": "cve", "normalized_key": "cve-2026-2222"},
                {"type": "product", "normalized_key": "fortios"},
            ],
        )
        assert result == ["CVE-2026-1111", "CVE-2026-2222"]

    def test_dedups_case_insensitively(self):
        from app.ingestion.ingester import _merge_entity_cves
        result = _merge_entity_cves(
            ["CVE-2026-1111"],
            [{"type": "cve", "normalized_key": "cve-2026-1111"}],
        )
        assert result == ["CVE-2026-1111"]

    def test_empty_inputs_return_empty_list(self):
        from app.ingestion.ingester import _merge_entity_cves
        assert _merge_entity_cves([], []) == []

    def test_no_cve_entities_leaves_cve_ids_unchanged(self):
        from app.ingestion.ingester import _merge_entity_cves
        result = _merge_entity_cves(
            ["CVE-2026-1111"],
            [{"type": "actor", "normalized_key": "lazarus-group"}],
        )
        assert result == ["CVE-2026-1111"]
