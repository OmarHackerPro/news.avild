"""Add brief_log table

Revision ID: f7b8c9d0e1f2
Revises: 0a1b2c3d4e5f
Create Date: 2026-05-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "0a1b2c3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "brief_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("period_date", sa.Date(), nullable=False),
        sa.Column("cluster_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="sent"),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("period_date", name="uq_brief_log_period_date"),
    )


def downgrade() -> None:
    op.drop_table("brief_log")
