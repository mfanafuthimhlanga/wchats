"""Tenant DB v10 migration — retrieval_metrics table.

Revision ID: 0010
Revises: 0009

Context:
    OPS-05/OPS-06: adds `retrieval_metrics` — one row per `retrieve_tool` call,
    written from inside `agent_tools.retrieve_tool` (NOT `agent.py`) because the
    BM25/vector/RRF/rerank rank-and-score data only exists inside that tool's
    closure and never crosses back into the SDK loop (21-RESEARCH.md Pattern 1,
    Anti-Patterns).

    OPS-05 (retrieval health): bm25_top_score / vector_top_score / rrf_top_score /
    rerank_top_score capture the BM25->vector->hybrid->reranker score progression.
    reranker_lift is the rerank_top_score - bm25_top_score delta (the BM25 baseline
    uses the native tsvector/ts_rank_cd score already computed by rrf_fuse — no
    pg_search/pgbm25, CLAUDE.md rule 8). recall_at_k/ndcg_at_10/mrr/cited_chunk_rank
    are computed against the pre-rerank RRF fusion ranking, using the reranker's own
    selection as the best available per-query relevance signal (no human-labeled
    ground truth exists per live query).

    OPS-06 (context engineering / "context rot"): retrieved_tokens is the character-
    proxy token estimate of the chunks actually returned to the agent;
    ctx_window_utilization is that against the 200k budget; carried_never_cited_tokens
    is the token cost of chunks that were fused/considered but did not survive rerank
    into the returned set; compaction_ratio is returned_tokens / considered_tokens.

    citation_coverage/faithfulness are nullable — they stay NULL from this write path
    and are filled later by the sampled Ragas faithfulness task (21-04).

    Follows the established raw-SQL + IF NOT EXISTS convention (mirrors
    0009_turn_metrics_message_feedback.py) — no SQLAlchemy ORM model, consistent
    with every other tenant-DB table.

    Schema — retrieval_metrics:
        id                          UUID PK DEFAULT gen_random_uuid()
        job_id                      TEXT NOT NULL           — correlates to control-DB
                                                                job_events / Langfuse trace
        conversation_id             UUID
        bm25_top_score               NUMERIC
        vector_top_score             NUMERIC
        rrf_top_score                 NUMERIC
        rerank_top_score              NUMERIC
        reranker_lift                 NUMERIC                 — rerank_top_score - bm25_top_score
        recall_at_k                   NUMERIC
        ndcg_at_10                     NUMERIC
        mrr                            NUMERIC
        cited_chunk_rank               INT                     — top returned chunk's rank in
                                                                  the pre-rerank fused ranking
        retrieved_tokens                INT                    — sum(len(content))//4 over
                                                                  returned chunks
        ctx_window_utilization          NUMERIC                — retrieved_tokens / 200000
        carried_never_cited_tokens      INT
        compaction_ratio                NUMERIC
        citation_coverage               NUMERIC                — nullable, filled by 21-04
        faithfulness                    NUMERIC                — nullable, filled by 21-04
        created_at                      TIMESTAMPTZ NOT NULL DEFAULT now()

    Indexes:
        ix_retrieval_metrics_job_id       ON retrieval_metrics (job_id)
        ix_retrieval_metrics_created_at   ON retrieval_metrics (created_at)

    All DDL statements use IF NOT EXISTS guards so the migration is safe to re-run
    on tenant DBs that may have been manually altered (established convention, see
    0008/0009's T-17-03 note).
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS retrieval_metrics (
            id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            job_id                      TEXT NOT NULL,
            conversation_id             UUID,
            bm25_top_score              NUMERIC,
            vector_top_score            NUMERIC,
            rrf_top_score               NUMERIC,
            rerank_top_score            NUMERIC,
            reranker_lift               NUMERIC,
            recall_at_k                 NUMERIC,
            ndcg_at_10                  NUMERIC,
            mrr                         NUMERIC,
            cited_chunk_rank            INT,
            retrieved_tokens            INT,
            ctx_window_utilization      NUMERIC,
            carried_never_cited_tokens  INT,
            compaction_ratio            NUMERIC,
            citation_coverage           NUMERIC,
            faithfulness                NUMERIC,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_retrieval_metrics_job_id
        ON retrieval_metrics (job_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_retrieval_metrics_created_at
        ON retrieval_metrics (created_at)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_retrieval_metrics_created_at")
    op.execute("DROP INDEX IF EXISTS ix_retrieval_metrics_job_id")
    op.execute("DROP TABLE IF EXISTS retrieval_metrics")
