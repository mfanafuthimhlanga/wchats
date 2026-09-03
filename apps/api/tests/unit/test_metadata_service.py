"""
Unit tests for app.services.metadata_service — ING-06.

Tests:
  1. test_enrich_chunk_returns_chunk_metadata_and_entities — return type + fields
  2. test_enrich_chunk_sends_the_routed_model             — model comes off PURPOSE_ROUTES
  3. test_enrich_chunk_uses_response_format_pydantic      — response_format=ChunkMetadataAndEntities (class)
  4. test_enrich_chunk_retries_on_rate_limit              — tenacity retries on RateLimitError
  5. test_entity_extraction_validates_type_literal        — Pydantic rejects invalid entity type
  6. test_chunk_metadata_and_entities_validates_shape     — correct field values on valid construct

Patch target: the client factory (app.core.model_client.make_client, through
model_doubles.factory). Ticket #47 moved construction there, so the module holds
no client of its own and every site is covered by one target.
"""

import base64
import os

# ---------------------------------------------------------------------------
# Environment setup — MUST run before any `from app` import (pydantic-settings)
# ---------------------------------------------------------------------------
os.environ.setdefault(
    "NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode()
)
os.environ.setdefault("NEON_API_KEY", "test_neon")
os.environ.setdefault(
    "CONTROL_DB_URL", "postgresql+asyncpg://user:pass@localhost/testdb"
)
os.environ.setdefault(
    "CONTROL_DB_SYNC_URL", "postgresql://user:pass@localhost/testdb"
)
os.environ.setdefault("ADMIN_KEY", "test_admin")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")
os.environ.setdefault("MAX_UPLOAD_SIZE_MB", "50")

# ---------------------------------------------------------------------------
# Imports (after env setup)
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock, patch

import httpx
import pytest

from tests.model_doubles import completion, factory, ledger

# ---------------------------------------------------------------------------
# Test 1: enrich_chunk returns ChunkMetadataAndEntities with correct fields
# ---------------------------------------------------------------------------


def test_enrich_chunk_returns_chunk_metadata_and_entities():
    """enrich_chunk returns a ChunkMetadataAndEntities instance with expected field values."""
    from app.services.metadata_service import (
        ChunkMetadataAndEntities,
        enrich_chunk,
    )

    mock_result = completion(
        parsed=ChunkMetadataAndEntities(
            summary="A product summary.",
            keywords=["product", "catalog"],
            questions=["What products are available?"],
            entities=[],
        )
    )

    mock_client = MagicMock()
    with factory(mock_client):
        mock_client.chat.completions.parse.return_value = mock_result
        result = enrich_chunk("Some chunk text about products.", ledger())

    assert isinstance(result, ChunkMetadataAndEntities)
    assert result.summary == "A product summary."
    assert result.keywords == ["product", "catalog"]
    assert result.questions == ["What products are available?"]
    assert result.entities == []


# ---------------------------------------------------------------------------
# Test 2: enrich_chunk sends the model its purpose routes to
# ---------------------------------------------------------------------------


def test_enrich_chunk_sends_the_routed_model():
    """The model comes off PURPOSE_ROUTES, so no literal in the module can drift.

    Issue #76: a site naming its own alias is how a call ends up billed under one
    model and served by another.
    """
    from app.core.model_client import route_for
    from app.services.metadata_service import (
        PURPOSE,
        ChunkMetadataAndEntities,
        enrich_chunk,
    )

    mock_result = completion(
        parsed=ChunkMetadataAndEntities(
            summary="s", keywords=["k"], questions=["q?"], entities=[]
        )
    )

    mock_client = MagicMock()
    with factory(mock_client):
        mock_client.chat.completions.parse.return_value = mock_result
        enrich_chunk("text", ledger())

        call_kwargs = mock_client.chat.completions.parse.call_args.kwargs
        expected = route_for(PURPOSE).model
        assert call_kwargs.get("model") == expected, (
            f"Expected model={expected!r} but got {call_kwargs.get('model')!r}"
        )


# ---------------------------------------------------------------------------
# Test 3: enrich_chunk passes response_format=ChunkMetadataAndEntities (the class)
# ---------------------------------------------------------------------------


