import calendar
import hashlib
import re
from datetime import datetime, timezone
from decimal import Decimal
from html.parser import HTMLParser
from typing import Callable, Optional

import feedparser

from app.ingestion.sources import FeedSource

# Type alias for a normalized article dict ready for DB insertion.
# Keys match NewsArticle ORM column names exactly.
NormalizedArticle = dict


# ---------------------------------------------------------------------------
# HTML stripping — stdlib only, no extra dependency
# ---------------------------------------------------------------------------

class _MLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.reset()
        self._fed: list[str] = []

    def handle_data(self, d: str) -> None:
        self._fed.append(d)

    def get_data(self) -> str:
        return " ".join(self._fed)


def strip_html(raw: str) -> str:
    if not raw:
        return ""
    s = _MLStripper()
    s.feed(raw)
    return re.sub(r"\s+", " ", s.get_data()).strip()


# ---------------------------------------------------------------------------
# Slug construction
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")[:200]


def build_slug(title: str, guid: str) -> str:
    """slugified title + 8-char SHA256 hash of the guid.

    Stable: same guid always yields the same slug.
    Unique: hash suffix prevents collisions between articles with similar titles.
    """
    base = _slugify(title)
    suffix = hashlib.sha256(guid.encode()).hexdigest()[:8]
    return f"{base}-{suffix}"


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

def _parse_date(entry: feedparser.FeedParserDict) -> datetime:
    """Convert feedparser's published_parsed (UTC time.struct_time) to datetime.
    Falls back to now() if absent or unparseable.
    """
    if getattr(entry, "published_parsed", None):
        return datetime.fromtimestamp(
            calendar.timegm(entry.published_parsed), tz=timezone.utc
        )
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _extract_tags(entry: feedparser.FeedParserDict) -> list[str]:
    """Extract RSS <category> elements into a flat list of strings."""
    return [
        t.get("term", "").strip()
        for t in entry.get("tags", [])
        if t.get("term", "").strip()
    ]


def _extract_first_image(html: str) -> Optional[str]:
    """Extract the src of the first <img> tag from HTML content."""
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    return m.group(1) if m else None


def _strip_wp_footer(text: str) -> str:
    """Remove WordPress 'The post X appeared first on Y.' syndication footer."""
    if not text:
        return ""
    return re.sub(r"\s*The post .+? appeared first on .+?\.\s*\Z", "", text).strip()


def _extract_cvss_score(html: str) -> Optional[Decimal]:
    """Extract a CVSS v3 base score from advisory HTML.

    Matches patterns like:
      Base Score: </th><td>9.8</td>
      CVSS v3.1 Base Score: 9.8
    """
    m = re.search(r"[Bb]ase\s+[Ss]core[^0-9]{0,30}(\d+\.\d+)", html)
    if m:
        try:
            score = float(m.group(1))
            if 0.0 <= score <= 10.0:
                return Decimal(str(score))
        except (ValueError, Exception):
            pass
    return None


def _extract_cve_ids(text: str) -> list[str]:
    """Extract all unique CVE IDs (CVE-YYYY-NNNNN) from text or HTML."""
    return list(dict.fromkeys(re.findall(r"CVE-\d{4}-\d+", text)))


def _extract_cvss_vector(html: str) -> Optional[str]:
    """Extract a CVSS v3 vector string (e.g. CVSS:3.1/AV:N/AC:L/...)."""
    m = re.search(r"(CVSS:3\.\d+/[A-Z0-9/:]+)", html)
    return m.group(1) if m else None


def _extract_advisory_id(url: str) -> Optional[str]:
    """Extract CISA advisory ID from URL (e.g. 'ICSA-26-057-05', 'AA25-099A')."""
    m = re.search(r"/((?:icsa|icsma|aa|ics)-[\d\w-]+)(?:\?|$|/)", url, re.IGNORECASE)
    return m.group(1).upper() if m else None


# ---------------------------------------------------------------------------
# Per-feed normalizers
# ---------------------------------------------------------------------------

def normalize_thn(
    entry: feedparser.FeedParserDict,
    source: FeedSource,
) -> Optional[NormalizedArticle]:
    """Normalizer for The Hacker News RSS feed.

    THN entry fields used:
      entry.title           plain text headline
      entry.link            canonical article URL
      entry.id / entry.guid unique identifier (may equal link)
      entry.summary         CDATA HTML description
      entry.published_parsed UTC struct_time
    """
    title = (entry.get("title") or "").strip()
    link  = (entry.get("link")  or "").strip()

    if not title or not link:
        return None  # signals ingester to skip this entry

    guid = (entry.get("id") or entry.get("guid") or link).strip()
    raw_desc = entry.get("summary") or entry.get("description") or ""
    desc = strip_html(raw_desc).strip() or title

    return NormalizedArticle(
        slug=build_slug(title, guid),
        guid=guid,
        source_name=source["name"],
        title=title[:500],
        author=(entry.get("author") or "").strip() or None,
        desc=desc,
        content_html=raw_desc or None,
        summary=strip_html(raw_desc).strip()[:2000] or None,
        content_source="rss" if raw_desc else None,
        tags=_extract_tags(entry),
        keywords=[],
        published_at=_parse_date(entry),
        severity=source["default_severity"],
        type=source["default_type"],
        category=source["default_category"],
        source_url=link[:2048],
    )


