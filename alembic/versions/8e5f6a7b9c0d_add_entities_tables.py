"""add entities tables

Revision ID: 8e5f6a7b9c0d
Revises: 7d4e5f6a8b9c
Create Date: 2026-03-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "8e5f6a7b9c0d"
down_revision = "7d4e5f6a8b9c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum via raw SQL with IF NOT EXISTS to avoid asyncpg checkfirst issues
    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE entity_type_enum AS ENUM "
        "('cve','vendor','product','actor','malware','tool'); "
        "EXCEPTION WHEN duplicate_object THEN null; "
        "END $$"
    )

    # Use sa.Text for column type to prevent SQLAlchemy from auto-creating the enum,
    # then ALTER the column to use the enum type.
    op.create_table(
        "entities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("normalized_key", sa.String(500), nullable=False),
        sa.Column("cvss_score", sa.Numeric(4, 1), nullable=True),
        sa.Column(
            "first_seen",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.execute(
        "ALTER TABLE entities "
        "ALTER COLUMN type TYPE entity_type_enum "
        "USING type::entity_type_enum"
    )
    op.create_index("ix_entities_type", "entities", ["type"])
    op.create_index("ix_entities_normalized_key", "entities", ["normalized_key"], unique=True)

    op.create_table(
        "article_entities",
        sa.Column("article_id", sa.String(500), nullable=False),
        sa.Column(
            "entity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("article_id", "entity_id"),
    )
    op.create_index("ix_article_entities_article_id", "article_entities", ["article_id"])
    op.create_index("ix_article_entities_entity_id", "article_entities", ["entity_id"])


def downgrade() -> None:
    op.drop_table("article_entities")
    op.drop_table("entities")
    op.execute("DROP TYPE IF EXISTS entity_type_enum")
