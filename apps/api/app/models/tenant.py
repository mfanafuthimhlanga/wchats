"""
Tenant ORM model — control DB.

Table: tenants
Schema: prd-M1.md §5
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Stores the argon2id hash of the raw API key.
    # Column name in DB is now 'api_key_hash' (renamed from 'api_key' in migration 0006).
    api_key_hash: Mapped[str] = mapped_column(
        Text, unique=True, nullable=False
    )
    # First 16 hex chars of HMAC-SHA256(raw_key, ADMIN_KEY) — not secret,
    # used for O(1) indexed lookup before argon2 verify().
    # See apps/api/app/core/security.py hmac_key_prefix() / WR-01 fix.
    api_key_prefix: Mapped[str | None] = mapped_column(
        Text, nullable=True, index=True
    )
    # Clerk user ID (user_xxx format) — added in migration 0005 (M4.1 Clerk auth).
    # Nullable so existing tenants (X-API-Key only) keep working without a Clerk ID.
    # Uniqueness is enforced by a partial DB index (migration 0007):
    #   UNIQUE WHERE deleted_at IS NULL AND clerk_user_id IS NOT NULL
    # This allows a soft-deleted user to re-register via Clerk.
    clerk_user_id: Mapped[str | None] = mapped_column(
        Text, nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Phase 18 BLR-01: per-tenant blast-radius warning thresholds, in cents.
    # NULL means "fall back to the platform default in settings
    # (BLAST_RADIUS_WARN_SINGLE_CENTS / BLAST_RADIUS_WARN_HOURLY_CENTS)" —
    # mirroring the tenants.daily_budget_usd + global-default convention from
    # migration 0008. No admin UI edits these columns in Phase 18.
    blast_radius_warn_single_cents: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    blast_radius_warn_hourly_cents: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
