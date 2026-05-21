"""protect API-sourced rows in cisa_kev and entity_intel

Revision ID: 7256091b69c2
Revises: c4d5e6f7a8b9
Create Date: 2026-05-21

cisa_kev: vulnerability_name, date_added, cwes are write-once after first sync.
entity_intel rows with source='mitre_attack': display_name, entity_type write-once.
Same hard-stop pattern as ner_cache_protect_api_rows_trg.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "7256091b69c2"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION cisa_kev_protect_immutable_cols()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.vulnerability_name IS DISTINCT FROM OLD.vulnerability_name
               OR NEW.date_added IS DISTINCT FROM OLD.date_added
               OR NEW.cwes::text IS DISTINCT FROM OLD.cwes::text THEN
                RAISE EXCEPTION
                    'cisa_kev row (cve_id=%) has API-immutable columns; mutate via DELETE + INSERT only',
                    OLD.cve_id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER cisa_kev_protect_immutable_cols_trg
        BEFORE UPDATE ON cisa_kev
        FOR EACH ROW
        EXECUTE FUNCTION cisa_kev_protect_immutable_cols();
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION entity_intel_protect_mitre_rows()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.source = 'mitre_attack' AND (
                NEW.display_name IS DISTINCT FROM OLD.display_name
                OR NEW.entity_type IS DISTINCT FROM OLD.entity_type
            ) THEN
                RAISE EXCEPTION
                    'entity_intel row (key=%) is mitre_attack-sourced; display_name and entity_type are immutable',
                    OLD.normalized_key
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER entity_intel_protect_mitre_rows_trg
        BEFORE UPDATE ON entity_intel
        FOR EACH ROW
        EXECUTE FUNCTION entity_intel_protect_mitre_rows();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS cisa_kev_protect_immutable_cols_trg ON cisa_kev")
    op.execute("DROP FUNCTION IF EXISTS cisa_kev_protect_immutable_cols()")
    op.execute("DROP TRIGGER IF EXISTS entity_intel_protect_mitre_rows_trg ON entity_intel")
    op.execute("DROP FUNCTION IF EXISTS entity_intel_protect_mitre_rows()")
