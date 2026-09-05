"""Control DB v21: one live checklist per agent, and the beat the guard reads (#129).

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

    NO DEFAULT AND NO BACKFILLED BEAT. A historical row must not acquire a beat
    it never had. The runs that predate this column are long finished, so their
    status answers the guard before their beat is consulted.

    AND THE BEAT ALONE STILL LETS TWO TRIGGERS THROUGH. The guard reads the row,
    decides, then writes; two triggers reading the same stale row both decide it
    is abandoned, both reap it and both insert. Nothing in the schema said an
    agent may hold one live checklist at a time, so the database accepted both
    and the two chains raced each other's writes for the rest of the run. The
    partial unique index says it: UNIQUE (agent_id) WHERE status = 'running'.
    Partial, because a finished run must not stop the next one, and this table
    keeps every run it ever made.

    THE ROWS THAT PREDATE THE INDEX ARE MADE TO FIT IT. A pair of live rows for
    one agent is exactly what #129 produced, so the upgrade cannot assume there
    are none: it marks every 'running' row but the newest per agent as failed
    first, and CREATE UNIQUE INDEX then succeeds instead of aborting the
    migration. The newest is kept because it is the one a chain may still be
    beating. Observed on the local control cluster before this was written:
    eleven rows, two complete and nine failed, no agent holding two live ones, so
    the backfill was a no-op there and the index built over it.

    THE BACKFILL IS NOT REVERSED. downgrade() drops the index and the column;
    a row this migration closed out stays closed, because reopening it would put
    a second live checklist back on an agent that has one.

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


#: The index that makes "one live checklist per agent" a property of the data.
ONE_LIVE_RUN_INDEX = "checklist_runs_one_live_run_per_agent_idx"


def upgrade() -> None:
    # Nullable, no default: a run that predates this column never beat, and the
    # guard reads that as "fall back to created_at" rather than as a beat.
    op.execute(
        "ALTER TABLE checklist_runs "
        "ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ"
    )
    # Every agent keeps its NEWEST live row and loses the rest. DISTINCT ON reads
    # one id per agent under that ordering, and created_at is tie-broken by id so
    # two rows written in the same instant still resolve to one survivor. Without
    # this the CREATE UNIQUE INDEX below aborts on any agent #129 left holding a
    # pair, and the whole migration with it.
    op.execute(
        "UPDATE checklist_runs SET status = 'failed' "
        "WHERE status = 'running' AND id NOT IN ("
        "SELECT DISTINCT ON (agent_id) id FROM checklist_runs "
        "WHERE status = 'running' "
        "ORDER BY agent_id, created_at DESC, id DESC)"
    )
    # PARTIAL, on the predicate the guard reads. A finished run must never block
    # the next checklist, and this table keeps every run it ever made.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS " + ONE_LIVE_RUN_INDEX + " "
        "ON checklist_runs (agent_id) WHERE status = 'running'"
    )


def downgrade() -> None:
    # Scoped strictly to what upgrade() added. The backfill stays: a row this
    # migration closed out must not come back as a second live checklist.
    op.execute("DROP INDEX IF EXISTS " + ONE_LIVE_RUN_INDEX)
    op.execute("ALTER TABLE checklist_runs DROP COLUMN IF EXISTS heartbeat_at")
