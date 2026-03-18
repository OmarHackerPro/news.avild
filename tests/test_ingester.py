from datetime import datetime, timezone
from app.ingestion.ingester import _prepare_article_doc


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
        assert doc["tags"] == []
        assert doc["keywords"] == []
        assert doc["cve_ids"] == []
        assert doc["content_html"] is None
        assert doc["summary"] is None
        assert doc["content_source"] is None
