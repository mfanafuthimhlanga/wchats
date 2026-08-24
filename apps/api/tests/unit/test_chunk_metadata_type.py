"""Unit tests for app.domain.chunk_metadata.ChunkMetadata (ticket #42, issues #7 and #23).

The type removes one failure mode: enrichment output that exists only as loose
dict keys, so a run that produced none of it looks the same as a run that
produced all of it. A ChunkMetadata is built only when a chunk was actually
enriched, so counting the objects is what makes "never produced" visible to the
task, and issue #23's wholly-failed job is a count of zero.

Every field is required. There is no default that lets a half-built record pass
for a whole one, and the tests below hold that: a missing field is a TypeError,
not a None that reaches the INSERT.
"""

import base64
import dataclasses
import os
import uuid

# Env setup before any `from app` import, matching tests/unit/test_chunk_type.py.
# app.domain.chunk_metadata imports the standard library only, so Settings never
# loads here, but the block keeps the file runnable in isolation.
os.environ.setdefault("NEON_API_KEY", "test_neon_key")
os.environ.setdefault("NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ADMIN_KEY", "test_admin_key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")

import pytest  # noqa: E402

from app.domain.chunk_metadata import ChunkMetadata  # noqa: E402

CHUNK = uuid.UUID("94a95541-fb48-5918-9a19-2a9e3932b380")
OTHER_CHUNK = uuid.UUID("67dfaf41-94e3-5d6e-8bf0-f082264b3f4c")


@dataclasses.dataclass(frozen=True)
class _Entity:
    """Stand-in for the entity records the enrichment call returns.

    app.domain is the bottom rung of the import layers, so it cannot name
    app.services.metadata_service.EntityExtraction. Any object with these three
    attributes is what the entities field carries.
    """

    name: str
    type: str
    normalized: str


def _metadata(**overrides) -> ChunkMetadata:
    fields = {
        "chunk_id": CHUNK,
        "summary": "The returns window is 30 days.",
        "keywords": ["returns", "refund window"],
        "questions": ["How long do I have to return an item?"],
        "entities": [_Entity(name="Acme Corp", type="product", normalized="acme corp")],
    }
    fields.update(overrides)
    return ChunkMetadata(**fields)


# ---------------------------------------------------------------------------
# The field set
# ---------------------------------------------------------------------------


def test_the_field_set_is_the_five_the_enrichment_step_produces():
    """chunk_id plus the four values the Haiku call returns, in that order."""
    names = [f.name for f in dataclasses.fields(ChunkMetadata)]
    assert names == ["chunk_id", "summary", "keywords", "questions", "entities"]


def test_the_chunk_id_is_kept_as_given():
    """Unlike Chunk.id, chunk_id is supplied. The row it belongs to already exists."""
    assert _metadata(chunk_id=OTHER_CHUNK).chunk_id == OTHER_CHUNK


@pytest.mark.parametrize(
    "omitted", ["chunk_id", "summary", "keywords", "questions", "entities"]
)
def test_every_field_is_required(omitted):
    """No field defaults. A partial record cannot stand in for an enriched chunk."""
    fields = {
        "chunk_id": CHUNK,
        "summary": "s",
        "keywords": ["k"],
        "questions": ["q?"],
        "entities": [],
    }
    del fields[omitted]
    with pytest.raises(TypeError):
        ChunkMetadata(**fields)


def test_no_entities_is_a_whole_record():
    """A chunk naming nobody is still enriched. The list is empty, never missing."""
    assert _metadata(entities=[]).entities == []


# ---------------------------------------------------------------------------
# Frozen
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("chunk_id", OTHER_CHUNK),
        ("summary", "rewritten"),
        ("keywords", ["something else"]),
        ("questions", []),
        ("entities", []),
    ],
)
def test_every_field_refuses_assignment(attribute, value):
    record = _metadata()
    before = getattr(record, attribute)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(record, attribute, value)
    assert getattr(record, attribute) == before


# ---------------------------------------------------------------------------
# Equality
# ---------------------------------------------------------------------------


def test_same_values_compare_equal():
    assert _metadata() == _metadata()


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("chunk_id", OTHER_CHUNK),
        ("summary", "a different summary"),
        ("keywords", ["returns"]),
        ("questions", ["Where is my order?"]),
        ("entities", []),
    ],
)
def test_a_difference_in_any_field_compares_unequal(attribute, value):
    assert _metadata() != _metadata(**{attribute: value})
