"""
Unit tests for app.services.metadata_service — ING-06.

Tests:
  1. test_enrich_chunk_returns_chunk_metadata_and_entities — return type + fields
  2. test_enrich_chunk_uses_haiku_model_constant          — model string matches HAIKU_MODEL
  3. test_enrich_chunk_uses_output_format_pydantic        — output_format=ChunkMetadataAndEntities (class)
  4. test_enrich_chunk_retries_on_rate_limit              — tenacity retries on RateLimitError
  5. test_entity_extraction_validates_type_literal        — Pydantic rejects invalid entity type
  6. test_chunk_metadata_and_entities_validates_shape     — correct field values on valid construct

Patch target: app.services.metadata_service._anthropic
(NOT anthropic.Anthropic — always patch the symbol imported into the module under test)
"""

import os
import base64

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

import pytest
import httpx
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Test 1: enrich_chunk returns ChunkMetadataAndEntities with correct fields
# ---------------------------------------------------------------------------


def test_enrich_chunk_returns_chunk_metadata_and_entities():
    """enrich_chunk returns a ChunkMetadataAndEntities instance with expected field values."""
    from app.services.metadata_service import (
        enrich_chunk,
        ChunkMetadataAndEntities,
    )

    mock_result = MagicMock()
    mock_result.parsed_output = ChunkMetadataAndEntities(
        summary="A product summary.",
        keywords=["product", "catalog"],
        questions=["What products are available?"],
        entities=[],
    )

    with patch("app.services.metadata_service._anthropic") as mock_anthropic:
        mock_anthropic.messages.parse.return_value = mock_result
        result = enrich_chunk("Some chunk text about products.")

    assert isinstance(result, ChunkMetadataAndEntities)
    assert result.summary == "A product summary."
    assert result.keywords == ["product", "catalog"]
    assert result.questions == ["What products are available?"]
    assert result.entities == []


# ---------------------------------------------------------------------------
# Test 2: enrich_chunk passes the HAIKU_MODEL constant as model kwarg
# ---------------------------------------------------------------------------


def test_enrich_chunk_uses_haiku_model_constant():
    """enrich_chunk calls messages.parse with model=HAIKU_MODEL (not a hard-coded string)."""
    from app.services.metadata_service import (
        enrich_chunk,
        ChunkMetadataAndEntities,
        HAIKU_MODEL,
    )

    mock_result = MagicMock()
    mock_result.parsed_output = ChunkMetadataAndEntities(
        summary="s", keywords=["k"], questions=["q?"], entities=[]
    )

    with patch("app.services.metadata_service._anthropic") as mock_anthropic:
        mock_anthropic.messages.parse.return_value = mock_result
        enrich_chunk("text")

        # Verify the call used HAIKU_MODEL
        call_kwargs = mock_anthropic.messages.parse.call_args.kwargs
        assert call_kwargs.get("model") == HAIKU_MODEL, (
            f"Expected model={HAIKU_MODEL!r} but got {call_kwargs.get('model')!r}"
        )


# ---------------------------------------------------------------------------
# Test 3: enrich_chunk passes output_format=ChunkMetadataAndEntities (the class)
# ---------------------------------------------------------------------------


def test_enrich_chunk_uses_output_format_pydantic():
    """enrich_chunk calls messages.parse with output_format=ChunkMetadataAndEntities (class, not instance)."""
    from app.services.metadata_service import (
        enrich_chunk,
        ChunkMetadataAndEntities,
    )

    mock_result = MagicMock()
    mock_result.parsed_output = ChunkMetadataAndEntities(
        summary="s", keywords=["k"], questions=["q?"], entities=[]
    )

    with patch("app.services.metadata_service._anthropic") as mock_anthropic:
        mock_anthropic.messages.parse.return_value = mock_result
        enrich_chunk("text")

        call_kwargs = mock_anthropic.messages.parse.call_args.kwargs
        assert call_kwargs.get("output_format") is ChunkMetadataAndEntities, (
            f"Expected output_format=ChunkMetadataAndEntities (class) but got "
            f"{call_kwargs.get('output_format')!r}"
        )


# ---------------------------------------------------------------------------
# Test 4: enrich_chunk retries on RateLimitError
# ---------------------------------------------------------------------------


