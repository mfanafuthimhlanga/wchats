"""
PendingConfirmation ORM model — control DB.

Table: pending_confirmations
Records tool calls that require human confirmation before execution proceeds.

Written by confirm_action_tool when capability_envelopes.requires_confirmation=true.
Phase 18 (Capability Admin UI) provides the resolution interface.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PendingConfirmation(Base):
    __tablename__ = "pending_confirmations"

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # Logical FK to agents.id — no ORM relationship to avoid teardown complexity.
    agent_id: Mapped[UUID] = mapped_column(nullable=False)
    skill: Mapped[str] = mapped_column(Text, nullable=False)
    # The tool arguments that triggered the confirmation request.  NULL-safe.
    arguments: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    # NULL means no deadline configured.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # NULL until Phase 18 resolves the confirmation.
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 'approved' | 'rejected' | 'expired'. NULL until resolved.
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("pending_confirmations_agent_id_idx", "agent_id"),
    )
