from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import auth, news
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
    version="0.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(news.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
