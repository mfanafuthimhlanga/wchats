"""Add daily_budget_usd column to tenants table (control DB).

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-18

Context:
    F4 (CRITICAL) — Cross-Phase Security Review finding.
    Without a tenant daily ceiling, an attacker can create a fresh
    conversation_id per request (bypassing the per-conversation cap)
    and exhaust Anthropic spend at ~$3/min = ~$4,320/day per agent.

    This migration adds tenants.daily_budget_usd to the CONTROL DB
    tenants table (apps/api/alembic/) so future per-tenant overrides
    can be stored alongside the tenant record. It does NOT modify any
    tenant DB (Neon per-tenant project).

    For M4.1, the widget dispatch uses settings.TENANT_DAILY_BUDGET_USD
    (global default = $5.00/day) for all tenants. Per-tenant override
    via admin UI is deferred to M5+.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tenants ADD COLUMN daily_budget_usd FLOAT NOT NULL DEFAULT 5.0;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS daily_budget_usd;")
