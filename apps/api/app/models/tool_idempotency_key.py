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
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)
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
