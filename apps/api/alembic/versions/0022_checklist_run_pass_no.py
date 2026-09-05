"""Control DB v22: the pass counter one checklist chain advances (#124).

Revision ID: 0022
Revises: 0021

Context:
    Control chain position: this migration chains from 0021 (checklist_runs
    heartbeat and the one-live-run index), the control head confirmed by reading
    `alembic/versions` before this file was written. The CONTROL tree numbers its
    revisions independently of `alembic_tenant/versions`.

    WHAT IT HOLDS. How many passes of run_deployment_checklist this run has
    taken. One row per checklist run, advanced by one on every pass, in the same
    fenced UPDATE that stamps the beat 0021 added.

    WHY THE COLUMN EXISTS. The checklist waits by re-queueing itself with the
    state of the wait rather than by sleeping in the worker slot, and the task is
    acks_late=True. A worker that dies between `apply_async` and the ack hands
    its message back, so one wait forks into two chains carrying the same run_id
    and the same wait_state. Both poll, both re-queue, both pass the ceiling and
    both decide: two orchestrator turns billed to the tenant, two ledger rows,
    and two `_persist_complete` writes landing last-writer-wins on one row. The
    guards that existed were sequential only. They refuse a SECOND checklist for
    an agent, and a fork is not a second checklist, it is the same one twice.

    WHAT THE COUNTER FENCES. A continuation carries the number it expects the row
    to hold. Its beat is `UPDATE ... WHERE id = :id AND status = 'running' AND
    pass_no = :expected`, advancing to expected + 1, so exactly one of two forks
    writes and the other reads zero rows and stops before it polls, collects,
    calls the orchestrator or persists. The row's own state decides which, which
    is the same shape as 0021's fence, one column further in.

    NOT NULL WITH A DEFAULT OF 0, unlike 0021's beat. A beat is a moment, and a
    default would hand a historical row a moment it never had. This is a count,
    and zero passes is the true count for every row written before the column
    existed: none of them advanced a counter that did not exist. The default also
    means an INSERT that names no pass_no gets 0, which is what the first pass
    then expects to find.

    NO BACKFILL, AND NOTHING TO REVERSE. Every existing row takes the default in
    place, so downgrade() drops the column and leaves every row's status and beat
    exactly as it found them.

    House convention (mirrors 0019, 0020 and 0021): raw op.execute() SQL, no
    op.add_column() helper, every statement guarded so a re-run is a safe no-op.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOT NULL with DEFAULT 0: the column counts passes, and every row that
    # predates it took none. The default is also what a fresh INSERT gets, so the
    # first pass of a new run expects 0 and finds it.
    op.execute(
        "ALTER TABLE checklist_runs "
        "ADD COLUMN IF NOT EXISTS pass_no INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    # Scoped strictly to what upgrade() added. No row's status, beat or report is
    # touched in either direction.
    op.execute("ALTER TABLE checklist_runs DROP COLUMN IF EXISTS pass_no")
