def test_placeholder():
    assert True


from app.ingestion.normalizer import _strip_wp_footer


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
