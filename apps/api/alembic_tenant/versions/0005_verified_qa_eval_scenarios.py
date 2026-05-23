"""Tenant DB v5 migration — verified_qa + eval_scenarios tables for M6 eval system.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-23

Context:
    M6: Eval System — adds the two tables that drive the automated nightly eval
    and the verified knowledge layer.

    verified_qa (D-05 LOCKED):
      Holds QA pairs that have passed both the faithfulness and relevancy
      promotion gates. The question_vector column (VECTOR(1024)) is indexed
      with HNSW for cosine similarity lookups at retrieval time (D-24/D-25).

      Source lifecycle:
        'sandbox_test'         — promoted by the nightly eval harness
        'production_promotion' — promoted from production mining (future)
        'human_authored'       — written directly by the tenant owner

    eval_scenarios (D-06 LOCKED):
      Stores test scenarios that the nightly eval harness runs against.
      Scenarios are either generated at build time (source='generated') or
      mined from flagged production conversations (source='mined').

    NOTE: eval_runs and eval_results already exist in 0001. Do NOT recreate them.

    HNSW index on question_vector follows the same pattern as
    embeddings_vector_hnsw_idx in 0001.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # verified_qa — promoted QA pairs with HNSW-indexed question vectors
    # Schema source: prd.md §6 Layer 4 + D-05 LOCKED
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE verified_qa (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            question        TEXT NOT NULL,
            question_vector VECTOR(1024) NOT NULL,
            answer          TEXT NOT NULL,
            citations       JSONB NOT NULL,
            source          TEXT NOT NULL
                                CHECK (source IN ('sandbox_test', 'production_promotion', 'human_authored')),
            faithfulness    NUMERIC,
            relevance       NUMERIC,
            promoted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            promoted_by     TEXT,
            last_used_at    TIMESTAMPTZ,
            use_count       INT DEFAULT 0,
            invalidated_at  TIMESTAMPTZ
        )
    """)
    # HNSW index for cosine similarity lookup at retrieval time (D-25: threshold 0.93)
    op.execute(
        "CREATE INDEX verified_qa_vector_idx ON verified_qa "
        "USING hnsw (question_vector vector_cosine_ops)"
    )

    # ------------------------------------------------------------------
    # eval_scenarios — test scenarios for the nightly eval harness
    # Schema source: D-06 LOCKED
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE eval_scenarios (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source             TEXT NOT NULL
                                   CHECK (source IN ('generated', 'mined')),
            question           TEXT NOT NULL,
            reference_answer   TEXT NOT NULL,
            retrieved_contexts JSONB NOT NULL DEFAULT '[]'::jsonb,
            scenario_category  TEXT,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_run_at        TIMESTAMPTZ,
            run_count          INT DEFAULT 0
        )
    """)


def downgrade() -> None:
    # Drop in dependency order — eval_scenarios has no FK references to verified_qa,
    # but drop it first to keep the order logical and safe.
    op.execute("DROP TABLE IF EXISTS eval_scenarios")
    op.execute("DROP TABLE IF EXISTS verified_qa")
