"""Unit tests for app.domain.retrieved_context (ticket #44, issue #7).

The type answers one question, "what did this retrieval hand back", with one
score and one rank per chunk. The four per-engine scores the retrieval module
computes (cosine, bm25, rrf, rerank) stay inside that module: `strategy` names
which engine produced the ranking, so `score` is never ambiguous.

The JSON shape is pinned with LITERALS. Round-tripping a value through
`to_json` and `from_json` and asserting the two objects are equal passes for
any key names at all, including renamed ones that a reader on the other side of
the wire cannot parse. The literal is what notices a key moving.

`from_json` raises on a wrong-shaped payload, and that is the whole point of
it. A payload that is missing its chunks must not read as a retrieval that
found nothing. A silent empty context is the shape a grounding Judge cannot
tell from a genuine miss.
"""

import dataclasses
import json
from decimal import Decimal

import pytest

from app.domain.retrieved_context import (
    InvalidRetrievedContext,
    RetrievedChunk,
    RetrievedContext,
)

CHUNK_JSON = {
    "chunk_id": "c1",
    "document_id": "d1",
    "content": "Unopened bags, 14 days.",
    "score": 0.95,
    "rank": 1,
}

CONTEXT_JSON = {
    "query": "what is the return window?",
    "strategy": "rerank",
    "chunks": [
        CHUNK_JSON,
        {
            "chunk_id": "c2",
            "document_id": "d2",
            "content": "Refunds take 5 days.",
            "score": 0.8,
            "rank": 2,
        },
    ],
}


def _chunk(**overrides) -> RetrievedChunk:
    fields = {
        "chunk_id": "c1",
        "document_id": "d1",
        "content": "Unopened bags, 14 days.",
        "score": 0.95,
        "rank": 1,
    }
    fields.update(overrides)
    return RetrievedChunk(**fields)


def _context(**overrides) -> RetrievedContext:
    fields = {
        "query": "what is the return window?",
        "chunks": (_chunk(), _chunk(chunk_id="c2", document_id="d2",
                                    content="Refunds take 5 days.", score=0.8, rank=2)),
        "strategy": "rerank",
    }
    fields.update(overrides)
    return RetrievedContext(**fields)


# ---------------------------------------------------------------------------
# Frozen
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("chunk_id", "other"),
        ("document_id", "other"),
        ("content", "rewritten"),
        ("score", 0.0),
        ("rank", 99),
    ],
)
def test_a_chunk_refuses_assignment(attribute, value):
    chunk = _chunk()
    before = getattr(chunk, attribute)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(chunk, attribute, value)
    assert getattr(chunk, attribute) == before


@pytest.mark.parametrize(
    "attribute,value",
    [("query", "another question"), ("chunks", ()), ("strategy", "vector")],
)
def test_a_context_refuses_assignment(attribute, value):
    context = _context()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(context, attribute, value)


# ---------------------------------------------------------------------------
# The chunks are a tuple, whatever the caller passed
# ---------------------------------------------------------------------------


def test_a_list_of_chunks_becomes_a_tuple():
    """A caller's list stays the caller's to append to; the context must not share it."""
    passed = [_chunk()]
    context = _context(chunks=passed)
    assert isinstance(context.chunks, tuple)

    passed.append(_chunk(chunk_id="c9"))
    assert len(context.chunks) == 1, "the context read a later append to the caller's list"


def test_an_empty_context_is_a_whole_context():
    """A retrieval that matched nothing is an observation, not a missing value."""
    assert _context(chunks=[]).chunks == ()


@pytest.mark.parametrize(
    "wrong",
    [
        # Not a sequence at all. `tuple("abc")` raises nothing and builds three
        # chunks that name no chunk.
        "c1c2",
        42,
        None,
        {"chunk_id": "c1"},
        # A sequence, carrying something that is not a chunk. A dict of the
        # right five keys is the near miss: `element.content` on it is an
        # AttributeError deep inside the framer, far from here.
        ["c1c2"],
        [42],
        [None],
        [CHUNK_JSON],
        (_chunk(), "c2"),
    ],
)
def test_chunks_that_are_not_a_sequence_of_chunks_are_refused(wrong):
    """Every element is a RetrievedChunk, or the context does not build."""
    with pytest.raises(TypeError):
        _context(chunks=wrong)


# ---------------------------------------------------------------------------
# The JSON shape, pinned with literals
# ---------------------------------------------------------------------------


def test_chunk_to_json_is_the_five_named_keys_in_order():
    assert _chunk().to_json() == CHUNK_JSON
    assert list(_chunk().to_json()) == ["chunk_id", "document_id", "content", "score", "rank"]


def test_context_to_json_is_the_pinned_shape():
    assert _context().to_json() == CONTEXT_JSON


def test_context_to_json_key_order_is_query_strategy_chunks():
    """`str()` over this dict is the model-facing text, so order is part of the shape."""
    assert list(_context().to_json()) == ["query", "strategy", "chunks"]


