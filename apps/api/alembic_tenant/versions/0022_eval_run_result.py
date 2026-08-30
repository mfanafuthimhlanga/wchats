"""Tenant DB v22 migration. An eval run stores what it measured (ticket 14, #51).

Revision ID: 0022
Revises: 0021

Context:
    A completed eval run leaves its scores in `eval_results`, one row per
    (scenario, metric), and its configuration in `eval_runs.config`. Neither
    holds the run's numbers. Every reader therefore derives them again:
    `api/v1/evals.py` runs its own `COUNT`/`AVG`, `deployment_service`
    `_fetch_eval_summary_sync` runs a second one, and `run_eval_suite` builds a
    third in Python for its return dict. Three arithmetics over one run, free to
    disagree, and the deploy gate reads whichever it reads.

    This migration adds the column that holds the answer as one value:

    result JSONB, nullable, no CHECK, no DEFAULT.
        `app.domain.eval_result.EvalResult.payload`: {"run_id", "agent_id",
        "prompt_version_id", "judge_identity", "requested_model",
        "served_model", "invocation", "datasets", "attempted", "valid",
        "scored", "cost", "context_proxy_version", "rule_version"}, stamped at
        run completion, on the below-floor path as well as the scored one.

    NULL means "this run did not record its result", which is every row written
    before this revision and every run on a tenant that has not been migrated.
    That is not the same claim as "this run measured nothing", and it is the same
    distinction 0021 drew for `red_team_runs.result` and 0015 for `coverage`: the
    reader reports the absence rather than substituting a number of its own.

    THE COLUMN IS BESIDE `config`, NOT INSIDE IT. `config` is the tuple a run is
    an assertion ABOUT (the model, the prompt version, the envelope hash, the
    corpus size) and it is written at INSERT, before the first turn. The result
    is what the run MEASURED and it is written at the end. Merging the two would
    put a measurement inside the record of the configuration it was measured
    under, and `update_eval_run_config`'s shallow `||` merge would then be one
    edit away from half-overwriting a dataset.

    NO CHECK, NO NOT NULL, NO INDEX. `EvalResult` refuses a payload that
    misreports a run at construction, on the way in and again on the way out
    through `from_payload`, which is the guard that actually runs. A second copy
    in the catalogue would need its own migration each time the record changes,
    and the two would drift. Same reasoning as 0021's refusal to constrain
    `result`, 0020's to constrain `judge_identity` and 0014's to constrain
    `dataset`.

    Raw SQL with an IF NOT EXISTS guard, the convention every tenant table has
    followed since 0008. No SQLAlchemy ORM model.

Additive, nullable, rollback-safe:
    `write_eval_result` catches psycopg2 UndefinedColumn and reports False, so a
    tenant that never receives this revision keeps running evals and its runs
    simply cannot say what they measured. `read_eval_result` returns None on the
    same catch, which is the reading a NULL column already produces. Rolling back
    is a no-op for the application.

    APPLIED AND VERIFIED 2026-08-30 against the local `wchats_tenant_probe`
    cluster through the production path (`migrations.run_tenant_migrations`):
    0021 to 0022, the column arrives `jsonb` and nullable with no DEFAULT,
    downgrade drops it, re-upgrade restores it.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # eval_runs.result, nullable JSONB. NULL means "this run did not record
    # what it measured", which readers report as such and never fill in from a
    # recomputation of their own.
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS result JSONB
    """)
    op.execute("""
        COMMENT ON COLUMN eval_runs.result IS
        'EvalResult.payload: the run identity, its invocation counters, one '
        'outcome per dataset carrying attempted/valid/scored and a '
        'value/observations/measured triple per metric, and the cost from the '
        'ledger. NULL means the run did not record it.'
    """)


def downgrade() -> None:
    # IF EXISTS so a downgrade against a database that never received 0022 is a
    # no-op rather than an error. Dropping the column loses each run's record of
    # what it measured; every run row survives untouched, `eval_results` still
    # holds the per-scenario scores, and readers fall back to reporting the
    # result as unrecorded.
    op.execute("ALTER TABLE eval_runs DROP COLUMN IF EXISTS result")
