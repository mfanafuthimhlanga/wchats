"""Control DB v21: checklist_runs.heartbeat_at, the liveness the guard reads (#129).

Revision ID: 0021
Revises: 0020

Context:
    Control chain position: this migration chains from 0020 (tenant_usage_daily),
    the control head confirmed by listing `alembic/versions` before this file was
    written. The CONTROL tree numbers its revisions independently of
    `alembic_tenant/versions`, which has its own 0021.

    WHAT IT HOLDS. The moment a pass of run_deployment_checklist last looked at
    the tenant DB, stamped by the worker that took the look. One row per
    checklist run, overwritten on every pass.

    WHY THE COLUMN EXISTS. The checklist's idempotency guard asked whether a
    'running' row had been CREATED within the last sixty minutes. A congested
    chain outlives that: the wait ceiling caps the observed wait at forty-five
    minutes, continuations queue behind the eval and red-team turns they are
    waiting for on the single documented local worker, and the deciding pass adds
    the orchestrator's own budget after the last poll. Past minute sixty a second
    trigger found no live row, started a second checklist for the same agent, and
    both ran to completion (#129). Age since creation cannot separate a chain
    that is still working from one nothing will ever finish. A beat can.

    NULLABLE, AND THE READER FALLS BACK TO created_at. Every row written before
    this migration has no beat, and so does a row inserted seconds ago whose first
    pass has not polled yet. Reading a NULL as "never beat, therefore abandoned"
    would reap a run in the middle of opening its own wait, so the guard reads
    COALESCE(heartbeat_at, created_at) and a NULL costs nothing.

    NO DEFAULT AND NO BACKFILL. A historical row must not acquire a beat it never
    had. The runs that predate this column are long finished, so their status
    answers the guard before their beat is consulted.

    NO NEW INDEX. The guard selects on (agent_id, status) and orders by
    created_at, all of which checklist_runs_agent_id_idx from 0011 already serves
    at the row counts this table holds.

    House convention (mirrors 0019_blast_radius_capability_v2.py and
    0020_tenant_usage_daily.py): raw op.execute() SQL, no op.add_column() helper,
    every statement guarded so a re-run is a safe no-op.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable, no default: a run that predates this column never beat, and the
    # guard reads that as "fall back to created_at" rather than as a beat.
    op.execute(
        "ALTER TABLE checklist_runs "
        "ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ"
    )


def downgrade() -> None:
    # Scoped strictly to the one column 0021 added above.
    op.execute("ALTER TABLE checklist_runs DROP COLUMN IF EXISTS heartbeat_at")
