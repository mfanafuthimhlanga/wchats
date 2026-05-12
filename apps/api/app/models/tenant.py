"""
Tenant ORM model — control DB.

Table: tenants
Schema: prd-M1.md §5
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Column name in DB is "api_key"; stores the argon2 hash.
    # ORM attribute is api_key_hash to make intent explicit and prevent
    # accidental logging of the raw DB value as if it were the plaintext key.
    api_key_hash: Mapped[str] = mapped_column(
        "api_key", Text, unique=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
