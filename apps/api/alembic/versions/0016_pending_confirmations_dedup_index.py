"""pending_confirmations: partial unique index to bound outstanding confirmations.

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-30

T-14-08-05 (Denial of Service — unbounded pending_confirmations rows):
    Plan 14-08 declared the WR-05 mitigation as "capability gate + minimal
    per-(agent_id, skill, action_reference) dedup on confirm_action". The
    capability gate shipped, but the dedup was never implemented — confirm_action
    inserted a fresh row on every call, so an agent with an enabled capability
    envelope could create unbounded duplicate confirmation rows for the same
    action. This migration adds the missing durable bound.

    Partial unique index uq_pending_confirmations_unresolved enforces at most one
    UNRESOLVED confirmation per (agent_id, skill, action_reference). Scoping the
    index to `resolved_at IS NULL` keeps it minimal: once Phase 18 resolves a
    confirmation the action can be requested again, and resolved rows never
    participate in the uniqueness check. NULL action_reference values (no key) are
    not constrained, consistent with Postgres NULL semantics.

    confirm_action_tool relies on this index: it inserts via the ORM and, on the
    resulting IntegrityError, returns the existing pending row instead of a
    duplicate (the application-level half of the mitigation).

Pre-existing duplicates:
    A unique index cannot build if duplicate unresolved rows already exist. The
    upgrade first supersedes every non-earliest unresolved duplicate
    (resolved_at = now(), resolution = 'superseded') so the index build cannot
    fail on legacy data. This is non-destructive — rows are retained, only marked
    resolved.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Supersede pre-existing unresolved duplicates so the unique index can
    # build. Keep the earliest (requested_at, id) per dedup key; mark the rest
    # resolved='superseded' which removes them from the partial index predicate.
    # ------------------------------------------------------------------
    op.execute("""
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY agent_id, skill, (arguments->>'action_reference')
                       ORDER BY requested_at, id
                   ) AS rn
            FROM pending_confirmations
            WHERE resolved_at IS NULL
              AND (arguments->>'action_reference') IS NOT NULL
        )
        UPDATE pending_confirmations p
        SET resolved_at = now(),
            resolution  = 'superseded'
        FROM ranked
        WHERE p.id = ranked.id
          AND ranked.rn > 1
    """)

    # ------------------------------------------------------------------
    # Partial unique index — at most one UNRESOLVED confirmation per
    # (agent_id, skill, action_reference). T-14-08-05 durable bound.
    # ------------------------------------------------------------------
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_pending_confirmations_unresolved
        ON pending_confirmations (agent_id, skill, (arguments->>'action_reference'))
        WHERE resolved_at IS NULL
    """)


def downgrade() -> None:
    # Drop the unique index. Superseded rows are left as-is (non-destructive
    # downgrade — un-resolving them would re-introduce the duplicates).
    op.execute("DROP INDEX IF EXISTS uq_pending_confirmations_unresolved")