def test_enrich_chunk_uses_response_format_pydantic():
    """enrich_chunk calls parse with response_format=ChunkMetadataAndEntities (class, not instance)."""
    from app.services.metadata_service import (
        ChunkMetadataAndEntities,
        enrich_chunk,
    )

    mock_result = completion(
        parsed=ChunkMetadataAndEntities(
            summary="s", keywords=["k"], questions=["q?"], entities=[]
        )
    )

    mock_client = MagicMock()
    with factory(mock_client):
        mock_client.chat.completions.parse.return_value = mock_result
        enrich_chunk("text", ledger())

        call_kwargs = mock_client.chat.completions.parse.call_args.kwargs
        assert call_kwargs.get("response_format") is ChunkMetadataAndEntities, (
            f"Expected response_format=ChunkMetadataAndEntities (class) but got "
            f"{call_kwargs.get('response_format')!r}"
        )


# ---------------------------------------------------------------------------
# Test 4: enrich_chunk retries on RateLimitError
# ---------------------------------------------------------------------------


def test_enrich_chunk_retries_on_rate_limit():
    """enrich_chunk retries when the provider raises RateLimitError (tenacity retry rule).

    Uses a real httpx.Request+Response to construct a valid openai.RateLimitError
    (the constructor requires response.request to be set and response.headers to exist).
    OpenAI is the provider these calls reach since issue #76, so its exception
    class is the one tenacity has to catch. `TRANSIENT_ERRORS` names both SDKs'.
    """
    import openai as openai_lib

    from app.services.metadata_service import ChunkMetadataAndEntities, enrich_chunk

    # Build a valid RateLimitError (requires request on response)
    real_request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    real_response = httpx.Response(
        429,
        request=real_request,
        content=b'{"error":{"type":"rate_limit_error","message":"rate limited"}}',
    )
    rate_limit_err = openai_lib.RateLimitError(
        "rate limited", response=real_response, body=None
    )

    # Second call returns a valid result
    mock_result = completion(
        parsed=ChunkMetadataAndEntities(
            summary="s", keywords=["k"], questions=["q?"], entities=[]
        )
    )

    side_effects = [rate_limit_err, mock_result]
    call_count = 0

    def _parse_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        effect = side_effects[call_count - 1]
        if isinstance(effect, Exception):
            raise effect
        return effect

    mock_client = MagicMock()
    with factory(mock_client):
        mock_client.chat.completions.parse.side_effect = _parse_side_effect
        # Override tenacity wait to speed up the test (no real sleep)
        with patch("tenacity.wait_exponential.__call__", return_value=0):
            result = enrich_chunk("text", ledger())

    assert call_count == 2, (
        f"Expected 2 calls (1 rate-limit retry), got {call_count}"
    )
    assert isinstance(result, ChunkMetadataAndEntities)


# ---------------------------------------------------------------------------
# Test 5: EntityExtraction rejects invalid type via Pydantic Literal enforcement
# ---------------------------------------------------------------------------


def test_entity_extraction_validates_type_literal():
    """EntityExtraction raises ValidationError when type is not one of the five allowed values."""
    import pydantic

    from app.services.metadata_service import EntityExtraction

    with pytest.raises((pydantic.ValidationError, ValueError)):
        EntityExtraction(name="Acme", type="invalid_type", normalized="acme")


# ---------------------------------------------------------------------------
# Test 6: ChunkMetadataAndEntities shape — constructs correctly, entities accessible
# ---------------------------------------------------------------------------


def test_chunk_metadata_and_entities_validates_shape():
    """ChunkMetadataAndEntities constructs correctly; entities list item accessible."""
    from app.services.metadata_service import ChunkMetadataAndEntities, EntityExtraction

    entity = EntityExtraction(name="Acme Corp", type="product", normalized="acme corp")
    result = ChunkMetadataAndEntities(
        summary="A summary about Acme.",
        keywords=["acme", "product"],
        questions=["What does Acme sell?"],
        entities=[entity],
    )

    assert result.summary == "A summary about Acme."
    assert result.keywords == ["acme", "product"]
    assert result.questions == ["What does Acme sell?"]
    assert len(result.entities) == 1
    assert result.entities[0].normalized == "acme corp"
    assert result.entities[0].type == "product"
    assert result.entities[0].name == "Acme Corp"


