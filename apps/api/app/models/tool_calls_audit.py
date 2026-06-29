"""
ToolCallsAudit ORM model — control DB.

Table: tool_calls_audit
Immutable audit log for every tool call made by a customer agent.

Audit rows are written for both success and error paths (the error column
captures adapter failures; result is NULL on error).

actor_decision and actor_rationale default to empty string '': Phase 14
writes blanks; Phase 15 (Actor validator) fills them with 'approve' |
'block' | 'require_human' and the Haiku rationale text.

capability_snapshot captures the envelope row at call time as a self-contained
JSONB blob so the audit record remains valid even if the envelope is later changed.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ToolCallsAudit(Base):
    __tablename__ = "tool_calls_audit"

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # Logical FK to agents.id — no ORM relationship to avoid teardown complexity.
    agent_id: Mapped[UUID] = mapped_column(nullable=False)
    # NULL when the tool is called outside a conversation context.
    conversation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    skill: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL allowed: capture may fail for malformed inputs before validation.
    arguments: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # NULL on adapter error paths.
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Phase 14: always ''. Phase 15: 'approve' | 'block' | 'require_human'.
    actor_decision: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    # Phase 14: always ''. Phase 15: Haiku rationale text.
    actor_rationale: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    # Snapshot of the capability_envelope row at call time (self-contained).
    capability_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # NULL on success paths; populated when the adapter raises an exception.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("tool_calls_audit_agent_skill_idx", "agent_id", "skill"),
    )
