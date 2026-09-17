"""Tenant DB v31 migration: a faithfulness row carries the claims it scored (#290).

Revision ID: 0031
Revises: 0030

Context:
    `eval_results.score` on a faithfulness row is the share of the answer's
    atomic statements the judge found in the retrieved text. The share cannot
    say which statement it did not find. On 2026-09-17 ten answers seeded with
    one claim the retrieved text does not carry scored between 0.80 and 0.92 at
    the judge, and the owner passed nine of them on the labelling sheet: a
    fraction over claims separates neither a planted lie from a long grounded
    answer nor a judge miss from a labeller miss. The claims do.

`eval_results.claims`
    JSONB, a list of `{"statement", "supported", "reason"}` in the order the
    judge decided them, written by `write_eval_results` from
    `JudgeRecord.claims`. The domain type refuses a list whose share of
    supported claims is not the row's own score, so the column and `score`
    cannot disagree on a row this build wrote.

    NULL on every row written before 0031, on every row of a metric that
    decides no claims (the other four), and on a faithfulness row whose judge
    call failed. NULL means no claims were recorded; it is never an empty list
    and never a passing answer.

    No index. The reader is step 3 of #290, the tenant's review of flagged
    claims, which reads a run's rows by `eval_run_id`, already indexed by 0001.

Additive, nullable, rollback-safe, the 0023 shape: IF NOT EXISTS up, IF EXISTS
down. Downgrade drops the claims and leaves `score` and the verdict untouched,
so a row loses its working and keeps its number.

APPLIED AND VERIFIED 2026-09-17 against the local `wchats_tenant_probe` cluster
through the production path (`migrations.run_tenant_migrations`), with
`command.downgrade(cfg, "0030")` for the way down: the column arrives as
nullable jsonb with no default and its comment, downgrade drops it, re-upgrade
restores it. The observed output is in `.dev/plans/260917-faithfulness-claims.md`.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE eval_results ADD COLUMN IF NOT EXISTS claims JSONB
    """)
    op.execute(
        "COMMENT ON COLUMN eval_results.claims IS "
        "'The atomic statements the judge decided this row''s score over, each "
        "with its verdict and reason, in the order decided. Written for "
        "faithfulness rows since #290; the share of supported claims is the "
        "row''s score. NULL means no claims were recorded: every row before "
        "0031, every metric that decides none, and a judge call that failed. "
        "NULL is never a passing answer.'"
    )


def downgrade() -> None:
    # IF EXISTS, so a downgrade against a database that never received 0031 is
    # a no-op. Dropping it loses each faithfulness row's claims; the score, the
    # verdict, the gate and the Judge survive untouched.
    op.execute("ALTER TABLE eval_results DROP COLUMN IF EXISTS claims")
