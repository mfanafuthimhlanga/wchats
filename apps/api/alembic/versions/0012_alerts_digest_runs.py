"""alerts and digest_runs tables (M10 OPS-01--OPS-04)

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-25
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            alert_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            message TEXT NOT NULL,
            triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved_at TIMESTAMPTZ NULL,
            CONSTRAINT alerts_type_check CHECK (alert_type IN ('eval_regression', 'red_team_critical')),
            CONSTRAINT alerts_severity_check CHECK (severity IN ('warning', 'critical'))
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS alerts_agent_id_idx ON alerts(agent_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS alerts_resolved_at_idx ON alerts(resolved_at)
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS digest_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            payload JSONB NOT NULL DEFAULT '{}'::jsonb
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS digest_runs_agent_id_idx ON digest_runs(agent_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS digest_runs")
    op.execute("DROP TABLE IF EXISTS alerts")
