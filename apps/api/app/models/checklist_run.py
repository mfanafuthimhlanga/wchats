"""ChecklistRun ORM model — control DB.

Table: checklist_runs
Created by migration 0011.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ChecklistRun(Base):
    __tablename__ = "checklist_runs"

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    agent_id: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'running'")
    )
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    warnings: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    warning_acknowledgments: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    all_warnings_acknowledged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("checklist_runs_agent_id_idx", "agent_id"),
    )
