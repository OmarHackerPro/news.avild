"""Add junk_tags JSONB to feed_sources

Revision ID: c1d2e3f4a5b6
Revises: b1c2d3e4f5a6
Create Date: 2026-05-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "feed_sources",
        sa.Column(
            "junk_tags",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    # Seed known blog-navigation junk per source
    op.execute("""
        UPDATE feed_sources SET junk_tags = '["news & events", "product updates", "testing and validation"]'::jsonb
        WHERE name = 'Red Canary'
    """)
    op.execute("""
        UPDATE feed_sources SET junk_tags = '["a little sunshine", "the coming storm", "ne''er-do-well news", "web fraud 2.0", "breadcrumbs"]'::jsonb
        WHERE name = 'Krebs on Security'
    """)
    op.execute("""
        UPDATE feed_sources SET junk_tags = '["uncategorized", "schneier news"]'::jsonb
        WHERE name = 'Schneier on Security'
    """)
    op.execute("""
        UPDATE feed_sources SET junk_tags = '["blog", "research (insikt)"]'::jsonb
        WHERE name = 'Recorded Future'
    """)
    op.execute("""
        UPDATE feed_sources SET junk_tags = '["full", "large", "medium", "thumbnail"]'::jsonb
        WHERE name = 'Securelist'
    """)
    op.execute("""
        UPDATE feed_sources SET junk_tags = '["my software", "update", "announcement", "beta"]'::jsonb
        WHERE name = 'Didier Stevens'
    """)
    op.execute("""
        UPDATE feed_sources SET junk_tags = '["featured", "in other news"]'::jsonb
        WHERE name = 'SecurityWeek'
    """)


def downgrade() -> None:
    op.drop_column("feed_sources", "junk_tags")
