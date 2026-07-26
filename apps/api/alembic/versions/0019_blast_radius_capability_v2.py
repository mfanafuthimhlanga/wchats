"""Phase 18: blast-radius warning thresholds, actor_mode, envelope-hash acknowledgement.

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-26

Context:
    Control chain position: this migration chains from 0018 (prompt_versions),
    which was the verified control head at authoring time (confirmed by
    listing apps/api/alembic/versions/ before writing this file). 0019 is the
    single control head after this migration.

    BLR-02 (envelope-hash acknowledgement): checklist_runs gains envelope_hash
    (the sha256 canonical-JSON hash of the capability envelope at checklist-run
    time, per 18-01-PLAN.md Open Decision 2) and envelope_acknowledged_at
    (stamped inside the existing POST /approve-deployment call — the approve
    gesture IS the acknowledgement gesture, no new endpoint). Both are
    nullable: historical runs predate the hash and must never be read as
    "matches whatever is live now" — that comparison is 18-07's contract, not
    this migration's.

    CAP-03 (per-skill Actor mode): capability_envelopes gains actor_mode,
    NOT NULL DEFAULT 'always-on' — the strictest mode. A row created without
    an explicit actor_mode gets full Actor review, never 'off'. This is a
    fail-safe default, not an arbitrary one: elevation-of-privilege via a
    silently-permissive default is the threat this column exists to close
    (T-18-CAP-01). The domain is enforced at the DB layer by
    ck_capability_envelopes_actor_mode, restricting the column to
    'always-on', 'off', or 'sample_at_rate_N' for N in 1..100 — wiring the
    sample_at_rate_N sampling behaviour itself is explicitly deferred
    (Open Decision 3b); this migration only makes the value representable
    and validated.

    BLR-01 (tenant-configured warning thresholds): tenants gains two nullable
    integer columns, blast_radius_warn_single_cents and
    blast_radius_warn_hourly_cents. NULL means "use the platform default in
    settings" — mirroring the existing tenants.daily_budget_usd + global-default
    convention introduced by migration 0008. No admin UI edits these columns
    in this phase (Open Decision 1b); the columns exist so a future UI can.

    Live-DB status: no live Neon control DB has been migrated past 0016 as of
    this migration's authoring date, so the live upgrade/downgrade roundtrip
    is deferred to plan 18-11 (an autonomous:false gate). The roundtrip test
    written alongside this migration (tests/unit/test_migration_0019.py) is
    INTEGRATION_TESTS_ENABLED-gated and skips by default, mirroring how
    Phases 13/15/16/17 handled their own live gates.

    House convention (mirrors 0018_prompt_versions.py / 0016_pending_confirmations_
    dedup_index.py): raw op.execute() SQL, no op.add_column() helper calls, every
    statement guarded so a re-run is a safe no-op. Postgres has no
    "ADD CONSTRAINT IF NOT EXISTS", so the actor_mode CHECK is wrapped in a
    DO $$ ... EXCEPTION WHEN duplicate_object THEN NULL; END $$; block.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # BLR-02: envelope-hash acknowledgement on checklist_runs. Nullable —
    # historical runs predate the hash and must never be treated as a match.
    op.execute(
        "ALTER TABLE checklist_runs "
        "ADD COLUMN IF NOT EXISTS envelope_hash TEXT"
    )
    op.execute(
        "ALTER TABLE checklist_runs "
        "ADD COLUMN IF NOT EXISTS envelope_acknowledged_at TIMESTAMPTZ"
    )

    # CAP-03: per-skill Actor mode on capability_envelopes. Fail-safe default
    # is the strictest mode ('always-on') so an unset row never silently
    # means "no Actor review".
    op.execute(
        "ALTER TABLE capability_envelopes "
        "ADD COLUMN IF NOT EXISTS actor_mode TEXT NOT NULL DEFAULT 'always-on'"
    )

    # BLR-01: per-tenant blast-radius warning thresholds. Both nullable — NULL
    # means "fall back to the platform default in settings.BLAST_RADIUS_WARN_*",
    # mirroring tenants.daily_budget_usd from migration 0008.
    op.execute(
        "ALTER TABLE tenants "
        "ADD COLUMN IF NOT EXISTS blast_radius_warn_single_cents INTEGER"
    )
    op.execute(
        "ALTER TABLE tenants "
        "ADD COLUMN IF NOT EXISTS blast_radius_warn_hourly_cents INTEGER"
    )

    # actor_mode domain constraint. Postgres has no "ADD CONSTRAINT IF NOT
    # EXISTS", so this is wrapped in a DO block catching duplicate_object so a
    # re-run is safe. Accepts exactly: 'always-on', 'off', or
    # 'sample_at_rate_N' for N in 1..100.
    op.execute("""
        DO $$
        BEGIN
            ALTER TABLE capability_envelopes
                ADD CONSTRAINT ck_capability_envelopes_actor_mode
                CHECK (
                    actor_mode IN ('always-on', 'off')
                    OR actor_mode ~ '^sample_at_rate_([1-9][0-9]?|100)$'
                );
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
    """)


def downgrade() -> None:
    # Scoped strictly to the five columns and one constraint 0019 added above
    # — must not touch any pre-existing checklist_runs/tenants column or
    # anything from 0018 and earlier.
    op.execute(
        "ALTER TABLE capability_envelopes "
        "DROP CONSTRAINT IF EXISTS ck_capability_envelopes_actor_mode"
    )
    op.execute(
        "ALTER TABLE checklist_runs DROP COLUMN IF EXISTS envelope_hash"
    )
    op.execute(
        "ALTER TABLE checklist_runs DROP COLUMN IF EXISTS envelope_acknowledged_at"
    )
    op.execute(
        "ALTER TABLE capability_envelopes DROP COLUMN IF EXISTS actor_mode"
    )
    op.execute(
        "ALTER TABLE tenants DROP COLUMN IF EXISTS blast_radius_warn_single_cents"
    )
    op.execute(
        "ALTER TABLE tenants DROP COLUMN IF EXISTS blast_radius_warn_hourly_cents"
    )
