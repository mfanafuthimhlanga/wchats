"""Tenant DB v1 schema — all 10 tables with vector and pg_trgm extensions.

Revision ID: 0001
Revises: None
Create Date: 2026-05-12

Schema source: prd-M1.md §5.1

Tables created (all empty in M1; populated from M2+):
  documents, chunks, embeddings, chunk_metadata,
  conversations, messages, tool_calls,
  eval_runs, eval_results, red_team_runs

Extensions:
  vector    — pgvector for HNSW similarity search (M2/M3)
  pg_trgm   — trigram matching for fuzzy text search

Index notes:
  chunks_content_tsv_idx — GIN index on tsvector for BM25 (M3, native tsvector + ts_rank_cd)
  embeddings_vector_hnsw_idx — HNSW index with vector_cosine_ops for pgvector (M2/M3)
  HNSW index requires the vector extension to exist BEFORE the table is created.

IMPORTANT: Extensions MUST be created before tables that reference them (HNSW index).
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Extensions — must precede table creation (HNSW index depends on vector)
    # ------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ------------------------------------------------------------------
    # documents
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE documents (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_type  TEXT NOT NULL,
            source_uri   TEXT NOT NULL,
            title        TEXT,
            metadata     JSONB NOT NULL DEFAULT '{}',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ------------------------------------------------------------------
    # chunks
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE chunks (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id  UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            ordinal      INT NOT NULL,
            content      TEXT NOT NULL,
            token_count  INT NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX chunks_document_id_idx ON chunks(document_id)")
    # GIN tsvector index for BM25 search (native pg, no pg_search/pgbm25)
    op.execute(
        "CREATE INDEX chunks_content_tsv_idx ON chunks USING GIN (to_tsvector('english', content))"
    )

    # ------------------------------------------------------------------
    # embeddings
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE embeddings (
            chunk_id   UUID PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
            model      TEXT NOT NULL,
            vector     VECTOR(1024) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # HNSW index for approximate nearest-neighbour search (pgvector)
    op.execute(
        "CREATE INDEX embeddings_vector_hnsw_idx ON embeddings "
        "USING hnsw (vector vector_cosine_ops)"
    )

    # ------------------------------------------------------------------
    # chunk_metadata
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE chunk_metadata (
            chunk_id   UUID PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
            summary    TEXT,
            keywords   TEXT[],
            questions  TEXT[],
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ------------------------------------------------------------------
    # conversations
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE conversations (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            external_id TEXT,
            started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            ended_at    TIMESTAMPTZ
        )
    """)

    # ------------------------------------------------------------------
    # messages
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE messages (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role            TEXT NOT NULL,
            content         TEXT NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX messages_conversation_id_idx ON messages(conversation_id)")

    # ------------------------------------------------------------------
    # tool_calls
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE tool_calls (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            tool_name  TEXT NOT NULL,
            arguments  JSONB NOT NULL,
            result     JSONB,
            latency_ms INT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ------------------------------------------------------------------
    # eval_runs
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE eval_runs (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            kind        TEXT NOT NULL,
            started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ,
            status      TEXT NOT NULL DEFAULT 'running'
        )
    """)

    # ------------------------------------------------------------------
    # eval_results
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE eval_results (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            eval_run_id UUID NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
            scenario_id TEXT NOT NULL,
            metric      TEXT NOT NULL,
            score       NUMERIC,
            detail      JSONB,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ------------------------------------------------------------------
    # red_team_runs
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE red_team_runs (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            kind         TEXT NOT NULL,
            started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at  TIMESTAMPTZ,
            findings     JSONB,
            max_severity TEXT
        )
    """)


def downgrade() -> None:
    # Drop in reverse dependency order
    op.execute("DROP TABLE IF EXISTS red_team_runs")
    op.execute("DROP TABLE IF EXISTS eval_results")
    op.execute("DROP TABLE IF EXISTS eval_runs")
    op.execute("DROP TABLE IF EXISTS tool_calls")
    op.execute("DROP TABLE IF EXISTS messages")
    op.execute("DROP TABLE IF EXISTS conversations")
    op.execute("DROP TABLE IF EXISTS chunk_metadata")
    op.execute("DROP TABLE IF EXISTS embeddings")
    op.execute("DROP TABLE IF EXISTS chunks")
    op.execute("DROP TABLE IF EXISTS documents")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS vector")
