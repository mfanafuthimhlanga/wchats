"""
Unit tests for app.services.bedrock_embedding_service — PROD-06.

Task 1 tests (Bedrock client):
  1. test_embed_texts_returns_1024_dim_vector       — embed_texts(["hello"], "document") → 1 vector of 1024 floats
  2. test_embed_texts_calls_invoke_model_correctly  — modelId, body shape (inputText, dimensions=1024, normalize=true)
  3. test_embed_texts_loops_per_text                — 3 texts → 3 invoke_model calls
  4. test_embed_texts_raises_on_dim_mismatch        — Bedrock returns 512-length vector → RuntimeError
  5. test_active_embedding_model_returns_bedrock_id — EMBEDDING_PROVIDER=bedrock → BEDROCK_EMBED_MODEL_ID

Task 2 tests (provider seam dispatch):
  6. test_embed_chunks_routes_to_bedrock            — EMBEDDING_PROVIDER=bedrock → bedrock embed_texts called
  7. test_embed_chunks_routes_to_voyage             — EMBEDDING_PROVIDER=voyage → Voyage _vo.embed called
  8. test_embed_query_routes_to_bedrock             — EMBEDDING_PROVIDER=bedrock → bedrock embed_texts called
  9. test_embed_query_routes_to_voyage              — EMBEDDING_PROVIDER=voyage → Voyage _vo.embed called

Patch target: app.services.bedrock_embedding_service._bedrock
(NOT boto3.client — always patch the symbol imported into the module under test)

NOTE: conftest.py already sets the base env vars (NEON_ENCRYPTION_KEY, ANTHROPIC_API_KEY, etc.)
      We add only the bedrock-specific override here.
"""

import json
import os

# ---------------------------------------------------------------------------
# Environment setup — MUST run before any `from app` import (pydantic-settings)
# ---------------------------------------------------------------------------
os.environ.setdefault("EMBEDDING_PROVIDER", "bedrock")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("BEDROCK_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")

# ---------------------------------------------------------------------------
# Imports (after env setup)
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_bedrock_response(dim: int = 1024) -> dict:
    """Return a mocked Bedrock invoke_model response with embedding of given dimension."""
    mock_body = MagicMock()
    mock_body.read.return_value = json.dumps({"embedding": [0.1] * dim}).encode()
    return {"body": mock_body}


# ===========================================================================
# Task 1 tests — Bedrock client module
# ===========================================================================


# ---------------------------------------------------------------------------
# Test 1: embed_texts returns 1024-dim vectors
# ---------------------------------------------------------------------------


def test_embed_texts_returns_1024_dim_vector():
    """embed_texts(['hello'], 'document') returns a list with one 1024-element float vector.

    Mocks _bedrock so no real AWS call is made.
    """
    from app.services.bedrock_embedding_service import embed_texts

    with patch("app.services.bedrock_embedding_service._bedrock") as mock_bedrock:
        mock_bedrock.invoke_model.return_value = _mock_bedrock_response(1024)
        result = embed_texts(["hello"], "document")

    assert len(result) == 1, f"Expected 1 vector but got {len(result)}"
    assert len(result[0]) == 1024, f"Expected 1024-dim vector but got {len(result[0])}"


# ---------------------------------------------------------------------------
# Test 2: invoke_model called with correct modelId and body fields
# ---------------------------------------------------------------------------


