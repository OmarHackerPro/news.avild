"""create ner_eval_judgments table

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-05-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9c0d1e2f3a4"
down_revision: Union[str, Sequence[str], None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ner_eval_judgments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_normalized_key", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("input_zone", sa.Text(), nullable=True),
        sa.Column("verdict", sa.Text(), nullable=True),
        sa.Column("judged_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("source IN ('haiku', 'local', 'both')", name="ck_source"),
        sa.CheckConstraint("input_zone IN ('shared', 'new-input') OR input_zone IS NULL", name="ck_input_zone"),
        sa.CheckConstraint("verdict IN ('correct', 'wrong', 'skip') OR verdict IS NULL", name="ck_verdict"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", "entity_type", "entity_normalized_key", "source", name="uq_ner_eval_judgment"),
    )
    op.create_index("ix_ner_eval_judgments_slug", "ner_eval_judgments", ["slug"])
    op.create_index("ix_ner_eval_judgments_unjudged", "ner_eval_judgments", ["verdict"], postgresql_where=sa.text("verdict IS NULL"))


def downgrade() -> None:
    op.drop_index("ix_ner_eval_judgments_unjudged", table_name="ner_eval_judgments")
    op.drop_index("ix_ner_eval_judgments_slug", table_name="ner_eval_judgments")
    op.drop_table("ner_eval_judgments")