def normalize_generic(
    entry: feedparser.FeedParserDict,
    source: FeedSource,
) -> Optional[NormalizedArticle]:
    """Fallback normalizer for standard RSS 2.0 / Atom feeds.
    Copy and rename this when adding a feed that needs custom field mapping.
    """
    title = (entry.get("title") or "").strip()
    link  = (entry.get("link")  or "").strip()

    if not title or not link:
        return None

    guid = (entry.get("id") or link).strip()
    content_list = entry.get("content") or []
    raw_desc = (
        entry.get("summary")
        or (content_list[0].get("value") if content_list else "")
        or entry.get("description")
        or ""
    )
    desc = strip_html(raw_desc).strip() or title

    return NormalizedArticle(
        slug=build_slug(title, guid),
        guid=guid,
        source_name=source["name"],
        title=title[:500],
        author=(entry.get("author") or "").strip() or None,
        desc=desc,
        content_html=raw_desc or None,
        summary=strip_html(raw_desc).strip()[:2000] or None,
        content_source="rss" if raw_desc else None,
        tags=_extract_tags(entry),
        keywords=[],
        published_at=_parse_date(entry),
        severity=source["default_severity"],
        type=source["default_type"],
        category=source["default_category"],
        source_url=link[:2048],
    )


def normalize_bleepingcomputer(
    entry: feedparser.FeedParserDict,
    source: FeedSource,
) -> Optional[NormalizedArticle]:
    """Normalizer for BleepingComputer RSS feed.

    Fields: title, link, pubDate, dc:creator (author), categories (3-5 per item),
    guid, description (summary only — no full article text in feed).
    """
    title = (entry.get("title") or "").strip()
    link  = (entry.get("link")  or "").strip()

    if not title or not link:
        return None

    guid = (entry.get("id") or link).strip()
    raw_desc = entry.get("summary") or entry.get("description") or ""
    desc = strip_html(raw_desc).strip() or None

    return NormalizedArticle(
        slug=build_slug(title, guid),
        guid=guid,
        source_name=source["name"],
        title=title[:500],
        author=(entry.get("author") or "").strip() or None,
        desc=desc,
        content_html=raw_desc or None,
        summary=strip_html(raw_desc).strip()[:2000] or None,
        content_source="rss" if raw_desc else None,
        tags=_extract_tags(entry),
        keywords=[],
        published_at=_parse_date(entry),
        severity=source["default_severity"],
        type=source["default_type"],
        category=source["default_category"],
        source_url=link[:2048],
    )


def normalize_securityweek(
    entry: feedparser.FeedParserDict,
    source: FeedSource,
) -> Optional[NormalizedArticle]:
    """Normalizer for SecurityWeek RSS feed (WordPress-based).

    Fields: title, link, pubDate, dc:creator (author), categories (5-10 per item),
    guid, description. Summary only — WordPress appends a footer we strip.
    """
    title = (entry.get("title") or "").strip()
    link  = (entry.get("link")  or "").strip()

    if not title or not link:
        return None

    guid = (entry.get("id") or link).strip()
    raw_desc = entry.get("summary") or entry.get("description") or ""
    desc_text = strip_html(raw_desc).strip()
    # WordPress appends "The post [title] appeared first on SecurityWeek." — strip it
    desc_text = re.sub(
        r"\s*The post .+? appeared first on SecurityWeek\.\s*$", "", desc_text
    ).strip()
    desc = desc_text or None

    return NormalizedArticle(
        slug=build_slug(title, guid),
        guid=guid,
        source_name=source["name"],
        title=title[:500],
        author=(entry.get("author") or "").strip() or None,
        desc=desc,
        content_html=raw_desc or None,
        summary=desc_text[:2000] or None,
        content_source="rss" if raw_desc else None,
        tags=_extract_tags(entry),
        keywords=[],
        published_at=_parse_date(entry),
        severity=source["default_severity"],
        type=source["default_type"],
        category=source["default_category"],
        source_url=link[:2048],
    )


def normalize_cisa_news(
    entry: feedparser.FeedParserDict,
    source: FeedSource,
) -> Optional[NormalizedArticle]:
    """Normalizer for CISA News feed.

    This feed provides only title, link, and pubDate.
    No description, no categories. Author is always "CISA".
    desc is intentionally None — the feed contains no summary content.
    """
    title = (entry.get("title") or "").strip()
    link  = (entry.get("link")  or "").strip()

    if not title or not link:
        return None

    guid = (entry.get("id") or link).strip()

    return NormalizedArticle(
        slug=build_slug(title, guid),
        guid=guid,
        source_name=source["name"],
        title=title[:500],
        author="CISA",
        desc=None,  # CISA News feed has no description content
        content_html=None,
        summary=None,
        content_source=None,
        tags=[],
        keywords=[],
        published_at=_parse_date(entry),
        severity=source["default_severity"],
        type=source["default_type"],
        category=source["default_category"],
        source_url=link[:2048],
    )


