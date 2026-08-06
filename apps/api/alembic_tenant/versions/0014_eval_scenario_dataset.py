"""Tenant DB v14 migration — the golden/exploratory split (measurement layer P2).

Revision ID: 0014
Revises: 0013

Context:
    `eval.py`'s scenario selector was `ORDER BY RANDOM() LIMIT 30` — a different
    sample every night. Run-to-run variance is then dominated by which 30 rows
    were drawn rather than by anything the agent did, and drift detection is
    impossible: at n=30 an unpaired five-point regression is invisible inside
    sampling noise, while the SAME 30 items scored twice make it a paired
    per-item delta and therefore detectable. Same cost, sharper instrument.

    This migration adds the one column that lets a run say which measurement it
    is making:

    dataset TEXT — nullable, no CHECK, no DEFAULT.
        'golden'      — a fixed set, run in FULL on every eval, unsampled. Its
                        score is comparable across runs.
        'exploratory' — everything else; a rotating sample. Its score is a
                        different measurement and must never be averaged with
                        the golden score.

    NULL is the state every existing row is in and it means "nobody has
    designated this row", which eval_service.dataset_of() resolves to
    'exploratory'. That direction is deliberate: a golden set is a curated
    claim, so membership must be asserted, never inherited by default. A
    backfill that promoted every existing row to golden would silently make a
    randomly-accumulated pile of Haiku-written scenarios into the stable
    instrument the whole comparison rests on.

    No CHECK constraint. The domain lives in eval_service (DATASET_GOLDEN /
    DATASET_EXPLORATORY) and dataset_of() treats every unrecognised value as
    exploratory, so an unexpected value degrades to "not golden" rather than
    breaking an INSERT on a live tenant. 0011 had to rewrite eval_scenarios'
    source CHECK precisely because an inline CHECK became the thing standing
    between a shipped feature and a working INSERT; this migration does not
    repeat that.

    No index. The golden query is `WHERE reference_answer != '' AND dataset =
    'golden'` over a table that holds tens to hundreds of rows per tenant, and
    an index that is never needed is still a write cost on every scenario
    INSERT.

Additive, nullable, rollback-safe (the plan's risk register):
    Like 0013, this cannot be verified against a live database on the
    development machine (no local PostgreSQL — every `-m integration` harness
    skips, and a skip is unobserved, never a pass). It is therefore strictly
    additive and strictly nullable: ADD COLUMN IF NOT EXISTS only, no CHECK, no
    NOT NULL, no DEFAULT, no backfill, no index, no constraint touched.

    Rolling back is a no-op for the application. `run_eval_suite` catches
    psycopg2 UndefinedColumn on the dataset-aware selector and falls back to the
    pre-0014 single query, reporting `dataset_column_available: False` on the
    run — so a tenant that predates this revision keeps evaluating, it simply
    has no golden set to hold fixed. Same tolerance shape as
    eval_service.insert_eval_run()'s pre-0013 fallback, and for the same reason:
    tenant DBs are migrated with `alembic upgrade head` at PROVISION time only.

    Follows the established raw-SQL convention (mirrors 0009/0010/0011/0012/0013)
    — no SQLAlchemy ORM model, consistent with every other tenant-DB table.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # eval_scenarios.dataset — nullable TEXT. NULL means "not designated",
    # which eval_service.dataset_of() reads as exploratory. Membership of
    # the golden set is asserted, never defaulted into.
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE eval_scenarios ADD COLUMN IF NOT EXISTS dataset TEXT
    """)


def downgrade() -> None:
    # IF EXISTS so a downgrade against a database that never received 0014 is a
    # no-op rather than an error. Dropping the column loses golden-set
    # membership, which is a curation loss, not a data loss: every scenario row
    # survives untouched and the selector falls back to the pre-0014 sample.
    op.execute("ALTER TABLE eval_scenarios DROP COLUMN IF EXISTS dataset")
