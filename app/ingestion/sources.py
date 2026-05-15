from typing import NotRequired, Optional, TypedDict


class FeedSource(TypedDict):
    id: NotRequired[int]
    name: str
    url: str
    default_type: str       # news | analysis | report | advisory | alert
    default_category: str   # research | deep-dives | beginner | dark-web | breaking
    default_severity: Optional[str]
    normalizer: str         # key into NORMALIZER_REGISTRY in normalizer.py
    credibility_weight: NotRequired[float]   # score multiplier; default 1.0
    extract_cves: NotRequired[bool]          # extract CVE IDs from advisory HTML
    extract_cvss: NotRequired[bool]          # extract CVSS score from advisory HTML
    junk_tags: NotRequired[list[str]]        # blog-nav labels to discard; default []

# ============================================================
# BOOTSTRAP SEED DATA — not the live source list.
#
# The ingestion pipeline reads EXCLUSIVELY from the PostgreSQL
# feed_sources table at runtime.  This list only exists to
# provision a fresh environment (or tests) via:
#
#   docker compose exec ingestion python scripts/seed_sources.py
#
# To add a permanent new source → add it here, then run the
# seeder above.  Do NOT add sources directly to this list and
# expect the running ingester to pick them up automatically.
# ============================================================
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
        junk_tags=["featured", "in other news"],
    ),
    FeedSource(
        name="Krebs on Security",
        url="https://krebsonsecurity.com/feed/",
        default_type="analysis",
        default_category="deep-dives",
        default_severity=None,
        normalizer="krebs",
        junk_tags=["a little sunshine", "the coming storm", "ne'er-do-well news", "web fraud 2.0", "breadcrumbs"],
    ),
    FeedSource(
        name="Schneier on Security",
        url="https://www.schneier.com/feed/atom/",
        default_type="analysis",
        default_category="deep-dives",
        default_severity=None,
        normalizer="generic",
        junk_tags=["uncategorized", "schneier news"],
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
        junk_tags=["my software", "update", "announcement", "beta"],
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
        junk_tags=["blog", "research (insikt)"],
    ),
    FeedSource(
        name="Red Canary",
        url="https://redcanary.com/blog/feed/",
        default_type="analysis",
        default_category="research",
        default_severity=None,
        normalizer="generic",
        junk_tags=["news & events", "product updates", "testing and validation"],
    ),
    FeedSource(
        name="CyberScoop",
        url="https://cyberscoop.com/feed/atom/",
        default_type="news",
        default_category="breaking",
        default_severity=None,
        normalizer="generic",
    ),
    FeedSource(
        name="Securelist",
        url="https://securelist.com/feed/",
        default_type="report",
        default_category="research",
        default_severity=None,
        normalizer="securelist",
        credibility_weight=1.2,
        extract_cves=True,
        junk_tags=["full", "large", "medium", "thumbnail"],
    ),
    # --- Tier 1 additions ---
    FeedSource(
        name="Microsoft MSRC",
        url="https://api.msrc.microsoft.com/update-guide/rss",
        default_type="advisory",
        default_category="breaking",
        default_severity=None,
        normalizer="generic",
        credibility_weight=1.5,
        extract_cves=True,
    ),
    FeedSource(
        name="Talos Intelligence",
        url="https://blog.talosintelligence.com/rss",
        default_type="analysis",
        default_category="research",
        default_severity=None,
        normalizer="generic",
        credibility_weight=1.2,
        extract_cves=True,
    ),
    FeedSource(
        name="Mandiant Blog",
        url="https://www.mandiant.com/resources/blog/rss.xml",
        default_type="report",
        default_category="research",
        default_severity=None,
        normalizer="generic",
        credibility_weight=1.3,
    ),
    FeedSource(
        name="CrowdStrike Blog",
        url="https://www.crowdstrike.com/blog/feed/",
        default_type="report",
        default_category="research",
        default_severity=None,
        normalizer="generic",
        credibility_weight=1.2,
    ),
    FeedSource(
        name="Cisco Security Advisories",
        url="https://tools.cisco.com/security/center/psirtrss20/CiscoSecurityAdvisory.xml",
        default_type="advisory",
        default_category="breaking",
        default_severity=None,
        normalizer="generic",
        credibility_weight=1.3,
        extract_cves=True,
    ),
    FeedSource(
        name="NCSC UK",
        url="https://www.ncsc.gov.uk/api/1/services/v1/all-rss-feed.xml",
        default_type="advisory",
        default_category="breaking",
        default_severity=None,
        normalizer="generic",
        credibility_weight=1.5,
        extract_cves=True,
    ),
    FeedSource(
        name="Check Point Research",
        url="https://research.checkpoint.com/feed/",
        default_type="analysis",
        default_category="research",
        default_severity=None,
        normalizer="generic",
        credibility_weight=1.2,
    ),
    FeedSource(
        name="ESET WeLiveSecurity",
        url="https://www.welivesecurity.com/en/feed/",
        default_type="analysis",
        default_category="research",
        default_severity=None,
        normalizer="generic",
        credibility_weight=1.1,
        extract_cves=True,
    ),
    # --- Tier 2 additions ---
    FeedSource(
        name="Sophos News",
        url="https://www.sophos.com/en-us/blog/feed?id=blt6f15f4f7deaf4242",
        default_type="analysis",
        default_category="research",
        default_severity=None,
        normalizer="generic",
        credibility_weight=1.1,
    ),
    # JPCERT/CC: disabled 2026-05-15 — content is primarily Japanese.
    # Source remains here for historical record. is_active=False in DB (set by scripts/cleanup_jpcert.py).
    # Do NOT re-enable without adding translation support.
    FeedSource(
        name="JPCERT/CC",
        url="https://www.jpcert.or.jp/rss/jpcert.rdf",
        default_type="advisory",
        default_category="breaking",
        default_severity=None,
        normalizer="generic",
        credibility_weight=1.3,
        extract_cves=True,
    ),
]
