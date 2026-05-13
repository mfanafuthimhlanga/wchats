"""
Unit tests for app.services.embedding_service — ING-07.

Tests:
  1. test_embed_chunks_returns_empty_for_empty_input     — [] input → [] output
  2. test_embed_chunks_batches_at_128                    — 256 texts → 2 Voyage calls
  3. test_embed_chunks_uses_pinned_model                 — model kwarg == "voyage-3"
  4. test_embed_chunks_uses_input_type_document          — input_type="document"
  5. test_embed_chunks_raises_on_count_mismatch          — API returns wrong count → RuntimeError
  6. test_embed_batch_retries_on_exception               — tenacity retries on first-call exception

Patch target: app.services.embedding_service._vo
(NOT voyageai.Client — always patch the symbol imported into the module under test)
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
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Test 1: empty input returns empty list
# ---------------------------------------------------------------------------


def test_embed_chunks_returns_empty_for_empty_input():
    """embed_chunks([]) must return [] without calling the Voyage API."""
    from app.services.embedding_service import embed_chunks

    with patch("app.services.embedding_service._vo") as mock_vo:
        result = embed_chunks([])

    assert result == [], f"Expected [] but got {result!r}"
    mock_vo.embed.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: 256 texts → exactly 2 Voyage API calls (128 per batch)
# ---------------------------------------------------------------------------


def test_embed_chunks_batches_at_128():
    """embed_chunks splits into batches of exactly 128 items.

    256 texts → 2 batches → 2 calls to _vo.embed.
    Each returned vector must have length 1024 (voyage-3 dimension).
    """
    from app.services.embedding_service import embed_chunks

    with patch("app.services.embedding_service._vo") as mock_vo:
        mock_vo.embed.return_value = MagicMock(embeddings=[[0.1] * 1024] * 128)
        result = embed_chunks(["t"] * 256)

    assert mock_vo.embed.call_count == 2, (
        f"Expected 2 Voyage API calls (256/128=2 batches) but got {mock_vo.embed.call_count}"
    )
    assert len(result) == 256, (
        f"Expected 256 embeddings but got {len(result)}"
    )
    assert len(result[0]) == 1024, (
        f"Expected 1024-dimensional vectors (voyage-3) but got {len(result[0])}"
    )


# ---------------------------------------------------------------------------
# Test 3: model kwarg is exactly "voyage-3" (pinned constant)
# ---------------------------------------------------------------------------


def test_embed_chunks_uses_pinned_model():
    """embed_chunks calls _vo.embed with model='voyage-3' (pinned, never voyage-latest)."""
    from app.services.embedding_service import embed_chunks, EMBEDDING_MODEL

    with patch("app.services.embedding_service._vo") as mock_vo:
        mock_vo.embed.return_value = MagicMock(embeddings=[[0.1] * 1024])
        embed_chunks(["t"])

        call_kwargs = mock_vo.embed.call_args.kwargs
        actual_model = call_kwargs.get("model") or mock_vo.embed.call_args.args[1] if mock_vo.embed.call_args.args and len(mock_vo.embed.call_args.args) > 1 else call_kwargs.get("model")

    assert actual_model == "voyage-3", (
        f"Expected model='voyage-3' but got {actual_model!r}. "
        "DO NOT use voyage-latest — PITFALLS.md §3."
    )
    assert EMBEDDING_MODEL == "voyage-3", (
        f"EMBEDDING_MODEL constant must be 'voyage-3' but is {EMBEDDING_MODEL!r}"
    )


# ---------------------------------------------------------------------------
# Test 4: input_type="document" is passed to _vo.embed
# ---------------------------------------------------------------------------


def test_embed_chunks_uses_input_type_document():
    """embed_chunks calls _vo.embed with input_type='document'."""
    from app.services.embedding_service import embed_chunks

    with patch("app.services.embedding_service._vo") as mock_vo:
        mock_vo.embed.return_value = MagicMock(embeddings=[[0.1] * 1024])
        embed_chunks(["t"])

        call_kwargs = mock_vo.embed.call_args.kwargs
        actual_input_type = call_kwargs.get("input_type")

    assert actual_input_type == "document", (
        f"Expected input_type='document' but got {actual_input_type!r}"
    )


# ---------------------------------------------------------------------------
# Test 5: RuntimeError raised when API returns wrong number of embeddings
# ---------------------------------------------------------------------------


def test_embed_chunks_raises_on_count_mismatch():
    """embed_chunks raises RuntimeError if the Voyage API returns a different count.

    This guards against API contract violations where fewer (or more) vectors
    are returned than requested — a silent mismatch would corrupt chunk_id ↔ vector mapping.
    """
    from app.services.embedding_service import embed_chunks

    with patch("app.services.embedding_service._vo") as mock_vo:
        # Return only 5 embeddings for 10 input texts
        mock_vo.embed.return_value = MagicMock(embeddings=[[0.1] * 1024] * 5)
        with pytest.raises(RuntimeError, match="embedding count mismatch"):
            embed_chunks(["t"] * 10)


# ---------------------------------------------------------------------------
# Test 6: tenacity retries on first-call exception
# ---------------------------------------------------------------------------


def test_embed_batch_retries_on_exception():
    """_embed_batch (called via embed_chunks) retries when the first call raises.

    tenacity is configured to retry on any Exception with wait_exponential.
    This test patches tenacity's sleep to avoid real wait time.
    """
    from app.services.embedding_service import embed_chunks

    call_count = 0

    def _side_effect(texts, model, input_type):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("transient API error")
        return MagicMock(embeddings=[[0.2] * 1024])

    with patch("app.services.embedding_service._vo") as mock_vo:
        mock_vo.embed.side_effect = _side_effect
        # Patch tenacity sleep to avoid real delays during the test
        with patch("tenacity.nap.time.sleep"):
            result = embed_chunks(["t"])

    assert mock_vo.embed.call_count >= 2, (
        f"Expected at least 2 calls (1 failure + 1 retry) but got {mock_vo.embed.call_count}"
    )
    assert len(result) == 1
    assert len(result[0]) == 1024
