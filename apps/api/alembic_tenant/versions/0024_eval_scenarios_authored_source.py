"""Admit source='authored' on eval_scenarios.

The golden registration route (POST /agents/{agent_id}/golden-scenarios, #56)
inserts rows the owner wrote by hand: dataset='golden', source='authored'.
0011's CHECK (eval_scenarios_source_check_v2) admits four machine origins and
would refuse the INSERT with CheckViolation, so this migration replaces it with
a v3 that adds 'authored'.

Same shape as 0011, for the same reasons:
  - the outgoing constraint is discovered via pg_constraint/pg_attribute, never
    hardcoded, so this applies whether the tenant sits on 0005's inline CHECK
    or 0011's v2;
  - the new constraint carries an explicit name so a re-run is a no-op;
  - downgrade restores v2 exactly.

No column changes. 'authored' is an ORIGIN claim ("the tenant submitted this
pair as their own"), never a label tier: the golden writer names no label
column, so label_trust_tier stays NULL on these rows.

Downgrade narrows the CHECK over live rows, so it fails while any
source='authored' row exists, exactly as 0011's narrowing fails over
'production' rows. Remove or re-source those rows first; the observed round
trip ran against a cleaned probe database.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_V3_CONSTRAINT_NAME = "eval_scenarios_source_check_v3"
_V2_CONSTRAINT_NAME = "eval_scenarios_source_check_v2"


def upgrade() -> None:
    op.execute(f"""
        DO $$
        DECLARE
            con_name text;
        BEGIN
            SELECT con.conname INTO con_name
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_attribute att
                ON att.attrelid = rel.oid AND att.attnum = ANY(con.conkey)
            WHERE rel.relname = 'eval_scenarios'
              AND con.contype = 'c'
              AND att.attname = 'source'
              AND con.conname <> '{_V3_CONSTRAINT_NAME}'
            LIMIT 1;

            IF con_name IS NOT NULL THEN
                EXECUTE format('ALTER TABLE eval_scenarios DROP CONSTRAINT %I', con_name);
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = '{_V3_CONSTRAINT_NAME}'
            ) THEN
                ALTER TABLE eval_scenarios
                    ADD CONSTRAINT {_V3_CONSTRAINT_NAME}
                    CHECK (source IN ('generated', 'mined', 'production', 'red_team', 'authored'));
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = '{_V3_CONSTRAINT_NAME}'
            ) THEN
                ALTER TABLE eval_scenarios DROP CONSTRAINT {_V3_CONSTRAINT_NAME};
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = '{_V2_CONSTRAINT_NAME}'
            ) THEN
                ALTER TABLE eval_scenarios
                    ADD CONSTRAINT {_V2_CONSTRAINT_NAME}
                    CHECK (source IN ('generated', 'mined', 'production', 'red_team'));
            END IF;
        END $$;
    """)
