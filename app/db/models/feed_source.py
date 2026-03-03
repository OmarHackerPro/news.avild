from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FeedSource(Base):
    __tablename__ = "feed_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    url: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    default_type: Mapped[str] = mapped_column(String(50), nullable=False)
    default_category: Mapped[str] = mapped_column(String(100), nullable=False)
    default_severity: Mapped[str | None] = mapped_column(String(50), nullable=True)
    normalizer_key: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="generic"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    last_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fetch_interval_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="60"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
