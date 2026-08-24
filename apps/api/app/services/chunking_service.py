"""
Chunking service — two-path text + table strategy for M2 ingestion pipeline.

PITFALLS.md §2 — Table Flattening:
    Docling's HybridChunker has a documented open issue where table structure gets
    corrupted in chunk output. The table path here explicitly skips any HybridChunker
    chunk whose doc_items contain a TableItem instance, and instead serialises every
    table as Markdown via table.export_to_markdown(doc=doc). Tables are NEVER fed to
    HybridChunker. This is a first-class design decision, not a post-hoc patch.

PITFALLS.md §11 — Indirect Prompt Injection (Sanitization Layer):
    All chunk content, whether produced by HybridChunker.contextualize() (text path)
    or by table.export_to_markdown() (table path), passes through
    sanitize_chunk_text() before a Chunk is built from it. The sanitized string is
    what gets stored in the tenant chunks table, so injection markers never reach
    the DB.

Function signature:
    chunk_document(doc, document_id) -> tuple[Chunk, ...]

Chunk (app/domain/chunk.py) carries document_id, ordinal, content, token_count and
is_table, and derives `id` from (document_id, ordinal) in its own constructor. This
service issues the ordinal and nothing else, so the ING-05 idempotency contract no
longer depends on one function remembering to pass the same counter twice. The
return is a tuple because a caller that appends to it would be adding a chunk whose
ordinal came from no counter at all.
"""

import structlog

from app.domain.chunk import Chunk
from app.utils.sanitize import sanitize_chunk_text

log = structlog.get_logger(__name__)


def chunk_document(doc, document_id: str) -> tuple[Chunk, ...]:
    """Produce ordered Chunks from a DoclingDocument.

    Two-path strategy (order: text path first, then table path appended):

    Text path:
        Iterate HybridChunker(max_tokens=512, merge_peers=True) output. For each
        chunk, skip it if any doc_item is a TableItem instance (table content handled
        separately below). Otherwise, call chunker.contextualize(chunk) — NOT
        the heading-breadcrumb-enriched string — to get the embed string, then apply
        sanitize_chunk_text() before building the Chunk.

    Table path:
        For each table in doc.tables, call table.export_to_markdown(doc=doc).
        Apply sanitize_chunk_text() on the Markdown output. One chunk per table.

    Both paths share a single monotonic ordinal counter, and Chunk derives each id
    from (document_id, ordinal) as a uuid5, stable across reruns (PITFALLS.md §8 /
    ING-05 idempotency contract).

    Args:
        doc:         DoclingDocument returned by docling_service.parse_document().
        document_id: String UUID of the document row in the tenant documents table.

    Returns:
        Ordered tuple of Chunk. `content` is sanitized and ready for chunks.content;
        `ordinal` is zero-indexed and monotonic across text then table chunks;
        `is_table` is True for the table path; `token_count` is an approximation
        (len(content.split())). Replace with a proper tokenizer in M3 if the token
        budget starts to matter.
    """
    from docling.chunking import HybridChunker  # lazy — only available in pipeline worker
    from docling_core.types.doc import TableItem  # noqa: F811
    chunker = HybridChunker(max_tokens=512, merge_peers=True)
    chunks: list[Chunk] = []
    ordinal = 0

    # -------------------------------------------------------------------------
    # Text path — HybridChunker output, skipping chunks that contain TableItem
    # -------------------------------------------------------------------------
    for chunk in chunker.chunk(doc):
        # Skip chunks whose doc_items include any TableItem instance.
        # HybridChunker may emit text-adjacent chunks that still carry table cells;
        # those must go through the table path below (PITFALLS.md §2).
        has_table = any(
            isinstance(item, TableItem)
            for item in getattr(chunk.meta, "doc_items", [])
        )
        if has_table:
            continue

        # contextualize() prepends heading breadcrumbs — the correct embed string.
        # Calling the raw attribute on the chunk (without context) must NOT be used here (PITFALLS.md §1).
        content = sanitize_chunk_text(chunker.contextualize(chunk))
        if not content:
            continue

        chunks.append(
            Chunk(
                document_id=document_id,
                ordinal=ordinal,
                content=content,
                token_count=len(content.split()),
                is_table=False,
            )
        )
        ordinal += 1

    # -------------------------------------------------------------------------
    # Table path — one Markdown chunk per table in doc.tables
    # -------------------------------------------------------------------------
    for table in doc.tables:
        md = table.export_to_markdown(doc=doc)  # pass doc= for proper formatting
        if not md.strip():
            continue

        content = sanitize_chunk_text(md)
        if not content:
            continue

        chunks.append(
            Chunk(
                document_id=document_id,
                ordinal=ordinal,
                content=content,
                token_count=len(content.split()),
                is_table=True,
            )
        )
        ordinal += 1

    log.info(
        "chunking_service.complete",
        document_id=document_id,
        chunk_count=len(chunks),
        table_count=sum(1 for c in chunks if c.is_table),
    )
    return tuple(chunks)
