"""drop news_articles and raw_feed_snapshots (migrated to OpenSearch)

Run this ONLY after:
  1. migrate_to_opensearch.py has completed successfully
  2. The application has been deployed and is running cleanly against OpenSearch
  3. OpenSearch document counts have been verified against the original PG row counts

Revision ID: 7d4e5f6a8b9c
Revises: 6c3d4e5f7a8b
Create Date: 2026-03-10
"""
from alembic import op

revision = "7d4e5f6a8b9c"
down_revision = "6c3d4e5f7a8b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the partitioned news_articles table. CASCADE automatically removes
    # all monthly child partitions (news_articles_2025_01, etc.) and the default
    # partition, as well as any foreign key constraints referencing this table.
    op.execute("DROP TABLE IF EXISTS news_articles CASCADE")

    # Drop the raw feed snapshots archive table.
    op.execute("DROP TABLE IF EXISTS raw_feed_snapshots")


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrade not supported — data was migrated to OpenSearch. "
        "To revert, restore from a pg_dump backup taken before running this migration."
    )
