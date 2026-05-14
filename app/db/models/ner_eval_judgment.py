"""SQLAlchemy model for ner_eval_judgments."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NerEvalJudgment(Base):
    __tablename__ = "ner_eval_judgments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_normalized_key: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)  # haiku | local | both
    input_zone: Mapped[str | None] = mapped_column(Text, nullable=True)  # shared | new-input | NULL
    verdict: Mapped[str | None] = mapped_column(Text, nullable=True)  # correct | wrong | skip | NULL
    judged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
