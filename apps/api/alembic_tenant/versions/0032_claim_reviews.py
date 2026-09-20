"""Tenant DB v32 migration: claim_reviews, the Tenant's answer on a flagged claim (#290 step 3).

Revision ID: 0032
Revises: 0031

Context:
    Since 0031 a faithfulness row carries the judge's claims, each marked
    supported or not. On the platform benchmark the judge flags every planted
    sentence and also a median of four claims on every real answer, and nobody
    has said whether those four are in the Tenant's documents. The Tenant is the
    one person who can. The review step in Deploy shows each flagged claim with
    the passage it came from and asks one question, "is this in your documents?",
    yes or no. This table holds the answers.

`claim_reviews`
    One row per (run, scenario, claim position) the Tenant answered. `position`
    is the claim's index in `eval_results.claims` on that scenario's faithfulness
    row, and `statement` is copied beside it so the answer still reads if the
    judge row is ever rescored. `supported` is the Tenant's answer. A second
    answer on the same claim replaces the first: the unique key is what makes
    the writer an upsert, and the newest answer is the one a reader wants.

WHAT READS IT
    The results route, so the console shows the Tenant's answer beside the
    judge's flag; the platform benchmark's import, which turns each answer into
    a truth row (owner decision 2026-09-17: a Tenant's answers feed the shared
    benchmark); and, once the judge's precision on reviewed claims is known, the
    gate rule that replaces the faithfulness fraction (#270). Nothing on the
    deploy path reads it yet.

NO FOREIGN KEY TO `eval_runs`
    Matching `eval_samples` (0027) and `eval_runs.source_run_id` (0030). A run
    row is inserted before any work starts and never deleted, so the reference
    holds by construction.

Additive, guarded both ways: IF NOT EXISTS up, IF EXISTS down. Downgrade drops
the Tenant's answers and nothing else.

APPLIED AND VERIFIED 2026-09-20 against the local `wchats_tenant_probe` cluster
through the production path (`migrations.run_tenant_migrations`), with
`command.downgrade(cfg, "0031")` for the way down: the table arrives with its
unique key and index, downgrade drops both, re-upgrade restores them. The
observed output is in `.dev/plans/260920-claims-review.md`, and the upsert and
the flagged-claims join run against a real tenant database in
`tests/integration/test_claim_reviews_db.py`.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS claim_reviews (
            id          UUID PRIMARY KEY,
            eval_run_id UUID NOT NULL,
            scenario_id TEXT NOT NULL,
            position    INTEGER NOT NULL,
            statement   TEXT NOT NULL,
            supported   BOOLEAN NOT NULL,
            reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (eval_run_id, scenario_id, position)
        )
    """)
    op.execute(
        "COMMENT ON TABLE claim_reviews IS "
        "'The Tenant''s yes or no on one claim the faithfulness judge flagged: is "
        "this in your documents. position indexes eval_results.claims on the "
        "scenario''s faithfulness row; statement is copied beside it. One row per "
        "(run, scenario, position), the newest answer replacing the last (#290).'"
    )
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_claim_reviews_eval_run_id
            ON claim_reviews (eval_run_id)
    """)


def downgrade() -> None:
    # IF EXISTS, so a downgrade against a database that never received 0032 is
    # a no-op. Dropping the table loses the Tenant's answers; the judge's claims
    # on eval_results survive untouched.
    op.execute("DROP INDEX IF EXISTS ix_claim_reviews_eval_run_id")
    op.execute("DROP TABLE IF EXISTS claim_reviews")