def test_embed_texts_calls_invoke_model_correctly():
    """invoke_model is called with modelId=BEDROCK_EMBED_MODEL_ID and body containing
    inputText, dimensions=1024, normalize=true."""
    from app.services.bedrock_embedding_service import embed_texts

    with patch("app.services.bedrock_embedding_service._bedrock") as mock_bedrock:
        mock_bedrock.invoke_model.return_value = _mock_bedrock_response(1024)
        embed_texts(["hello world"], "document")

        call_kwargs = mock_bedrock.invoke_model.call_args.kwargs
        model_id = call_kwargs.get("modelId")
        body = json.loads(call_kwargs.get("body", "{}"))

    assert model_id == "amazon.titan-embed-text-v2:0", (
        f"Expected modelId='amazon.titan-embed-text-v2:0' but got {model_id!r}"
    )
    assert body.get("inputText") == "hello world", (
        f"Expected inputText='hello world' but got {body.get('inputText')!r}"
    )
    assert body.get("dimensions") == 1024, (
        f"Expected dimensions=1024 but got {body.get('dimensions')!r}"
    )
    assert body.get("normalize") is True, (
        f"Expected normalize=True but got {body.get('normalize')!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: 3 texts → 3 separate invoke_model calls (Titan v2 is one-text-per-call)
# ---------------------------------------------------------------------------


def test_embed_texts_loops_per_text():
    """embed_texts with 3 texts makes 3 separate invoke_model calls.

    Titan v2 accepts a single inputText per call; embed_texts must loop,
    unlike Cohere batch API.
    """
    from app.services.bedrock_embedding_service import embed_texts

    with patch("app.services.bedrock_embedding_service._bedrock") as mock_bedrock:
        mock_bedrock.invoke_model.return_value = _mock_bedrock_response(1024)
        result = embed_texts(["a", "b", "c"], "document")

    assert mock_bedrock.invoke_model.call_count == 3, (
        f"Expected 3 invoke_model calls (one per text) but got {mock_bedrock.invoke_model.call_count}"
    )
    assert len(result) == 3, f"Expected 3 vectors but got {len(result)}"


# ---------------------------------------------------------------------------
# Test 4: Dimension mismatch → RuntimeError with descriptive message
# ---------------------------------------------------------------------------


def test_embed_texts_raises_on_dim_mismatch():
    """If Bedrock returns a vector of length 512, embed_texts raises RuntimeError
    naming the dimension mismatch. This is the VECTOR(1024) guard."""
    from app.services.bedrock_embedding_service import embed_texts

    with patch("app.services.bedrock_embedding_service._bedrock") as mock_bedrock:
        # Mock returns 512-dim vector (wrong dimension for VECTOR(1024) schema)
        mock_bedrock.invoke_model.return_value = _mock_bedrock_response(512)
        with pytest.raises(RuntimeError, match="bedrock embedding dim mismatch"):
            embed_texts(["hello"], "document")


# ---------------------------------------------------------------------------
# Test 5: active_embedding_model() returns BEDROCK_EMBED_MODEL_ID when provider=bedrock
# ---------------------------------------------------------------------------


def test_active_embedding_model_returns_bedrock_id():
    """active_embedding_model() returns settings.BEDROCK_EMBED_MODEL_ID when
    EMBEDDING_PROVIDER='bedrock' (default in test env)."""
    from app.services.bedrock_embedding_service import active_embedding_model

    result = active_embedding_model()
    assert result == "amazon.titan-embed-text-v2:0", (
        f"Expected 'amazon.titan-embed-text-v2:0' but got {result!r}"
    )


# ===========================================================================
# Task 2 tests — provider seam dispatch (embed_chunks + embed_query)
# ===========================================================================


# ---------------------------------------------------------------------------
# Test 6: embed_chunks routes to bedrock when EMBEDDING_PROVIDER=bedrock
# ---------------------------------------------------------------------------


def test_embed_chunks_routes_to_bedrock():
    """With EMBEDDING_PROVIDER=bedrock, embed_chunks dispatches to bedrock embed_texts
    with input_type='document'. 2 texts → 2 invoke_model calls; returns 2 × 1024 vectors."""
    from app.services.embedding_service import embed_chunks

    with patch("app.services.bedrock_embedding_service._bedrock") as mock_bedrock:
        mock_bedrock.invoke_model.return_value = _mock_bedrock_response(1024)
        result = embed_chunks(["chunk_a", "chunk_b"])

    assert mock_bedrock.invoke_model.call_count == 2, (
        f"Expected 2 Bedrock calls (one per text) but got {mock_bedrock.invoke_model.call_count}"
    )
    assert len(result) == 2, f"Expected 2 vectors but got {len(result)}"
    assert len(result[0]) == 1024, f"Expected 1024-dim vector but got {len(result[0])}"


# ---------------------------------------------------------------------------
# Test 7: embed_chunks falls back to Voyage when EMBEDDING_PROVIDER=voyage
# ---------------------------------------------------------------------------


def test_embed_chunks_routes_to_voyage():
    """With EMBEDDING_PROVIDER=voyage, embed_chunks uses the Voyage path (regression guard).

    Patches settings.EMBEDDING_PROVIDER to 'voyage' and verifies _vo.embed is called.
    """
    from app.services.embedding_service import embed_chunks

    # Patch the settings reference in embedding_service to use voyage provider
    with patch("app.services.embedding_service.settings") as mock_settings:
        mock_settings.EMBEDDING_PROVIDER = "voyage"
        with patch("app.services.embedding_service._vo") as mock_vo:
            mock_vo.embed.return_value = MagicMock(embeddings=[[0.2] * 1024, [0.3] * 1024])
            result = embed_chunks(["a", "b"])

    assert mock_vo.embed.call_count >= 1, (
        f"Expected Voyage _vo.embed to be called but call_count={mock_vo.embed.call_count}"
    )
    assert len(result) == 2, f"Expected 2 vectors but got {len(result)}"


# ---------------------------------------------------------------------------
# Test 8: embed_query routes to bedrock when EMBEDDING_PROVIDER=bedrock
# ---------------------------------------------------------------------------


def test_embed_query_routes_to_bedrock():
    """With EMBEDDING_PROVIDER=bedrock, embed_query dispatches to bedrock embed_texts
    with input_type='query' and returns one 1024-dim vector."""
    from app.services.retrieval_service import embed_query

    with patch("app.services.bedrock_embedding_service._bedrock") as mock_bedrock:
        mock_bedrock.invoke_model.return_value = _mock_bedrock_response(1024)
        result = embed_query("what is the return policy?")

    assert mock_bedrock.invoke_model.call_count == 1, (
        f"Expected 1 Bedrock call but got {mock_bedrock.invoke_model.call_count}"
    )
    assert len(result) == 1024, f"Expected 1024-dim vector but got {len(result)}"


# ---------------------------------------------------------------------------
# Test 9: embed_query falls back to Voyage when EMBEDDING_PROVIDER=voyage
# ---------------------------------------------------------------------------


def test_embed_query_routes_to_voyage():
    """With EMBEDDING_PROVIDER=voyage, embed_query uses the Voyage path (regression guard).

    Patches settings.EMBEDDING_PROVIDER to 'voyage' and verifies _vo.embed is called.
    """
    from app.services.retrieval_service import embed_query

    with patch("app.services.retrieval_service.settings") as mock_settings:
        mock_settings.EMBEDDING_PROVIDER = "voyage"
        with patch("app.services.retrieval_service._get_vo") as mock_get_vo:
            mock_client = MagicMock()
            mock_client.embed.return_value = MagicMock(
                embeddings=[[0.5] * 1024]
            )
            mock_get_vo.return_value = mock_client
            result = embed_query("what is the return policy?")

    assert mock_client.embed.call_count == 1, (
        f"Expected Voyage embed to be called but call_count={mock_client.embed.call_count}"
    )
    assert len(result) == 1024, f"Expected 1024-dim vector but got {len(result)}"
