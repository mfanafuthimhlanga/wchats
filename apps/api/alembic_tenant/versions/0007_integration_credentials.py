"""Tenant DB v7 migration — integration_credentials table for per-tenant provider credentials.

Revision ID: 0007
Revises: 0006

Context:
    INT-01: adds `integration_credentials` table to every tenant Neon project.
    Stores Fernet-encrypted provider credentials (BYTEA) keyed per-tenant via
    HKDF derivation from PLATFORM_CREDENTIAL_KEY.

    The table is in the *tenant* DB (alembic_tenant migration) — NOT the control DB —
    because credentials are scoped to the tenant's Neon project for data isolation
    and Neon branching compatibility (eval branches include this table).

    Schema:
        id UUID PK DEFAULT gen_random_uuid()
        provider_type TEXT NOT NULL  — 'stripe' | 'shopify' | 'woocommerce' | 'calendly'
        credential_data BYTEA NOT NULL  — Fernet-encrypted provider API key / secret
        config_data JSONB NOT NULL DEFAULT '{}'  — provider-specific config (shop_url, etc.)
        currency_code TEXT NOT NULL DEFAULT 'USD'  — INT-07: single currency per tenant
        enabled_skills JSONB NOT NULL DEFAULT '[]'  — list of skill names this provider serves
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()

    Index:
        ix_integration_credentials_provider_type ON integration_credentials (provider_type)

    Both upgrade() statements use IF NOT EXISTS guards so the migration is safe
    to re-run on tenant DBs that may have been manually altered.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS integration_credentials (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            provider_type   TEXT NOT NULL,
            credential_data BYTEA NOT NULL,
            config_data     JSONB NOT NULL DEFAULT '{}',
            currency_code   TEXT NOT NULL DEFAULT 'USD',
            enabled_skills  JSONB NOT NULL DEFAULT '[]',
            created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_integration_credentials_provider_type
        ON integration_credentials (provider_type)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_integration_credentials_provider_type")
    op.execute("DROP TABLE IF EXISTS integration_credentials")
