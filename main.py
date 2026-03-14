from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    admin, auth, clusters, digest, entities, exports, feeds, news,
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


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="news.avild.com — Security News & Threat Intelligence Platform",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
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


@app.get("/health", tags=["health"], summary="Health check")
async def health():
    return {"status": "ok", "version": "1.0.0"}
