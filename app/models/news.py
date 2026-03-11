# Re-export from canonical schemas location for backwards compatibility
from app.schemas.news import NewsItem, NewsListResponse

__all__ = ["NewsItem", "NewsListResponse"]