def test_enrich_chunk_retries_on_rate_limit():
    """enrich_chunk retries when Anthropic raises RateLimitError (tenacity retry rule).

    Uses a real httpx.Request+Response to construct a valid anthropic.RateLimitError
    (the constructor requires response.request to be set and response.headers to exist).
    """
    import anthropic as anthropic_lib
    from app.services.metadata_service import enrich_chunk, ChunkMetadataAndEntities

    # Build a valid RateLimitError (requires request on response)
    real_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    real_response = httpx.Response(
        429,
        request=real_request,
        content=b'{"error":{"type":"rate_limit_error","message":"rate limited"}}',
    )
    rate_limit_err = anthropic_lib.RateLimitError(
        "rate limited", response=real_response, body=None
    )

    # Second call returns a valid result
    mock_result = MagicMock()
    mock_result.parsed_output = ChunkMetadataAndEntities(
        summary="s", keywords=["k"], questions=["q?"], entities=[]
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

    with patch("app.services.metadata_service._anthropic") as mock_anthropic:
        mock_anthropic.messages.parse.side_effect = _parse_side_effect
        # Override tenacity wait to speed up the test (no real sleep)
        with patch("tenacity.wait_exponential.__call__", return_value=0):
            result = enrich_chunk("text")

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

    Verifies one Haiku call extracts metadata for multiple chunks, uses BatchResult
    as output_format, packs chunks into <chunk index="N"> tags, and budgets
    max_tokens=4096.
    """
    from app.services.metadata_service import (
        enrich_chunks_batch,
        ChunkMetadataAndEntities,
        BatchResult,
        HAIKU_MODEL,
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
    mock_result = MagicMock()
    mock_result.parsed_output = batch

    with patch("app.services.metadata_service._anthropic") as mock_anthropic:
        mock_anthropic.messages.parse.return_value = mock_result
        results = enrich_chunks_batch(["chunk one text", "chunk two text"])

    # Returns a plain list of per-chunk results in submission order
    assert isinstance(results, list)
    assert len(results) == 2
    assert all(isinstance(r, ChunkMetadataAndEntities) for r in results)
    assert results[0].summary == "First chunk."
    assert results[1].summary == "Second chunk."

    # Single Haiku call (one batched request, not one per chunk)
    assert mock_anthropic.messages.parse.call_count == 1

    call_kwargs = mock_anthropic.messages.parse.call_args.kwargs
    assert call_kwargs.get("model") == HAIKU_MODEL
    assert call_kwargs.get("output_format") is BatchResult
    assert call_kwargs.get("max_tokens") == 4096

    # Chunks are packed with index-tagged wrappers in order
    user_content = call_kwargs["messages"][0]["content"]
    assert '<chunk index="0">' in user_content
    assert '<chunk index="1">' in user_content
    assert "chunk one text" in user_content
    assert "chunk two text" in user_content


# ---------------------------------------------------------------------------
# Test 8: enrich_chunks_batch raises ValueError on batch size mismatch
# ---------------------------------------------------------------------------


def test_enrich_chunks_batch_size_mismatch_raises_value_error():
    """enrich_chunks_batch raises ValueError when Haiku returns a different count."""
    from app.services.metadata_service import (
        enrich_chunks_batch,
        ChunkMetadataAndEntities,
        BatchResult,
    )

    # Sent 2 chunks but model returns only 1 result
    batch = BatchResult(
        chunks=[
            ChunkMetadataAndEntities(
                summary="Only one.", keywords=["a"], questions=["q?"], entities=[]
            ),
        ]
    )
    mock_result = MagicMock()
    mock_result.parsed_output = batch

    with patch("app.services.metadata_service._anthropic") as mock_anthropic:
        mock_anthropic.messages.parse.return_value = mock_result
        with pytest.raises(ValueError, match="Batch size mismatch"):
            enrich_chunks_batch(["chunk one", "chunk two"])


# ---------------------------------------------------------------------------
# Test 9: enrich_chunks_batch retries on RateLimitError (tenacity)
# ---------------------------------------------------------------------------


def test_enrich_chunks_batch_retries_on_rate_limit():
    """enrich_chunks_batch retries when Anthropic raises RateLimitError."""
    import anthropic as anthropic_lib
    from app.services.metadata_service import (
        enrich_chunks_batch,
        ChunkMetadataAndEntities,
        BatchResult,
    )

    real_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    real_response = httpx.Response(
        429,
        request=real_request,
        content=b'{"error":{"type":"rate_limit_error","message":"rate limited"}}',
    )
    rate_limit_err = anthropic_lib.RateLimitError(
        "rate limited", response=real_response, body=None
    )

    mock_result = MagicMock()
    mock_result.parsed_output = BatchResult(
        chunks=[
            ChunkMetadataAndEntities(
                summary="s", keywords=["k"], questions=["q?"], entities=[]
            ),
        ]
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

    with patch("app.services.metadata_service._anthropic") as mock_anthropic:
        mock_anthropic.messages.parse.side_effect = _parse_side_effect
        with patch("tenacity.wait_exponential.__call__", return_value=0):
            results = enrich_chunks_batch(["text"])

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
    import anthropic as anthropic_lib
    from app.services.metadata_service import enrich_chunks_batch

    real_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    real_response = httpx.Response(
        401,
        request=real_request,
        content=b'{"error":{"type":"authentication_error","message":"bad key"}}',
    )
    auth_err = anthropic_lib.AuthenticationError(
        "bad key", response=real_response, body=None
    )

    call_count = 0

    def _parse_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise auth_err

    with patch("app.services.metadata_service._anthropic") as mock_anthropic:
        mock_anthropic.messages.parse.side_effect = _parse_side_effect
        with pytest.raises(anthropic_lib.AuthenticationError):
            enrich_chunks_batch(["text"])

    # Fatal — no retry; exactly one call
    assert call_count == 1, (
        f"AuthenticationError must NOT retry; expected 1 call, got {call_count}"
    )
