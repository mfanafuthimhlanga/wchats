"""Unit tests for app.services.strategy_service — M9 Retrieval Strategy Synthesis.

De-xfailed in Phase 09-03. Tests cover:
    test_corpus_signals_shape              — _fetch_corpus_signals_sync returns correct keys/types
    test_strategy_validate_string_inputs   — RetrievalStrategy tolerates non-numeric string inputs
    test_run_strategist_calls_asyncio_run  — asyncio.run is called at module boundary
    test_expand_query_returns_three        — _expand_query returns [original] + 2 variants
    test_expansion_calls_rrf_fuse_per_variant — rrf_fuse called once per query variant
"""

import base64
import os

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

from app.services.retrieval_service import (
    RetrievalStrategy,
    _expand_query,
    rrf_fuse_with_expansion,
)
from app.domain.ingestion_job import IngestionJob
from app.services.strategy_service import (
    _fetch_corpus_signals_sync,
    run_strategist,
)

# run_strategist now builds its client through app.core.model_client.make_client, so it
# takes the three ids each ledger row carries and the tenant database each row is
# written to. The dsn is a separate argument because the job type holds no connection
# string and has no field for one (project rule 1).
_JOB = IngestionJob(
    tenant_id="11111111-1111-1111-1111-111111111111",
    agent_id="22222222-2222-2222-2222-222222222222",
    job_id="33333333-3333-3333-3333-333333333333",
    document_ids=[],
)
TENANT_DSN = "postgresql://tenant-probe"

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


def test_run_strategist_calls_anthropic_api():
    """run_strategist uses the direct Anthropic API (messages.create with tool_use)."""
    result_container = {}

    mock_block = MagicMock()
    mock_block.type = "tool_use"
    mock_block.name = "generate_strategy"
    mock_block.input = {"vector_k": 15, "bm25_k": 15, "final_k": 3,
                        "rerank_threshold": 0.1, "query_expansion": False,
                        "metadata_filters": []}

    mock_response = MagicMock()
    mock_response.content = [mock_block]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch("anthropic.Anthropic", return_value=mock_client):
        run_strategist("{}", result_container, _JOB, TENANT_DSN)

    mock_client.messages.create.assert_called_once()
    assert result_container["strategy"]["vector_k"] == 15


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
        real_module = sys.modules["anthropic"]
        sys.modules["anthropic"] = mock_module

        try:
            result = _expand_query("orig")
        finally:
            # Put the REAL module object back, rather than deleting the entry.
            # Deleting it makes the next `import anthropic` build a second module
            # object, and every module that already holds a reference to the first
            # one then ignores `patch("anthropic.Anthropic", ...)`. That reached a
            # live 401 from api.anthropic.com when this file ran before
            # test_judgement_temperature.py on 2026-08-25.
            sys.modules["anthropic"] = real_module

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

    # EMBEDDING_PROVIDER defaults to "bedrock" (P13-02 seam, config.py). Without
    # this pin, rrf_fuse_with_expansion takes the Bedrock branch, ignores the
    # _get_vo patch, and issues a real boto3 InvokeModel call.
    with patch(
        "app.services.retrieval_service.settings.EMBEDDING_PROVIDER",
        "voyage",
    ), patch(
        "app.services.retrieval_service._expand_query",
        return_value=["q", "q1", "q2"],
    ), patch(
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


# ---------------------------------------------------------------------------
# The model-call ledger (ticket #46, issue #22)
#
# run_strategist is the first of the ten ad-hoc `anthropic.Anthropic()` sites to
# build its client through app.core.model_client.make_client. The two tests below
# cover the two halves of that. The first reads what the site asked the factory
# for. The second lets the real factory, the real SDK and the real response hook
# run against a canned provider body, and reads the INSERT that comes out the far
# end, so nothing between the call site and psycopg2 is stubbed.
# ---------------------------------------------------------------------------


def _messages_response(usage: dict | None = None) -> dict:
    """A messages response with a generate_strategy tool call and provider counts."""
    return {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": "deepseek-v4-flash",
        "content": [{
            "type": "tool_use",
            "id": "toolu_01",
            "name": "generate_strategy",
            "input": {
                "vector_k": 15, "bm25_k": 15, "final_k": 3,
                "rerank_threshold": 0.1, "query_expansion": False,
                "metadata_filters": [],
            },
        }],
        "stop_reason": "tool_use",
        "usage": usage or {
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_read_input_tokens": 2000,
            "cache_creation_input_tokens": 300,
        },
    }


def test_run_strategist_asks_the_factory_for_the_jobs_ids():
    """Every ledger row this call leaves is billed to the job's tenant."""
    seen: dict = {}

    def spy(purpose, **kwargs):
        seen.update(kwargs, purpose=purpose)
        return MagicMock()

    with patch("app.services.strategy_service.make_client", spy):
        run_strategist("{}", {}, _JOB, TENANT_DSN)

    assert seen["purpose"] == "retrieval_strategist"
    assert seen["tenant_id"] == _JOB.tenant_id
    assert seen["agent_id"] == _JOB.agent_id
    assert seen["job_id"] == _JOB.job_id
    assert callable(seen["recorder"]), "a client with no recorder records nothing"


def test_run_strategist_lands_one_ledger_row_in_the_jobs_tenant_database():
    """End to end. Real factory, real SDK, real hook, real recorder, canned body."""
    import httpx

    from app.core.model_client import make_client as real_make_client

    def spy(purpose, **kwargs):
        # Everything the call site asked for, plus the transport this test needs.
        return real_make_client(
            purpose,
            http_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=_messages_response())
                )
            ),
            **kwargs,
        )

    executed: list = []
    cursor = MagicMock()
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    cursor.execute.side_effect = lambda sql, params=None: executed.append((sql, params))
    connection = MagicMock()
    connection.cursor.return_value = cursor

    result_container: dict = {}
    with patch("app.services.strategy_service.make_client", spy), patch(
        "app.core.model_client.psycopg2.connect", return_value=connection
    ) as connect:
        run_strategist("{}", result_container, _JOB, TENANT_DSN)

    assert result_container["strategy"]["vector_k"] == 15, (
        "the Strategist's own output must still reach the caller"
    )
    assert connect.call_args[0][0] == TENANT_DSN, (
        "the row belongs to the tenant database the task handed in"
    )
    assert len(executed) == 1, f"expected one INSERT, got {executed}"
    sql, params = executed[0]
    assert "model_calls" in sql
    assert 1000 in params and 500 in params and 2000 in params and 300 in params
    assert _JOB.tenant_id in params
    assert _JOB.job_id in params
    assert "retrieval_strategist" in params