def normalize_cisa_advisory(
    entry: feedparser.FeedParserDict,
    source: FeedSource,
) -> Optional[NormalizedArticle]:
    """Normalizer for CISA Cybersecurity Advisories feed.

    The <description> contains full HTML with CVSS tables, CVE references,
    affected products, and remediation guidance — stored as content_html.
    CVSS score and CVE IDs are extracted into dedicated columns.
    Advisory-specific extras (CVSS vector, advisory ID) go into raw_metadata.
    """
    title = (entry.get("title") or "").strip()
    link  = (entry.get("link")  or "").strip()

    if not title or not link:
        return None

    guid = (entry.get("id") or link).strip()
    content_html = entry.get("summary") or entry.get("description") or ""
    # Plain-text excerpt for list views (capped to avoid huge previews)
    desc = strip_html(content_html).strip()[:2000] or None

    cvss_score = _extract_cvss_score(content_html)
    cve_ids    = _extract_cve_ids(content_html)

    raw_metadata: dict = {}
    advisory_id = _extract_advisory_id(link)
    if advisory_id:
        raw_metadata["advisory_id"] = advisory_id
    cvss_vector = _extract_cvss_vector(content_html)
    if cvss_vector:
        raw_metadata["cvss_vector"] = cvss_vector

    return NormalizedArticle(
        slug=build_slug(title, guid),
        guid=guid,
        source_name=source["name"],
        title=title[:500],
        author="CISA",
        desc=desc,
        content_html=content_html or None,
        summary=desc,
        content_source="rss" if content_html else None,
        tags=[],
        keywords=[],
        published_at=_parse_date(entry),
        severity=source["default_severity"],
        type=source["default_type"],
        category=source["default_category"],
        source_url=link[:2048],
        cvss_score=cvss_score,
        cve_ids=cve_ids if cve_ids else None,
        raw_metadata=raw_metadata or None,
    )


def normalize_krebs(
    entry: feedparser.FeedParserDict,
    source: FeedSource,
) -> Optional[NormalizedArticle]:
    """Normalizer for Krebs on Security RSS feed.

    Krebs provides full article HTML in content:encoded, 10-20 categories per
    article, embedded images, and comment metadata (count + RSS feed link).
    """
    title = (entry.get("title") or "").strip()
    link  = (entry.get("link")  or "").strip()

    if not title or not link:
        return None

    guid = (entry.get("id") or link).strip()

    # Short teaser (entry.summary / description)
    raw_desc = entry.get("summary") or entry.get("description") or ""
    desc = strip_html(raw_desc).strip() or None

    # Full article HTML from content:encoded
    content_list = entry.get("content") or []
    content_html = (content_list[0].get("value") if content_list else "") or None

    # First image extracted from article body
    image_url = _extract_first_image(content_html) if content_html else None

    # CVE IDs from <category> tags (Krebs tags CVEs directly) + article body
    tags = _extract_tags(entry)
    tag_text = " ".join(tags)
    body_text = content_html or ""
    cve_ids = _extract_cve_ids(f"{tag_text} {body_text}")

    # Comment metadata
    raw_metadata: dict = {}
    comment_count = entry.get("slash_comments")
    if comment_count is not None:
        try:
            raw_metadata["comment_count"] = int(comment_count)
        except (ValueError, TypeError):
            pass
    comment_rss = entry.get("wfw_commentrss") or entry.get("wfw_comment_rss")
    if comment_rss:
        raw_metadata["comment_rss_url"] = comment_rss
    comments_url = entry.get("comments")
    if comments_url:
        raw_metadata["comments_url"] = comments_url

    return NormalizedArticle(
        slug=build_slug(title, guid),
        guid=guid,
        source_name=source["name"],
        title=title[:500],
        author=(entry.get("author") or "").strip() or None,
        desc=desc,
        content_html=content_html,
        summary=strip_html(content_html).strip()[:2000] if content_html else None,
        content_source="rss" if content_html else None,
        image_url=image_url[:2048] if image_url else None,
        tags=tags,
        keywords=[],
        published_at=_parse_date(entry),
        severity=source["default_severity"],
        type=source["default_type"],
        category=source["default_category"],
        source_url=link[:2048],
        cve_ids=cve_ids if cve_ids else None,
        raw_metadata=raw_metadata or None,
    )


# ---------------------------------------------------------------------------
# Registry — string key → callable
# Keeps FeedSource as pure serializable data (no Callable references there).
# ---------------------------------------------------------------------------

NORMALIZER_REGISTRY: dict[str, Callable] = {
    "thn":              normalize_thn,
    "generic":          normalize_generic,
    "bleepingcomputer": normalize_bleepingcomputer,
    "securityweek":     normalize_securityweek,
    "cisa_news":        normalize_cisa_news,
    "cisa_advisory":    normalize_cisa_advisory,
    "krebs":            normalize_krebs,
}