# ---------------------------------------------------------------------------
# Test 7: enrich_chunks_batch happy path — 2 chunks in, 2 results out (in order)
# ---------------------------------------------------------------------------


def test_enrich_chunks_batch_happy_path():
    """enrich_chunks_batch returns a list of ChunkMetadataAndEntities in input order.

    Verifies one model call extracts metadata for multiple chunks, uses BatchResult
    as response_format, packs chunks into <chunk index="N"> tags, and budgets
    max_completion_tokens=4096.
    """
    from app.core.model_client import route_for
    from app.services.metadata_service import (
        PURPOSE,
        BatchResult,
        ChunkMetadataAndEntities,
        enrich_chunks_batch,
    )

    batch = BatchResult(
        chunks=[
            ChunkMetadataAndEntities(
                summary="First chunk.", keywords=["a"], questions=["q1?"], entities=[]
            ),
            ChunkMetadataAndEntities(
                summary="Second chunk.", keywords=["b"], questions=["q2?"], entities=[]
            ),
        ]
    )
    mock_result = completion(parsed=batch)

    mock_client = MagicMock()
    with factory(mock_client):
        mock_client.chat.completions.parse.return_value = mock_result
        results = enrich_chunks_batch(["chunk one text", "chunk two text"], ledger())

    # Returns a plain list of per-chunk results in submission order
    assert isinstance(results, list)
    assert len(results) == 2
    assert all(isinstance(r, ChunkMetadataAndEntities) for r in results)
    assert results[0].summary == "First chunk."
    assert results[1].summary == "Second chunk."

    # One batched request, not one per chunk
    assert mock_client.chat.completions.parse.call_count == 1

    call_kwargs = mock_client.chat.completions.parse.call_args.kwargs
    assert call_kwargs.get("model") == route_for(PURPOSE).model
    assert call_kwargs.get("response_format") is BatchResult
    assert call_kwargs.get("max_completion_tokens") == 4096

    # Chunks are packed with index-tagged wrappers in order. [0] is the system
    # prompt; the packed chunks ride the user turn.
    user_content = call_kwargs["messages"][1]["content"]
    assert '<chunk index="0">' in user_content
    assert '<chunk index="1">' in user_content
    assert "chunk one text" in user_content
    assert "chunk two text" in user_content


# ---------------------------------------------------------------------------
# Test 8: enrich_chunks_batch raises ValueError on batch size mismatch
# ---------------------------------------------------------------------------


def test_enrich_chunks_batch_size_mismatch_raises_value_error():
    """enrich_chunks_batch raises ValueError when the model returns a different count."""
    from app.services.metadata_service import (
        BatchResult,
        ChunkMetadataAndEntities,
        enrich_chunks_batch,
    )

    # Sent 2 chunks but model returns only 1 result
    batch = BatchResult(
        chunks=[
            ChunkMetadataAndEntities(
                summary="Only one.", keywords=["a"], questions=["q?"], entities=[]
            ),
        ]
    )
    mock_result = completion(parsed=batch)

    mock_client = MagicMock()
    with factory(mock_client):
        mock_client.chat.completions.parse.return_value = mock_result
        with pytest.raises(ValueError, match="Batch size mismatch"):
            enrich_chunks_batch(["chunk one", "chunk two"], ledger())


# ---------------------------------------------------------------------------
# Test 9: enrich_chunks_batch retries on RateLimitError (tenacity)
# ---------------------------------------------------------------------------


