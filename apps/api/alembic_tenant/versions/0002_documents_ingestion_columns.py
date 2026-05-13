"""Tenant DB v2 migration — ingestion columns + entities / chunk_entities tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-13

Changes:
  documents: ADD COLUMN source_hash TEXT
             ADD COLUMN parse_status TEXT NOT NULL DEFAULT 'pending'
             ADD COLUMN chunk_count INT

  NEW TABLE entities (id, name, type, normalized, created_at)
    UNIQUE (normalized, type) — upsert semantics for entity deduplication

  NEW TABLE chunk_entities (chunk_id FK->chunks, entity_id FK->entities, PK composite)
    Joins chunks to their extracted entities (many-to-many, with FK cascade deletes)

ING-06 coverage note (partial):
  These tables are the prerequisite storage layer for entity extraction in Wave 4 (plan
  02-04). Wave 4's generate_metadata task writes entity rows; this migration creates the
  tables only.

T-02-01-04: Schema DDL is non-sensitive; no PII in column names or default values.
T-02-01-05: ALTER TABLE ADD COLUMN with DEFAULT executes a table rewrite; documents
            table is empty in M2-pre-state, so cost is O(0) — accepted.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Extend documents table with ingestion tracking columns
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE documents ADD COLUMN source_hash TEXT")
    op.execute(
        "ALTER TABLE documents ADD COLUMN parse_status TEXT NOT NULL DEFAULT 'pending'"
    )
    op.execute("ALTER TABLE documents ADD COLUMN chunk_count INT")

    # ------------------------------------------------------------------
    # entities — canonical entity registry for the tenant
    #
    # UNIQUE (normalized, type) enables upsert deduplication:
    #   same entity appearing in multiple chunks → one row, N chunk_entities rows.
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE entities (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name        TEXT NOT NULL,
            type        TEXT NOT NULL,
            normalized  TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (normalized, type)
        )
    """)

    # ------------------------------------------------------------------
    # chunk_entities — join table linking chunks to their entities
    #
    # ON DELETE CASCADE on both FKs: deleting a chunk or entity automatically
    # removes the association rows without requiring explicit cleanup.
    # Composite PK prevents duplicate (chunk, entity) pairs.
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE chunk_entities (
            chunk_id    UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
            entity_id   UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
            PRIMARY KEY (chunk_id, entity_id)
        )
    """)


def downgrade() -> None:
    # Drop in reverse dependency order — chunk_entities references both
    # chunks and entities, so it must be dropped first.
    op.execute("DROP TABLE IF EXISTS chunk_entities")
    op.execute("DROP TABLE IF EXISTS entities")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS chunk_count")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS parse_status")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS source_hash")
