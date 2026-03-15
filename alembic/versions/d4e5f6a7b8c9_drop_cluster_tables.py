"""Drop cluster tables — clusters now live in OpenSearch

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-03-14 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("cluster_articles")
    op.drop_table("clusters")


def downgrade() -> None:
    op.create_table(
        "clusters",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("label", sa.String(500), nullable=False),
        sa.Column("state", sa.String(50), nullable=False, server_default="new"),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("why_it_matters", sa.Text, nullable=True),
        sa.Column("score", sa.Numeric(5, 2), nullable=True),
        sa.Column("confidence", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_clusters_state", "clusters", ["state"])

    op.create_table(
        "cluster_articles",
        sa.Column(
            "cluster_id",
            UUID(as_uuid=True),
            sa.ForeignKey("clusters.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("article_id", sa.String(500), primary_key=True),
    )
    op.create_index("ix_cluster_articles_article_id", "cluster_articles", ["article_id"])
