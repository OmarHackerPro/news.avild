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
