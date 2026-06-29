"""idempotency reservation: status, args_hash, reserved_at columns on tool_idempotency_keys

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-29

TXN-02 / CR-02 substrate — atomic reserve-before-execute idempotency guard
  Adds three columns that make a row insertable BEFORE the adapter runs
  (pending reservation) and updatable AFTER it succeeds (completed).

  This plan does NOT change idempotency.py / enforcement.py / tools.py —
  only the schema, model, and a migration test (14-05). The reserve-before-execute
  engine is implemented in plan 14-06.

WR-02 substrate — argument binding
  args_hash stores a sha256 hex of the canonicalized tool arguments so a key
  replayed with different arguments is detectable.

Columns added to tool_idempotency_keys (control DB):

  status TEXT NOT NULL DEFAULT 'completed'
    Reservation lifecycle:
      'pending'   — key claimed before adapter runs; no result yet
      'completed' — adapter finished; result stored
    DEFAULT 'completed' is the fail-safe for all pre-existing / legacy-path rows
    (which arrive via store_idempotency, not the new reserve path) so they are
    never mistaken for an in-progress reservation.

  args_hash TEXT NULL
    sha256 hex of canonicalized tool arguments (WR-02).
    Nullable so legacy store_idempotency rows remain valid without a hash.

  reserved_at TIMESTAMPTZ NOT NULL DEFAULT now()
    When the reservation was claimed (the INSERT moment for the reserve path).
    Used by 14-06 to reclaim a stale 'pending' row left by a crash so the
    key cannot deadlock forever.

Column changed:

  result JSONB NOT NULL → JSONB NULL
    A pending reservation has no result yet; the constraint is relaxed so the
    reserve INSERT can omit result and the finalize UPDATE can set it.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Add status column — DEFAULT 'completed' makes all pre-existing rows
    # immediately valid (they are finished operations, not pending ones).
    # T-14-05-01: ensures legacy rows are never seen as in-progress.
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE tool_idempotency_keys
        ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'completed'
    """)

    # ------------------------------------------------------------------
    # Add args_hash — nullable so legacy rows (no hash) stay valid.
    # WR-02: bind idempotency key to its argument fingerprint.
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE tool_idempotency_keys
        ADD COLUMN IF NOT EXISTS args_hash TEXT
    """)

    # ------------------------------------------------------------------
    # Add reserved_at — NOT NULL DEFAULT now() so the INSERT moment is
    # always recorded; used by 14-06 stale-pending reclaim logic.
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE tool_idempotency_keys
        ADD COLUMN IF NOT EXISTS reserved_at TIMESTAMPTZ NOT NULL DEFAULT now()
    """)

    # ------------------------------------------------------------------
    # Relax result from NOT NULL → NULL so a pending reservation can be
    # inserted without a result and finalized with one later.
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE tool_idempotency_keys
        ALTER COLUMN result DROP NOT NULL
    """)


def downgrade() -> None:
    # ------------------------------------------------------------------
    # T-14-05-02: backfill any NULL result values before re-asserting
    # NOT NULL so the downgrade cannot fail on pending rows left behind.
    # ------------------------------------------------------------------
    op.execute("""
        UPDATE tool_idempotency_keys
        SET result = '{}'::jsonb
        WHERE result IS NULL
    """)

    op.execute("""
        ALTER TABLE tool_idempotency_keys
        ALTER COLUMN result SET NOT NULL
    """)

    # Drop added columns in reverse order (safe: no FK dependencies)
    op.execute("""
        ALTER TABLE tool_idempotency_keys
        DROP COLUMN IF EXISTS reserved_at
    """)

    op.execute("""
        ALTER TABLE tool_idempotency_keys
        DROP COLUMN IF EXISTS args_hash
    """)

    op.execute("""
        ALTER TABLE tool_idempotency_keys
        DROP COLUMN IF EXISTS status
    """)
