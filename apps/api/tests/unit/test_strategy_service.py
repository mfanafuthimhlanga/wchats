"""Unit tests for app.services.strategy_service — M9 Retrieval Strategy Synthesis.

De-xfailed in Phase 09-03. Tests cover:
    test_corpus_signals_shape              — _fetch_corpus_signals_sync returns correct keys/types
    test_strategy_validate_string_inputs   — RetrievalStrategy tolerates non-numeric string inputs
    test_run_strategist_calls_asyncio_run  — asyncio.run is called at module boundary
    test_expand_query_returns_three        — _expand_query returns [original] + 2 variants
    test_expansion_calls_rrf_fuse_per_variant — rrf_fuse called once per query variant
"""

import os
import base64

# Safety: ensure required env vars are present even if conftest is not loaded
os.environ.setdefault("NEON_API_KEY", "test_neon_key")
os.environ.setdefault("NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ADMIN_KEY", "test_admin_key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")
os.environ.setdefault("JWT_SECRET", "test_jwt_secret")
os.environ.setdefault("CLERK_WEBHOOK_SIGNING_SECRET", "test_clerk_secret")

from unittest.mock import MagicMock, patch

from app.services.strategy_service import (
    _fetch_corpus_signals_sync,
    run_strategist,
)
from app.services.retrieval_service import (
    RetrievalStrategy,
    _expand_query,
    rrf_fuse_with_expansion,
)


# ---------------------------------------------------------------------------
# Helper: build a mock psycopg2 connection with controllable multi-cursor responses
# ---------------------------------------------------------------------------


def _make_psycopg2_conn_multi(fetchone_sequence=None, fetchall_value=None):
    """Return a mock psycopg2 connection whose cursor returns responses in sequence.

    Each context-manager use of conn.cursor() returns a fresh cursor mock that
    pops from fetchone_sequence. fetchall_value is used on the last cursor call.
    """
    mock_conn = MagicMock()

    fetchone_sequence = list(fetchone_sequence or [])
    call_count = [0]

    def _make_cursor():
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        idx = call_count[0]
        call_count[0] += 1
        if idx < len(fetchone_sequence):
            cursor.fetchone.return_value = fetchone_sequence[idx]
        else:
            cursor.fetchone.return_value = None
        cursor.fetchall.return_value = fetchall_value if fetchall_value is not None else []
        return cursor

    mock_conn.cursor.side_effect = _make_cursor
    return mock_conn


# ---------------------------------------------------------------------------
# Corpus signal collection
# ---------------------------------------------------------------------------


def test_corpus_signals_shape():
    """_fetch_corpus_signals_sync returns a dict with all required keys and correct types."""
    # Query 1 row: (chunk_count=50, doc_count=5, avg_chunk_len=300.5, max_chunk_len=800)
    # Query 2 row: (table_chunks=10, total_chunks=50)
    # Query 3 row: (entity_count=25,)
    # Query 4 fetchall: doc types distribution
    mock_conn = _make_psycopg2_conn_multi(
        fetchone_sequence=[
            (50, 5, 300.5, 800),    # Query 1: chunk metrics
            (10, 50),               # Query 2: table ratio
            (25,),                  # Query 3: entity count
        ],
        fetchall_value=[("pdf", 3), ("docx", 2)],
    )

    with patch("app.services.strategy_service.psycopg2.connect", return_value=mock_conn):
        result = _fetch_corpus_signals_sync("agent-1", "postgresql://fake")

    assert set(result.keys()) == {
        "chunk_count",
        "doc_count",
        "avg_chunk_len",
        "max_chunk_len",
        "table_ratio",
        "entity_count",
        "doc_types",
    }
    assert result["chunk_count"] == 50
    assert result["doc_count"] == 5
    assert result["avg_chunk_len"] == 300.5
    assert result["max_chunk_len"] == 800
    assert result["entity_count"] == 25
    assert result["doc_types"] == {"pdf": 3, "docx": 2}
    # table_ratio = 10 / max(50, 1) = 0.2
    assert abs(result["table_ratio"] - 0.2) < 1e-6

    # Defensive coercion test: avg_chunk_len when row[2] is None → 0.0
    mock_conn_empty = _make_psycopg2_conn_multi(
        fetchone_sequence=[
            (0, 0, None, None),     # Query 1: empty corpus
            (0, 0),                 # Query 2: table ratio
            (0,),                   # Query 3: entity count
        ],
        fetchall_value=[],
    )
    with patch("app.services.strategy_service.psycopg2.connect", return_value=mock_conn_empty):
        result2 = _fetch_corpus_signals_sync("agent-1", "postgresql://fake")

    assert result2["avg_chunk_len"] == 0.0


