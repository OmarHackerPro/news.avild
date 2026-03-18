from typing import Optional, TypedDict


class FeedSource(TypedDict):
    name: str
    url: str
    default_type: str       # news | analysis | report | advisory | alert
    default_category: str   # research | deep-dives | beginner | dark-web | breaking
    default_severity: Optional[str]
    normalizer: str         # key into NORMALIZER_REGISTRY in normalizer.py


# Seed data — used by scripts/seed_sources.py and migration 5b2c3d4e6f7a.
# Runtime ingestion reads from the feed_sources DB table instead.
SEED_SOURCES: list[FeedSource] = [
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
    FeedSource(
        name="Schneier on Security",
        url="https://www.schneier.com/feed/atom/",
        default_type="analysis",
        default_category="deep-dives",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="Unit 42",
        url="https://unit42.paloaltonetworks.com/feed/",
        default_type="analysis",
        default_category="research",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="The DFIR Report",
        url="https://thedfirreport.com/feed/",
        default_type="report",
        default_category="deep-dives",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="SANS ISC",
        url="https://isc.sans.edu/rssfeed_full.xml",
        default_type="analysis",
        default_category="research",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="Troy Hunt",
        url="https://feeds.feedburner.com/TroyHunt",
        default_type="analysis",
        default_category="deep-dives",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="Didier Stevens",
        url="https://blog.didierstevens.com/feed/atom/",
        default_type="analysis",
        default_category="research",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="Dark Reading",
        url="https://www.darkreading.com/rss.xml",
        default_type="news",
        default_category="breaking",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="Google Threat Intelligence",
        url="https://cloudblog.withgoogle.com/topics/threat-intelligence/rss/",
        default_type="report",
        default_category="research",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="PortSwigger Research",
        url="https://portswigger.net/research/rss",
        default_type="analysis",
        default_category="research",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="Recorded Future",
        url="https://www.recordedfuture.com/feed",
        default_type="report",
        default_category="research",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="Red Canary",
        url="https://redcanary.com/blog/feed/",
        default_type="analysis",
        default_category="research",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="CyberScoop",
        url="https://cyberscoop.com/feed/atom/",
        default_type="news",
        default_category="breaking",
        default_severity=None,
        normalizer="generic",
    ),
]
