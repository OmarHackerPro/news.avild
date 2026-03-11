from fastapi import APIRouter

from app.api.routes import admin, auth, feeds, news

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(news.router)
api_router.include_router(feeds.router)
api_router.include_router(admin.router)
