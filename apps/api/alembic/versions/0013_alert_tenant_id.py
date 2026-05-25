"""alerts: add tenant_id column and unique partial index on (agent_id, alert_type) WHERE resolved_at IS NULL

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-25

WR-01: alerts table missing tenant_id for direct ownership verification.
WR-04: no DB-level uniqueness constraint preventing duplicate unresolved alerts.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # WR-01: add tenant_id column (nullable for backward-compat with existing rows)
    op.execute("""
        ALTER TABLE alerts
        ADD COLUMN IF NOT EXISTS tenant_id UUID NULL REFERENCES tenants(id) ON DELETE SET NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS alerts_tenant_id_idx ON alerts(tenant_id)
    """)

    # Backfill tenant_id from the agents table for existing rows
    op.execute("""
        UPDATE alerts a
        SET tenant_id = ag.tenant_id
        FROM agents ag
        WHERE a.agent_id = ag.id
          AND a.tenant_id IS NULL
    """)

    # WR-04: unique partial index enforces at-most-one unresolved alert per
    # (agent_id, alert_type). The application guard in _active_alert_exists
    # provides a fast short-circuit; this index is DB-level enforcement.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS alerts_agent_alert_type_unresolved_idx
        ON alerts (agent_id, alert_type)
        WHERE resolved_at IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS alerts_agent_alert_type_unresolved_idx")
    op.execute("DROP INDEX IF EXISTS alerts_tenant_id_idx")
    op.execute("ALTER TABLE alerts DROP COLUMN IF EXISTS tenant_id")
