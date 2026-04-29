"""HTML page routes — serves Jinja2-rendered pages with server-side SEO.

Why this lives in FastAPI rather than nginx:
- Dynamic content pages (/cluster, /entity, /category) need their <title>,
  description, canonical, and JSON-LD rendered server-side from real data so
  crawlers and link previews see the right content without executing JS.
- Static SPA shells (/, /search, /preferences, ...) still benefit because they
  share the same SEO partial — single source of truth.

nginx is updated to proxy these paths to FastAPI; /static/* and /api/* still
go through nginx directly.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from opensearchpy import NotFoundError

from app.core.seo import (
    SEO,
    absolutize,
    build_canonical,
    default_schema,
    schema_article,
    schema_breadcrumb,
)
from app.core.templates import templates
from app.db.opensearch import INDEX_CLUSTERS, get_os_client

router = APIRouter(tags=["pages"], include_in_schema=False)


def _render(request: Request, template: str, seo: SEO) -> HTMLResponse:
    """One-line helper so every page route looks the same."""
    return templates.TemplateResponse(template, {"request": request, "seo": seo})


# ---------- Static SPA shells ----------------------------------------------
# Content is JS-driven, so SEO is generic per page-type. The reusable partial
# guarantees we never get duplicate or inconsistent meta tags.

@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    seo = SEO(
        title="news.avild.com — Security News & Threat Intel",
        description=(
            "Cybersecurity news, threat intelligence, and analysis. "
            "Stay updated on breaches, APT campaigns, malware, and infosec."
        ),
        canonical=build_canonical(request),
        og_type="website",
        og_image=absolutize("/static/img/og-default.png"),
        schema=default_schema(),
    )
    return _render(request, "index.html", seo)


@router.get("/search", response_class=HTMLResponse)
async def search_page(request: Request):
    seo = SEO(
        title="Search — news.avild.com",
        description="Search security news, IOCs, threat actors, CVEs, and APT reports.",
        # `q` is intentionally NOT kept — search-result URLs are not canonical
        # (would create infinite duplicate URLs for crawlers).
        canonical=build_canonical(request),
        schema=default_schema(),
    )
    return _render(request, "search.html", seo)


@router.get("/preferences", response_class=HTMLResponse)
async def preferences_page(request: Request):
    seo = SEO(
        title="My Stack — news.avild.com",
        description="Manage your followed topics, sources, and digest preferences.",
        canonical=build_canonical(request),
        robots="noindex, follow",  # user-specific page; don't index
    )
    return _render(request, "preferences.html", seo)


@router.get("/webhooks", response_class=HTMLResponse)
async def webhooks_page(request: Request):
    seo = SEO(
        title="Webhooks — news.avild.com",
        description="Configure webhook endpoints to receive real-time security event notifications.",
        canonical=build_canonical(request),
        robots="noindex, follow",
    )
    return _render(request, "webhooks.html", seo)


@router.get("/rss-config", response_class=HTMLResponse)
async def rss_config_page(request: Request):
    seo = SEO(
        title="RSS Feed — news.avild.com",
        description="Generate a personalized RSS feed of security news filtered by your interests.",
        canonical=build_canonical(request),
        robots="noindex, follow",
    )
    return _render(request, "rss-config.html", seo)


@router.get("/digest", response_class=HTMLResponse)
async def digest_page(request: Request):
    seo = SEO(
        title="Email Digest — news.avild.com",
        description="Subscribe to a daily or weekly email digest of curated cybersecurity news.",
        canonical=build_canonical(request),
        robots="noindex, follow",
    )
    return _render(request, "digest.html", seo)


# Auth pages — never indexable.
def _auth_seo(request: Request, title: str, desc: str) -> SEO:
    return SEO(
        title=title, description=desc,
        canonical=build_canonical(request),
        robots="noindex, nofollow",
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return _render(request, "login.html",
                   _auth_seo(request, "Log in — news.avild.com", "Log in to your account."))


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    return _render(request, "signup.html",
                   _auth_seo(request, "Sign up — news.avild.com", "Create an account."))


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return _render(request, "forgot-password.html",
                   _auth_seo(request, "Forgot password — news.avild.com", "Reset your password."))


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request):
    return _render(request, "reset-password.html",
                   _auth_seo(request, "Reset password — news.avild.com", "Set a new password."))


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    return _render(request, "profile.html",
                   _auth_seo(request, "Profile — news.avild.com", "Your account profile."))


# ---------- Dynamic content pages ------------------------------------------
# These render real per-URL SEO. Crawlers see the actual cluster/entity/category
# title and description in the initial HTML response.

_CATEGORY_LABELS = {
    "breaking": "Breaking News",
    "threat-intel": "Threat Intelligence",
    "malware": "Malware",
    "apt": "APT Campaigns",
    "breaches": "Data Breaches",
    "pentest": "Penetration Testing",
    "bug-bounty": "Bug Bounty",
    "deep-dives": "Deep Dives",
    "beginner": "Beginner",
    "research": "Research",
    "dark-web": "Dark Web",
}


@router.get("/category", response_class=HTMLResponse)
async def category_page(request: Request, category: Optional[str] = Query(None)):
    label = _CATEGORY_LABELS.get(category or "", "All Categories")
    seo = SEO(
        title=f"{label} — news.avild.com",
        description=f"Latest {label.lower()} security news, advisories, and analysis.",
        # Keep `category` in canonical — different categories ARE different pages.
        canonical=build_canonical(request, keep_params=["category"]),
        schema=[
            *default_schema(),
            schema_breadcrumb([
                ("Home", "/"),
                (label, f"/category?category={category}" if category else "/category"),
            ]),
        ],
    )
    return _render(request, "category.html", seo)


@router.get("/entity", response_class=HTMLResponse)
async def entity_page(request: Request, id: Optional[str] = Query(None)):
    # Minimal SEO — extend by fetching entity from OpenSearch like /cluster does.
    title = f"Entity {id} — news.avild.com" if id else "Entity — news.avild.com"
    seo = SEO(
        title=title,
        description="Security entity profile: CVE, vendor, product, threat actor, or malware family.",
        canonical=build_canonical(request, keep_params=["id"]),
        schema=default_schema(),
    )
    return _render(request, "entity.html", seo)


@router.get("/cluster", response_class=HTMLResponse)
async def cluster_page(request: Request, id: str = Query(...)):
    """Canonical example of dynamic SSR SEO.

    Fetches the cluster from OpenSearch and emits its real title, summary, and
    Article JSON-LD in the initial HTML — no JS required for crawlers.
    Falls back to generic SEO + 404 status if the cluster is missing.
    """
    try:
        resp = await get_os_client().get(index=INDEX_CLUSTERS, id=id)
        src = resp["_source"]
    except NotFoundError:
        # Render the page with no-index SEO + 404 status so crawlers drop the URL.
        seo = SEO(
            title="Cluster not found — news.avild.com",
            description="The requested security event cluster could not be found.",
            canonical=build_canonical(request, keep_params=["id"]),
            robots="noindex, follow",
        )
        return templates.TemplateResponse(
            "cluster.html", {"request": request, "seo": seo}, status_code=404,
        )

    headline = src.get("label") or "Security event cluster"
    summary = (src.get("summary") or src.get("why_it_matters")
               or "Deduplicated security event with sources, timeline, and analysis.")
    # Meta description sweet spot: 120–160 chars.
    description = summary[:157].rstrip() + "…" if len(summary) > 160 else summary

    canonical = build_canonical(request, keep_params=["id"])
    timeline = src.get("timeline") or []
    published = timeline[0].get("at") if timeline else None
    modified = timeline[-1].get("at") if timeline else None

    seo = SEO(
        title=f"{headline} — news.avild.com",
        description=description,
        canonical=canonical,
        og_type="article",
        schema=[
            *default_schema(),
            schema_article(
                headline=headline,
                description=description,
                url=canonical,
                published_at=published,
                modified_at=modified,
                news=True,  # NewsArticle for security news clusters
            ),
            schema_breadcrumb([
                ("Home", "/"),
                ("Clusters", "/"),
                (headline, canonical),
            ]),
        ],
    )
    return _render(request, "cluster.html", seo)
