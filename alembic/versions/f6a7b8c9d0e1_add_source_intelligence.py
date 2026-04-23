"""Add source intelligence: credibility fields + source_categories table

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-23

Changes:
  - feed_sources: add credibility_weight (FLOAT DEFAULT 1.0)
  - feed_sources: add extract_cves (BOOL DEFAULT FALSE)
  - feed_sources: add extract_cvss (BOOL DEFAULT FALSE)
  - Create source_categories table
  - Data: seed credibility weights for known high-signal sources
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. New columns on feed_sources
    # ------------------------------------------------------------------
    op.add_column(
        "feed_sources",
        sa.Column("credibility_weight", sa.Float(), nullable=False, server_default="1.0"),
    )
    op.add_column(
        "feed_sources",
        sa.Column("extract_cves", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "feed_sources",
        sa.Column("extract_cvss", sa.Boolean(), nullable=False, server_default="false"),
    )

    # ------------------------------------------------------------------
    # 2. source_categories table
    # ------------------------------------------------------------------
    op.create_table(
        "source_categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("feed_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category_label", sa.String(255), nullable=False),
        sa.Column("ingest", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("priority_modifier", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("classified_by", sa.String(20), nullable=False),
        sa.Column("classification_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("source_id", "category_label", name="uq_source_categories"),
    )

    # ------------------------------------------------------------------
    # 3. Seed credibility weights for known high-signal sources
    # ------------------------------------------------------------------
    op.execute(
        "UPDATE feed_sources SET credibility_weight = 1.5 "
        "WHERE name IN ('CISA Advisories', 'CISA News')"
    )
    op.execute(
        "UPDATE feed_sources SET credibility_weight = 1.2 "
        "WHERE name IN ('Krebs on Security', 'Schneier on Security')"
    )
    op.execute(
        "UPDATE feed_sources SET extract_cves = true, extract_cvss = true "
        "WHERE name = 'CISA Advisories'"
    )


def downgrade() -> None:
    op.drop_table("source_categories")
    op.drop_column("feed_sources", "extract_cvss")
    op.drop_column("feed_sources", "extract_cves")
    op.drop_column("feed_sources", "credibility_weight")