def test_enrich_chunks_batch_retries_on_rate_limit():
    """enrich_chunks_batch retries when the provider raises RateLimitError."""
    import openai as openai_lib

    from app.services.metadata_service import (
        BatchResult,
        ChunkMetadataAndEntities,
        enrich_chunks_batch,
    )

    real_request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    real_response = httpx.Response(
        429,
        request=real_request,
        content=b'{"error":{"type":"rate_limit_error","message":"rate limited"}}',
    )
    rate_limit_err = openai_lib.RateLimitError(
        "rate limited", response=real_response, body=None
    )

    mock_result = completion(
        parsed=BatchResult(
            chunks=[
                ChunkMetadataAndEntities(
                    summary="s", keywords=["k"], questions=["q?"], entities=[]
                ),
            ]
        )
    )

    side_effects = [rate_limit_err, mock_result]
    call_count = 0

    def _parse_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        effect = side_effects[call_count - 1]
        if isinstance(effect, Exception):
            raise effect
        return effect

    mock_client = MagicMock()
    with factory(mock_client):
        mock_client.chat.completions.parse.side_effect = _parse_side_effect
        with patch("tenacity.wait_exponential.__call__", return_value=0):
            results = enrich_chunks_batch(["text"], ledger())

    assert call_count == 2, (
        f"Expected 2 calls (1 rate-limit retry), got {call_count}"
    )
    assert len(results) == 1
    assert isinstance(results[0], ChunkMetadataAndEntities)


# ---------------------------------------------------------------------------
# Test 10: enrich_chunks_batch — AuthenticationError is fatal (no retry)
# ---------------------------------------------------------------------------


def test_enrich_chunks_batch_auth_error_is_fatal():
    """enrich_chunks_batch does NOT retry on AuthenticationError — raised immediately."""
    import openai as openai_lib

    from app.services.metadata_service import enrich_chunks_batch

    real_request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    real_response = httpx.Response(
        401,
        request=real_request,
        content=b'{"error":{"type":"authentication_error","message":"bad key"}}',
    )
    auth_err = openai_lib.AuthenticationError(
        "bad key", response=real_response, body=None
    )

    call_count = 0

    def _parse_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise auth_err

    mock_client = MagicMock()
    with factory(mock_client):
        mock_client.chat.completions.parse.side_effect = _parse_side_effect
        with pytest.raises(openai_lib.AuthenticationError):
            enrich_chunks_batch(["text"], ledger())

    # Fatal — no retry; exactly one call
    assert call_count == 1, (
        f"AuthenticationError must NOT retry; expected 1 call, got {call_count}"
    )


# ---------------------------------------------------------------------------
# The refusal is the reason. Both parse sites have to carry it.
#
# `chat.completions.parse` returns `parsed=None` for two different events: the
# model declined, and something else went wrong. When it declined it writes the
# reason into `message.refusal`, and that string is the ONLY record of it. The
# body carries no other trace. Raising "returned no parsed output" over the top
# of it turns a content-policy refusal, which is a prompt or a corpus problem,
# into an unexplained empty result, which reads as a provider problem. The chunk
# it happened to is then re-enriched on the next run and refused again.
# ---------------------------------------------------------------------------


def test_enrich_chunk_error_carries_the_models_refusal():
    from app.services.metadata_service import enrich_chunk

    mock_client = MagicMock()
    with factory(mock_client):
        mock_client.chat.completions.parse.return_value = completion(
            parsed=None, refusal="content policy"
        )
        with pytest.raises(ValueError) as exc_info:
            enrich_chunk("Some chunk text.", ledger())

    assert "content policy" in str(exc_info.value), (
        "the error drops `message.refusal`, which is the only place the model "
        f"wrote down why it declined. error={str(exc_info.value)!r}"
    )


def test_enrich_chunks_batch_error_carries_the_models_refusal():
    from app.services.metadata_service import enrich_chunks_batch

    mock_client = MagicMock()
    with factory(mock_client):
        mock_client.chat.completions.parse.return_value = completion(
            parsed=None, refusal="content policy"
        )
        with pytest.raises(ValueError) as exc_info:
            enrich_chunks_batch(["chunk one", "chunk two"], ledger())

    assert "content policy" in str(exc_info.value), (
        "the batch error drops `message.refusal`, so a refusal that stopped ten "
        f"chunks at once is unexplained. error={str(exc_info.value)!r}"
    )


def test_a_parse_failure_with_no_refusal_still_says_what_happened():
    """`parsed is None` with no refusal is a different event and keeps its own text."""
    from app.services.metadata_service import enrich_chunk

    mock_client = MagicMock()
    with factory(mock_client):
        mock_client.chat.completions.parse.return_value = completion(parsed=None)
        with pytest.raises(ValueError, match="no parsed output"):
            enrich_chunk("Some chunk text.", ledger())
