from typing import Optional, TypedDict


class FeedSource(TypedDict):
    name: str
    url: str
    default_type: str       # news | analysis | report | advisory | alert
    default_category: str   # research | deep-dives | beginner | dark-web | breaking
    default_severity: Optional[str]
    normalizer: str         # key into NORMALIZER_REGISTRY in normalizer.py


# To add a new feed: append one FeedSource entry here. Nothing else changes.
FEED_SOURCES: list[FeedSource] = [
    FeedSource(
        name="The Hacker News",
        url="https://feeds.feedburner.com/TheHackersNews",
        default_type="news",
        default_category="breaking",
        default_severity=None,
        normalizer="thn",
    ),
    FeedSource(
        name="BleepingComputer",
        url="https://www.bleepingcomputer.com/feed/",
        default_type="news",
        default_category="breaking",
        default_severity=None,
        normalizer="bleepingcomputer",
    ),
    FeedSource(
        name="CISA News",
        url="https://www.cisa.gov/news.xml",
        default_type="news",
        default_category="breaking",
        default_severity=None,
        normalizer="cisa_news",
    ),
    FeedSource(
        name="CISA Advisories",
        url="https://www.cisa.gov/cybersecurity-advisories/all.xml",
        default_type="advisory",
        default_category="breaking",
        default_severity=None,
        normalizer="cisa_advisory",
    ),
    FeedSource(
        name="SecurityWeek",
        url="https://www.securityweek.com/feed/",
        default_type="news",
        default_category="breaking",
        default_severity=None,
        normalizer="securityweek",
    ),
    FeedSource(
        name="Krebs on Security",
        url="https://krebsonsecurity.com/feed/",
        default_type="analysis",
        default_category="deep-dives",
        default_severity=None,
        normalizer="krebs",
    ),
]
