"""RetrievedContext, what one retrieval handed back (ticket #44, issue #7).

WHY ONE SCORE AND ONE RANK
    Four engines run behind this type and each names its number differently:
    cosine_score, bm25_score, rrf_score, rerank_score, with vector_rank and
    bm25_rank alongside. While the retrieval module returned plain dicts, every
    reader had to know which engine produced the row before it could read a
    number off it, and `chunk["rrf_score"]` on a reranked row is a KeyError that
    only fires on the path nobody tested.

    `strategy` names the engine, so `score` and `rank` mean one thing per
    context and mean it for every chunk in that context. The per-engine numbers
    stay inside app.services.retrieval_service, where the engine that computed
    them is in scope.

WHY THE JSON IS SEPARATE FROM THE DATACLASS
    `to_json` is the wire form, and `str()` over it is still what the Agent
    reads as a tool result. Its key ORDER is therefore part of the shape,
    which is why it is written out rather than taken from `dataclasses.asdict`.

    `from_json` IGNORES KEYS IT DOES NOT KNOW, so a payload written by another
    revision still reads. What it refuses is a payload it cannot read as a
    context at all. A missing `chunks` key must raise rather than build an empty
    context, because "this retrieval found nothing" is an observation a
    grounding Judge acts on and "this payload could not be read" is not.

WHY chunks IS A TUPLE
    The record is frozen, so what it holds is immutable too. An empty tuple is a
    whole context. A retrieval that matched nothing ran and reported.

Rung: `app.domain` imports the standard library, third-party packages and its
domain siblings. This module imports the standard library only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# What a chunk key is allowed to arrive as on the wire. `score` takes an int
# too, because JSON writes 1.0 as 1 and that is the same number.
_CHUNK_TYPES: dict[str, tuple[type, ...]] = {
    "chunk_id": (str,),
    "document_id": (str,),
    "content": (str,),
    "score": (int, float),
    "rank": (int,),
}
_CHUNK_KEYS = tuple(_CHUNK_TYPES)
_CONTEXT_KEYS = ("query", "strategy", "chunks")


class InvalidRetrievedContext(ValueError):
    """A payload that cannot be read as a retrieved context.

    A top-level key is missing, `chunks` is not a list, or one chunk is missing
    a field. A ValueError, so callers that already catch ValueError keep
    catching it.
    """


@dataclass(frozen=True)
class RetrievedChunk:
    """One passage a retrieval returned, at its position in that ranking.

    Args:
        chunk_id:    String UUID of the row in the tenant `chunks` table.
        document_id: String UUID of the document the passage came from.
        content:     The passage text, as the Agent and the Judges read it.
                     `chunks.content` is TEXT NOT NULL in the tenant schema
                     (alembic_tenant 0001), so every row carries a str.
        score:       The ranking number of the engine named by the context's
                     `strategy`. Comparable within one context, never across two.
        rank:        1-based position in that same ranking.
    """

    chunk_id: str
    document_id: str
    content: str
    score: float
    rank: int

    def __post_init__(self) -> None:
        # psycopg2 reads a NUMERIC column as a Decimal and json.dumps refuses
        # one, so the record normalises to the two builtins the wire form
        # writes. object.__setattr__ is how a frozen dataclass sets a field.
        object.__setattr__(self, "score", float(self.score))
        object.__setattr__(self, "rank", int(self.rank))

    def to_json(self) -> dict[str, Any]:
        """The five keys, in the order the wire form pins."""
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "content": self.content,
            "score": self.score,
            "rank": self.rank,
        }

    @classmethod
    def from_json(cls, payload: Any) -> RetrievedChunk:
        """Read one chunk, refusing anything this type cannot name.

        Presence is not shape. A string where `score` belongs survives every
        key check and reaches the Agent as a string, so the type of each
        field is read here as well as its presence.

        Raises:
            InvalidRetrievedContext: not a mapping, a field is absent, or a
                field arrived as a type this chunk cannot hold.
        """
        if not isinstance(payload, Mapping):
            raise InvalidRetrievedContext(
                f"a chunk reads as a mapping of {len(_CHUNK_TYPES)} keys, got "
                f"{type(payload).__name__}"
            )
        missing = [key for key in _CHUNK_TYPES if key not in payload]
        if missing:
            raise InvalidRetrievedContext(f"chunk is missing {', '.join(missing)}")
        for key, allowed in _CHUNK_TYPES.items():
            value = payload[key]
            # bool passes isinstance(value, int), and True is not a rank.
            if isinstance(value, bool) or not isinstance(value, allowed):
                names = " or ".join(one.__name__ for one in allowed)
                raise InvalidRetrievedContext(
                    f"chunk {key} reads as {names}, got {type(value).__name__}"
                )
        return cls(**{key: payload[key] for key in _CHUNK_TYPES})


@dataclass(frozen=True)
class RetrievedContext:
    """The chunks one retrieval returned for one query, under one strategy.

    Args:
        query:    The text the retrieval ran for, as the customer asked it.
        chunks:   The ranked passages. A list is accepted and copied; the
                  context holds a tuple. Empty is allowed.
        strategy: Which engine produced the ranking, so `score` and `rank` on
                  every chunk read as that engine's numbers. "vector", "bm25",
                  "rrf" and "rerank" are what retrieval_service produces.

    Raises:
        TypeError: chunks is neither a list nor a tuple, or one element of it
            is not a RetrievedChunk.
    """

    query: str
    # The init input, not what the record holds. __post_init__ copies whatever
    # sequence it is handed into a tuple.
    chunks: Sequence[RetrievedChunk]
    strategy: str

    def __post_init__(self) -> None:
        if not isinstance(self.chunks, (list, tuple)):
            # A string is the expensive case. tuple("abc") raises nothing and
            # builds three chunks that name no chunk.
            raise TypeError(
                "RetrievedContext needs chunks as a list or a tuple, got "
                f"{type(self.chunks).__name__}"
            )
        wrong = [
            type(element).__name__
            for element in self.chunks
            if not isinstance(element, RetrievedChunk)
        ]
        if wrong:
            # A dict of the right five keys is the near miss. It reads as a
            # chunk everywhere a reader only indexes it, and `element.content`
            # raises AttributeError deep inside the framer instead.
            raise TypeError(
                "RetrievedContext needs every chunk to be a RetrievedChunk, got "
                + ", ".join(wrong)
            )
        # object.__setattr__ is how a frozen dataclass normalises a field.
        object.__setattr__(self, "chunks", tuple(self.chunks))

    def to_json(self) -> dict[str, Any]:
        """The wire form: query, strategy, then the chunks in ranked order."""
        return {
            "query": self.query,
            "strategy": self.strategy,
            "chunks": [chunk.to_json() for chunk in self.chunks],
        }

    @classmethod
    def from_json(cls, payload: Any) -> RetrievedContext:
        """Read a context back, refusing a payload that is not one.

        Raises:
            InvalidRetrievedContext: not a mapping, a key is absent, chunks is
                not a list, or one chunk is malformed.
        """
        if not isinstance(payload, Mapping):
            raise InvalidRetrievedContext(
                f"a context reads as a mapping, got {type(payload).__name__}"
            )
        missing = [key for key in _CONTEXT_KEYS if key not in payload]
        if missing:
            raise InvalidRetrievedContext(f"context is missing {', '.join(missing)}")
        chunks = payload["chunks"]
        if not isinstance(chunks, (list, tuple)):
            raise InvalidRetrievedContext(
                f"chunks reads as a list, got {type(chunks).__name__}"
            )
        return cls(
            query=payload["query"],
            chunks=tuple(RetrievedChunk.from_json(chunk) for chunk in chunks),
            strategy=payload["strategy"],
        )
