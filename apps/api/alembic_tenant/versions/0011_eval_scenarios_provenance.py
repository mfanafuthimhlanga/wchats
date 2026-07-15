"""Tenant DB v11 migration — eval_scenarios provenance (OPS-11/OPS-12/OPS-14).

Revision ID: 0011
Revises: 0010

Context:
    OPS-11 (promote-trace-to-scenario) and OPS-14 (21-08, red-team finding
    containment) both need to INSERT INTO eval_scenarios with new source
    values ('production', 'red_team') that the live CHECK constraint
    (0005_verified_qa_eval_scenarios.py:79-80) does not allow — it currently
    only permits ('generated', 'mined'). Inserting either new value before
    widening the constraint raises psycopg2.errors.CheckViolation at INSERT
    time (21-RESEARCH.md Pitfall 2).

    This migration widens the CHECK *and* adds the two new provenance columns
    in the SAME migration — doing them separately would leave a window where
    the constraint is still narrow but code already expects the new columns,
    or vice versa (Pitfall 2's explicit "must land together" requirement).

    Constraint name discovery:
        The original CHECK on eval_scenarios.source was written as an inline,
        unnamed constraint (`source TEXT NOT NULL CHECK (source IN (...))`).
        Postgres auto-names unnamed inline CHECKs (typically
        `eval_scenarios_source_check`), but this migration does NOT hardcode
        that name — it discovers whatever CHECK constraint currently governs
        the `source` column via pg_constraint/pg_attribute at migration-apply
        time (21-RESEARCH.md Assumption A5) and drops exactly that one. This
        is safe even if the live DB's auto-generated name differs from what
        reading the 0005 source would suggest.

    Idempotency:
        The widened constraint is created under an explicit, stable name
        (`eval_scenarios_source_check_v2`) so re-running this migration is
        safe — the DO block checks for that name's existence before adding
        it, and the dynamic DROP only fires for a *different*-named CHECK
        still attached to the source column (i.e. it is a no-op on a second
        run, once the v2 constraint is already in place).

    provenance / origin_trace_id (nullable, no backfill):
        Existing eval_scenarios rows all have provenance IS NULL after this
        migration — they predate provenance tracking and were either
        source='generated' or source='mined'. Per 21-RESEARCH.md's Runtime
        State Inventory, the read path (GET /eval-runs ledger, 21-06 Task 3)
        must treat provenance IS NULL as "authored", never as an error state.
        No backfill UPDATE is performed here — that's a read-path concern.

    Follows the established raw-SQL convention (mirrors 0009/0010) — no
    SQLAlchemy ORM model, consistent with every other tenant-DB table.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WIDENED_CONSTRAINT_NAME = "eval_scenarios_source_check_v2"
_NARROW_CONSTRAINT_NAME = "eval_scenarios_source_check"


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Step 1 — discover and DROP whatever CHECK constraint currently
    # governs eval_scenarios.source (do NOT hardcode the auto-generated
    # name — query pg_constraint/pg_attribute instead, Pitfall 2 / A5).
    # Step 2 — ADD the widened CHECK under a stable, explicit name, only
    # if it does not already exist (idempotent re-run safety).
    # ------------------------------------------------------------------
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
              AND con.conname <> '{_WIDENED_CONSTRAINT_NAME}'
            LIMIT 1;

            IF con_name IS NOT NULL THEN
                EXECUTE format('ALTER TABLE eval_scenarios DROP CONSTRAINT %I', con_name);
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = '{_WIDENED_CONSTRAINT_NAME}'
            ) THEN
                ALTER TABLE eval_scenarios
                    ADD CONSTRAINT {_WIDENED_CONSTRAINT_NAME}
                    CHECK (source IN ('generated', 'mined', 'production', 'red_team'));
            END IF;
        END $$;
    """)

    # ------------------------------------------------------------------
    # Step 3 — add provenance + origin_trace_id (nullable, no backfill).
    # provenance carries a human-readable origin tag (e.g. the trace_id or
    # finding_id that produced this scenario); origin_trace_id is the
    # structured job_id/trace_id used for the idempotency pre-check in
    # promote_trace_to_scenario (OPS-11).
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE eval_scenarios ADD COLUMN IF NOT EXISTS provenance TEXT
    """)
    op.execute("""
        ALTER TABLE eval_scenarios ADD COLUMN IF NOT EXISTS origin_trace_id TEXT
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_eval_scenarios_origin_trace_id
        ON eval_scenarios (origin_trace_id)
    """)


def downgrade() -> None:
    # Reverse in the opposite order: drop the index, drop the new columns,
    # drop the widened constraint, restore the original narrow constraint.
    op.execute("DROP INDEX IF EXISTS ix_eval_scenarios_origin_trace_id")
    op.execute("ALTER TABLE eval_scenarios DROP COLUMN IF EXISTS origin_trace_id")
    op.execute("ALTER TABLE eval_scenarios DROP COLUMN IF EXISTS provenance")

    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = '{_WIDENED_CONSTRAINT_NAME}'
            ) THEN
                ALTER TABLE eval_scenarios DROP CONSTRAINT {_WIDENED_CONSTRAINT_NAME};
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = '{_NARROW_CONSTRAINT_NAME}'
            ) THEN
                ALTER TABLE eval_scenarios
                    ADD CONSTRAINT {_NARROW_CONSTRAINT_NAME}
                    CHECK (source IN ('generated', 'mined'));
            END IF;
        END $$;
    """)
