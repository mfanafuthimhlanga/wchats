"""alerts: widen alerts_type_check to allow 'index_staleness' (Phase 21 OPS-08)

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-16

Context:
    OPS-08's check_index_staleness task reuses the EXISTING alert_service
    (alert_service._write_alert / _active_alert_exists) rather than a new
    table (per plan: "raise an alert via the existing alert_service ...
    no new table"). alerts.alert_type has a live CHECK constraint
    (0012_alerts_digest_runs.py:29) restricted to
    ('eval_regression', 'red_team_critical') — the same class of landmine
    21-RESEARCH.md's Pitfall 2 documents for eval_scenarios.source. Writing
    alert_type='index_staleness' without widening this constraint first
    raises psycopg2.errors.CheckViolation at INSERT time.

    This migration was not in 21-04-PLAN.md's `files_modified` list — it was
    added as a Rule 3 (blocking-issue) auto-fix, discovered while
    implementing the plan's explicit instruction to reuse alert_service.
    See 21-04-SUMMARY.md for the deviation note.

    The constraint is named inline (alerts_type_check) in 0012, so — unlike
    Pitfall 2's unnamed eval_scenarios constraint — no information_schema
    lookup is required to find the name.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE alerts DROP CONSTRAINT IF EXISTS alerts_type_check")
    op.execute("""
        ALTER TABLE alerts ADD CONSTRAINT alerts_type_check
        CHECK (alert_type IN ('eval_regression', 'red_team_critical', 'index_staleness'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE alerts DROP CONSTRAINT IF EXISTS alerts_type_check")
    op.execute("""
        ALTER TABLE alerts ADD CONSTRAINT alerts_type_check
        CHECK (alert_type IN ('eval_regression', 'red_team_critical'))
    """)
