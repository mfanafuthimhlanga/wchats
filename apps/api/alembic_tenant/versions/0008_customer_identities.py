"""Tenant DB v8 migration — customer_identities table for OTP-verified sessions.

Revision ID: 0008
Revises: 0007

Context:
    IDV-01: adds `customer_identities` table to every tenant Neon project.

    OD-1 (per-tenant scope): The table is in the *tenant* DB (alembic_tenant migration)
    — NOT the control DB — because customer identity records are scoped per tenant.
    There is NO `agent_id` column: uniqueness is enforced on `external_id` alone so that
    a single verified customer session is valid across all agents on the same tenant.

    OD-3 (OTP state in Redis): OTP challenge state (the 6-digit code, attempt counter,
    send-rate window) lives in Redis with a short TTL. This migration creates ONLY the
    durable verified-session record — there is NO `otp_pending` table.

    OD-4 (global session TTL): VERIFIED_SESSION_TTL_SECONDS=3600 (1 hour) is the global
    default. Per-envelope TTL overrides are deferred to Phase 18.

    OD-5 (external_id format): external_id is the delivery address — lowercased email
    for email OTP, E.164 phone number for SMS OTP.

    Schema:
        id                  UUID PK DEFAULT gen_random_uuid()
        external_id         TEXT NOT NULL UNIQUE  — lowercased email / E.164 phone
        verified_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        verification_method TEXT NOT NULL  — 'email' | 'sms'
        session_token_hash  TEXT NOT NULL  — SHA-256 hex of the issued session token
        session_expires_at  TIMESTAMPTZ NOT NULL  — absolute expiry (now() + TTL)
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()

    Indexes:
        ix_customer_identities_token_hash  ON customer_identities (session_token_hash)
        ix_customer_identities_expires_at  ON customer_identities (session_expires_at)

    All DDL statements use IF NOT EXISTS guards so the migration is safe to re-run
    on tenant DBs that may have been manually altered (T-17-03).
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS customer_identities (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            external_id         TEXT NOT NULL,
            verified_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            verification_method TEXT NOT NULL,
            session_token_hash  TEXT NOT NULL,
            session_expires_at  TIMESTAMPTZ NOT NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_customer_identities_external_id UNIQUE (external_id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_customer_identities_token_hash
        ON customer_identities (session_token_hash)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_customer_identities_expires_at
        ON customer_identities (session_expires_at)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_customer_identities_expires_at")
    op.execute("DROP INDEX IF EXISTS ix_customer_identities_token_hash")
    op.execute("DROP TABLE IF EXISTS customer_identities")
