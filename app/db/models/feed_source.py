from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
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
    credibility_weight: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="1.0"
    )
    extract_cves: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    extract_cvss: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
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
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    def to_source_dict(self) -> dict:
        """Return a dict matching the FeedSource TypedDict shape.

        Normalizers access source["name"], source["default_type"], etc.
        This bridges the ORM model to that interface without changing
        any normalizer code.
        """
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "default_type": self.default_type,
            "default_category": self.default_category,
            "default_severity": self.default_severity,
            "normalizer": self.normalizer_key,
            "credibility_weight": self.credibility_weight,
            "extract_cves": self.extract_cves,
            "extract_cvss": self.extract_cvss,
        }
