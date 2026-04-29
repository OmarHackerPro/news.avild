"""SEO model + helpers (reusable across all page renderers).

Pages build an `SEO` instance and pass it to templates; the `_seo.html` partial
emits title, meta description, canonical, Open Graph, Twitter, and JSON-LD.

Schema builders return plain dicts so the partial can serialize them with
Jinja's `tojson` filter (safe HTML escaping, no string concatenation).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urlencode, urlparse

from fastapi import Request

from app.core.config import settings

SITE_NAME = "news.avild.com"
SITE_LOGO = "/static/img/logo.png"
DEFAULT_OG_IMAGE = "/static/img/og-default.png"

# Query params we never want in canonical URLs (tracking, session, etc.).
_VOLATILE_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "referrer",
}


@dataclass
class SEO:
    """Per-page SEO context. Pass to templates as `seo`."""

    title: str
    description: str
    canonical: str
    og_type: str = "website"            # "website" | "article" | "product"
    og_image: str | None = None         # absolute or root-relative; partial absolutizes
    twitter_card: str = "summary_large_image"
    robots: str = "index, follow"
    locale: str = "en_US"
    schema: list[dict[str, Any]] = field(default_factory=list)


def _site_origin() -> str:
    """Origin (scheme://host) for absolute URLs. Driven by APP_BASE_URL config
    so canonical URLs stay stable across environments and behind proxies where
    `request.url` may report the internal host."""
    return settings.APP_BASE_URL.rstrip("/")


def absolutize(url: str | None) -> str | None:
    """Turn root-relative paths into absolute URLs against the site origin."""
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    return _site_origin() + ("" if url.startswith("/") else "/") + url.lstrip("/")


def build_canonical(
    request: Request,
    *,
    keep_params: Iterable[str] = (),
) -> str:
    """Generate a canonical URL for the current request.

    - Always uses APP_BASE_URL as origin (so prod canonicals don't leak dev hosts).
    - Drops tracking params and any param not in `keep_params`.
    - Sorts the kept params for deterministic output (avoids duplicate canonicals
      for `?a=1&b=2` vs `?b=2&a=1`).
    """
    path = urlparse(str(request.url)).path or "/"
    keep = set(keep_params)
    qp = sorted(
        (k, v) for k, v in request.query_params.multi_items()
        if k in keep and k not in _VOLATILE_PARAMS
    )
    base = _site_origin() + path
    return f"{base}?{urlencode(qp)}" if qp else base


# ---------- JSON-LD schema builders ----------------------------------------
# Each returns a dict; the partial serializes via |tojson. Keep output minimal —
# Google ignores unknown fields but warns on missing required ones.

def schema_organization() -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "Organization",
        "@id": f"{_site_origin()}/#org",
        "name": SITE_NAME,
        "url": _site_origin() + "/",
        "logo": {"@type": "ImageObject", "url": absolutize(SITE_LOGO)},
    }


def schema_website() -> dict[str, Any]:
    """WebSite + SearchAction enables Google's sitelinks search box."""
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "@id": f"{_site_origin()}/#site",
        "name": SITE_NAME,
        "url": _site_origin() + "/",
        "publisher": {"@id": f"{_site_origin()}/#org"},
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": f"{_site_origin()}/search?q={{search_term_string}}",
            },
            "query-input": "required name=search_term_string",
        },
    }


def schema_breadcrumb(items: list[tuple[str, str]]) -> dict[str, Any]:
    """items: [(name, url), ...] in order from root to current."""
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "name": name,
                "item": absolutize(url),
            }
            for i, (name, url) in enumerate(items)
        ],
    }


def schema_article(
    *,
    headline: str,
    description: str,
    url: str,
    published_at: str | None = None,
    modified_at: str | None = None,
    image: str | None = None,
    author: str | None = None,
    news: bool = False,
) -> dict[str, Any]:
    """Article (or NewsArticle when `news=True`).

    `headline` is capped at 110 chars per Google guidance for NewsArticle.
    """
    schema_type = "NewsArticle" if news else "Article"
    headline = (headline or "").strip()
    if news and len(headline) > 110:
        headline = headline[:107].rstrip() + "…"

    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": schema_type,
        "headline": headline,
        "description": description,
        "mainEntityOfPage": {"@type": "WebPage", "@id": absolutize(url)},
        "url": absolutize(url),
        "publisher": {
            "@type": "Organization",
            "name": SITE_NAME,
            "logo": {"@type": "ImageObject", "url": absolutize(SITE_LOGO)},
        },
    }
    if image:
        data["image"] = [absolutize(image)]
    if author:
        data["author"] = {"@type": "Person", "name": author}
    if published_at:
        data["datePublished"] = published_at
    if modified_at:
        data["dateModified"] = modified_at
    return data


def schema_product(
    *,
    name: str,
    description: str,
    url: str,
    image: str | None = None,
    brand: str | None = None,
    sku: str | None = None,
) -> dict[str, Any]:
    """Included for FE-12 completeness — wire up if a product surface ships."""
    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": name,
        "description": description,
        "url": absolutize(url),
    }
    if image:
        data["image"] = absolutize(image)
    if brand:
        data["brand"] = {"@type": "Brand", "name": brand}
    if sku:
        data["sku"] = sku
    return data


# ---------- Defaults --------------------------------------------------------

def default_schema() -> list[dict[str, Any]]:
    """Org + WebSite — emit on every page so Google has site-wide context."""
    return [schema_organization(), schema_website()]
