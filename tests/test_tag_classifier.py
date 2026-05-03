import pytest
from app.ingestion.tag_classifier import classify_tags, TOPIC_MAP


# ---------------------------------------------------------------------------
# Junk filter
# ---------------------------------------------------------------------------

def test_junk_filter_strips_image_sizes():
    result = classify_tags(["full", "large", "medium", "thumbnail", "Malware"], [])
    assert "full" not in result["clean_tags"]
    assert "large" not in result["clean_tags"]
    assert "Malware" in result["clean_tags"]


def test_junk_filter_strips_emails():
    result = classify_tags(["user@example.com", "Ransomware"], [])
    assert not any("@" in t for t in result["clean_tags"])
    assert "Ransomware" in result["clean_tags"]


def test_junk_filter_strips_numeric():
    result = classify_tags(["12345", "CVE-2026-1234"], [])
    assert "12345" not in result["clean_tags"]
    assert "CVE-2026-1234" in result["clean_tags"]


def test_junk_filter_strips_source_specific():
    result = classify_tags(["uncategorized", "schneier news", "AI"], ["uncategorized", "schneier news"])
    assert "uncategorized" not in result["clean_tags"]
    assert "schneier news" not in result["clean_tags"]
    assert "AI" in result["clean_tags"]


def test_junk_filter_case_insensitive_source_specific():
    result = classify_tags(["Uncategorized", "AI"], ["uncategorized"])
    assert "Uncategorized" not in result["clean_tags"]
    assert "AI" in result["clean_tags"]


# ---------------------------------------------------------------------------
# Entity classifier
# ---------------------------------------------------------------------------

def test_entity_classifier_cve_pattern():
    result = classify_tags(["CVE-2026-12345"], [])
    entity_keys = [e["normalized_key"] for e in result["tag_entities"]]
    assert "cve-2026-12345" in entity_keys
    entity = next(e for e in result["tag_entities"] if e["normalized_key"] == "cve-2026-12345")
    assert entity["type"] == "cve"
    assert entity["source"] == "tag"
    assert entity["sources"] == ["tag"]


def test_entity_classifier_cve_infers_vulnerability_topic():
    result = classify_tags(["CVE-2026-12345"], [])
    assert "vulnerability" in result["normalized_topics"]


def test_entity_classifier_vendor_tag():
    result = classify_tags(["Ivanti"], [])
    entity_keys = [e["normalized_key"] for e in result["tag_entities"]]
    assert "ivanti" in entity_keys
    entity = next(e for e in result["tag_entities"] if e["normalized_key"] == "ivanti")
    assert entity["type"] == "vendor"


def test_entity_classifier_vendor_does_not_infer_topic():
    result = classify_tags(["Microsoft"], [])
    assert result["normalized_topics"] == []


def test_entity_classifier_malware_family():
    result = classify_tags(["LockBit"], [])
    entity_keys = [e["normalized_key"] for e in result["tag_entities"]]
    assert "lockbit" in entity_keys
    entity = next(e for e in result["tag_entities"] if e["normalized_key"] == "lockbit")
    assert entity["type"] == "malware"
    assert "malware" in result["normalized_topics"]


def test_entity_classifier_threat_actor_infers_nation_state():
    result = classify_tags(["Volt Typhoon"], [])
    entity_keys = [e["normalized_key"] for e in result["tag_entities"]]
    assert "volt-typhoon" in entity_keys
    assert "nation-state" in result["normalized_topics"]


def test_entity_classifier_tool_infers_malware_topic():
    result = classify_tags(["Mimikatz"], [])
    entity_keys = [e["normalized_key"] for e in result["tag_entities"]]
    assert "mimikatz" in entity_keys
    entity = next(e for e in result["tag_entities"] if e["normalized_key"] == "mimikatz")
    assert entity["type"] == "tool"
    assert "malware" in result["normalized_topics"]


# ---------------------------------------------------------------------------
# Topic mapper
# ---------------------------------------------------------------------------

def test_topic_mapper_vulnerability():
    result = classify_tags(["exploited", "zero-day"], [])
    assert "vulnerability" in result["normalized_topics"]


def test_topic_mapper_ransomware_maps_to_malware():
    result = classify_tags(["Ransomware"], [])
    assert "malware" in result["normalized_topics"]


def test_topic_mapper_phishing():
    result = classify_tags(["phishing"], [])
    assert "phishing" in result["normalized_topics"]


def test_topic_mapper_supply_chain():
    result = classify_tags(["supply chain"], [])
    assert "supply-chain" in result["normalized_topics"]


def test_topic_mapper_ai_security():
    result = classify_tags(["prompt injection"], [])
    assert "ai-security" in result["normalized_topics"]


def test_topic_mapper_unknown_tag_kept_in_clean_tags():
    result = classify_tags(["some-unknown-niche-tag"], [])
    assert "some-unknown-niche-tag" in result["clean_tags"]
    assert result["normalized_topics"] == []
    assert result["tag_entities"] == []


def test_topic_mapper_deduplicates_topics():
    # Both "Ransomware" (entity→malware) and "malware" (topic map) → only one "malware"
    result = classify_tags(["Ransomware", "malware"], [])
    assert result["normalized_topics"].count("malware") == 1


def test_empty_tags_returns_empty_result():
    result = classify_tags([], [])
    assert result["normalized_topics"] == []
    assert result["tag_entities"] == []
    assert result["clean_tags"] == []
