"""Tenant DB v26 migration — retrieval_metrics.context_source (issue #120).

Revision ID: 0026
Revises: 0025

Context:
    `retrieval_metrics.faithfulness` is one column holding scores from three
    different instruments:

        the SDK-era proxy      `str(block.content)[:200]`, a repr of the block list
        the post-#48 proxy     `wire_text(wire)[:200]`, joined text with a json payload
        the chunks themselves  one string per chunk, untruncated, from 280ff05

    #84 closed by removing the proxy rather than versioning it, which fixed the
    instrument going forward and left the history unlabelled. Nothing on a row
    says which of the three produced its number, so an average over the column
    mixes all three and reports an instrument change as a quality change.

    `judge_identity` (0020) does not answer this. It is NULL for every row
    written before it, so it separates the eras only by accident, and it names
    the MODEL rather than the shape of the text that model was shown.

NULLABLE, WITH NO BACKFILL, AND THAT IS THE HONEST ANSWER
    NULL reads as "one of the two proxies, shape unknown", which is exactly what
    the history is. The information needed to tell the two proxies apart is not
    on the row and cannot be reconstructed, so a backfill would have to guess,
    and a guessed label is worse than an absent one: it looks authoritative.
    0025's backfill was justified because the order it reconstructs is the order
    the writer wrote in. There is no equivalent here.

WHAT WRITES IT
    `retrieval_eval._update_retrieval_metrics`, from
    `app.domain.eval_result.CONTEXT_PROXY_VERSION`, the same constant the offline
    eval record stamps, so the live Judge and the offline one name one shape with
    one string. A row carrying only citation_coverage gets NULL: that number is
    arithmetic the task does itself, with no judge and no assembled context to
    describe, which is the rule `judge_identity` already follows one column over.

WHAT READS IT
    `retrieval_metrics_service.read_retrieval_health` averages faithfulness over
    the stamped rows only and reports how many rows it covered and how many it
    left out. citation_coverage is unfiltered, because the cutover did not move
    it.

TEXT AND NOT AN ENUM OR A CHECK
    The value is a version string that BUMPS whenever the text reaching a judge
    changes shape. A CHECK would need its own migration every time, and the two
    would drift. The constant is the single source and the write path is one
    function.

APPLIED AND VERIFIED 2026-09-04 against the local `wchats_tenant_probe` cluster
through the production path (`migrations.run_tenant_migrations`). The observed
round trip is recorded in tests/unit/test_migration_tenant_0026.py.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE retrieval_metrics
        ADD COLUMN IF NOT EXISTS context_source TEXT
    """)
    op.execute("""
        COMMENT ON COLUMN retrieval_metrics.context_source IS
        'Issue #120. The shape of the text this row''s faithfulness score was computed over, written from app.domain.eval_result.CONTEXT_PROXY_VERSION. NULL means one of the two pre-280ff05 proxies, shape unknown, which is what the history is; a reader averaging faithfulness across rows filters on this or it mixes instruments.'
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE retrieval_metrics DROP COLUMN IF EXISTS context_source")
