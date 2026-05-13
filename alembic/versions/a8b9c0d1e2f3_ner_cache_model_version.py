"""ner_cache add model_version column and composite primary key

Revision ID: a8b9c0d1e2f3
Revises: f7b8c9d0e1f2
Create Date: 2026-05-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "f7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add nullable, backfill, then enforce NOT NULL and switch PK
    op.add_column("ner_cache", sa.Column("model_version", sa.Text(), nullable=True))
    op.execute("UPDATE ner_cache SET model_version = 'haiku-4-5' WHERE model_version IS NULL")
    op.alter_column("ner_cache", "model_version", nullable=False)
    op.drop_constraint("ner_cache_pkey", "ner_cache", type_="primary")
    op.create_primary_key("ner_cache_pkey", "ner_cache", ["slug", "model_version"])


def downgrade() -> None:
    op.drop_constraint("ner_cache_pkey", "ner_cache", type_="primary")
    op.create_primary_key("ner_cache_pkey", "ner_cache", ["slug"])
    op.drop_column("ner_cache", "model_version")
