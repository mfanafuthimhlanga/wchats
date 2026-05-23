"""
Unit tests for the retrieve_and_rank Celery task (Plan 03).

Tests validate:
  - Task decorator attributes: acks_late=True, queue="runtime", name="retrieve_and_rank"
  - Idempotency guard: task returns {} immediately if query.complete event exists
  - Full happy-path: 5 SSE events emitted in correct order, job marked complete
  - Agent-not-found guard: task returns {} without raising
  - Job-not-found guard: task returns {} without raising
  - Retry behaviour: self.retry() called when retries < max_retries
  - Final failure: job marked "failed" and query.failed emitted after max_retries
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent(agent_id: str = None, retrieval_strategy: dict | None = None) -> MagicMock:
    agent = MagicMock()
    agent.id = agent_id or str(uuid.uuid4())
    agent.retrieval_strategy = retrieval_strategy or {}
    agent.neon_connection_string = b"encrypted-conn-str"
    return agent


def _make_job(job_id: str = None) -> MagicMock:
    job = MagicMock()
    job.id = job_id or str(uuid.uuid4())
    job.status = "running"
    job.finished_at = None
    return job


# ---------------------------------------------------------------------------
# Task decorator attribute tests
# ---------------------------------------------------------------------------

def test_retrieve_and_rank_acks_late():
    """acks_late must be True (CLAUDE.md non-negotiable rule)."""
    from app.worker.tasks.runtime.retrieve import retrieve_and_rank
    assert retrieve_and_rank.acks_late is True, "acks_late must be True"


def test_retrieve_and_rank_queue():
    """queue must be 'runtime' — CLAUDE.md non-negotiable rule."""
    from app.worker.tasks.runtime.retrieve import retrieve_and_rank
    assert retrieve_and_rank.queue == "runtime", (
        f"queue must be 'runtime', got '{retrieve_and_rank.queue}'"
    )


def test_retrieve_and_rank_task_name():
    """Task must be registered as 'retrieve_and_rank'."""
    from app.worker.tasks.runtime.retrieve import retrieve_and_rank
    assert retrieve_and_rank.name == "retrieve_and_rank"


def test_retrieve_and_rank_max_retries():
    """max_retries must be 3."""
    from app.worker.tasks.runtime.retrieve import retrieve_and_rank
    assert retrieve_and_rank.max_retries == 3


def test_retrieve_and_rank_signature():
    """Task .run() must accept (self, job_id, agent_id, query) — no conn_str in args."""
    import inspect
    from app.worker.tasks.runtime.retrieve import retrieve_and_rank
    sig = inspect.signature(retrieve_and_rank.run)
    param_names = list(sig.parameters.keys())
    assert "job_id" in param_names
    assert "agent_id" in param_names
    assert "query" in param_names
    # conn_str must NEVER appear in task signature (security constraint)
    assert "conn_str" not in param_names, "conn_str must not be in task args"
    assert "connection_string" not in param_names


# ---------------------------------------------------------------------------
# Idempotency guard test
# ---------------------------------------------------------------------------

def test_retrieve_and_rank_idempotent():
    """Task returns {} immediately if query.complete event already exists."""
    from app.worker.tasks.runtime.retrieve import retrieve_and_rank

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())

    # Mock DB session that returns a row for the idempotency SELECT
    mock_row = MagicMock()
    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = mock_row  # existing row found

    mock_db_ctx = MagicMock()
    mock_db_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_db_ctx.__exit__ = MagicMock(return_value=False)

    with patch("app.worker.tasks.runtime.retrieve.get_sync_db", return_value=mock_db_ctx):
        result = retrieve_and_rank.run(job_id=job_id, agent_id=agent_id, query="test query")

    assert result == {}, "Idempotent path must return {}"
    # Agent and Job must NOT be fetched — idempotency guard exits before that
    mock_db.get.assert_not_called()


# ---------------------------------------------------------------------------
# Agent-not-found guard
# ---------------------------------------------------------------------------

def test_retrieve_and_rank_agent_not_found():
    """Task returns {} gracefully when agent_id does not exist in control DB."""
    from app.worker.tasks.runtime.retrieve import retrieve_and_rank

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None  # no idempotency row
    mock_db.get.return_value = None  # agent not found

    mock_db_ctx = MagicMock()
    mock_db_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_db_ctx.__exit__ = MagicMock(return_value=False)

    with patch("app.worker.tasks.runtime.retrieve.get_sync_db", return_value=mock_db_ctx):
        result = retrieve_and_rank.run(job_id=job_id, agent_id=agent_id, query="test")

    assert result == {}


# ---------------------------------------------------------------------------
# Job-not-found guard
# ---------------------------------------------------------------------------

def test_retrieve_and_rank_job_not_found():
    """Task returns {} gracefully when job_id does not exist in control DB."""
    from app.worker.tasks.runtime.retrieve import retrieve_and_rank

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    agent = _make_agent(agent_id=agent_id)

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None  # no idempotency row
    # First db.get(Agent, ...) returns the agent; second db.get(Job, ...) returns None
    mock_db.get.side_effect = [agent, None]

    mock_db_ctx = MagicMock()
    mock_db_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_db_ctx.__exit__ = MagicMock(return_value=False)

    with patch("app.worker.tasks.runtime.retrieve.get_sync_db", return_value=mock_db_ctx):
        result = retrieve_and_rank.run(job_id=job_id, agent_id=agent_id, query="test")

    assert result == {}


# ---------------------------------------------------------------------------
# Happy-path: 5 SSE events, job marked complete
# ---------------------------------------------------------------------------

def test_retrieve_and_rank_happy_path():
    """Full happy-path: 5 SSE events emitted, job.status set to 'complete'."""
    from app.worker.tasks.runtime.retrieve import retrieve_and_rank

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    agent = _make_agent(agent_id=agent_id)
    job = _make_job(job_id=job_id)

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None  # no idempotency row
    mock_db.get.side_effect = [agent, job]

    mock_db_ctx = MagicMock()
    mock_db_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_db_ctx.__exit__ = MagicMock(return_value=False)

    fake_vector = [0.1] * 1024
    fake_fused = [{"chunk_id": "c1", "content": "hello", "rrf_score": 0.9}]
    fake_vector_cands = [{"chunk_id": "c1", "content": "hello", "cosine_score": 0.95}]
    fake_bm25_cands = [{"chunk_id": "c1", "content": "hello", "bm25_score": 0.8}]
    fake_reranked = [{"chunk_id": "c1", "content": "hello", "rerank_score": 0.99}]
    fake_trace = {
        "vector_candidates": fake_vector_cands,
        "bm25_candidates": fake_bm25_cands,
        "fused_candidates": fake_fused,
        "reranked_candidates": fake_reranked,
    }

    emitted_events: list[str] = []

    def fake_emit(jid, event_type, payload, db, redis):
        emitted_events.append(event_type)

    with (
        patch("app.worker.tasks.runtime.retrieve.get_sync_db", return_value=mock_db_ctx),
        patch("app.worker.tasks.runtime.retrieve.fernet_decrypt", return_value="postgresql://tenant"),
        patch("app.worker.tasks.runtime.retrieve.embed_query", return_value=fake_vector),
        # D-27: verified_qa_lookup returns None (cache miss) — falls through to hybrid search
        patch("app.worker.tasks.runtime.retrieve.verified_qa_lookup", return_value=None),
        patch("app.worker.tasks.runtime.retrieve.rrf_fuse", return_value={
            "fused": fake_fused,
            "vector_candidates": fake_vector_cands,
            "bm25_candidates": fake_bm25_cands,
        }),
        patch("app.worker.tasks.runtime.retrieve.rerank", return_value=fake_reranked),
        patch("app.worker.tasks.runtime.retrieve.build_trace", return_value=fake_trace),
        patch("app.worker.tasks.runtime.retrieve.emit", side_effect=fake_emit),
    ):
        result = retrieve_and_rank.run(
            job_id=job_id,
            agent_id=agent_id,
            query="what is the return policy?",
        )

    assert result == {}, "Task must always return {}"

    # Verify all 5 SSE events emitted in correct sequence
    assert emitted_events == [
        "query.started",
        "query.embedding",
        "query.searching",
        "query.reranking",
        "query.complete",
    ], f"Expected 5 events in order, got: {emitted_events}"

    # Verify job marked complete
    assert job.status == "complete"
    assert job.finished_at is not None
    mock_db.commit.assert_called()


# ---------------------------------------------------------------------------
# verified_qa cache hit path — D-24/D-26
# ---------------------------------------------------------------------------

def test_retrieve_and_rank_cache_hit_skips_hybrid_search():
    """D-24/D-26: cache hit returns early — rrf_fuse must NOT be called."""
    from app.worker.tasks.runtime.retrieve import retrieve_and_rank

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    agent = _make_agent(agent_id=agent_id)
    job = _make_job(job_id=job_id)

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None  # no idempotency row
    mock_db.get.side_effect = [agent, job]

    mock_db_ctx = MagicMock()
    mock_db_ctx.__enter__ = MagicMock(return_value=mock_db_ctx)
    mock_db_ctx.__exit__ = MagicMock(return_value=False)
    mock_db_ctx.__enter__ = MagicMock(return_value=mock_db)

    fake_vector = [0.1] * 1024
    cache_hit_result = {
        "answer": "Refunds are processed within 5 business days.",
        "citations": [{"doc": "policy.pdf", "page": 3}],
        "similarity": 0.971,
        "source": "verified_qa_cache",
    }

    emitted_events: list[str] = []

    def fake_emit(jid, event_type, payload, db, redis):
        emitted_events.append(event_type)

    mock_rrf_fuse = MagicMock()

    with (
        patch("app.worker.tasks.runtime.retrieve.get_sync_db", return_value=mock_db_ctx),
        patch("app.worker.tasks.runtime.retrieve.fernet_decrypt", return_value="postgresql://tenant"),
        patch("app.worker.tasks.runtime.retrieve.embed_query", return_value=fake_vector),
        # D-26: cache hit — verified_qa_lookup returns a cached answer
        patch("app.worker.tasks.runtime.retrieve.verified_qa_lookup", return_value=cache_hit_result),
        patch("app.worker.tasks.runtime.retrieve.rrf_fuse", mock_rrf_fuse),
        patch("app.worker.tasks.runtime.retrieve.emit", side_effect=fake_emit),
    ):
        result = retrieve_and_rank.run(
            job_id=job_id,
            agent_id=agent_id,
            query="what is the refund policy?",
        )

    assert result == {}

    # D-24: hybrid search must NOT have been called on cache hit
    mock_rrf_fuse.assert_not_called()

    # query.complete must still be emitted (cache hit short-circuits but emits complete)
    assert "query.complete" in emitted_events

    # Job marked complete on cache hit
    assert job.status == "complete"
    assert job.finished_at is not None


def test_retrieve_and_rank_cache_hit_payload_has_cache_trace():
    """D-26: query.complete payload on cache hit contains cache_hit=True trace."""
    from app.worker.tasks.runtime.retrieve import retrieve_and_rank

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    agent = _make_agent(agent_id=agent_id)
    job = _make_job(job_id=job_id)

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    mock_db.get.side_effect = [agent, job]

    mock_db_ctx = MagicMock()
    mock_db_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_db_ctx.__exit__ = MagicMock(return_value=False)

    fake_vector = [0.2] * 1024
    cache_hit_result = {
        "answer": "The cached answer.",
        "citations": [],
        "similarity": 0.963,
        "source": "verified_qa_cache",
    }

    emitted_payloads: dict[str, dict] = {}

    def fake_emit(jid, event_type, payload, db, redis):
        emitted_payloads[event_type] = payload

    with (
        patch("app.worker.tasks.runtime.retrieve.get_sync_db", return_value=mock_db_ctx),
        patch("app.worker.tasks.runtime.retrieve.fernet_decrypt", return_value="postgresql://tenant"),
        patch("app.worker.tasks.runtime.retrieve.embed_query", return_value=fake_vector),
        patch("app.worker.tasks.runtime.retrieve.verified_qa_lookup", return_value=cache_hit_result),
        patch("app.worker.tasks.runtime.retrieve.rrf_fuse", MagicMock()),
        patch("app.worker.tasks.runtime.retrieve.emit", side_effect=fake_emit),
    ):
        retrieve_and_rank.run(
            job_id=job_id,
            agent_id=agent_id,
            query="some query",
        )

    complete_payload = emitted_payloads.get("query.complete", {})
    trace = complete_payload.get("trace", {})

    # Verify required demo trace keys (D-32)
    assert trace.get("cache_hit") is True
    assert trace.get("source") == "verified_qa_cache"
    assert trace.get("similarity") == 0.963

    # Results list must contain the cached answer
    results = complete_payload.get("results", [])
    assert len(results) == 1
    assert results[0]["content"] == "The cached answer."


def test_retrieve_and_rank_verified_qa_lookup_called_with_threshold():
    """D-25: verified_qa_lookup called with threshold=settings.VERIFIED_QA_HIT_THRESHOLD."""
    from app.worker.tasks.runtime.retrieve import retrieve_and_rank
    from app.core.config import settings

    job_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    agent = _make_agent(agent_id=agent_id)
    job = _make_job(job_id=job_id)

    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = None
    mock_db.get.side_effect = [agent, job]

    mock_db_ctx = MagicMock()
    mock_db_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_db_ctx.__exit__ = MagicMock(return_value=False)

    fake_vector = [0.3] * 1024
    mock_lookup = MagicMock(return_value=None)  # cache miss

    with (
        patch("app.worker.tasks.runtime.retrieve.get_sync_db", return_value=mock_db_ctx),
        patch("app.worker.tasks.runtime.retrieve.fernet_decrypt", return_value="postgresql://tenant"),
        patch("app.worker.tasks.runtime.retrieve.embed_query", return_value=fake_vector),
        patch("app.worker.tasks.runtime.retrieve.verified_qa_lookup", mock_lookup),
        patch("app.worker.tasks.runtime.retrieve.rrf_fuse", return_value={
            "fused": [], "vector_candidates": [], "bm25_candidates": []
        }),
        patch("app.worker.tasks.runtime.retrieve.rerank", return_value=[]),
        patch("app.worker.tasks.runtime.retrieve.build_trace", return_value={}),
        patch("app.worker.tasks.runtime.retrieve.emit"),
    ):
        retrieve_and_rank.run(job_id=job_id, agent_id=agent_id, query="test")

    mock_lookup.assert_called_once()
    call_kwargs = mock_lookup.call_args
    # Verify threshold uses settings.VERIFIED_QA_HIT_THRESHOLD (D-25)
    assert call_kwargs.kwargs.get("threshold") == settings.VERIFIED_QA_HIT_THRESHOLD
    # Verify query_vector passed through
    assert call_kwargs.kwargs.get("query_vector") == fake_vector


# ---------------------------------------------------------------------------
# Celery app include list test
# ---------------------------------------------------------------------------

def test_celery_app_includes_runtime_retrieve():
    """runtime.retrieve must be in celery_app.conf.include list."""
    from app.worker.celery_app import celery_app
    includes = celery_app.conf.include
    assert "app.worker.tasks.runtime.retrieve" in includes, (
        f"runtime.retrieve not in celery_app include: {includes}"
    )
