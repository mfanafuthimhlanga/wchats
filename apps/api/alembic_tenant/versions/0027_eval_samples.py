"""Tenant DB v27 migration: eval_samples, the text an eval run scored (issue #58).

Revision ID: 0027
Revises: 0026

Context:
    `eval_results` holds one score per (scenario, metric) and nothing else about
    the row: the agent's answer, the chunks it retrieved and the reference it was
    scored against exist only inside `run_eval_suite` while Ragas runs. The
    calibration harness needs those four strings in front of a human, beside the
    verdict the Judge reached on them, and the Judge's identity is on the
    `eval_results` row (0023). Without the text there is nothing to label, so
    every artifact the harness can write names no Judge and the deploy gate reads
    `not_calibrated_yet` forever.

WHAT WRITES IT
    `eval_service.write_eval_samples`, once per run, after the agent turns and
    before scoring, from the same rows `run_ragas_eval` is handed. One row per
    scenario the run scored; a scenario the agent did not answer has no row
    here and no row in `eval_results`, so the two tables agree on the scored
    set.

WHAT READS IT
    The calibration harness, over `CALIBRATION_TENANT_DSN`, to build the
    labelling sheet for a run and to join each label to the run's verdict.

A TABLE AND NOT `eval_results.detail`
    `detail` was emptied in 0023 because it repeated a scenario's four scores on
    each of its four rows. The text is one row per scenario, and it has to
    outlive a scoring outage, which the results rows do not: they are written
    after scoring returns.

NO FOREIGN KEY TO `eval_runs`
    Matching `eval_results`, which carries `eval_run_id` unconstrained. A run row
    is inserted before the turns begin and never deleted, so the reference holds
    by construction and a constraint would only make the writer's failure mode
    depend on write order.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS eval_samples (
            id                 UUID PRIMARY KEY,
            eval_run_id        UUID NOT NULL,
            scenario_id        TEXT NOT NULL,
            dataset            TEXT,
            user_input         TEXT NOT NULL,
            response           TEXT NOT NULL,
            retrieved_contexts JSONB NOT NULL,
            reference          TEXT NOT NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "COMMENT ON TABLE eval_samples IS "
        "'The four strings Ragas scored for one scenario of one eval run, written "
        "before scoring so they survive a scoring outage. The calibration harness "
        "puts them in front of a human beside the verdict on the matching "
        "eval_results rows (issue #58).'"
    )
    op.execute(
        "COMMENT ON COLUMN eval_samples.retrieved_contexts IS "
        "'The chunk texts the agent retrieved during this turn, one string each, "
        "in the order the tool returned them. Never the scenario''s stored "
        "contexts.'"
    )
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_eval_samples_eval_run_id
            ON eval_samples (eval_run_id)
    """)


def downgrade() -> None:
    # IF EXISTS so a downgrade against a database that never received 0027 is a
    # no-op. Dropping the table loses every run's scored text; the scores in
    # eval_results survive untouched.
    op.execute("DROP TABLE IF EXISTS eval_samples")
