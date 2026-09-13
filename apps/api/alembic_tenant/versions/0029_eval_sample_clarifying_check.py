"""Tenant DB v29 migration: an ambiguous scenario's verdict lives on its sample row (#226).

Revision ID: 0029
Revises: 0028

Context:
    0028 gave `eval_scenarios` an `ambiguous` flag: true when a correct reply is
    a clarifying question rather than an answer. Such a row is not scored by the
    four Ragas metrics, because a correct clarifying question retrieves nothing
    and faithfulness over no context is not a measurement. Its verdict is a
    deterministic check on the response, and this column is where that verdict
    is kept, beside the four strings the check read (ADR 0012).

`eval_samples.clarifying_check`
    NULL for every row of a scenario that is not ambiguous, which is every row
    written before 0029. TRUE when the response asked a clarifying question,
    FALSE when it answered instead. The run counts a TRUE as the scenario
    passing and a FALSE as it failing, in the same per-dataset counts the Judge's
    verdicts land in, so the existing golden and exploratory rules read it
    without a fifth metric.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE eval_samples
            ADD COLUMN IF NOT EXISTS clarifying_check BOOLEAN
    """)
    op.execute(
        "COMMENT ON COLUMN eval_samples.clarifying_check IS "
        "'For a scenario marked ambiguous: TRUE when the response asked a "
        "clarifying question, FALSE when it answered. NULL for every other row. "
        "This is the row''s verdict; such a row has no eval_results (#226).'"
    )


def downgrade() -> None:
    # IF EXISTS, so a downgrade against a database that never received 0029 is
    # a no-op. Dropping it loses the verdict on ambiguous rows; the scenario's
    # flag, the strings and every Judge score survive untouched.
    op.execute("ALTER TABLE eval_samples DROP COLUMN IF EXISTS clarifying_check")
