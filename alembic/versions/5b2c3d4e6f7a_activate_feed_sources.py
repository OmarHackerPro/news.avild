"""activate feed_sources: add operational columns and seed initial feeds

Revision ID: 5b2c3d4e6f7a
Revises: 4a1b3c5d7e9f
Create Date: 2026-03-04

Changes:
  - feed_sources: add consecutive_failures (INT DEFAULT 0)
  - feed_sources: add updated_at (TIMESTAMPTZ)
  - Seed 6 initial RSS feed sources
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "5b2c3d4e6f7a"
down_revision: Union[str, Sequence[str], None] = "4a1b3c5d7e9f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Add operational columns
    # ------------------------------------------------------------------
    op.add_column(
        "feed_sources",
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "feed_sources",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ------------------------------------------------------------------
    # 2. Seed initial feeds
    # ------------------------------------------------------------------
    feed_sources = sa.table(
        "feed_sources",
        sa.column("name", sa.String),
        sa.column("url", sa.String),
        sa.column("default_type", sa.String),
        sa.column("default_category", sa.String),
        sa.column("default_severity", sa.String),
        sa.column("normalizer_key", sa.String),
    )
    op.bulk_insert(feed_sources, [
        {
            "name": "The Hacker News",
            "url": "https://feeds.feedburner.com/TheHackersNews",
            "default_type": "news",
            "default_category": "breaking",
            "default_severity": None,
            "normalizer_key": "thn",
        },
        {
            "name": "BleepingComputer",
            "url": "https://www.bleepingcomputer.com/feed/",
            "default_type": "news",
            "default_category": "breaking",
            "default_severity": None,
            "normalizer_key": "bleepingcomputer",
        },
        {
            "name": "CISA News",
            "url": "https://www.cisa.gov/news.xml",
            "default_type": "news",
            "default_category": "breaking",
            "default_severity": None,
            "normalizer_key": "cisa_news",
        },
        {
            "name": "CISA Advisories",
            "url": "https://www.cisa.gov/cybersecurity-advisories/all.xml",
            "default_type": "advisory",
            "default_category": "breaking",
            "default_severity": None,
            "normalizer_key": "cisa_advisory",
        },
        {
            "name": "SecurityWeek",
            "url": "https://www.securityweek.com/feed/",
            "default_type": "news",
            "default_category": "breaking",
            "default_severity": None,
            "normalizer_key": "securityweek",
        },
        {
            "name": "Krebs on Security",
            "url": "https://krebsonsecurity.com/feed/",
            "default_type": "analysis",
            "default_category": "deep-dives",
            "default_severity": None,
            "normalizer_key": "krebs",
        },
    ])


def downgrade() -> None:
    op.execute("DELETE FROM feed_sources WHERE name IN ("
               "'The Hacker News', 'BleepingComputer', 'CISA News', "
               "'CISA Advisories', 'SecurityWeek', 'Krebs on Security')")
    op.drop_column("feed_sources", "updated_at")
    op.drop_column("feed_sources", "consecutive_failures")
