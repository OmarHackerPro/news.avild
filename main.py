from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    admin, auth, clusters, digest, entities, exports, feed, feeds, news,
    preferences, rss, search, sources,
)
from app.core.config import settings
from app.db.opensearch import close_os_client, ensure_indexes
from app.db.session import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path("static/uploads/avatars").mkdir(parents=True, exist_ok=True)
    await ensure_indexes()
    yield
    await close_os_client()
    if engine is not None:
        await engine.dispose()


openapi_tags = [
    {"name": "news", "description": "Browse and filter cybersecurity news articles"},
    {"name": "clusters", "description": "Deduplicated clusters of related articles about the same event"},
    {"name": "entities", "description": "CVEs, vendors, products, threat actors, and malware/tools"},
    {"name": "search", "description": "Full-text search with faceted results"},
    {"name": "auth", "description": "User registration, login, and profile management"},
    {"name": "preferences", "description": "User preferences, followed categories, and bookmarks"},
    {"name": "digest", "description": "Daily/weekly digests and trending topics"},
    {"name": "exports", "description": "CSV, JSON, and STIX 2.1 data exports"},
    {"name": "rss", "description": "RSS 2.0 feed generation"},
    {"name": "feed", "description": "Main cluster feed for the home page"},
    {"name": "feeds", "description": "Feed source management (admin)"},
    {"name": "sources", "description": "Public list of active feed sources"},
    {"name": "admin", "description": "Administrative operations (requires X-Admin-Secret)"},
    {"name": "health", "description": "Service health check"},
]

app = FastAPI(
    title=settings.PROJECT_NAME,
    description=(
        "# news.avild.com API\n\n"
        "Cybersecurity news intelligence platform. Ingests, deduplicates, clusters, "
        "and ranks security news and advisories.\n\n"
        "## Authentication\n"
        "Most endpoints are public. Endpoints requiring authentication use JWT Bearer tokens. "
        "Obtain a token via `POST /api/auth/login` or `POST /api/auth/signup`, then pass it "
        "as `Authorization: Bearer <token>`.\n\n"
        "## Filtering\n"
        "List endpoints support filtering by category, type, severity, source, tags, CVE IDs, "
        "date range, and full-text search.\n\n"
        "## Fixtures\n"
        "Example response payloads are available at `/static/fixtures/*.json` for frontend development."
    ),
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    openapi_tags=openapi_tags,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(news.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(entities.router, prefix="/api")
app.include_router(feeds.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(sources.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(rss.router, prefix="/api")
app.include_router(preferences.router, prefix="/api")
app.include_router(digest.router, prefix="/api")
app.include_router(exports.router, prefix="/api")
app.include_router(clusters.router, prefix="/api")
app.include_router(feed.router, prefix="/api")


@app.get("/health", tags=["health"], summary="Health check")
async def health():
    return {"status": "ok", "version": "1.0.0"}
