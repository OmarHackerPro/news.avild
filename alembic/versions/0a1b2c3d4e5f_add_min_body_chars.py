"""Add min_body_chars to feed_sources

Revision ID: 0a1b2c3d4e5f
Revises: c1d2e3f4a5b6
Create Date: 2026-05-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0a1b2c3d4e5f"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "feed_sources",
        sa.Column("min_body_chars", sa.Integer(), nullable=True),
    )
    # MSRC/NVD sources not yet seeded in feed_sources — these are no-ops until added.
    op.execute("UPDATE feed_sources SET min_body_chars = 200 WHERE name IN ('Microsoft MSRC', 'Microsoft Security Response Center')")
    op.execute("UPDATE feed_sources SET min_body_chars = 200 WHERE name IN ('NVD', 'NIST NVD') OR url LIKE '%nist.gov%'")
    op.execute("UPDATE feed_sources SET min_body_chars = 400 WHERE name IN ('CISA Advisories', 'CISA News')")
    op.execute("UPDATE feed_sources SET min_body_chars = 800 WHERE name = 'Krebs on Security'")


def downgrade() -> None:
    op.drop_column("feed_sources", "min_body_chars")
