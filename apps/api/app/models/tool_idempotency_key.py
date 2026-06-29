"""
ToolIdempotencyKey ORM model — control DB.

Table: tool_idempotency_keys
Durable idempotency guard for mutating tool calls (TXN-02).

Survives Redis restarts — unlike TTL-based Redis keys, this table persists
across service restarts and provides correct deduplication under Celery
acks_late=True when a task is redelivered after a crash.

UNIQUE(agent_id, skill, idempotency_key) named uq_tool_idempotency_keys
is the correctness anchor: INSERT ... ON CONFLICT DO NOTHING means only
one winner stores the result; subsequent reads return the same result without
re-executing the mutation.

The idempotency key is scoped to (agent_id, skill) so the same key value
cannot accidentally replay a different skill for the same agent.

--- Migration 0015 additions (CR-02 + WR-02 substrate) ---

status (CR-02 substrate):
  Reservation lifecycle column supporting atomic reserve-before-execute.
  'pending'   — key claimed before the adapter runs; result is NULL.
  'completed' — adapter finished; result is populated.
  DEFAULT 'completed' is the fail-safe so any row created by the legacy
  store_idempotency path (which writes directly to 'completed') and all
  pre-existing rows are never mistaken for an in-progress reservation.
  The reserve-before-execute engine that writes 'pending' is implemented
  in plan 14-06.

args_hash (WR-02 substrate):
  sha256 hex of the canonicalized tool arguments that created this row.
  Nullable so legacy store_idempotency rows (no hash) remain valid.
  The argument-mismatch detection engine is implemented in plan 14-06/14-08.

reserved_at:
  TIMESTAMPTZ recording when the reservation was claimed (the INSERT
  moment for the new reserve path; DEFAULT now() for legacy-path rows).
  Used by 14-06 to reclaim a stale 'pending' row left by a crash so the
  key cannot deadlock forever.

result is now nullable:
  A pending reservation has no result yet; nullability is relaxed so a
  reserve INSERT can omit result and a finalize UPDATE can set it.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ToolIdempotencyKey(Base):
    __tablename__ = "tool_idempotency_keys"

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # Logical FK to agents.id — no ORM relationship to avoid teardown complexity.
    agent_id: Mapped[UUID] = mapped_column(nullable=False)
    skill: Mapped[str] = mapped_column(Text, nullable=False)
    # Client-provided UUID idempotency key, scoped to (agent_id, skill).
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    # Full tool response dict: {"content": [...], "is_error": bool}.
    # Nullable as of migration 0015: a pending reservation has no result yet.
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Reservation lifecycle (migration 0015 / CR-02 substrate).
    # DEFAULT 'completed' ensures legacy rows are never seen as pending.
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'completed'"),
    )
    # sha256 hex of canonicalized tool arguments (migration 0015 / WR-02 substrate).
    # Nullable so legacy rows without a hash remain valid.
    args_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When the reservation was claimed (DEFAULT now() for legacy-path rows).
    # Used by 14-06 stale-pending reclaim to prevent key deadlock after a crash.
    reserved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint(
            "agent_id", "skill", "idempotency_key",
            name="uq_tool_idempotency_keys",
        ),
        Index("tool_idempotency_keys_agent_skill_idx", "agent_id", "skill"),
    )
