"""Chunk — the record the chunker emits and the pipeline persists (ticket #42, issue #7).

WHY THE ID IS NOT A CONSTRUCTOR PARAMETER
    `deterministic_chunk_id(document_id, ordinal)` is the ING-05 idempotency
    contract: the same position rebuilds the same UUID, so a retried task
    upserts its own rows instead of duplicating them. While the chunker emitted
    plain dicts, that contract held only because one function happened to fill
    `id` and `ordinal` from the same counter. Any later writer could set them
    apart, and a chunk whose id names position 4 while its ordinal says 7
    upserts over a row that belongs to a different passage.

    Here the id is derived in `__post_init__` from the two fields it is defined
    by, and `field(init=False)` keeps it out of the generated `__init__`, so
    `Chunk(id=...)` is a TypeError. There is no construction path that produces
    a disagreement.

WHY A FROZEN STDLIB DATACLASS RATHER THAN PYDANTIC
    The rejection of a supplied id comes free from `field(init=False)`, whereas
    a pydantic model needs `extra="forbid"` plus a validator to say the same
    thing, and `id` being a real field would make `extra` the wrong lever
    anyway. `transactional_schemas.py` is pydantic because it validates tool
    arguments arriving from a model and publishes a JSON schema; Chunk
    validates nothing and is constructed only by our own chunker, so the
    dataclass carries the whole contract in five lines.

Rung: `app.domain` imports the standard library, third-party packages and its
domain siblings. `app.domain.chunk_id` is a sibling.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.domain.chunk_id import deterministic_chunk_id


@dataclass(frozen=True)
class Chunk:
    """One passage of a document, at a known position, with its derived id.

    Args:
        document_id: String UUID of the row in the tenant `documents` table.
        ordinal:     Zero-based position within that document, monotonic across
                     the text path then the table path.
        content:     Sanitised text, written straight into `chunks.content`.
        token_count: Approximate token count for the content.
        is_table:    True when the chunker produced this from `doc.tables` as
                     Markdown, False for HybridChunker text output.

    Attributes:
        id: uuid5 over (document_id, ordinal). Computed, never passed.
    """

    document_id: str
    ordinal: int
    content: str
    token_count: int
    is_table: bool
    id: uuid.UUID = field(init=False)

    def __post_init__(self) -> None:
        # object.__setattr__ is how a frozen dataclass fills a derived field.
        object.__setattr__(
            self, "id", deterministic_chunk_id(self.document_id, self.ordinal)
        )
