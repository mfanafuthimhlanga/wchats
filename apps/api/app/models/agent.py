"""
Agent ORM model — control DB.

Table: agents
Schema: prd-M1.md §5

neon_connection_string  — BYTEA: Fernet-encrypted pooled URI for application traffic.
neon_direct_connection_string — BYTEA: Fernet-encrypted direct (non-pooled) URI used
    exclusively by Alembic migrations (poolers do not support DDL).
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, LargeBinary, Select, Text, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    soul: Mapped[dict] = mapped_column(JSONB, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    neon_project_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Fernet-encrypted pooled connection URI (application traffic)
    neon_connection_string: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )
    # Fernet-encrypted direct (non-pooled) connection URI (Alembic only)
    neon_direct_connection_string: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )
    schema_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    # M3: hybrid retrieval configuration — strategy, weights, rerank model, etc.
    retrieval_strategy: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # M4.2: widget design customization (appearance + colors + typography)
    widget_config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # M5: validation chain — persistent Auditor ungrounded failures trigger resynthesis
    strategy_resynthesis_flagged: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    # M8: deployment gate — set to True on POST /approve-deployment (DEP-06)
    is_deployed: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    # M4: structured soul fields (additive — legacy soul JSONB + role TEXT kept for M1 compat)
    soul_voice: Mapped[str | None] = mapped_column(Text, nullable=True)
    soul_do_list: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    soul_donot_list: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    soul_role: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("agents_tenant_id_idx", "tenant_id"),
    )


def select_beat_fanout_agents() -> Select:
    """The agents a scheduled beat may spend money on: deployed AND ready (#134).

    The five beat fan-outs (nightly eval, weekly red team, weekly digest, daily
    alert check, index staleness) all call this so one edit moves all five.

    Both filters are load-bearing and neither alone is enough:

    - `is_deployed` says the owner approved this agent and armed its schedules. It
      has one writer, POST /approve-deployment. Selecting on `status` alone put the
      nightly eval on every ready agent no customer could reach (#32).
    - `status == 'ready'` is what the request routes check before they serve anyone
      (agent_chat.py, query.py, widget.py, documents.py). Nothing clears
      `is_deployed` when status later leaves 'ready', so selecting on it alone kept
      buying eval and red-team runs for an agent whose own chat route answers 409.

    Returns:
        A SELECT over Agent that the caller executes against the control DB.
    """
    return select(Agent).where(
        Agent.is_deployed == True,  # noqa: E712
        Agent.status == "ready",
    )