# ---------------------------------------------------------------------------
# Strategist orchestration
# ---------------------------------------------------------------------------


def test_strategy_validate_string_inputs():
    """RetrievalStrategy.model_validate coerces string values to correct types."""
    result = RetrievalStrategy.model_validate({
        "vector_k": "30",
        "bm25_k": "25",
        "final_k": "5",
        "rerank_threshold": "0.3",
        "query_expansion": "true",
        "metadata_filters": [],
    })
    assert result.vector_k == 30
    assert isinstance(result.vector_k, int)
    assert result.bm25_k == 25
    assert result.final_k == 5
    assert abs(result.rerank_threshold - 0.3) < 1e-9
    assert result.query_expansion is True


def test_run_strategist_calls_asyncio_run():
    """run_strategist calls asyncio.run at the module boundary (not global asyncio.run)."""
    result_container = {}

    with patch("app.services.strategy_service.asyncio.run") as mock_asyncio_run:
        run_strategist("{}", result_container)

    mock_asyncio_run.assert_called_once()


# ---------------------------------------------------------------------------
# Query expansion
# ---------------------------------------------------------------------------


def test_expand_query_returns_three():
    """_expand_query returns [original] + up to 2 generated variants — length 3."""
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="variant one\nvariant two")]

    mock_client_instance = MagicMock()
    mock_client_instance.messages.create.return_value = mock_message

    mock_anthropic_cls = MagicMock(return_value=mock_client_instance)

    with patch("app.services.retrieval_service.anthropic", create=True) as _:
        # Patch the lazy `import anthropic` inside _expand_query by injecting at sys.modules
        import sys
        mock_module = MagicMock()
        mock_module.Anthropic = mock_anthropic_cls
        sys.modules["anthropic"] = mock_module

        try:
            result = _expand_query("orig")
        finally:
            # Restore so other tests are not polluted
            del sys.modules["anthropic"]

    assert result == ["orig", "variant one", "variant two"]
    assert len(result) == 3


def test_expansion_calls_rrf_fuse_per_variant():
    """rrf_fuse_with_expansion calls rrf_fuse once per query variant when query_expansion=True."""
    strategy = RetrievalStrategy(
        query_expansion=True,
        vector_k=10,
        bm25_k=10,
        final_k=3,
        rerank_threshold=0.0,
        metadata_filters=[],
    )

    # Canned fused result returned by each rrf_fuse call
    fake_fused_result = {
        "fused": [{"chunk_id": "c1", "rrf_score": 0.9, "content": "x"}],
        "vector_candidates": [],
        "bm25_candidates": [],
    }

    with patch(
        "app.services.retrieval_service._expand_query",
        return_value=["q", "q1", "q2"],
    ) as mock_expand, patch(
        "app.services.retrieval_service.rrf_fuse",
        return_value=fake_fused_result,
    ) as mock_rrf_fuse, patch(
        "app.services.retrieval_service._get_vo",
    ) as mock_get_vo:
        # Voyage embed returns 3 vectors (one per variant)
        mock_vo = MagicMock()
        mock_vo.embed.return_value.embeddings = [
            [0.1] * 10,
            [0.2] * 10,
            [0.3] * 10,
        ]
        mock_get_vo.return_value = mock_vo

        result = rrf_fuse_with_expansion("conn", [0.0] * 10, "q", strategy)

    # rrf_fuse should be called once per variant (3 variants → 3 calls)
    assert mock_rrf_fuse.call_count == 3

    # Result shape must match rrf_fuse contract
    assert set(result.keys()) == {"fused", "vector_candidates", "bm25_candidates"}
