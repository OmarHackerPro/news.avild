"""Tests for scripts/sync_cisa_kev.py — vendor normalization and KEV parsing."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.sync_cisa_kev import _normalize_vendor, _parse_kev


def test_normalize_vendor_strips_corp_suffix():
    assert _normalize_vendor("Microsoft Corp.") == "Microsoft"


def test_normalize_vendor_strips_llc():
    assert _normalize_vendor("Google LLC") == "Google"


def test_normalize_vendor_strips_inc():
    assert _normalize_vendor("Apple Inc.") == "Apple"


def test_normalize_vendor_strips_parenthetical():
    assert _normalize_vendor("Ivanti (formerly Pulse Secure)") == "Ivanti"


def test_normalize_vendor_passes_clean_name():
    assert _normalize_vendor("Fortinet") == "Fortinet"


def test_normalize_vendor_strips_whitespace():
    assert _normalize_vendor("  Cisco  ") == "Cisco"


def test_parse_kev_returns_cve_rows():
    raw = {
        "vulnerabilities": [
            {
                "cveID": "CVE-2024-1234",
                "vendorProject": "Microsoft Corp.",
                "product": "Windows",
                "vulnerabilityName": "Windows RCE",
                "dateAdded": "2024-01-15",
                "dueDate": "2024-02-05",
                "knownRansomwareCampaignUse": "Known",
                "cwes": ["CWE-79"],
            }
        ]
    }
    rows = _parse_kev(raw)
    assert len(rows) == 1
    row = rows[0]
    assert row["cve_id"] == "CVE-2024-1234"
    assert row["vendor"] == "Microsoft"          # normalized
    assert row["product"] == "Windows"
    assert row["known_ransomware_use"] is True
    assert row["cwes"] == ["CWE-79"]


def test_parse_kev_unknown_ransomware_is_false():
    raw = {
        "vulnerabilities": [
            {
                "cveID": "CVE-2024-9999",
                "vendorProject": "Cisco",
                "product": "IOS XE",
                "vulnerabilityName": "Test",
                "dateAdded": "2024-03-01",
                "dueDate": None,
                "knownRansomwareCampaignUse": "Unknown",
                "cwes": [],
            }
        ]
    }
    rows = _parse_kev(raw)
    assert rows[0]["known_ransomware_use"] is False


def test_parse_kev_deduplicates_vendors():
    """Two KEV entries from same vendor (after normalization) yield one vendor key."""
    raw = {
        "vulnerabilities": [
            {
                "cveID": "CVE-2024-0001",
                "vendorProject": "Microsoft Corp.",
                "product": "Windows",
                "vulnerabilityName": "A",
                "dateAdded": "2024-01-01",
                "dueDate": None,
                "knownRansomwareCampaignUse": "Unknown",
                "cwes": [],
            },
            {
                "cveID": "CVE-2024-0002",
                "vendorProject": "Microsoft",
                "product": "Exchange",
                "vulnerabilityName": "B",
                "dateAdded": "2024-01-02",
                "dueDate": None,
                "knownRansomwareCampaignUse": "Unknown",
                "cwes": [],
            },
        ]
    }
    rows = _parse_kev(raw)
    vendor_names = {r["vendor"] for r in rows}
    assert vendor_names == {"Microsoft"}  # both normalize to same vendor name
    assert len(rows) == 2  # two CVE rows still exist
