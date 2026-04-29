def test_placeholder():
    assert True


from app.ingestion.normalizer import _strip_wp_footer, normalize_generic
from app.ingestion.sources import FeedSource


def _make_source(**overrides) -> FeedSource:
    defaults = {
        "name": "TestFeed",
        "url": "https://example.com/feed",
        "default_type": "news",
        "default_category": "breaking",
        "default_severity": None,
        "normalizer": "generic",
    }
    defaults.update(overrides)
    return FeedSource(**defaults)


class TestNormalizeGeneric:
    def test_prefers_content_encoded_over_summary(self):
        """content:encoded (full body) should be content_html, summary from desc."""
        entry = {
            "title": "Test Article",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "Short teaser text.",
            "content": [{"value": "<p>Full article body with <b>HTML</b> content here.</p>"}],
        }
        result = normalize_generic(entry, _make_source())
        assert result is not None
        assert result["content_html"] == "<p>Full article body with <b>HTML</b> content here.</p>"
        assert "Full article body" in result["summary"]
        assert result["desc"] == "Short teaser text."
        assert result["content_source"] == "rss"

    def test_falls_back_to_summary_when_no_content(self):
        """When no content:encoded, use summary for everything."""
        entry = {
            "title": "Test Article",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "<p>Only a summary here.</p>",
        }
        result = normalize_generic(entry, _make_source())
        assert result is not None
        assert result["content_html"] == "<p>Only a summary here.</p>"
        assert result["summary"] == "Only a summary here."
        assert result["desc"] == "Only a summary here."

    def test_strips_wp_footer_from_desc_and_summary(self):
        entry = {
            "title": "Test",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "Content here. The post Test appeared first on MySite.",
        }
        result = normalize_generic(entry, _make_source())
        assert result is not None
        assert "appeared first on" not in result["desc"]
        assert "appeared first on" not in result["summary"]

    def test_extracts_image_url(self):
        entry = {
            "title": "Test",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "text",
            "media_thumbnail": [{"url": "https://example.com/thumb.jpg"}],
        }
        result = normalize_generic(entry, _make_source())
        assert result is not None
        assert result["image_url"] == "https://example.com/thumb.jpg"

    def test_extracts_cve_ids_from_content(self):
        entry = {
            "title": "Patch for CVE-2026-1234",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "content": [{"value": "<p>Fixes CVE-2026-1234 and CVE-2026-5678</p>"}],
            "summary": "teaser",
        }
        result = normalize_generic(entry, _make_source())
        assert result is not None
        assert "CVE-2026-1234" in result["cve_ids"]
        assert "CVE-2026-5678" in result["cve_ids"]

    def test_extracts_cve_ids_from_tags(self):
        entry = {
            "title": "Test",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "text",
            "tags": [{"term": "CVE-2026-9999"}, {"term": "security"}],
        }
        result = normalize_generic(entry, _make_source())
        assert result is not None
        assert result["cve_ids"] == ["CVE-2026-9999"]

    def test_no_cve_ids_returns_empty_list(self):
        entry = {
            "title": "No CVEs here",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "text",
        }
        result = normalize_generic(entry, _make_source())
        assert result is not None
        assert result["cve_ids"] == []

    def test_returns_none_for_missing_title(self):
        entry = {"link": "https://example.com/article"}
        assert normalize_generic(entry, _make_source()) is None

    def test_returns_none_for_missing_link(self):
        entry = {"title": "Test"}
        assert normalize_generic(entry, _make_source()) is None

    def test_tags_extracted(self):
        entry = {
            "title": "Test",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "text",
            "tags": [{"term": "ransomware"}, {"term": "malware"}],
        }
        result = normalize_generic(entry, _make_source())
        assert result is not None
        assert "ransomware" in result["tags"]
        assert "malware" in result["tags"]

    def test_content_source_set_when_content_exists(self):
        entry = {
            "title": "Test",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "text",
        }
        result = normalize_generic(entry, _make_source())
        assert result["content_source"] == "rss"

    def test_content_source_none_when_no_content(self):
        entry = {
            "title": "Test",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
        }
        result = normalize_generic(entry, _make_source())
        assert result is not None
        assert result["content_source"] is None


class TestStripWpFooter:
    def test_strips_standard_footer(self):
        text = "Article summary here. The post My Title appeared first on SecurityWeek."
        assert _strip_wp_footer(text) == "Article summary here."

    def test_strips_unit42_footer(self):
        text = "Some content. The post Open, Closed and Broken appeared first on Unit 42."
        assert _strip_wp_footer(text) == "Some content."

    def test_no_match_returns_unchanged(self):
        text = "This is normal text with no footer."
        assert _strip_wp_footer(text) == "This is normal text with no footer."

    def test_empty_string(self):
        assert _strip_wp_footer("") == ""

    def test_only_footer(self):
        text = "The post Title appeared first on Site."
        assert _strip_wp_footer(text) == ""

    def test_does_not_match_mid_text(self):
        text = "The post appeared first on stage. Then more text follows here."
        assert _strip_wp_footer(text) == "The post appeared first on stage. Then more text follows here."


