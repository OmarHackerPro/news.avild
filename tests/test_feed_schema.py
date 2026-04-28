from datetime import datetime, timezone

from app.schemas.feed import FeedSourceCreate, FeedSourceResponse, FeedSourceUpdate


def test_feed_source_create_accepts_runtime_config_fields():
    body = FeedSourceCreate(
        name="Test Feed",
        url="https://example.com/feed.xml",
        default_type="news",
        default_category="breaking",
        normalizer_key="generic",
        credibility_weight=1.2,
        extract_cves=True,
        extract_cvss=False,
        fetch_interval_minutes=30,
    )
    assert body.credibility_weight == 1.2
    assert body.extract_cves is True
    assert body.extract_cvss is False


def test_feed_source_update_accepts_runtime_config_fields():
    body = FeedSourceUpdate(
        credibility_weight=1.5,
        extract_cves=True,
        extract_cvss=True,
    )
    payload = body.model_dump(exclude_unset=True)
    assert payload["credibility_weight"] == 1.5
    assert payload["extract_cves"] is True
    assert payload["extract_cvss"] is True


def test_feed_source_response_includes_runtime_config_fields():
    row = type(
        "FeedRow",
        (),
        {
            "id": 1,
            "name": "Test Feed",
            "url": "https://example.com/feed.xml",
            "default_type": "news",
            "default_category": "breaking",
            "default_severity": None,
            "normalizer_key": "generic",
            "credibility_weight": 1.1,
            "extract_cves": True,
            "extract_cvss": False,
            "is_active": True,
            "last_fetched_at": None,
            "fetch_interval_minutes": 60,
            "consecutive_failures": 0,
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        },
    )()
    result = FeedSourceResponse.model_validate(row)
    assert result.credibility_weight == 1.1
    assert result.extract_cves is True
    assert result.extract_cvss is False
