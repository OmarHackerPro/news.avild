"""Add preferences JSONB column to users

Revision ID: 9a1b2c3d4e5f
Revises: 8e5f6a7b9c0d
Create Date: 2026-03-13 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "9a1b2c3d4e5f"
down_revision = "8e5f6a7b9c0d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("preferences", JSONB, server_default="{}", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "preferences")
