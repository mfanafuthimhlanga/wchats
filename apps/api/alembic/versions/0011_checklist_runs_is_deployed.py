"""Add checklist_runs table and agents.is_deployed column for M8 pre-deployment checklist.

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-24
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS checklist_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_id UUID NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            recommendation TEXT,
            report JSONB,
            warnings JSONB NOT NULL DEFAULT '[]',
            warning_acknowledgments JSONB NOT NULL DEFAULT '{}',
            all_warnings_acknowledged BOOLEAN NOT NULL DEFAULT false,
            approved_at TIMESTAMPTZ,
            approved_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS checklist_runs_agent_id_idx ON checklist_runs (agent_id)"
    )
    op.execute(
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_deployed BOOLEAN NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS is_deployed")
    op.execute("DROP TABLE IF EXISTS checklist_runs")
