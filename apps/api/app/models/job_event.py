"""
JobEvent ORM model — control DB.

Table: job_events
Schema: prd-M1.md §5

id is BIGSERIAL (auto-incrementing BIGINT). Used as the SSE event ID for
late-join replay ordering and as the durable audit log key.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    job_id: Mapped[UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("job_events_job_id_created_at_idx", "job_id", "created_at"),
    )
