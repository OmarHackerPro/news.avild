from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import news
from app.core.config import settings
from app.db.session import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if engine is not None:
        await engine.dispose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="news.avild.com — Security News & Threat Intelligence Platform",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(news.router, prefix="/api")
