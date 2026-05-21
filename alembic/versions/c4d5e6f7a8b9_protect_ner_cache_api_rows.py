"""protect ner_cache API-paid rows from update/delete

Revision ID: c4d5e6f7a8b9
Revises: 631e5372e334
Create Date: 2026-05-18

Anthropic-API rows in ner_cache (model_version not 'securebert%') are paid
extractions and must never be overwritten or deleted. This trigger enforces
that at the database level — application bugs, --force backfills, and manual
edits all hit the same hard stop.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "631e5372e334"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION ner_cache_protect_api_rows()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.model_version NOT LIKE 'securebert%' THEN
                RAISE EXCEPTION
                    'ner_cache row (slug=%, model_version=%) is API-paid and read-only',
                    OLD.slug, OLD.model_version
                    USING ERRCODE = 'check_violation';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER ner_cache_protect_api_rows_trg
        BEFORE UPDATE OR DELETE ON ner_cache
        FOR EACH ROW
        EXECUTE FUNCTION ner_cache_protect_api_rows();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ner_cache_protect_api_rows_trg ON ner_cache")
    op.execute("DROP FUNCTION IF EXISTS ner_cache_protect_api_rows()")
