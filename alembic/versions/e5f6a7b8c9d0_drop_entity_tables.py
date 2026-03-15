"""Drop entity tables — entities now live in OpenSearch

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-15 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("article_entities")
    op.drop_table("entities")
    op.execute("DROP TYPE IF EXISTS entity_type_enum")


def downgrade() -> None:
    op.execute(
        "CREATE TYPE entity_type_enum AS ENUM "
        "('cve', 'vendor', 'product', 'actor', 'malware', 'tool')"
    )

    op.create_table(
        "entities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("type", sa.Enum(
            "cve", "vendor", "product", "actor", "malware", "tool",
            name="entity_type_enum", create_type=False,
        ), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("normalized_key", sa.String(500), unique=True, nullable=False),
        sa.Column("aliases", sa.ARRAY(sa.String), server_default="{}", nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("cvss_score", sa.Numeric(4, 1), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_entities_type", "entities", ["type"])
    op.create_index("ix_entities_normalized_key", "entities", ["normalized_key"])

    op.create_table(
        "article_entities",
        sa.Column("article_id", sa.String(500), primary_key=True),
        sa.Column(
            "entity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
