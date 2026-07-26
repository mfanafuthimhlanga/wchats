"""
CapabilityEnvelope ORM model — control DB.

Table: capability_envelopes
Per-skill authorization envelope for each agent.

enabled server_default=false enforces fail-closed at the schema level (T-14-01-01):
a missing or disabled row means the skill is off.  The enforcement layer in
Plan 03 checks this before any tool call executes.

UNIQUE(agent_id, skill) named uq_capability_envelopes_agent_skill guarantees
at most one envelope row per agent/skill pair.

Phase 18 (BLR-02, CAP-03): actor_mode is part of the canonical envelope-hash
input used to detect BLR-02 drift between checklist-run time and approval
time — id and updated_at are deliberately excluded from that hash (a no-op
re-save must not fire false drift). See 18-01-PLAN.md Open Decision 2.
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
    # Phase 18 CAP-03: per-skill Actor mode. Legal shapes (enforced by
    # ck_capability_envelopes_actor_mode at the DB layer): 'always-on'
    # (default, full Actor review), 'off', or 'sample_at_rate_N' for N in
    # 1..100. Default is fail-safe strictest — an unset row never silently
    # means "no Actor review". Wiring sample_at_rate_N sampling behaviour
    # into call_actor_gate is deferred (Open Decision 3b).
    actor_mode: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'always-on'")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("agent_id", "skill", name="uq_capability_envelopes_agent_skill"),
        Index("capability_envelopes_agent_id_idx", "agent_id"),
    )
