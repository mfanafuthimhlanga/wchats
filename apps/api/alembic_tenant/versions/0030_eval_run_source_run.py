"""Tenant DB v30 migration: a rejudge run names the run it rescored (#274).

Revision ID: 0030
Revises: 0029

Context:
    #274 replaced the instrument behind the gated `answer_relevancy` metric. The
    46 owner labels that measured the old one were collected on run 0a99f7ab, and
    the only way to measure the new Judge against them is to run it over that
    run's stored `eval_samples` and join on `scenario_id`. That produces a second
    `eval_runs` row for answers the agent gave once, and without a column saying
    so it would be indistinguishable from a run that measured the agent again.

`eval_runs.source_run_id`
    NULL on every run that drove an agent, which is every row written before
    0030. Set on a run of kind `rejudge:{agent_id}`, naming the run whose stored
    samples it rescored. Three readers:

      - `existing_rejudge_run` pairs it with the run's recorded Judge identity,
        so a second rejudge by the same Judge returns the first rather than
        paying for a hundred judge calls again;
      - `calibrate_run.py --score` joins the new run's verdicts to the source
        run's labels, on scenario ids the copied samples preserve;
      - a reader of the table, who can otherwise not tell a second opinion from a
        second measurement.

    No foreign key, matching `eval_samples.eval_run_id` (0027) and NOT
    `eval_results.eval_run_id`, which does carry one (0001:167). A run row is
    inserted before any work starts and never deleted, so the reference holds by
    construction, and a constraint here would make the writer's failure mode
    depend on write order. It would also point a column on `eval_runs` at
    `eval_runs`, which is a self-reference no reader needs enforced.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE eval_runs
            ADD COLUMN IF NOT EXISTS source_run_id UUID
    """)
    op.execute(
        "COMMENT ON COLUMN eval_runs.source_run_id IS "
        "'For a run of kind rejudge:{agent_id}: the eval_runs.id whose stored "
        "eval_samples this run rescored, with no agent turn of its own. NULL on "
        "every run that measured an agent (#274).'"
    )
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_eval_runs_source_run_id
            ON eval_runs (source_run_id)
            WHERE source_run_id IS NOT NULL
    """)


def downgrade() -> None:
    # IF EXISTS, so a downgrade against a database that never received 0030 is a
    # no-op. Dropping it loses the link from a rejudge run to the run it
    # rescored; the verdicts, the samples and every source run survive untouched.
    op.execute("DROP INDEX IF EXISTS ix_eval_runs_source_run_id")
    op.execute("ALTER TABLE eval_runs DROP COLUMN IF EXISTS source_run_id")
