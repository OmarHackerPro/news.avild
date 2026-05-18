"""add_entity_intel_tables

Revision ID: 631e5372e334
Revises: b9c0d1e2f3a4
Create Date: 2026-05-18 07:03:51.525375

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '631e5372e334'
down_revision: Union[str, Sequence[str], None] = 'b9c0d1e2f3a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "entity_intel",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("normalized_key", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("aliases", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "last_synced",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_key"),
    )
    op.create_index("entity_intel_type_idx", "entity_intel", ["entity_type"])
    op.create_index("entity_intel_source_idx", "entity_intel", ["source"])

    op.create_table(
        "cisa_kev",
        sa.Column("cve_id", sa.String(), nullable=False),
        sa.Column("vendor", sa.String(), nullable=False),
        sa.Column("product", sa.String(), nullable=False),
        sa.Column("vulnerability_name", sa.String(), nullable=False),
        sa.Column("date_added", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("known_ransomware_use", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("cwes", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "last_synced",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("cve_id"),
    )
    op.create_index("cisa_kev_vendor_idx", "cisa_kev", ["vendor"])


def downgrade() -> None:
    op.drop_index("cisa_kev_vendor_idx", "cisa_kev")
    op.drop_table("cisa_kev")
    op.drop_index("entity_intel_source_idx", "entity_intel")
    op.drop_index("entity_intel_type_idx", "entity_intel")
    op.drop_table("entity_intel")
