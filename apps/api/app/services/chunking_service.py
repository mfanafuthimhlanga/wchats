"""
Chunking service — two-path text + table strategy for M2 ingestion pipeline.

PITFALLS.md §2 — Table Flattening:
    Docling's HybridChunker has a documented open issue where table structure gets
    corrupted in chunk output. The table path here explicitly skips any HybridChunker
    chunk whose doc_items contain a TableItem instance, and instead serialises every
    table as Markdown via table.export_to_markdown(doc=doc). Tables are NEVER fed to
    HybridChunker. This is a first-class design decision, not a post-hoc patch.

PITFALLS.md §11 — Indirect Prompt Injection (Sanitization Layer):
    All chunk text — whether produced by HybridChunker.contextualize() (text path) or
    by table.export_to_markdown() (table path) — passes through sanitize_chunk_text()
    before being added to the returned list. The sanitized text is what gets stored in
    the tenant chunks table. This ensures injection markers never reach the DB.

Function signature:
    chunk_document(doc, document_id) → list[dict]

Each dict has keys: id, text, ordinal, is_table, token_count.
"""

import structlog
from docling.chunking import HybridChunker
from docling_core.types.doc import TableItem

from app.utils.chunk_id import deterministic_chunk_id
from app.utils.sanitize import sanitize_chunk_text

log = structlog.get_logger(__name__)


def chunk_document(doc, document_id: str) -> list[dict]:
    """Produce an ordered list of chunk dicts from a DoclingDocument.

    Two-path strategy (order: text path first, then table path appended):

    Text path:
        Iterate HybridChunker(max_tokens=512, merge_peers=True) output. For each
        chunk, skip it if any doc_item is a TableItem instance (table content handled
        separately below). Otherwise, call chunker.contextualize(chunk) — NOT
        the heading-breadcrumb-enriched string — to get the embed string, then apply
        sanitize_chunk_text() before appending.

    Table path:
        For each table in doc.tables, call table.export_to_markdown(doc=doc).
        Apply sanitize_chunk_text() on the Markdown output. One chunk per table.

    Both paths share a single monotonic ordinal counter. Chunk IDs are derived via
    deterministic_chunk_id(document_id, ordinal) — uuid5 that is stable across
    reruns (PITFALLS.md §8 / ING-05 idempotency contract).

    Args:
        doc:         DoclingDocument returned by docling_service.parse_document().
        document_id: String UUID of the document row in the tenant documents table.

    Returns:
        Ordered list of dicts, each with keys:
            id          — str UUID (deterministic_chunk_id result)
            text        — str (sanitized; ready to write into chunks.content)
            ordinal     — int (zero-indexed, monotonic across text + table chunks)
            is_table    — bool (True for table path, False for HybridChunker output)
            token_count — int (approximation: len(text.split()); replace with
                          proper tokenizer in M3 if token budget matters)
    """
    chunker = HybridChunker(max_tokens=512, merge_peers=True)
    chunks: list[dict] = []
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
        text = sanitize_chunk_text(chunker.contextualize(chunk))
        if not text:
            continue

        chunk_id = deterministic_chunk_id(document_id, ordinal)
        chunks.append(
            {
                "id": str(chunk_id),
                "text": text,
                "ordinal": ordinal,
                "is_table": False,
                "token_count": len(text.split()),
            }
        )
        ordinal += 1

    # -------------------------------------------------------------------------
    # Table path — one Markdown chunk per table in doc.tables
    # -------------------------------------------------------------------------
    for table in doc.tables:
        md = table.export_to_markdown(doc=doc)  # pass doc= for proper formatting
        if not md.strip():
            continue

        text = sanitize_chunk_text(md)
        if not text:
            continue

        chunk_id = deterministic_chunk_id(document_id, ordinal)
        chunks.append(
            {
                "id": str(chunk_id),
                "text": text,
                "ordinal": ordinal,
                "is_table": True,
                "token_count": len(text.split()),
            }
        )
        ordinal += 1

    log.info(
        "chunking_service.complete",
        document_id=document_id,
        chunk_count=len(chunks),
        table_count=sum(1 for c in chunks if c["is_table"]),
    )
    return chunks
