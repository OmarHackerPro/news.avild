from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SourceCategory(Base):
    __tablename__ = "source_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("feed_sources.id", ondelete="CASCADE"), nullable=False
    )
    category_label: Mapped[str] = mapped_column(String(255), nullable=False)
    ingest: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    priority_modifier: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0.0"
    )
    classified_by: Mapped[str] = mapped_column(String(20), nullable=False)
    classification_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        sa.UniqueConstraint("source_id", "category_label", name="uq_source_categories"),
    )
