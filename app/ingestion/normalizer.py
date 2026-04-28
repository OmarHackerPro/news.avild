import calendar
import hashlib
import re
from datetime import datetime, timezone
from decimal import Decimal
from html.parser import HTMLParser
from typing import Optional

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


def _extract_image_url(
    entry: feedparser.FeedParserDict, content_html: Optional[str]
) -> Optional[str]:
    """Extract article image URL from feed entry metadata or HTML content.

    Priority: media:thumbnail > media:content (image) > enclosure (image) > featuredImage > <img> tag.
    """
    # 1. media:thumbnail
    thumbs = entry.get("media_thumbnail") or []
    if thumbs and thumbs[0].get("url"):
        return thumbs[0]["url"][:2048]

    # 2. media:content (image types only)
    media = entry.get("media_content") or []
    for m in media:
        mtype = (m.get("type") or "").lower()
        if mtype.startswith("image/") and m.get("url"):
            return m["url"][:2048]

    # 3. enclosure links with image type
    for link in entry.get("links") or []:
        if link.get("rel") == "enclosure":
            ltype = (link.get("type") or "").lower()
            if ltype.startswith("image/") and link.get("href"):
                return link["href"][:2048]

    # 4. Custom fields (e.g. Unit 42 featuredImage) — feedparser lowercases element names
    featured = entry.get("featuredimage") or entry.get("featuredImage")
    if featured:
        return (featured if isinstance(featured, str) else str(featured))[:2048]

    # 5. <img> tag from HTML content
    if content_html:
        img = _extract_first_image(content_html)
        if img:
            return img[:2048]

    return None


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
    """Extract CISA advisory ID from URL (e.g. 'ICSA-26-057-05', 'AA25-099A').

    Handles both hyphenated prefixes (icsa-26-...) and year-embedded prefixes (aa25-...).
    """
    m = re.search(r"/((?:icsa|icsma|aa|ics)\d*-[\d\w-]+)(?:\?|$|/)", url, re.IGNORECASE)
    return m.group(1).upper() if m else None


def _build_cve_source(title: str, content_html: Optional[str], tags: list[str]) -> str:
    """Build a text blob for CVE extraction from article fields."""
    tag_text = " ".join(tags)
    return f"{title} {content_html or ''} {tag_text}"


def _body_quality_fields(
    *,
    title: str,
    body_source: str,
    body_text: str,
    summary_text: Optional[str],
) -> tuple[str, str, bool]:
    """Classify the quality/source of RSS body text for downstream auditing."""
    if body_source == "none":
        return "empty", "none", False

    body_text = (summary_text or body_text).strip()
    teaser = body_text.endswith("[...]")
    if teaser:
        return "teaser", body_source, True
    if len(body_text) >= 600:
        return "full", body_source, False
    return "partial", body_source, False


# ---------------------------------------------------------------------------
# Per-feed normalizers
# ---------------------------------------------------------------------------