from app.ingestion.normalizer import _clean_truncated_text


class TestCleanTruncatedText:
    def test_complete_sentence_unchanged(self):
        text = "Microsoft patched the flaw on Tuesday."
        assert _clean_truncated_text(text) == text

    def test_question_mark_unchanged(self):
        text = "Is your stack vulnerable?"
        assert _clean_truncated_text(text) == text

    def test_quoted_sentence_unchanged(self):
        text = 'The vendor said the issue is "fully resolved."'
        assert _clean_truncated_text(text) == text

    def test_already_ellipsised_unchanged(self):
        text = "Some teaser…"
        assert _clean_truncated_text(text) == text

    def test_trims_back_to_last_full_sentence(self):
        text = (
            "An admin role meant for AI agents within Microsoft Entra ID could enable "
            "privilege escalation, according to new findings from Silverfort. "
            "Agent ID Administrator is a built-in role introduced by Microsoft to handle "
            "all aspects of an AI agent's identity lifecycle operations in a"
        )
        result = _clean_truncated_text(text)
        # Clean trim to the last complete sentence — no ellipsis needed.
        assert not result.endswith("…")
        assert "lifecycle operations in a" not in result
        assert result.startswith("An admin role")
        assert result.endswith("from Silverfort.")

    def test_appends_ellipsis_when_no_clean_break(self):
        text = "A very short fragment with no terminator that"
        result = _clean_truncated_text(text)
        assert result.endswith("…")

    def test_keeps_text_when_trim_loses_too_much(self):
        text = "Very short fragment that"
        result = _clean_truncated_text(text)
        assert result.endswith("…")
        assert "Very short fragment" in result

    def test_empty_string_returns_empty(self):
        assert _clean_truncated_text("") == ""


from app.ingestion.normalizer import _extract_image_url


class TestExtractImageUrl:
    def test_media_thumbnail(self):
        entry = {"media_thumbnail": [{"url": "https://example.com/thumb.jpg"}]}
        assert _extract_image_url(entry, None) == "https://example.com/thumb.jpg"

    def test_media_content_image(self):
        entry = {"media_content": [{"url": "https://example.com/img.png", "type": "image/png"}]}
        assert _extract_image_url(entry, None) == "https://example.com/img.png"

    def test_media_content_skips_non_image(self):
        entry = {"media_content": [{"url": "https://example.com/video.mp4", "type": "video/mp4"}]}
        assert _extract_image_url(entry, None) is None

    def test_enclosure_image(self):
        entry = {"links": [{"rel": "enclosure", "type": "image/jpeg", "href": "https://example.com/photo.jpg"}]}
        assert _extract_image_url(entry, None) == "https://example.com/photo.jpg"

    def test_enclosure_skips_audio(self):
        entry = {"links": [{"rel": "enclosure", "type": "audio/mpeg", "href": "https://example.com/podcast.mp3"}]}
        assert _extract_image_url(entry, None) is None

    def test_featured_image_custom_field(self):
        entry = {"featuredimage": "https://example.com/featured.jpg"}
        assert _extract_image_url(entry, None) == "https://example.com/featured.jpg"

    def test_img_tag_fallback(self):
        entry = {}
        html = '<p>Text <img src="https://example.com/inline.jpg" /> more</p>'
        assert _extract_image_url(entry, html) == "https://example.com/inline.jpg"

    def test_priority_media_thumbnail_over_img_tag(self):
        entry = {"media_thumbnail": [{"url": "https://example.com/thumb.jpg"}]}
        html = '<img src="https://example.com/inline.jpg" />'
        assert _extract_image_url(entry, html) == "https://example.com/thumb.jpg"

    def test_no_image_anywhere(self):
        entry = {}
        assert _extract_image_url(entry, None) is None
        assert _extract_image_url(entry, "<p>No images here</p>") is None

    def test_truncates_long_url(self):
        entry = {"media_thumbnail": [{"url": "https://example.com/" + "a" * 3000}]}
        result = _extract_image_url(entry, None)
        assert result is not None
        assert len(result) <= 2048


def test_source_category_model_imports():
    from app.db.models.source_category import SourceCategory
    assert SourceCategory.__tablename__ == "source_categories"

