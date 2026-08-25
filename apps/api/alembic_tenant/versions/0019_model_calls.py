"""Tenant DB v19 migration for the model_calls ledger (ticket #46, issue #22).

Revision ID: 0019
Revises: 0018

Context:
    Ten call sites in `apps/api/app` build an anthropic client, read the text back
    and throw the response's `usage` and `model` away. Nothing recorded what a call
    spent, so `eval_runs.config.model_id` held the requested alias and
    `turn_metrics.cost_usd` held the CLI's Anthropic-book figure for calls DeepSeek
    served. The first Harness run could not be priced from either. This table is
    where one call's token counts are written down, one row per call, written by
    `app.core.model_client.record_model_call` from the httpx response hook.

    NO MONEY COLUMN. Dollars and rand are derived at read time by
    `app.domain.pricing` against a versioned book and a dated fx table. A stored
    cost freezes yesterday's price into a row that a corrected book can never
    reach, and correcting the book is the reason the book carries a version.

    `at` TAKES NO DEFAULT, where `turn_metrics.created_at` does. The writer always
    knows when the call happened, because the response hook stamps it. A DEFAULT
    now() would let a row that lost its instant read as though it happened at
    insert time, and pricing reads the CAT peak window off that instant. A call
    placed in the wrong window prices at a fifth or at five times what it cost.

    THE IDS ARE TEXT, where `turn_metrics.agent_id` is UUID. Recording is fail open
    by design: an insert that fails logs loudly and lets the model call succeed, so
    a column type that can refuse a value `ModelCall` accepts turns a recordable
    call into a silently lost row. `ModelCall` guarantees a non-empty string and
    nothing narrower, and these columns accept exactly that. `job_id` also matches
    `turn_metrics.job_id`, which has been TEXT since 0009.

    NO CHECK ON model_source. `ModelCall` refuses any value outside the enum at
    construction, which is the guard that runs on every write. A second copy in the
    catalogue would need its own migration every time the enum grows, and the two
    would drift.

    Raw SQL with IF NOT EXISTS guards, the convention every tenant table has
    followed since 0008. No SQLAlchemy ORM model.

    APPLIED AND VERIFIED 2026-08-25 against the local `wchats_tenant_probe` cluster,
    through the production path (`migrations.run_tenant_migrations`): 0018 -> 0019,
    the table arrives with fourteen columns and both indexes, a row seeded through
    `record_model_call` reads back with its counts intact, downgrade to 0018 drops
    the table and re-upgrade restores it.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS model_calls (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            purpose                 TEXT NOT NULL,
            provider                TEXT NOT NULL,
            requested_model         TEXT NOT NULL,
            served_model            TEXT NOT NULL,
            model_source            TEXT NOT NULL,
            input_tokens            INT NOT NULL,
            output_tokens           INT NOT NULL,
            cache_read_tokens       INT NOT NULL,
            cache_creation_tokens   INT NOT NULL,
            at                      TIMESTAMPTZ NOT NULL,
            tenant_id               TEXT NOT NULL,
            agent_id                TEXT,
            job_id                  TEXT
        )
    """)
    op.execute("""
        COMMENT ON TABLE model_calls IS
        'Ticket #46. One row per call to a model, written by app.core.model_client from the httpx response hook. Tokens are the fact the provider reported; money is derived at read time by app.domain.pricing against a versioned book, so no cost is stored here.'
    """)
    # Every rollup reads a window of time, so `at` carries the report.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_model_calls_at
        ON model_calls (at)
    """)
    # One job's spend is the other read: a turn, an eval run, an ingestion chain.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_model_calls_job_id
        ON model_calls (job_id)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_model_calls_job_id")
    op.execute("DROP INDEX IF EXISTS ix_model_calls_at")
    op.execute("DROP TABLE IF EXISTS model_calls")