def normalize_generic(
    entry: feedparser.FeedParserDict,
    source: FeedSource,
) -> Optional[NormalizedArticle]:
    """Universal normalizer for RSS 2.0 and Atom feeds."""
    return normalize_article(entry, source)


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
        source_id=source.get("id"),
        source_name=source["name"],
        title=title[:500],
        author="CISA",
        desc=None,  # CISA News feed has no description content
        content_html=None,
        summary=None,
        content_source=None,
        body_quality="empty",
        body_source="none",
        is_teaser=False,
        tags=[],
        keywords=[],
        published_at=_parse_date(entry),
        severity=source["default_severity"],
        type=source["default_type"],
        category=source["default_category"],
        source_url=link[:2048],
        credibility_weight=source.get("credibility_weight", 1.0),
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

    body_quality, body_source, is_teaser = _body_quality_fields(
        title=title,
        body_source="summary" if content_html else "none",
        body_text=strip_html(content_html),
        summary_text=desc,
    )

    return NormalizedArticle(
        slug=build_slug(title, guid),
        guid=guid,
        source_id=source.get("id"),
        source_name=source["name"],
        title=title[:500],
        author="CISA",
        desc=desc,
        content_html=content_html or None,
        summary=desc,
        content_source="rss" if content_html else None,
        body_quality=body_quality,
        body_source=body_source,
        is_teaser=is_teaser,
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


def normalize_article(
    entry: feedparser.FeedParserDict,
    source: dict,
) -> Optional[NormalizedArticle]:
    """Config-driven normalizer — reads extract_cvss flags from source dict.

    Replaces the per-source class hierarchy for all sources except cisa_news
    (which uses its own minimal function due to that feed's empty content).
    source dict must have: name, url, default_type, default_category, default_severity.
    Optional: id, credibility_weight (default 1.0), extract_cvss (default False).
    """
    title = (entry.get("title") or "").strip()
    link = (entry.get("link") or "").strip()

    if not title or not link:
        return None

    guid = (entry.get("id") or link).strip()

    # Content body: prefer content:encoded / Atom <content>, fall back to summary
    content_list = entry.get("content") or []
    content_value = (content_list[0].get("value") if content_list else "") or ""
    raw_desc = entry.get("summary") or entry.get("description") or ""

    if content_value:
        content_html = content_value or None
        desc_text = (
            _strip_wp_footer(strip_html(raw_desc).strip())
            or strip_html(content_value).strip()
            or title
        )
        summary_text = _strip_wp_footer(strip_html(content_value).strip())[:2000] or None
        body_source = "content"
    elif raw_desc:
        content_html = raw_desc or None
        desc_text = _strip_wp_footer(strip_html(raw_desc).strip()) or title
        summary_text = _strip_wp_footer(strip_html(raw_desc).strip())[:2000] or None
        body_source = "summary"
    else:
        content_html = None
        desc_text = title
        summary_text = None
        body_source = "none"

    tags = _extract_tags(entry)
    image_url = _extract_image_url(entry, content_html)
    body_quality, body_source, is_teaser = _body_quality_fields(
        title=title,
        body_source=body_source,
        body_text=strip_html(content_html or raw_desc),
        summary_text=summary_text,
    )
    cve_ids = _extract_cve_ids(_build_cve_source(title, content_html, tags))

    article = NormalizedArticle(
        slug=build_slug(title, guid),
        guid=guid,
        source_id=source.get("id"),
        source_name=source["name"],
        title=title[:500],
        author=(entry.get("author") or "").strip() or None,
        desc=desc_text,
        content_html=content_html,
        summary=summary_text,
        content_source="rss" if content_html else None,
        body_quality=body_quality,
        body_source=body_source,
        is_teaser=is_teaser,
        image_url=image_url[:2048] if image_url else None,
        tags=tags,
        keywords=[],
        published_at=_parse_date(entry),
        severity=source["default_severity"],
        type=source["default_type"],
        category=source["default_category"],
        source_url=link[:2048],
        cve_ids=cve_ids if cve_ids else [],
        credibility_weight=source.get("credibility_weight", 1.0),
    )

    # Conditional: extract CVSS score + advisory metadata
    if source.get("extract_cvss"):
        cvss = _extract_cvss_score(content_html or "")
        if cvss is not None:
            article["cvss_score"] = cvss
        raw_metadata: dict = {}
        advisory_id = _extract_advisory_id(link)
        if advisory_id:
            raw_metadata["advisory_id"] = advisory_id
        cvss_vector = _extract_cvss_vector(content_html or "")
        if cvss_vector:
            raw_metadata["cvss_vector"] = cvss_vector
        if raw_metadata:
            article["raw_metadata"] = raw_metadata

    return article


def normalize_with_registry(
    entry: feedparser.FeedParserDict,
    source: FeedSource | dict,
) -> Optional[NormalizedArticle]:
    """Normalize an entry using the same registry dispatch as live ingestion."""
    flags = NORMALIZER_REGISTRY.get(source["normalizer"])
    if flags is None:
        raise KeyError(source["normalizer"])
    handler = flags.get("_handler")
    if handler:
        return handler(entry, source)
    merged_source = {**source, **{k: v for k, v in flags.items() if not k.startswith("_")}}
    return normalize_article(entry, merged_source)


# ---------------------------------------------------------------------------
# Registry — string key → flag dict (or _handler for special cases).
# Keeps FeedSource as pure serializable data (no Callable references there).
# Ingester dispatch: if "_handler" present → call handler(entry, source);
#                    otherwise → normalize_article(entry, {**source, **flags})
# ---------------------------------------------------------------------------

NORMALIZER_REGISTRY: dict[str, dict] = {
    "generic":          {},
    "thn":              {},
    "bleepingcomputer": {},
    "securityweek":     {},
    "krebs":            {},
    "securelist":       {"extract_cves": True},
    "cisa_advisory":    {"extract_cves": True, "extract_cvss": True},
    "cisa_news":        {"_handler": normalize_cisa_news},
}
