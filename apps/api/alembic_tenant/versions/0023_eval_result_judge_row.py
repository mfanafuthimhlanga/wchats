"""Tenant DB v23 migration. A judge row says what it decided (ticket 14, #51).

Revision ID: 0023
Revises: 0022

Context:
    `eval_results` holds one row per (scenario, metric) and carries `score` and
    a `detail` JSONB. Since #47 that `detail` has been the WHOLE score row plus
    the Judge identity, so each of a scenario's four rows repeats all four of
    that scenario's scores. Four copies of every number, and the row's own
    `metric` is the only thing telling a reader which copy belongs to the row.

    A reader after the verdict has it worse. Nothing on the row says whether the
    score cleared its gate. `api/v1/evals.py` rebuilds the comparison from
    `settings` at read time, so raising a threshold silently restates every
    historical verdict and a run scored under the old gate reads as though it
    had been scored under the new one.

    Four columns, so the row carries its own decision:

    binary_verdict BOOLEAN, did this score clear this row's threshold. NULL
        when the metric has no threshold, and NULL when the judge returned no
        score. Never False for either, because "nobody set a gate here" and
        "this failed the gate" are different claims, and a deploy gate cannot
        tell them apart once the second one is written down.

    threshold NUMERIC, the number the score was compared against, as it stood
        when the run was scored. Two of the four metrics have one
        (`EVAL_FAITHFULNESS_THRESHOLD`, `EVAL_RELEVANCY_THRESHOLD`);
        `context_precision` and `context_recall` have none anywhere in the
        codebase, so their rows carry NULL and no verdict rather than a default.
        A default would be a gate nobody chose.

    judge_identity JSONB, the model, the reasoning effort and the prompt
        version of the Judge that produced THIS dimension's score. It was inside
        `detail` and it moves to a column for the reason 0020 gave
        `retrieval_metrics` one: #53 groups verdicts by identity, and grouping
        is a column's job.

    ledger_purpose TEXT, which ledger rows paid for this row's verdict, at the
        finest grain the ledger can actually answer. See below.

WHY THE LEDGER REFERENCE IS A PURPOSE AND NOT A CALL ID
    The obvious column is `model_calls.id`, one per judge call per scenario per
    metric. The ledger cannot supply it. `record_model_call` mints the row's
    uuid inside itself and `Recorder` returns None, so no caller anywhere holds
    a call id. The row is written from an httpx response hook on the async
    client, which fires underneath ragas' own scoring loop and sees a
    `CallContext` of purpose, tenant, agent and job. No scenario reaches it, and
    one `ascore` on Faithfulness makes several calls, so even a hook that could
    see the scenario would owe this row a list rather than an id.

    What the ledger does answer is which BUCKET the calls landed in.
    `eval_service._run_ledger` binds `job_id = run_id`, and each metric scores
    through its own purpose from `JUDGE_PURPOSES`. So this row's judge calls are
    exactly:

        SELECT * FROM model_calls
        WHERE job_id = <this row's eval_run_id> AND purpose = <ledger_purpose>

    which is per metric within the run, never per scenario. The column is stored
    rather than derived from `metric` because `JUDGE_PURPOSE_BY_METRIC` is read
    live and a re-route would restate history, the same reason `judge_identity`
    is stored rather than looked up.

Additive, nullable, rollback-safe:
    IF NOT EXISTS on the way up and IF EXISTS on the way down, the 0022 shape.
    Every row written before this revision reads NULL on all four, which is the
    honest reading: those rows recorded no verdict, no gate, no Judge of their
    own and no bucket.

    NO CHECK CONSTRAINTS. `app.domain.judge_record.JudgeRecord` refuses a row
    whose verdict disagrees with its own score and threshold, on the way in and
    again on the way out through `from_payload`, and that is the guard that
    actually runs. A second copy in the catalogue would need its own migration
    every time the rule changes and the two would drift, which is 0020's, 0021's
    and 0022's reasoning unchanged.

    APPLIED AND VERIFIED 2026-08-30 against the local `wchats_tenant_probe`
    cluster through the production path (`migrations.run_tenant_migrations`):
    0022 to 0023, the four columns arrive nullable with no DEFAULT, downgrade
    drops them, re-upgrade restores them.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # The verdict and the number it was reached against. Both nullable, and a
    # NULL verdict is never a failed one.
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE eval_results ADD COLUMN IF NOT EXISTS binary_verdict BOOLEAN
    """)
    op.execute(
        "COMMENT ON COLUMN eval_results.binary_verdict IS "
        "'Did this row''s score clear this row''s threshold, decided when the run "
        "was scored rather than when the row is read. NULL when the metric has no "
        "threshold and NULL when the judge returned no score. NULL is never a "
        "failed verdict.'"
    )

    op.execute("""
        ALTER TABLE eval_results ADD COLUMN IF NOT EXISTS threshold NUMERIC
    """)
    op.execute(
        "COMMENT ON COLUMN eval_results.threshold IS "
        "'The number this row''s score was compared against, as it stood at scoring "
        "time. NULL for a metric that has no threshold: context_precision and "
        "context_recall have none, so their rows carry no gate rather than a default.'"
    )

    # ------------------------------------------------------------------
    # Out of `detail` and into a column, so #53 can group on it.
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE eval_results ADD COLUMN IF NOT EXISTS judge_identity JSONB
    """)
    op.execute(
        "COMMENT ON COLUMN eval_results.judge_identity IS "
        "'The model, reasoning effort and prompt version of the Judge that produced "
        "THIS dimension''s score. Moved here from detail, matching "
        "retrieval_metrics.judge_identity (0020). NULL means the Judge is unknown.'"
    )

    # ------------------------------------------------------------------
    # The ledger reference, honest about its grain: per metric within the run,
    # never per scenario. The docstring above says why the ledger cannot go
    # finer.
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE eval_results ADD COLUMN IF NOT EXISTS ledger_purpose TEXT
    """)
    op.execute(
        "COMMENT ON COLUMN eval_results.ledger_purpose IS "
        "'Which model_calls rows paid for this row''s verdict: those whose job_id is "
        "this row''s eval_run_id AND whose purpose is this value. THE GRAIN IS THE "
        "METRIC WITHIN THE RUN, NOT THE SCENARIO. The ledger is written from an httpx "
        "response hook under ragas'' scoring loop, which sees no scenario, and one "
        "metric call leaves several ledger rows. NULL means no bucket was recorded.'"
    )


def downgrade() -> None:
    # IF EXISTS so a downgrade against a database that never received 0023 is a
    # no-op rather than an error. Dropping these loses each judge row's verdict,
    # its gate, its Judge and its ledger bucket; `score`, `metric` and `detail`
    # survive untouched, and a reader falls back to reporting the verdict as
    # unrecorded.
    op.execute("ALTER TABLE eval_results DROP COLUMN IF EXISTS binary_verdict")
    op.execute("ALTER TABLE eval_results DROP COLUMN IF EXISTS threshold")
    op.execute("ALTER TABLE eval_results DROP COLUMN IF EXISTS judge_identity")
    op.execute("ALTER TABLE eval_results DROP COLUMN IF EXISTS ledger_purpose")
