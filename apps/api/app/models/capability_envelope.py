"""
CapabilityEnvelope ORM model — control DB.

Table: capability_envelopes
Per-skill authorization envelope for each agent.

enabled server_default=false enforces fail-closed at the schema level (T-14-01-01):
a missing or disabled row means the skill is off.  The enforcement layer in
Plan 03 checks this before any tool call executes.

UNIQUE(agent_id, skill) named uq_capability_envelopes_agent_skill guarantees
at most one envelope row per agent/skill pair.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CapabilityEnvelope(Base):
    __tablename__ = "capability_envelopes"

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # Logical FK to agents.id — no ORM relationship to avoid teardown complexity.
    agent_id: Mapped[UUID] = mapped_column(nullable=False)
    skill: Mapped[str] = mapped_column(Text, nullable=False)
    # Fail-closed default: disabled unless an operator explicitly enables the skill.
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # Format: "N/<unit>" e.g. "5/hour", "10/day".  NULL = no rate limit.
    rate_limit: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Constraint overrides: max_amount_cents, scope filters, etc.
    constraints: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    requires_confirmation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    requires_identity_verification: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("agent_id", "skill", name="uq_capability_envelopes_agent_skill"),
        Index("capability_envelopes_agent_id_idx", "agent_id"),
    )
