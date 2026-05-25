"""Unit tests for app.worker.tasks.pipeline.strategy — M9 Retrieval Strategy Synthesis.

De-xfailed in Phase 09-03. Tests cover:
    test_strategy_written_to_db          — synthesize_retrieval_strategy persists JSONB to agents table
    test_receives_embed_result_dict      — task correctly unpacks embed result dict from Wave 1
    test_idempotency_skip                — task returns early when retrieval_strategy already set
    test_resynthesis_flag_bypasses_guard — strategy_resynthesis_flagged=True bypasses idempotency guard

Mock strategy (follows test_deployment_task.py exactly):
    - app.worker.tasks.pipeline.strategy.run_strategist patched at module boundary
    - app.worker.tasks.pipeline.strategy.get_sync_db patched as context manager
    - app.worker.tasks.pipeline.strategy.fernet_decrypt patched to return plain conn_str
    - app.worker.tasks.pipeline.strategy._fetch_corpus_signals_sync patched
    - app.worker.tasks.pipeline.strategy.emit patched (no-op)
    - Tasks called via .run(...) to bypass Celery broker
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

import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from app.worker.tasks.pipeline.strategy import synthesize_retrieval_strategy


# ---------------------------------------------------------------------------
# Helper: build a mock get_sync_db context manager
# ---------------------------------------------------------------------------


def _make_sync_db_ctx(mock_db):
    """Return a patched get_sync_db that yields mock_db when used as 'with get_sync_db() as db'."""
    @contextmanager
    def _fake_get_sync_db():
        yield mock_db

    return _fake_get_sync_db


# ---------------------------------------------------------------------------
# Canned signal dict used across tests
# ---------------------------------------------------------------------------

_FAKE_SIGNALS = {
    "chunk_count": 200,
    "doc_count": 10,
    "avg_chunk_len": 300.0,
    "max_chunk_len": 600,
    "table_ratio": 0.05,
    "entity_count": 50,
    "doc_types": {"pdf": 10},
}

_FAKE_STRATEGY = {
    "vector_k": 15,
    "bm25_k": 15,
    "final_k": 3,
    "rerank_threshold": 0.1,
    "query_expansion": True,
    "metadata_filters": [],
}


# ---------------------------------------------------------------------------
# Happy-path persistence
# ---------------------------------------------------------------------------


def test_strategy_written_to_db():
    """synthesize_retrieval_strategy writes the strategy JSONB to agents.retrieval_strategy.

    asyncio.run side_effect closes the coroutine (suppresses RuntimeWarning).
    Task falls through to RetrievalStrategy() defaults when container is empty.
    Asserts: agent.retrieval_strategy set to non-empty dict and db.commit() called.
    """
    agent_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())

    mock_agent = MagicMock()
    mock_agent.retrieval_strategy = {}            # empty → synthesis runs
    mock_agent.strategy_resynthesis_flagged = False
    mock_agent.neon_connection_string = b"enc"

    mock_db = MagicMock()
    mock_db.get.return_value = mock_agent

    with patch(
        "app.worker.tasks.pipeline.strategy.get_sync_db",
        _make_sync_db_ctx(mock_db),
    ), patch(
        "app.worker.tasks.pipeline.strategy.fernet_decrypt",
        return_value="postgresql://test/tenant",
    ), patch(
        "app.worker.tasks.pipeline.strategy._fetch_corpus_signals_sync",
        return_value=_FAKE_SIGNALS,
    ), patch(
        "app.worker.tasks.pipeline.strategy.run_strategist",
    ), patch(
        "app.worker.tasks.pipeline.strategy.emit",
    ):
        synthesize_retrieval_strategy.run(
            result={
                "tenant_id": "t1",
                "agent_id": agent_id,
                "job_id": job_id,
                "document_ids": ["d1"],
            }
        )

    # agent.retrieval_strategy was set to a non-empty dict
    assert mock_agent.retrieval_strategy is not None
    assert isinstance(mock_agent.retrieval_strategy, dict)
    assert mock_agent.retrieval_strategy != {}
    assert mock_db.commit.called


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_receives_embed_result_dict():
    """Task correctly unpacks the embed result dict from Wave 1.

    Part A: valid result dict with agent_id — task extracts agent_id and runs.
    Part B: result={} (missing agent_id) — task returns result unchanged, no DB write.
    """
    # Part A: valid result dict
    agent_id = str(uuid.uuid4())

    mock_agent = MagicMock()
    mock_agent.retrieval_strategy = {}
    mock_agent.strategy_resynthesis_flagged = False
    mock_agent.neon_connection_string = b"enc"

    mock_db = MagicMock()
    mock_db.get.return_value = mock_agent

    with patch(
        "app.worker.tasks.pipeline.strategy.get_sync_db",
        _make_sync_db_ctx(mock_db),
    ), patch(
        "app.worker.tasks.pipeline.strategy.fernet_decrypt",
        return_value="postgresql://test/tenant",
    ), patch(
        "app.worker.tasks.pipeline.strategy._fetch_corpus_signals_sync",
        return_value=_FAKE_SIGNALS,
    ), patch(
        "app.worker.tasks.pipeline.strategy.run_strategist",
    ), patch(
        "app.worker.tasks.pipeline.strategy.emit",
    ):
        result = synthesize_retrieval_strategy.run(
            result={
                "tenant_id": "t1",
                "agent_id": agent_id,
                "job_id": "j1",
                "document_ids": ["d1"],
            }
        )

    # Chain pass-through: returns result dict with agent_id intact
    assert result["agent_id"] == agent_id

    # Part B: missing agent_id → early return, no DB commit
    mock_db_empty = MagicMock()

    with patch(
        "app.worker.tasks.pipeline.strategy.get_sync_db",
        _make_sync_db_ctx(mock_db_empty),
    ):
        result_empty = synthesize_retrieval_strategy.run(result={})

    assert result_empty == {}
    mock_db_empty.commit.assert_not_called()


# ---------------------------------------------------------------------------
# Idempotency guard
# ---------------------------------------------------------------------------


def test_idempotency_skip():
    """synthesize_retrieval_strategy returns early when retrieval_strategy is already populated.

    When agent.retrieval_strategy is non-empty and strategy_resynthesis_flagged is False,
    neither _fetch_corpus_signals_sync nor asyncio.run should be called.
    """
    agent_id = str(uuid.uuid4())

    mock_agent = MagicMock()
    mock_agent.retrieval_strategy = {"vector_k": 20}    # already set
    mock_agent.strategy_resynthesis_flagged = False       # not flagged
    mock_agent.neon_connection_string = b"enc"

    mock_db = MagicMock()
    mock_db.get.return_value = mock_agent

    with patch(
        "app.worker.tasks.pipeline.strategy.get_sync_db",
        _make_sync_db_ctx(mock_db),
    ), patch(
        "app.worker.tasks.pipeline.strategy.fernet_decrypt",
        return_value="postgresql://test/tenant",
    ), patch(
        "app.worker.tasks.pipeline.strategy._fetch_corpus_signals_sync",
    ) as mock_signals, patch(
        "app.worker.tasks.pipeline.strategy.run_strategist",
    ) as mock_run_strategist:
        synthesize_retrieval_strategy.run(
            result={
                "tenant_id": "t1",
                "agent_id": agent_id,
                "job_id": "j1",
                "document_ids": [],
            }
        )

    # Neither corpus fetch nor strategist should have been invoked
    mock_signals.assert_not_called()
    mock_run_strategist.assert_not_called()
    # Strategy value untouched (no DB write expected on idempotent skip)
    assert mock_agent.retrieval_strategy == {"vector_k": 20}


# ---------------------------------------------------------------------------
# Resynthesis flag bypasses guard
# ---------------------------------------------------------------------------


def test_resynthesis_flag_bypasses_guard():
    """force_resynthesize=True causes the task to run even when retrieval_strategy is set.

    When strategy_resynthesis_flagged=True, the idempotency guard is bypassed,
    asyncio.run is called, and the flag is cleared (set to False) after synthesis.
    """
    agent_id = str(uuid.uuid4())

    mock_agent = MagicMock()
    mock_agent.retrieval_strategy = {"vector_k": 20}    # already set
    mock_agent.strategy_resynthesis_flagged = True        # flagged → bypass guard
    mock_agent.neon_connection_string = b"enc"

    mock_db = MagicMock()
    mock_db.get.return_value = mock_agent

    with patch(
        "app.worker.tasks.pipeline.strategy.get_sync_db",
        _make_sync_db_ctx(mock_db),
    ), patch(
        "app.worker.tasks.pipeline.strategy.fernet_decrypt",
        return_value="postgresql://test/tenant",
    ), patch(
        "app.worker.tasks.pipeline.strategy._fetch_corpus_signals_sync",
        return_value=_FAKE_SIGNALS,
    ), patch(
        "app.worker.tasks.pipeline.strategy.run_strategist",
    ) as mock_run_strategist, patch(
        "app.worker.tasks.pipeline.strategy.emit",
    ):
        synthesize_retrieval_strategy.run(
            result={
                "tenant_id": "t1",
                "agent_id": agent_id,
                "job_id": "j1",
                "document_ids": [],
            }
        )

    # run_strategist MUST have been called (strategist ran despite existing strategy)
    mock_run_strategist.assert_called_once()

    # strategy_resynthesis_flagged must be cleared
    assert mock_agent.strategy_resynthesis_flagged is False
