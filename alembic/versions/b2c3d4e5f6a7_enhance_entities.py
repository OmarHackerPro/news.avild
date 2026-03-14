"""Add aliases and description to entities

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-13 12:02:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "entities",
        sa.Column("aliases", ARRAY(sa.String), server_default="{}", nullable=False),
    )
    op.add_column(
        "entities",
        sa.Column("description", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("entities", "description")
    op.drop_column("entities", "aliases")
