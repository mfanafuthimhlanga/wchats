"""Tenant DB v18 migration for chunks.is_table (ticket #42, issue #7).

Revision ID: 0018
Revises: 0017

Context:
    `chunking_service.chunk_document` runs a two-path strategy and records which
    path each chunk came from: True for a table serialised as Markdown by
    `table.export_to_markdown()`, False for HybridChunker text. That flag was
    computed on every chunk since M2 and then dropped. The INSERT in
    `app/worker/tasks/pipeline/chunk.py` listed five columns (id, document_id,
    ordinal, content, token_count), and the flag reached only the log line that
    counts how many tables a document had.

    So nothing downstream could tell a table from prose. Retrieval ranks a
    pipe-delimited Markdown grid against the same BM25 and embedding assumptions
    as a paragraph, and the offline judges see a wall of cell text with no way to
    say it is a table. This column is where the flag is written down.

    WHY NOT NULL WITH A DEFAULT, where 0017 refuses both. 0017's column had to
    keep NULL and an empty array apart, because they record different
    observations about a retrieval. There is no third state here. A chunk either
    came from the table path or it did not, and every row written before this
    column existed came from that same split, so `false` is what a pre-0018 row
    means rather than a guess about it. A nullable column would invent an
    "unknown" the writer cannot produce and every reader would then carry.

    The DEFAULT is also what makes this safe on a live tenant with rows already
    in `chunks`: PostgreSQL 11 and later store a non-volatile column default in
    the catalogue instead of rewriting the table, so the ALTER is a metadata
    change and no backfill statement is needed. On this corpus the flag is
    wrong only for pre-existing table chunks, and a re-ingest corrects those
    through the ON CONFLICT (id) DO UPDATE path, which now sets is_table too.

    APPLIED AND VERIFIED 2026-08-24 against the local `wchats_tenant_probe`
    cluster, through the production path (`migrations.run_tenant_migrations`):
    0017 -> 0018, the column arrives `boolean`, NOT NULL, DEFAULT false, the
    COMMENT lands, and an existing row reads back as false. Downgrade to 0017
    drops it and re-upgrade restores it.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF NOT EXISTS so a re-run is a no-op, matching 0017.
    op.execute("""
        ALTER TABLE chunks
        ADD COLUMN IF NOT EXISTS is_table boolean NOT NULL DEFAULT false
    """)
    op.execute("""
        COMMENT ON COLUMN chunks.is_table IS
        'Ticket #42. True when chunking_service produced this chunk from doc.tables as Markdown, false when it came from HybridChunker text. Rows written before this column existed read false, which is what the text-or-table split that predates it already meant.'
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS is_table")
