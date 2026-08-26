"""Tenant DB v20 migration for retrieval_metrics.judge_identity (ticket #47, AC3).

Revision ID: 0020
Revises: 0019

Context:
    A calibration figure says how well one Judge agrees with a human, so it is
    measured against a named Judge. `eval_results.detail` has carried that name
    for the four offline metrics since #47's slice C. The fifth Judge scores live
    traffic from `app.worker.tasks.runtime.retrieval_eval`, and its verdict lands
    in `retrieval_metrics.faithfulness`, where nothing said which model, which
    reasoning effort or which prompt produced it. Two runs on different Judges
    read as one population, and the agreement number is then computed across both.

    JSONB AND NOT THREE COLUMNS. `JudgeIdentity` is one value with three fields
    that are only ever read together, the grain #53's CalibrationStatus groups on.
    Three columns would let a row hold one field and not the others, which is the
    partial key the domain type refuses at construction.

    NULLABLE, AND NULL MEANS UNKNOWN. Every row written before this migration has
    no Judge recorded, and so does every row whose faithfulness is NULL, because
    citation_coverage is arithmetic the task does itself and no Judge ran for it.
    A verdict whose Judge is unknown is unknown, never filed under the Judge that
    happened to run last.

    NO CHECK CONSTRAINT ON THE SHAPE. `JudgeIdentity.__post_init__` refuses an
    empty or blank field on every write, which is the guard that actually runs.
    A second copy in the catalogue would need its own migration each time the
    type changes, and the two would drift.

    Raw SQL with IF NOT EXISTS guards, the convention every tenant table has
    followed since 0008. No SQLAlchemy ORM model.

    APPLIED AND VERIFIED 2026-08-25 against the local `wchats_tenant_probe`
    cluster through the production path (`migrations.run_tenant_migrations`).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE retrieval_metrics
        ADD COLUMN IF NOT EXISTS judge_identity JSONB
    """)
    op.execute("""
        COMMENT ON COLUMN retrieval_metrics.judge_identity IS
        'Ticket #47 AC3. The model, reasoning effort and prompt version of the Judge that produced this row''s faithfulness score, written by app.worker.tasks.runtime.retrieval_eval. NULL means no Judge is known for this row, which is what a row with no faithfulness score has.'
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE retrieval_metrics DROP COLUMN IF EXISTS judge_identity")
