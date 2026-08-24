"""ChunkMetadata, what enrichment produced for one chunk (ticket #42, issues #7, #23).

WHY A TYPE RATHER THAN THE LOOSE KEYS IT REPLACES
    The enrichment step read `meta.summary`, `meta.keywords`, `meta.questions`
    and `meta.entities` straight off the model's parsed response and passed them
    to an INSERT. Nothing in the task held the idea "this chunk was enriched", so
    a run that enriched nothing looked, from the outside, exactly like a run that
    enriched everything: on 2026-08-22 three documents failed every batch and the
    job still reported succeeded.

    Building one of these per enriched chunk makes the count real, and
    generate_metadata fails the job when the count is zero (issue #23).

WHY chunk_id IS A PARAMETER, UNLIKE Chunk.id
    Chunk derives its id because it is naming a position it just created.
    ChunkMetadata describes a chunks row that already exists, so its chunk_id is
    read from that row and carried, never recomputed. The value is the same one
    `Chunk.id` holds, and it is the foreign key of every write the task makes.

WHY A FROZEN STDLIB DATACLASS
    Same reasoning as Chunk: this validates nothing and is constructed only by
    our own task, from a response pydantic has already validated. Five fields,
    all required, no defaults. A record that exists is a chunk that was enriched.

Rung: `app.domain` imports the standard library, third-party packages and its
domain siblings. This module imports the standard library only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChunkMetadata:
    """The enrichment of one chunk, as the task holds it before it is persisted.

    Args:
        chunk_id:  The `chunks.id` this describes, the value `Chunk.id` computes.
        summary:   One or two sentences covering the chunk.
        keywords:  Noun-phrase keywords, written to `chunk_metadata.keywords`.
        questions: Hypothetical questions this chunk answers.
        entities:  Named entities found in the chunk, each carrying `name`, `type`
                   and `normalized`. Typed loosely because the class behind them,
                   app.services.metadata_service.EntityExtraction, sits above
                   app.domain in the import layers and cannot be named here.
                   Empty is a whole record: a chunk may name nobody.
    """

    chunk_id: uuid.UUID
    summary: str
    keywords: list[str]
    questions: list[str]
    entities: list[Any]
