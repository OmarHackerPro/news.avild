import pytest
from app.ingestion.body_extractor import (
    classify_length,
    extract_text,
    _strip_related_footer,
)


@pytest.mark.parametrize("length,threshold,expected", [
    (3000, 1500, "ok"),
    (1500, 1500, "ok"),
    (1499, 1500, "weak"),
    (500, 1500, "weak"),
    (499, 1500, "empty"),
    (0, 1500, "empty"),
    # Per-source override (NVD = 200)
    (250, 200, "ok"),
    (200, 200, "ok"),
    (199, 200, "weak"),  # falls into [ceil(200/3), threshold) = [67, 200) bucket
    (67, 200, "weak"),   # exact weak_floor boundary: ceil(200/3) = 67
    (66, 200, "empty"),  # below ceil(200/3) = 67, so empty
])
def test_classify_length(length, threshold, expected):
    assert classify_length(length, threshold) == expected


SAMPLE_HTML = """
<html><body>
<nav>Site nav junk we don't want</nav>
<article>
<h1>Linux LPE in copy_file_range</h1>
<p>A vulnerability in the Linux kernel affecting all distributions
with kernels newer than 2017 was disclosed today. The flaw enables
local privilege escalation and has been confirmed across Ubuntu,
RHEL, SUSE, and Amazon Linux.</p>
<p>Mitigation: apply kernel patches as soon as your distribution
publishes them. CISA is expected to add CVE-2026-31431 to KEV.</p>
</article>
<footer>Copyright 2026</footer>
</body></html>
"""


def test_extract_text_returns_main_content():
    result = extract_text(SAMPLE_HTML)
    assert result is not None
    # Body content present
    assert "Linux kernel" in result
    assert "CVE-2026-31431" in result
    # Junk sections gone
    assert "Site nav junk" not in result
    assert "Copyright 2026" not in result


def test_extract_text_returns_none_on_empty_input():
    assert extract_text("") is None
    assert extract_text(None) is None  # type: ignore[arg-type]


def test_extract_text_returns_none_on_garbage():
    assert extract_text("not html at all") in (None, "")


def test_strip_related_footer_drops_trailing_links():
    text = (
        "Real article body about a Daemon Tools supply chain attack.\n"
        "More real content here.\n"
        "Related: 1,800 Hit in Mini Shai-Hulud Attack on SAP\n"
        "Related: SAP NPM Packages Targeted in Supply Chain Attack"
    )
    cleaned = _strip_related_footer(text)
    assert "Daemon Tools" in cleaned
    assert "More real content" in cleaned
    assert "Shai-Hulud" not in cleaned
    assert "Related:" not in cleaned


def test_strip_related_footer_tolerates_blank_lines():
    text = "Body paragraph.\n\nRelated: Some Other Article\n\nRelated: Another One\n"
    assert _strip_related_footer(text) == "Body paragraph."


def test_strip_related_footer_keeps_inline_related_mentions():
    """Only trailing Related: lines are stripped, not mid-body mentions."""
    text = "Related work in this field is ongoing.\nThe attack continued."
    assert _strip_related_footer(text) == text


def test_strip_related_footer_noop_without_footer():
    text = "Just a normal article body.\nSecond paragraph."
    assert _strip_related_footer(text) == text
