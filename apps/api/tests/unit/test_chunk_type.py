"""Unit tests for app.domain.chunk.Chunk — the frozen chunk record (ticket #42, issue #7).

The type removes one failure mode: a chunk whose id disagrees with the position
it claims. `id` is derived inside the constructor from (document_id, ordinal)
and is not a constructor parameter, so no caller can set the two apart, and a
retry that rebuilds the same position rebuilds the same id (ING-05).

The id assertions carry UUID LITERALS, computed once with stdlib
`uuid.uuid5(uuid.NAMESPACE_DNS, f"{document_id}:{ordinal}")`. Calling
`deterministic_chunk_id` inside the test to build the expected value would
compare the implementation against itself and stay green for any namespace or
any name format, including a changed one that orphans every chunk row already
stored in a tenant DB.

Read NAMESPACE_DNS literally. `CHUNK_UUID_NAMESPACE` holds
6ba7b810-9dad-11d1-80b4-00c04fd430c8, which is `uuid.NAMESPACE_DNS`, while
chunk_id.py named it NAMESPACE_URL (6ba7b811-...) in prose until 2026-08-24.
The pinned VALUE is what every chunk row in every tenant DB was keyed with and
is not up for correction; only the prose was wrong. These literals are the
first assertion in the repo that would notice the value moving, because
test_chunk_id.py's namespace test builds its expected UUID from the same
constant it is checking.
"""

import base64
import dataclasses
import os
import uuid

# Env setup before any `from app` import, matching tests/unit/test_chunk_id.py.
# app.domain.chunk imports stdlib and one domain sibling only, so Settings never
# loads here, but the block keeps the file runnable in isolation.
os.environ.setdefault("NEON_API_KEY", "test_neon_key")
os.environ.setdefault("NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ADMIN_KEY", "test_admin_key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")

import pytest  # noqa: E402

from app.domain.chunk import Chunk  # noqa: E402

DOC = "550e8400-e29b-41d4-a716-446655440000"

# uuid5(NAMESPACE_DNS, "550e8400-e29b-41d4-a716-446655440000:<ordinal>").
ID_AT_0 = uuid.UUID("94a95541-fb48-5918-9a19-2a9e3932b380")
ID_AT_1 = uuid.UUID("67dfaf41-94e3-5d6e-8bf0-f082264b3f4c")
ID_AT_7 = uuid.UUID("45759b03-307b-5766-886a-69bf400d1e9d")


def _chunk(ordinal: int = 0, content: str = "body text", **overrides) -> Chunk:
    fields = {
        "document_id": DOC,
        "ordinal": ordinal,
        "content": content,
        "token_count": 2,
        "is_table": False,
    }
    fields.update(overrides)
    return Chunk(**fields)


# ---------------------------------------------------------------------------
# The id is computed, never supplied
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ordinal,expected",
    [(0, ID_AT_0), (1, ID_AT_1), (7, ID_AT_7)],
)
def test_construction_computes_the_id_for_the_position(ordinal, expected):
    """Chunk(document_id, ordinal, ...) lands on the uuid5 that position owns."""
    assert _chunk(ordinal=ordinal).id == expected


def test_the_id_is_a_uuid_not_a_string():
    assert isinstance(_chunk().id, uuid.UUID)


def test_passing_an_id_is_rejected():
    """A caller who supplies an id is the failure mode this type exists to stop."""
    with pytest.raises(TypeError):
        Chunk(
            document_id=DOC,
            ordinal=0,
            content="body text",
            token_count=2,
            is_table=False,
            id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        )


# ---------------------------------------------------------------------------
# Frozen
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("id", uuid.UUID("00000000-0000-0000-0000-000000000000")),
        ("document_id", "some-other-doc"),
        ("ordinal", 99),
        ("content", "rewritten"),
        ("token_count", 0),
        ("is_table", True),
    ],
)
def test_every_field_refuses_assignment(attribute, value):
    chunk = _chunk()
    before = getattr(chunk, attribute)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(chunk, attribute, value)
    assert getattr(chunk, attribute) == before


# ---------------------------------------------------------------------------
# Position decides the id, content does not
# ---------------------------------------------------------------------------


def test_same_position_gives_the_same_id_whatever_the_content():
    plain = _chunk(ordinal=3, content="one body", token_count=2, is_table=False)
    table = _chunk(ordinal=3, content="| a | b |\n|---|---|", token_count=6, is_table=True)
    assert plain.id == table.id


def test_ids_are_unique_and_deterministic_across_many_positions():
    """48 positions. Each id is stable for its position and shared with no other."""
    seen: dict[uuid.UUID, tuple[str, int]] = {}
    for doc in [f"doc-{n:04d}" for n in range(6)]:
        for ordinal in range(8):
            first = Chunk(
                document_id=doc,
                ordinal=ordinal,
                content=f"body of {doc} at {ordinal}",
                token_count=5,
                is_table=False,
            )
            second = Chunk(
                document_id=doc,
                ordinal=ordinal,
                content="entirely different body",
                token_count=3,
                is_table=True,
            )
            assert first.id == second.id, f"id changed with content at ({doc}, {ordinal})"
            assert first.id not in seen, (
                f"({doc}, {ordinal}) collides with {seen.get(first.id)} on {first.id}"
            )
            seen[first.id] = (doc, ordinal)

    assert len(seen) == 48
