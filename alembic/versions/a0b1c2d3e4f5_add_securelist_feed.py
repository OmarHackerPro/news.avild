"""Add Securelist feed source

Revision ID: a0b1c2d3e4f5
Revises: f6a7b8c9d0e1
Create Date: 2026-04-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a0b1c2d3e4f5"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    feed_sources = sa.table(
        "feed_sources",
        sa.column("name", sa.String),
        sa.column("url", sa.String),
        sa.column("default_type", sa.String),
        sa.column("default_category", sa.String),
        sa.column("default_severity", sa.String),
        sa.column("normalizer_key", sa.String),
        sa.column("credibility_weight", sa.Float),
        sa.column("extract_cves", sa.Boolean),
    )
    op.bulk_insert(feed_sources, [
        {
            "name": "Securelist",
            "url": "https://securelist.com/feed/",
            "default_type": "report",
            "default_category": "research",
            "default_severity": None,
            "normalizer_key": "securelist",
            "credibility_weight": 1.2,
            "extract_cves": True,
        },
    ])


def downgrade() -> None:
    op.execute("DELETE FROM feed_sources WHERE name = 'Securelist'")
