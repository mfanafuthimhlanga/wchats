"""Tenant DB v6 migration — add status and deployment_blocked to red_team_runs.

Revision ID: 0006
Revises: 0005

Context:
    M7 Red Team — adds two columns to the red_team_runs table (created in 0001)
    that the red team service requires for idempotency and deployment gating.

    status TEXT NOT NULL DEFAULT 'running'
        Mirrors the eval_runs pattern. The run_red_team Celery task checks this
        column for idempotency: if a run with status='running' exists for the
        same agent within the last 30 minutes, the task skips duplicate work.
        Allowed values: 'running' | 'complete' | 'failed'

    deployment_blocked BOOLEAN NOT NULL DEFAULT false
        Set to true when max_severity == 'critical'. The M8 pre-deployment
        checklist orchestrator reads this column to gate deployment approval.
        RED-06: critical findings block deployment; high findings warn only.

    Both columns use IF NOT EXISTS guards in upgrade() so the migration is
    safe to re-run on any tenant DB that may already have been manually altered.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add status column — mirrors eval_runs.status for idempotency check pattern
    op.execute("ALTER TABLE red_team_runs ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'running'")
    # Add deployment_blocked flag — RED-06 gate: true when max_severity == 'critical'
    op.execute("ALTER TABLE red_team_runs ADD COLUMN IF NOT EXISTS deployment_blocked BOOLEAN NOT NULL DEFAULT false")


def downgrade() -> None:
    # Drop in reverse order of creation
    op.execute("ALTER TABLE red_team_runs DROP COLUMN IF EXISTS deployment_blocked")
    op.execute("ALTER TABLE red_team_runs DROP COLUMN IF EXISTS status")