def test_feed_source_to_source_dict_includes_new_fields():
    from app.db.models.feed_source import FeedSource
    # Check the method signature returns new fields by inspecting column names
    cols = {c.key for c in FeedSource.__table__.columns}
    assert "credibility_weight" in cols
    assert "extract_cves" in cols
    assert "extract_cvss" in cols


from app.ingestion.normalizer import NORMALIZER_REGISTRY, normalize_cisa_news, normalize_article


class TestNormalizeArticle:
    def _make_source(self, **overrides) -> dict:
        defaults = {
            "name": "TestFeed",
            "url": "https://example.com/feed",
            "default_type": "news",
            "default_category": "breaking",
            "default_severity": None,
            "normalizer": "generic",
            "credibility_weight": 1.0,
            "extract_cves": False,
            "extract_cvss": False,
        }
        defaults.update(overrides)
        return defaults

    def test_returns_article_with_credibility_weight(self):
        entry = {
            "title": "Test Article",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "text",
        }
        result = normalize_article(entry, self._make_source(credibility_weight=1.5))
        assert result is not None
        assert result["credibility_weight"] == 1.5

    def test_credibility_weight_defaults_to_1(self):
        source = self._make_source()
        source.pop("credibility_weight")
        entry = {
            "title": "Test Article",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "text",
        }
        result = normalize_article(entry, source)
        assert result is not None
        assert result["credibility_weight"] == 1.0

    def test_does_not_extract_cves_when_flag_false(self):
        entry = {
            "title": "Patch for CVE-2026-1234",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "<p>Fixes CVE-2026-1234</p>",
        }
        result = normalize_article(entry, self._make_source(extract_cves=False))
        assert result is not None
        assert result.get("cve_ids") in ([], None)

    def test_extracts_cves_when_flag_true(self):
        entry = {
            "title": "Advisory",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "<p>Fixes CVE-2026-1234 and CVE-2026-5678</p>",
        }
        result = normalize_article(entry, self._make_source(extract_cves=True))
        assert result is not None
        assert "CVE-2026-1234" in result["cve_ids"]
        assert "CVE-2026-5678" in result["cve_ids"]

    def test_does_not_extract_cvss_when_flag_false(self):
        entry = {
            "title": "Advisory",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "Base Score: 9.8",
        }
        result = normalize_article(entry, self._make_source(extract_cvss=False))
        assert result is not None
        assert result.get("cvss_score") is None

    def test_extracts_cvss_when_flag_true(self):
        entry = {
            "title": "Advisory",
            "link": "https://example.com/article",
            "id": "https://example.com/article",
            "summary": "CVSS v3.1 Base Score: 9.8",
        }
        result = normalize_article(entry, self._make_source(extract_cvss=True))
        assert result is not None
        assert float(result["cvss_score"]) == 9.8

    def test_extracts_advisory_id_into_raw_metadata_when_flag_true(self):
        entry = {
            "title": "Advisory",
            "link": "https://www.cisa.gov/advisories/aa25-099A",
            "id": "https://www.cisa.gov/advisories/aa25-099A",
            "summary": "content",
        }
        result = normalize_article(entry, self._make_source(extract_cvss=True))
        assert result is not None
        assert result.get("raw_metadata", {}).get("advisory_id") == "AA25-099A"

    def test_returns_none_for_missing_title(self):
        entry = {"link": "https://example.com/article"}
        assert normalize_article(entry, self._make_source()) is None

    def test_returns_none_for_missing_link(self):
        entry = {"title": "Test"}
        assert normalize_article(entry, self._make_source()) is None


class TestNormalizerRegistry:
    def test_all_keys_present(self):
        expected = {
            "generic", "thn", "bleepingcomputer", "securityweek",
            "krebs", "cisa_news", "cisa_advisory",
        }
        assert set(NORMALIZER_REGISTRY.keys()) == expected

    def test_flag_entries_are_dicts(self):
        for key in ("generic", "thn", "bleepingcomputer", "securityweek", "krebs", "cisa_advisory"):
            assert isinstance(NORMALIZER_REGISTRY[key], dict), f"{key} must be a dict"

    def test_cisa_advisory_has_extraction_flags(self):
        flags = NORMALIZER_REGISTRY["cisa_advisory"]
        assert flags.get("extract_cves") is True
        assert flags.get("extract_cvss") is True

    def test_cisa_news_has_handler(self):
        assert "_handler" in NORMALIZER_REGISTRY["cisa_news"]
        assert callable(NORMALIZER_REGISTRY["cisa_news"]["_handler"])

    def test_generic_flags_are_empty(self):
        for key in ("generic", "thn", "bleepingcomputer", "securityweek", "krebs"):
            assert NORMALIZER_REGISTRY[key] == {}
