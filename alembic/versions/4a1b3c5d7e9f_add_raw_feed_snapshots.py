"""add raw_feed_snapshots table for RSS content archival

Revision ID: 4a1b3c5d7e9f
Revises: 3b7d2e9f1a4c
Create Date: 2026-03-03

Changes:
  - New table: raw_feed_snapshots (stores raw RSS/XML per fetch)
  - Unique index on content_hash (SHA-256 dedup — identical fetches skipped)
  - B-tree index on source_name, fetched_at DESC
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "4a1b3c5d7e9f"
down_revision: Union[str, Sequence[str], None] = "3b7d2e9f1a4c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "raw_feed_snapshots",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("raw_content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("entry_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uix_raw_feed_snapshots_content_hash",
        "raw_feed_snapshots",
        ["content_hash"],
        unique=True,
    )
    op.create_index(
        "ix_raw_feed_snapshots_source_name",
        "raw_feed_snapshots",
        ["source_name"],
    )
    op.create_index(
        "ix_raw_feed_snapshots_fetched_at",
        "raw_feed_snapshots",
        [sa.text("fetched_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_raw_feed_snapshots_fetched_at", table_name="raw_feed_snapshots")
    op.drop_index("ix_raw_feed_snapshots_source_name", table_name="raw_feed_snapshots")
    op.drop_index("uix_raw_feed_snapshots_content_hash", table_name="raw_feed_snapshots")
    op.drop_table("raw_feed_snapshots")
