from datetime import date
from app.briefing.formatter import format_brief


def test_format_brief_structure():
    clusters = [
        {
            "label": "Apache RCE",
            "summary_text": "Critical RCE in Apache. Millions affected. Patch now.",
            "cve_ids": ["CVE-2026-1234"],
            "max_cvss": 9.8,
            "cisa_kev": True,
        },
        {
            "label": "Windows Zero-Day",
            "summary_text": "Active exploitation of Windows kernel flaw.",
            "cve_ids": [],
            "max_cvss": 7.5,
            "cisa_kev": False,
        },
    ]
    brief_date = date(2026, 5, 8)
    result = format_brief(clusters, brief_date)

    assert "*Kiber Daily Brief*" in result
    assert "May 8, 2026" in result
    assert "2 stories" in result
    assert "*Apache RCE*" in result
    assert "CVE-2026-1234" in result
    assert "CVSS 9.8" in result
    assert "CISA KEV" in result
    assert "*Windows Zero-Day*" in result
    assert "news.avild.com" in result


def test_format_brief_no_cve_no_cvss():
    clusters = [
        {
            "label": "Generic Threat",
            "summary_text": "A generic threat was observed.",
            "cve_ids": [],
            "max_cvss": None,
            "cisa_kev": False,
        }
    ]
    result = format_brief(clusters, date(2026, 5, 8))
    assert "CVE" not in result
    assert "CVSS" not in result
    assert "CISA KEV" not in result
    assert "*Generic Threat*" in result


def test_format_brief_single_story_grammar():
    clusters = [
        {
            "label": "Solo Incident",
            "summary_text": "One story only.",
            "cve_ids": [],
            "max_cvss": None,
            "cisa_kev": False,
        }
    ]
    result = format_brief(clusters, date(2026, 5, 8))
    assert "1 story" in result
    assert "1 stories" not in result
