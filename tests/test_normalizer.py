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
