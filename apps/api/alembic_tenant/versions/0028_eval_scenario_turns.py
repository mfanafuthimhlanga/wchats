"""Tenant DB v28 migration: a scenario is a conversation, not a lone question (#227).

Revision ID: 0028
Revises: 0027

Context:
    An eval scenario is a question and a reference answer. The eval turn runs
    with an empty history, so the only customer message the agent ever sees is
    the first one of a conversation. A customer's third message, "how do I start
    the dev server", is bound by the project they named in their first, and
    until this migration there was nowhere to put that binding: no column held
    the earlier turns, so the eval could neither send them, test the agent's use
    of them, nor score an answer that relied on them.

    Four columns, two tables.

`eval_scenarios.turns`
    The messages before the question, oldest first. `question` stays the last
    customer message and is NOT repeated in `turns`, so one string is the thing
    scored and one list is the context it was asked in. Every row written before
    0028 gets `[]`, which is the single-turn scenario the eval already runs.

`eval_scenarios.ambiguous`
    True when the right reply is a clarifying question rather than an answer
    (#226). The reference for such a row is the clarifying question. It is a
    column and not a `source` value because a scenario's source says where the
    question came from and this says what a correct reply looks like; a mined
    row and an authored row can both be ambiguous.

`eval_samples.turns`
    The same list, copied onto the row the calibration harness reads, so the
    owner labelling an answer sees the conversation it was given in. Without it
    the sheet shows a follow-up question with its binding stripped off and asks
    a human to judge an answer to a question nobody asked.

`eval_samples.resolved_question`
    The question rewritten to stand alone against its own history, which is what
    answer relevancy is scored against for a multi-turn row (PR 2 of #227).
    NULLABLE, and null is a real state: the resolution step is a model call, and
    a failed one scores the raw question and marks the row rather than inventing
    a reference. NOT NULL with a default would make "never resolved" and
    "resolved to the empty string" the same row.

WHY DEFAULTS AND NOT A BACKFILL
    `[]` and `false` describe every existing row correctly. A scenario written
    before 0028 has no prior turns and is not ambiguous, because the eval that
    wrote it could express neither.

    OBSERVED 2026-09-09 rather than assumed, because every tenant this migration
    reaches has a populated `eval_scenarios`. A row inserted at 0027 and then
    carried up came out `turns = []`, `ambiguous = false`,
    `resolved_question = NULL`, on the local probe cluster at PostgreSQL 17.6.
    The reproduction is in `.dev/reference/260909-tenant-0028-observed.md`.

WHY THE READ PATH STILL DEGRADES
    `run_eval_suite._fetch_scenario_rows` tries the widest projection first and
    falls to a narrower one when a column is absent, because a tenant database
    that stopped at 0027 must still run its eval. The rung it lands on is
    logged, and it keeps the golden split it can still ask for.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE eval_scenarios
            ADD COLUMN IF NOT EXISTS turns JSONB NOT NULL DEFAULT '[]'::jsonb
    """)
    op.execute("""
        ALTER TABLE eval_scenarios
            ADD COLUMN IF NOT EXISTS ambiguous BOOLEAN NOT NULL DEFAULT false
    """)
    op.execute(
        "COMMENT ON COLUMN eval_scenarios.turns IS "
        "'Prior turns, oldest first, each {\"role\": \"user\" | \"assistant\", "
        "\"content\": text}. The question column is the last customer message "
        "and is not repeated here. Empty for a single-turn scenario, which every "
        "row written before 0028 is.'"
    )
    op.execute(
        "COMMENT ON COLUMN eval_scenarios.ambiguous IS "
        "'True when a correct reply is a clarifying question rather than an "
        "answer (#226). The reference_answer of such a row IS the clarifying "
        "question.'"
    )
    op.execute("""
        ALTER TABLE eval_samples
            ADD COLUMN IF NOT EXISTS turns JSONB NOT NULL DEFAULT '[]'::jsonb
    """)
    op.execute("""
        ALTER TABLE eval_samples
            ADD COLUMN IF NOT EXISTS resolved_question TEXT
    """)
    op.execute(
        "COMMENT ON COLUMN eval_samples.turns IS "
        "'The conversation this answer was given in, copied from the scenario so "
        "the calibration sheet can show a human the binding the question relied "
        "on.'"
    )
    op.execute(
        "COMMENT ON COLUMN eval_samples.resolved_question IS "
        "'The last customer message rewritten to stand alone against this row''s "
        "turns, and the string answer relevancy is scored against. NULL when the "
        "resolution step did not run or failed, which scores the raw question "
        "and marks the row.'"
    )


def downgrade() -> None:
    # IF EXISTS on all four, so a downgrade against a database that never
    # received 0028 is a no-op. Dropping them loses the conversation a scenario
    # was asked in; the questions, references and every score survive untouched.
    op.execute("ALTER TABLE eval_scenarios DROP COLUMN IF EXISTS turns")
    op.execute("ALTER TABLE eval_scenarios DROP COLUMN IF EXISTS ambiguous")
    op.execute("ALTER TABLE eval_samples DROP COLUMN IF EXISTS turns")
    op.execute("ALTER TABLE eval_samples DROP COLUMN IF EXISTS resolved_question")
