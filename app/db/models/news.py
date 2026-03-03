from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NewsArticle(Base):
    __tablename__ = "news_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    guid: Mapped[str | None] = mapped_column(String(2048), unique=True, nullable=True)

    # Source tracking
    source_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("feed_sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )

    # Core content
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    desc: Mapped[str | None] = mapped_column(Text, nullable=True)  # was NOT NULL
    content_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # Tags & keywords — GIN indexed in migration
    tags: Mapped[list] = mapped_column(ARRAY(String), default=list)
    keywords: Mapped[list] = mapped_column(ARRAY(String), default=list)

    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    severity: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # news | analysis | report | advisory | alert
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    # research | deep-dives | beginner | dark-web | breaking
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # Advisory-specific
    cvss_score: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 1), nullable=True, index=True
    )
    cve_ids: Mapped[list | None] = mapped_column(ARRAY(String), nullable=True)

    # Feed-specific extras (comment counts, CSAF links, CVSS vectors, etc.)
    raw_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )
