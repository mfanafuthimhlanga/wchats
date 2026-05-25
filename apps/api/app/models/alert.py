"""Alert ORM model — control DB alerts table (M10)."""
from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import String, Text, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from app.models.base import Base


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # WR-01: tenant_id added for direct ownership check (defense-in-depth).
    # Populated by _write_alert from agent.tenant_id at creation time.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("alerts_agent_id_idx", "agent_id"),
        Index("alerts_resolved_at_idx", "resolved_at"),
        # WR-04: unique partial index prevents duplicate unresolved alerts per type.
        # ON CONFLICT enforcement is at DB level; application guard in _active_alert_exists
        # provides a fast short-circuit. Use text DDL in migration (see 0013).
    )
