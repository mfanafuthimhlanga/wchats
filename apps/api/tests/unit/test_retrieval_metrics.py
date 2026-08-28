"""
Unit tests for OPS-05/06 retrieval-metrics instrumentation in agent_tools.retrieve_tool.

TDD RED -> GREEN:
  RED:   these tests fail because retrieve_tool does not yet compute/write a
         retrieval_metrics row and _job_id_var does not yet exist.
  GREEN: retrieve_tool computes the OPS-05/06 vitals from rrf_result/reranked
         and writes one row via retrieval_metrics_service.write_retrieval_metrics,
         with job_id read from the new _job_id_var ContextVar into a local
         BEFORE any run_in_executor call (Pitfall 4).

Drives the real tool definitions, which since ticket #49 need no SDK, same as
test_agent_tools.py, so tests run without the SDK binary present.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

import app.services.agent_tools as agent_tools
from app.domain.retrieved_context import RetrievedChunk, RetrievedContext  # noqa: E402
from app.services.retrieval_service import RrfFusion  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _fn(tool_obj):
    """The async callable behind a `@tool` declaration.

    One shape since ticket #49. `app.domain.tool_def.ToolDefinition` is frozen
    and not callable, and its handler lives on `.handler`, which is where
    `tool_loop.dispatch` looks too.

    There were two shapes until then, and the reason is worth keeping. The real
    `claude_agent_sdk.tool` returned an `SdkMcpTool`, while the fake this module
    installed returned the function itself, so which one a test received
    depended on whether another module had already imported the real package.
    `getattr(obj, "handler", obj)` was what made that race invisible.
    """
    return getattr(tool_obj, "handler", tool_obj)


# ---------------------------------------------------------------------------
# Shared fixture: fused (pre-rerank) + reranked (post-rerank, reordered so
# the reranker promotes a chunk that was ranked #4 in the fused list up to
# #1 in the final returned set) candidate shapes.
#
#   fused (RRF order): c1 (rank1, rrf=0.9), c2 (rank2, rrf=0.8),
#                       c3 (rank3, rrf=0.5), c4 (rank4, rrf=0.3)
#   bm25_candidates:   top bm25_score = 0.6 (c2)
#   vector_candidates: top cosine_score = 0.8 (c1)
#   reranked (final):  c4 (rerank=0.95), c1 (rerank=0.85), c3 (rerank=0.6)
#     -> the final returned set is {c4, c1, c3} — c2 was dropped, c4 (fused
#        rank 4) was promoted to the top of the returned set.
#
# Each fused chunk's content is exactly 400 chars so retrieved_tokens /
# carried_never_cited_tokens / compaction_ratio land on exact values.
# ---------------------------------------------------------------------------


QUERY = "test query"


def _context(strategy: str, rows: list[tuple]) -> RetrievedContext:
    """(chunk_id, document_id, content, score) rows, ranked in the order given."""
    return RetrievedContext(
        query=QUERY,
        chunks=tuple(
            RetrievedChunk(chunk_id, document_id, content, score, position)
            for position, (chunk_id, document_id, content, score) in enumerate(
                rows, start=1
            )
        ),
        strategy=strategy,
    )


def _build_fixture():
    fused = _context("rrf", [
        ("c1", "d1", "A" * 400, 0.9),
        ("c2", "d1", "B" * 400, 0.8),
        ("c3", "d2", "C" * 400, 0.5),
        ("c4", "d2", "D" * 400, 0.3),
    ])
    bm25_candidates = _context("bm25", [
        ("c2", "d1", "B" * 400, 0.6),
        ("c1", "d1", "A" * 400, 0.5),
    ])
    vector_candidates = _context("vector", [
        ("c1", "d1", "A" * 400, 0.8),
        ("c2", "d1", "B" * 400, 0.7),
    ])
    rrf_result = RrfFusion(
        fused=fused,
        vector_candidates=vector_candidates,
        bm25_candidates=bm25_candidates,
    )
    reranked = _context("rerank", [
        ("c4", "d2", "D" * 400, 0.95),  # c4 -> promoted to #1
        ("c1", "d1", "A" * 400, 0.85),  # c1
        ("c3", "d2", "C" * 400, 0.6),   # c3
    ])
    return rrf_result, reranked


# ---------------------------------------------------------------------------
# Test 1: retrieve_tool writes exactly one retrieval_metrics row with the
# correct OPS-05/06 vitals computed from rrf_result + reranked.
# ---------------------------------------------------------------------------


def test_retrieve_tool_writes_retrieval_metrics_row():
    agent_tools._retrieve_call_count_var.set(0)
    agent_tools._conn_str_var.set("postgresql://test:test@localhost/testdb")
    agent_tools._conversation_id_var.set("conv-metrics-1")
    agent_tools._job_id_var.set("job-metrics-1")

    rrf_result, reranked = _build_fixture()

    with (
        patch("app.services.agent_tools.embed_query", return_value=[0.1] * 1024),
        patch("app.services.agent_tools.rrf_fuse", return_value=rrf_result),
        patch("app.services.agent_tools.rerank", return_value=reranked),
        patch("app.services.agent_tools.write_retrieval_metrics") as mock_write,
    ):
        _run(_fn(agent_tools.retrieve_tool)({"query": "test query"}))

    mock_write.assert_called_once()
    call_args = mock_write.call_args
    conn_str_arg = call_args.args[0]
    row = call_args.args[1]

    assert conn_str_arg == "postgresql://test:test@localhost/testdb"

    assert row["job_id"] == "job-metrics-1"
    assert row["conversation_id"] == "conv-metrics-1"

    assert row["bm25_top_score"] == 0.6
    assert row["vector_top_score"] == 0.8
    assert row["rrf_top_score"] == 0.9
    assert row["rerank_top_score"] == 0.95
    assert row["reranker_lift"] == pytest.approx(0.35)

    # cited_chunk_rank: top returned chunk is c4, whose position in the
    # pre-rerank fused ranking was #4.
    assert row["cited_chunk_rank"] == 4
    assert row["mrr"] == pytest.approx(0.25)

    # recall_at_k: of the 3 returned chunks {c4, c1, c3}, only c1 (rank1) and
    # c3 (rank3) fall within the top-3 of the fused ranking; c4 (rank4) does
    # not -> 2/3.
    assert row["recall_at_k"] == pytest.approx(2 / 3)

    # ndcg_at_10: binary relevance (returned-set membership) over the fused
    # top-10 order, normalized by the ideal (top-3-relevant) DCG.
    assert row["ndcg_at_10"] == pytest.approx(0.90603, abs=1e-4)

    # retrieved_tokens = sum(len(content))//4 over the 3 returned chunks
    # (400*3)//4 = 300.
    assert row["retrieved_tokens"] == 300
    assert row["ctx_window_utilization"] == pytest.approx(300 / 200000)

    # carried_never_cited_tokens = total fused tokens (400*4//4=400) minus
    # retrieved_tokens (300) = 100.
    assert row["carried_never_cited_tokens"] == 100
    assert row["compaction_ratio"] == pytest.approx(300 / 400)


# ---------------------------------------------------------------------------
# Test 2: Pitfall 4 regression — job_id must be read from the ContextVar into
# a LOCAL before any run_in_executor call, so the written row carries it.
# ---------------------------------------------------------------------------


def test_retrieve_tool_job_id_read_from_contextvar_into_local():
    agent_tools._retrieve_call_count_var.set(0)
    agent_tools._conn_str_var.set("postgresql://test:test@localhost/testdb")
    agent_tools._conversation_id_var.set("conv-jobid-2")
    agent_tools._job_id_var.set("job-sentinel-777")

    rrf_result, reranked = _build_fixture()

    with (
        patch("app.services.agent_tools.embed_query", return_value=[0.1] * 1024),
        patch("app.services.agent_tools.rrf_fuse", return_value=rrf_result),
        patch("app.services.agent_tools.rerank", return_value=reranked),
        patch("app.services.agent_tools.write_retrieval_metrics") as mock_write,
    ):
        _run(_fn(agent_tools.retrieve_tool)({"query": "job id plumbing check"}))

    mock_write.assert_called_once()
    row = mock_write.call_args.args[1]
    assert row["job_id"] == "job-sentinel-777", (
        "job_id ContextVar must reach the write path via a local read at the "
        "top of retrieve_tool (Pitfall 4) — never a .get() inside the executor lambda"
    )


# ---------------------------------------------------------------------------
# Test 3: job_id defaults to "" when the ContextVar was never set, and the
# row is still written (with an empty job_id) plus a warning is logged.
# ---------------------------------------------------------------------------


def test_retrieve_tool_writes_row_with_empty_job_id_and_warns_when_unset():
    agent_tools._retrieve_call_count_var.set(0)
    agent_tools._conn_str_var.set("postgresql://test:test@localhost/testdb")
    agent_tools._conversation_id_var.set("conv-nojob-3")
    # Explicitly reset job_id ContextVar to the unset default.
    agent_tools._job_id_var.set("")

    rrf_result, reranked = _build_fixture()

    with (
        patch("app.services.agent_tools.embed_query", return_value=[0.1] * 1024),
        patch("app.services.agent_tools.rrf_fuse", return_value=rrf_result),
        patch("app.services.agent_tools.rerank", return_value=reranked),
        patch("app.services.agent_tools.write_retrieval_metrics") as mock_write,
        patch("app.services.agent_tools.log") as mock_log,
    ):
        _run(_fn(agent_tools.retrieve_tool)({"query": "no job id"}))

    mock_write.assert_called_once()
    row = mock_write.call_args.args[1]
    assert row["job_id"] == ""

    warning_events = [
        call.args[0]
        for call in mock_log.warning.call_args_list
        if call.args and call.args[0] == "retrieve_tool.metrics_write_no_job_id"
    ]
    assert len(warning_events) >= 1, (
        "Expected log.warning('retrieve_tool.metrics_write_no_job_id') "
        "when the job_id ContextVar was never set"
    )


# ---------------------------------------------------------------------------
# Test 4: no fabricated "filters applied" metric (Pitfall 7) — row keys are
# exactly the OPS-05/06 vitals, nothing filter-related.
# ---------------------------------------------------------------------------


def test_retrieve_tool_metrics_row_has_no_filters_key():
    agent_tools._retrieve_call_count_var.set(0)
    agent_tools._conn_str_var.set("postgresql://test:test@localhost/testdb")
    agent_tools._conversation_id_var.set("conv-nofilter-4")
    agent_tools._job_id_var.set("job-nofilter-4")

    rrf_result, reranked = _build_fixture()

    with (
        patch("app.services.agent_tools.embed_query", return_value=[0.1] * 1024),
        patch("app.services.agent_tools.rrf_fuse", return_value=rrf_result),
        patch("app.services.agent_tools.rerank", return_value=reranked),
        patch("app.services.agent_tools.write_retrieval_metrics") as mock_write,
    ):
        _run(
            _fn(agent_tools.retrieve_tool)(
                {"query": "filters test", "filters": [{"document_id": "abc"}]}
            )
        )

    row = mock_write.call_args.args[1]
    filter_like_keys = [k for k in row if "filter" in k.lower()]
    assert filter_like_keys == [], (
        f"retrieval_metrics row must not surface a filters-applied metric "
        f"(Pitfall 7 — filters are a documented no-op), found: {filter_like_keys}"
    )


# ---------------------------------------------------------------------------
# Test 5: empty rrf_result/reranked (no candidates) does not raise — metrics
# degrade to None/0 rather than crashing the tool.
# ---------------------------------------------------------------------------


def test_retrieve_tool_metrics_handles_empty_candidates():
    agent_tools._retrieve_call_count_var.set(0)
    agent_tools._conn_str_var.set("postgresql://test:test@localhost/testdb")
    agent_tools._conversation_id_var.set("conv-empty-5")
    agent_tools._job_id_var.set("job-empty-5")

    empty_rrf = RrfFusion(
        fused=_context("rrf", []),
        vector_candidates=_context("vector", []),
        bm25_candidates=_context("bm25", []),
    )

    with (
        patch("app.services.agent_tools.embed_query", return_value=[0.1] * 1024),
        patch("app.services.agent_tools.rrf_fuse", return_value=empty_rrf),
        patch("app.services.agent_tools.rerank", return_value=_context("rerank", [])),
        patch("app.services.agent_tools.write_retrieval_metrics") as mock_write,
    ):
        result = _run(_fn(agent_tools.retrieve_tool)({"query": "no results anywhere"}))

    assert result.get("is_error") is not True
    mock_write.assert_called_once()
    row = mock_write.call_args.args[1]
    assert row["bm25_top_score"] is None
    assert row["vector_top_score"] is None
    assert row["rrf_top_score"] is None
    assert row["rerank_top_score"] is None
    assert row["reranker_lift"] is None
    assert row["cited_chunk_rank"] is None
    assert row["mrr"] is None
    assert row["recall_at_k"] is None
    assert row["ndcg_at_10"] is None
    assert row["retrieved_tokens"] == 0
    assert row["ctx_window_utilization"] == 0.0
    assert row["carried_never_cited_tokens"] == 0
    assert row["compaction_ratio"] is None


# ---------------------------------------------------------------------------
# Test 6-8 (D1/P1b, BACKLOG 2.5): the recorded side-effect mode.
#
# From P2 the nightly eval drives retrieve_tool through the same seam the chat
# path uses. These rows are observations about the tenant's PRODUCTION retrieval
# quality — OPS-05/06 is what the ops room's recall and nDCG tiles read — and an
# eval's scenario queries would move those numbers nightly without a single
# customer having asked anything. Faithfulness would then be measured against a
# corpus whose reported retrieval health is partly the eval's own reflection.
#
# What is NOT suppressed is the retrieve RESULT. Retrieval is a read; the agent
# under evaluation must see exactly the chunks production would hand it, or the
# eval measures a different agent, which is the one failure this whole phase
# exists to prevent.
# ---------------------------------------------------------------------------


from contextlib import contextmanager  # noqa: E402


@contextmanager
def _side_effect_mode(mode: str):
    """Enter `mode` with a fresh sink, and leave nothing behind.

    ContextVars persist for the whole pytest session — a leaked "recorded" would
    make Tests 1-5 above stop writing their row while still asserting on it, and
    they would fail for a reason unrelated to what they test. Reset by token
    rather than by re-setting a guessed previous value.
    """
    token_mode = agent_tools._side_effects_var.set(mode)
    token_sink = agent_tools._recorded_side_effects_var.set([])
    try:
        yield
    finally:
        agent_tools._recorded_side_effects_var.reset(token_sink)
        agent_tools._side_effects_var.reset(token_mode)


def test_recorded_mode_does_not_write_the_retrieval_metrics_row():
    agent_tools._retrieve_call_count_var.set(0)
    agent_tools._conn_str_var.set("postgresql://test:test@localhost/testdb")
    agent_tools._conversation_id_var.set("conv-recorded-6")
    agent_tools._job_id_var.set("job-recorded-6")

    rrf_result, reranked = _build_fixture()

    with (
        _side_effect_mode("recorded"),
        patch("app.services.agent_tools.embed_query", return_value=[0.1] * 1024),
        patch("app.services.agent_tools.rrf_fuse", return_value=rrf_result),
        patch("app.services.agent_tools.rerank", return_value=reranked),
        patch("app.services.agent_tools.write_retrieval_metrics") as mock_write,
    ):
        result = _run(_fn(agent_tools.retrieve_tool)({"query": "test query"}))
        recorded = agent_tools.get_recorded_side_effects()

    mock_write.assert_not_called()

    # The row is recorded, not discarded: it is still a real observation of what
    # the agent's own retrieval did on this scenario, which is exactly what P2
    # needs when it scores faithfulness against the contexts the agent SAW
    # rather than the ones the scenario was written from.
    assert len(recorded) == 1, f"expected one recorded side effect, got {recorded!r}"
    assert recorded[0]["kind"] == "retrieval_metrics.write"
    assert recorded[0]["detail"]["job_id"] == "job-recorded-6"
    assert recorded[0]["detail"]["row"]["bm25_top_score"] == 0.6

    # And the agent still gets its chunks. Suppressing the write must not
    # suppress the read.
    assert result.get("is_error") is not True
    assert "content" in result
    assert result["_citations"], (
        "recorded mode returned no citations, so the agent under evaluation saw "
        "less than production would hand it — the eval would be measuring a "
        "differently-informed agent."
    )


def test_live_mode_still_writes_the_retrieval_metrics_row():
    """The anti-tautology partner of the test above.

    `assert_not_called` is satisfied by a retrieve that never reached the write
    at all — a raised exception, an early return, a broken fixture. Driving the
    identical fixture in live mode and asserting the write DOES happen isolates
    the mode as the only difference between the two outcomes.
    """
    agent_tools._retrieve_call_count_var.set(0)
    agent_tools._conn_str_var.set("postgresql://test:test@localhost/testdb")
    agent_tools._conversation_id_var.set("conv-recorded-7")
    agent_tools._job_id_var.set("job-recorded-7")

    rrf_result, reranked = _build_fixture()

    with (
        _side_effect_mode("live"),
        patch("app.services.agent_tools.embed_query", return_value=[0.1] * 1024),
        patch("app.services.agent_tools.rrf_fuse", return_value=rrf_result),
        patch("app.services.agent_tools.rerank", return_value=reranked),
        patch("app.services.agent_tools.write_retrieval_metrics") as mock_write,
    ):
        _run(_fn(agent_tools.retrieve_tool)({"query": "test query"}))
        recorded = agent_tools.get_recorded_side_effects()

    mock_write.assert_called_once()
    assert recorded == [], (
        "live mode recorded a suppressed side effect. It suppressed nothing, so "
        f"there is nothing to record; got {recorded!r}."
    )
