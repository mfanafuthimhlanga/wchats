"""PromptVersion ORM model — control DB.

Table: prompt_versions
Created by migration 0018 (control chain; see that migration's numbering note).

Immutable, append-only ledger of soul edits (OPS-16). soul_role/soul_voice/
soul_do_list/soul_donot_list are NEVER updated on an existing row after INSERT —
only `label` and `canary_percent` are ever mutated (moving the production/canary
pointer). See prompt_version_service.py for the create/diff/canary/rollback logic
that enforces this invariant.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    agent_id: Mapped[UUID] = mapped_column(nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    soul_role: Mapped[str | None] = mapped_column(Text, nullable=True)
    soul_voice: Mapped[str | None] = mapped_column(Text, nullable=True)
    soul_do_list: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    soul_donot_list: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    label: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'draft'")
    )
    canary_percent: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("prompt_versions_agent_id_idx", "agent_id"),
        Index("prompt_versions_agent_id_label_idx", "agent_id", "label"),
    )
