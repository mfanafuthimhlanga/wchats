"""ChecklistRun ORM model — control DB.

Table: checklist_runs
Created by migration 0011.
"""

from __future__ import annotations

from datetime import datetime
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
    # Phase 18 BLR-02: sha256 canonical-JSON hash of the capability envelope
    # at checklist-run time. Nullable — historical runs predate the hash and
    # NULL must never be read as "matches whatever is live now" (that
    # comparison is 18-07's contract).
    envelope_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Phase 18 BLR-02: stamped inside the existing POST /approve-deployment
    # call — the approve gesture IS the acknowledgement gesture, no separate
    # acknowledgement endpoint exists.
    envelope_acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    # Migration 0021 (#129): the moment a pass of run_deployment_checklist last
    # looked at the tenant DB, stamped by the worker that looked. The idempotency
    # guard reads it to tell a chain that is still working from one nothing will
    # ever finish; age since created_at cannot separate the two, and a congested
    # chain that outlived the old sixty-minute window let a second checklist
    # start beside it. NULL on a historical row and on a run whose first pass has
    # not polled yet, so the guard falls back to created_at.
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("checklist_runs_agent_id_idx", "agent_id"),
        # Migration 0021 (#129): one live checklist per agent, said in the schema
        # rather than only in the task's guard. Two triggers reading the same
        # stale row both decided it was abandoned, both reaped it and both
        # inserted, because nothing refused the second insert. PARTIAL: this
        # table keeps every run it ever made, and a finished one must not block
        # the next.
        Index(
            "checklist_runs_one_live_run_per_agent_idx",
            "agent_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )
