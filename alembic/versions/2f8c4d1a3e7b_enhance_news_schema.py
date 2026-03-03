"""enhance news schema: feed_sources table and new article columns

Revision ID: 2f8c4d1a3e7b
Revises: b42302738ee1
Create Date: 2026-03-03 00:00:00.000000

Changes:
  - New table: feed_sources (source registry with ingestion config)
  - news_articles: add guid, source_id, source_name, author, content_html,
    image_url, cvss_score, cve_ids, raw_metadata
  - news_articles: make desc nullable (CISA News has no description)
  - news_articles: add GIN indexes on tags, keywords, cve_ids
  - news_articles: add B-tree indexes on guid, source_id, source_name,
    author, cvss_score
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '2f8c4d1a3e7b'
down_revision: Union[str, Sequence[str], None] = 'b42302738ee1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Create feed_sources table
    # ------------------------------------------------------------------
    op.create_table(
        'feed_sources',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('url', sa.String(length=2048), nullable=False),
        sa.Column('default_type', sa.String(length=50), nullable=False),
        sa.Column('default_category', sa.String(length=100), nullable=False),
        sa.Column('default_severity', sa.String(length=50), nullable=True),
        sa.Column('normalizer_key', sa.String(length=50), nullable=False, server_default='generic'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('last_fetched_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('fetch_interval_minutes', sa.Integer(), nullable=False, server_default='60'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_feed_sources_name'),
        sa.UniqueConstraint('url', name='uq_feed_sources_url'),
    )
    op.create_index(op.f('ix_feed_sources_id'), 'feed_sources', ['id'], unique=False)

    # ------------------------------------------------------------------
    # 2. Add new columns to news_articles
    # ------------------------------------------------------------------
    op.add_column('news_articles', sa.Column('guid', sa.String(length=2048), nullable=True))
    op.add_column('news_articles', sa.Column('source_id', sa.Integer(), nullable=True))
    op.add_column('news_articles', sa.Column('source_name', sa.String(length=255), nullable=True))
    op.add_column('news_articles', sa.Column('author', sa.String(length=255), nullable=True))
    op.add_column('news_articles', sa.Column('content_html', sa.Text(), nullable=True))
    op.add_column('news_articles', sa.Column('image_url', sa.String(length=2048), nullable=True))
    op.add_column('news_articles', sa.Column('cvss_score', sa.Numeric(precision=4, scale=1), nullable=True))
    op.add_column('news_articles', sa.Column('cve_ids', postgresql.ARRAY(sa.String()), nullable=True))
    op.add_column('news_articles', sa.Column('raw_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True))

    # ------------------------------------------------------------------
    # 3. Make desc nullable (CISA News items have no description)
    # ------------------------------------------------------------------
    op.alter_column('news_articles', 'desc', existing_type=sa.Text(), nullable=True)

    # ------------------------------------------------------------------
    # 4. Add constraints
    # ------------------------------------------------------------------
    op.create_unique_constraint('uq_news_articles_guid', 'news_articles', ['guid'])
    op.create_foreign_key(
        'fk_news_articles_source_id',
        'news_articles', 'feed_sources',
        ['source_id'], ['id'],
        ondelete='SET NULL',
    )

    # ------------------------------------------------------------------
    # 5. B-tree indexes
    # ------------------------------------------------------------------
    op.create_index('ix_news_articles_guid', 'news_articles', ['guid'], unique=False)
    op.create_index('ix_news_articles_source_id', 'news_articles', ['source_id'], unique=False)
    op.create_index('ix_news_articles_source_name', 'news_articles', ['source_name'], unique=False)
    op.create_index('ix_news_articles_author', 'news_articles', ['author'], unique=False)
    op.create_index('ix_news_articles_cvss_score', 'news_articles', ['cvss_score'], unique=False)

    # ------------------------------------------------------------------
    # 6. GIN indexes for array columns (efficient @> containment queries)
    # ------------------------------------------------------------------
    op.create_index(
        'ix_news_articles_tags_gin', 'news_articles', ['tags'],
        unique=False, postgresql_using='gin',
    )
    op.create_index(
        'ix_news_articles_keywords_gin', 'news_articles', ['keywords'],
        unique=False, postgresql_using='gin',
    )
    op.create_index(
        'ix_news_articles_cve_ids_gin', 'news_articles', ['cve_ids'],
        unique=False, postgresql_using='gin',
    )


def downgrade() -> None:
    # GIN indexes
    op.drop_index('ix_news_articles_cve_ids_gin', table_name='news_articles')
    op.drop_index('ix_news_articles_keywords_gin', table_name='news_articles')
    op.drop_index('ix_news_articles_tags_gin', table_name='news_articles')

    # B-tree indexes
    op.drop_index('ix_news_articles_cvss_score', table_name='news_articles')
    op.drop_index('ix_news_articles_author', table_name='news_articles')
    op.drop_index('ix_news_articles_source_name', table_name='news_articles')
    op.drop_index('ix_news_articles_source_id', table_name='news_articles')
    op.drop_index('ix_news_articles_guid', table_name='news_articles')

    # Constraints
    op.drop_constraint('fk_news_articles_source_id', 'news_articles', type_='foreignkey')
    op.drop_constraint('uq_news_articles_guid', 'news_articles', type_='unique')

    # Restore desc as NOT NULL
    op.alter_column('news_articles', 'desc', existing_type=sa.Text(), nullable=False)

    # Remove new columns
    op.drop_column('news_articles', 'raw_metadata')
    op.drop_column('news_articles', 'cve_ids')
    op.drop_column('news_articles', 'cvss_score')
    op.drop_column('news_articles', 'image_url')
    op.drop_column('news_articles', 'content_html')
    op.drop_column('news_articles', 'author')
    op.drop_column('news_articles', 'source_name')
    op.drop_column('news_articles', 'source_id')
    op.drop_column('news_articles', 'guid')

    # Drop feed_sources
    op.drop_index(op.f('ix_feed_sources_id'), table_name='feed_sources')
    op.drop_table('feed_sources')
