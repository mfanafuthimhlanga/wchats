"""Alert ORM model — control DB alerts table (M10)."""
from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import String, Text, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, TIMESTAMPTZ
from app.models.base import Base


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)

    __table_args__ = (
        Index("alerts_agent_id_idx", "agent_id"),
        Index("alerts_resolved_at_idx", "resolved_at"),
    )