def test_to_json_survives_a_round_trip_through_a_real_json_string():
    """`json.dumps` is the wire. A value the encoder refuses fails there, not here."""
    context = _context()
    through_a_string = json.loads(json.dumps(context.to_json()))
    assert through_a_string == CONTEXT_JSON
    assert RetrievedContext.from_json(through_a_string) == context


def test_to_json_emits_a_plain_float_and_a_plain_int():
    """psycopg2 hands a NUMERIC column back as a Decimal, and json.dumps refuses one."""
    chunk = _chunk(score=Decimal("0.95"), rank=Decimal("1"))
    row = chunk.to_json()
    assert type(row["score"]) is float
    assert type(row["rank"]) is int
    assert json.loads(json.dumps(row)) == CHUNK_JSON


def test_from_json_reads_the_pinned_shape_back():
    assert RetrievedContext.from_json(CONTEXT_JSON) == _context()


def test_a_round_trip_is_equal_to_what_went_in():
    context = _context()
    assert RetrievedContext.from_json(context.to_json()) == context


def test_a_round_trip_of_an_empty_context_is_equal_too():
    context = _context(chunks=[])
    assert RetrievedContext.from_json(context.to_json()) == context


def test_equality_reads_the_chunks_not_the_object_identity():
    assert _context() == _context()
    assert _context() != _context(strategy="rrf")
    assert _context() != _context(chunks=[_chunk()])


# ---------------------------------------------------------------------------
# from_json fails loudly. A malformed payload is never an empty context.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["query", "strategy", "chunks"])
def test_a_missing_top_level_key_is_refused(key):
    payload = {name: value for name, value in CONTEXT_JSON.items() if name != key}
    with pytest.raises(InvalidRetrievedContext):
        RetrievedContext.from_json(payload)


def test_missing_chunks_never_reads_as_a_retrieval_that_found_nothing():
    """An unreadable payload is not a corpus miss."""
    payload = {"query": "q", "strategy": "rerank"}
    with pytest.raises(InvalidRetrievedContext):
        RetrievedContext.from_json(payload)


@pytest.mark.parametrize("wrong", ["c1c2", 42, None, {"chunk_id": "c1"}])
def test_chunks_that_are_not_a_list_are_refused(wrong):
    with pytest.raises(InvalidRetrievedContext):
        RetrievedContext.from_json(dict(CONTEXT_JSON, chunks=wrong))


@pytest.mark.parametrize("key", ["chunk_id", "document_id", "content", "score", "rank"])
def test_a_chunk_missing_one_key_is_refused(key):
    broken = {name: value for name, value in CHUNK_JSON.items() if name != key}
    with pytest.raises(InvalidRetrievedContext):
        RetrievedContext.from_json(dict(CONTEXT_JSON, chunks=[broken]))


@pytest.mark.parametrize("wrong", ["c1", 42, None, ["c1"]])
def test_a_chunk_that_is_not_a_mapping_is_refused(wrong):
    with pytest.raises(InvalidRetrievedContext):
        RetrievedContext.from_json(dict(CONTEXT_JSON, chunks=[wrong]))


@pytest.mark.parametrize(
    "key,wrong",
    [
        ("chunk_id", 1),
        ("document_id", 1),
        ("content", 42),
        ("content", None),
        ("score", "0.9"),
        ("score", None),
        ("rank", "1"),
        ("rank", 1.5),
        ("rank", None),
    ],
)
def test_a_chunk_field_of_the_wrong_type_is_refused(key, wrong):
    """Presence is not shape. A string score reaches the Agent as a string."""
    broken = dict(CHUNK_JSON, **{key: wrong})
    with pytest.raises(InvalidRetrievedContext):
        RetrievedContext.from_json(dict(CONTEXT_JSON, chunks=[broken]))


def test_a_whole_number_score_reads_as_a_float():
    """JSON writes 1.0 as 1, so an int score is the same number, not a wrong type."""
    payload = dict(CONTEXT_JSON, chunks=[dict(CHUNK_JSON, score=1)])
    chunk = RetrievedContext.from_json(payload).chunks[0]
    assert chunk.score == 1.0
    assert type(chunk.score) is float


def test_an_extra_key_is_ignored_rather_than_refused():
    """A payload written by another revision still reads.

    A per-engine score arriving on the wire is dropped, which is the same
    promise the type makes everywhere else: one score, one rank.
    """
    payload = dict(CONTEXT_JSON, chunks=[dict(CHUNK_JSON, cosine_score=0.9)])
    payload["retriever_version"] = "3"
    assert RetrievedContext.from_json(payload).chunks[0] == _chunk()


def test_invalid_retrieved_context_is_a_value_error():
    """Callers that already catch ValueError keep catching it."""
    assert issubclass(InvalidRetrievedContext, ValueError)
