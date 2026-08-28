"""
Unit tests proving ContextVar-based isolation in agent_tools.py  (PROD-14).

Tests:
  1. test_asyncio_run_propagation   — ContextVar set in sync context before asyncio.run() is
                                       visible inside the coroutine (must-shape #8).
  2. test_two_context_no_bleed      — Two separate copy_context() runs (simulating two
                                       concurrent Celery tasks) see only their own _conn_str /
                                       _agent_id values; no cross-request state bleed.
  3. test_retrieve_counter_isolated — Per-turn retrieve counter increments are isolated between
                                       contexts; no bleed across simulated tasks.

RED PHASE: These tests fail with AttributeError because _conn_str_var / _agent_id_var /
_retrieve_call_count_var do not yet exist on the agent_tools module (globals not yet
converted to ContextVars).  They will pass (GREEN) once the refactor lands in
agent_tools.py.
"""
from __future__ import annotations

import asyncio
import contextvars
from unittest.mock import MagicMock

import app.services.agent_tools as agent_tools

# ---------------------------------------------------------------------------
# Test 1: ContextVar propagation across asyncio.run()
# ---------------------------------------------------------------------------

def test_asyncio_run_propagation():
    """ContextVar set before asyncio.run(coro) is visible inside coro and its callees.

    Python 3.7+ guarantees that asyncio.run() inherits the caller's context via an
    implicit copy_context() on task creation.  This proves that values set by
    bind_tool_context (sync Celery task body) are visible inside run_agent_loop (async,
    entered via asyncio.run) — closing the RESEARCH.md Cluster 7 propagation question.
    """
    # Set a sentinel value in the current (sync) context.
    agent_tools._conn_str_var.set("postgresql://propagation-test/db")

    seen_value: list[str] = []

    async def _inner() -> None:
        # Reads the ContextVar from within the coroutine — must see the sentinel.
        seen_value.append(agent_tools._conn_str_var.get())

    asyncio.run(_inner())

    assert len(seen_value) == 1, "_inner() coroutine did not execute"
    assert seen_value[0] == "postgresql://propagation-test/db", (
        f"ContextVar propagation across asyncio.run() failed: "
        f"expected 'postgresql://propagation-test/db', got {seen_value[0]!r}"
    )


# ---------------------------------------------------------------------------
# Test 2: Two-context no-bleed (PROD-14 isolation invariant)
# ---------------------------------------------------------------------------

def test_two_context_no_bleed():
    """Two copy_context() runs see only their own _conn_str and _agent_id values.

    Simulates two interleaved Celery task contexts — the scenario that is UNSAFE
    when module globals are used with worker concurrency > 1.  With ContextVar,
    each context holds its own copy of every variable: mutations in context A do
    not affect context B and vice versa.
    """
    results: dict = {}

    def _run_task_a() -> None:
        agent_tools._conn_str_var.set("postgresql://tenant-A/db")
        agent_tools._agent_id_var.set("agent-A")
        agent_tools._retrieve_call_count_var.set(0)
        # Simulate one retrieve call
        count = agent_tools._retrieve_call_count_var.get() + 1
        agent_tools._retrieve_call_count_var.set(count)
        results["a_conn"] = agent_tools._conn_str_var.get()
        results["a_agent"] = agent_tools._agent_id_var.get()
        results["a_count"] = agent_tools._retrieve_call_count_var.get()

    def _run_task_b() -> None:
        agent_tools._conn_str_var.set("postgresql://tenant-B/db")
        agent_tools._agent_id_var.set("agent-B")
        agent_tools._retrieve_call_count_var.set(0)
        # Simulate two retrieve calls
        for _ in range(2):
            count = agent_tools._retrieve_call_count_var.get() + 1
            agent_tools._retrieve_call_count_var.set(count)
        results["b_conn"] = agent_tools._conn_str_var.get()
        results["b_agent"] = agent_tools._agent_id_var.get()
        results["b_count"] = agent_tools._retrieve_call_count_var.get()

    ctx_a = contextvars.copy_context()
    ctx_b = contextvars.copy_context()

    # Interleaved order: A then B — prove the second run doesn't see A's writes
    ctx_a.run(_run_task_a)
    ctx_b.run(_run_task_b)

    assert results["a_conn"] == "postgresql://tenant-A/db", (
        f"Context A conn_str bleed detected: got {results.get('a_conn')!r}"
    )
    assert results["b_conn"] == "postgresql://tenant-B/db", (
        f"Context B conn_str bleed detected: got {results.get('b_conn')!r}"
    )
    assert results["a_agent"] == "agent-A", (
        f"Context A agent_id bleed: got {results.get('a_agent')!r}"
    )
    assert results["b_agent"] == "agent-B", (
        f"Context B agent_id bleed: got {results.get('b_agent')!r}"
    )
    # Counter isolation: A ran 1 call, B ran 2 calls — neither sees the other's count
    assert results["a_count"] == 1, (
        f"Counter isolation failed for context A: expected 1, got {results.get('a_count')}"
    )
    assert results["b_count"] == 2, (
        f"Counter isolation failed for context B: expected 2, got {results.get('b_count')}"
    )


# ---------------------------------------------------------------------------
# Test 3: Retrieve counter isolation between contexts
# ---------------------------------------------------------------------------

def test_retrieve_counter_isolated():
    """Per-turn retrieve counter increments are fully isolated between contexts.

    Context X increments 3 times; Context Y increments 7 times.  Neither context
    observes the other's counter state — the counter resets per bind_tool_context call
    and is scoped to the task's ContextVar copy.  This is the T-13-07-02 (stale counter)
    mitigation check.
    """
    counts: dict[str, int] = {}

    def _increment_n(ctx_name: str, n: int) -> None:
        # Simulate bind_tool_context reset at turn start
        agent_tools._retrieve_call_count_var.set(0)
        for _ in range(n):
            current = agent_tools._retrieve_call_count_var.get()
            agent_tools._retrieve_call_count_var.set(current + 1)
        counts[ctx_name] = agent_tools._retrieve_call_count_var.get()

    ctx_x = contextvars.copy_context()
    ctx_y = contextvars.copy_context()

    ctx_x.run(_increment_n, "x", 3)
    ctx_y.run(_increment_n, "y", 7)

    assert counts["x"] == 3, (
        f"Counter isolation failed for context X: expected 3, got {counts.get('x')}"
    )
    assert counts["y"] == 7, (
        f"Counter isolation failed for context Y: expected 7, got {counts.get('y')}"
    )


# ---------------------------------------------------------------------------
# Tests 4-6: IDV-05 _verified_session_token_var ContextVar plumbing (17-03)
# ---------------------------------------------------------------------------

def test_verified_session_token_var_default_empty():
    """_verified_session_token_var defaults to '' in a fresh context.

    An empty-string default means 'no verified session — all non-IDV tool calls
    pass through' (IDV-05, Phase 17).  This test runs inside copy_context() to
    simulate a fresh Celery task context that has never called bind_tool_context.
    """
    result: list[str] = []

    def _read_default() -> None:
        result.append(agent_tools._verified_session_token_var.get())

    ctx = contextvars.copy_context()
    ctx.run(_read_default)

    assert len(result) == 1, "Reading ContextVar inside copy_context() did not execute"
    assert result[0] == "", (
        f"_verified_session_token_var default should be '' but got {result[0]!r}"
    )


def test_bind_tool_context_sets_verified_session_token():
    """bind_tool_context with verified_session_token='tok_abc' sets the ContextVar.

    The test calls bind_tool_context with the new kwarg and immediately reads
    _verified_session_token_var to confirm the token was threaded into the
    task-scoped ContextVar.  Uses the same MagicMock SDK server pattern as the
    module-level monkeypatch above (create_sdk_mcp_server is already patched).
    """

    result: list[str] = []

    def _run() -> None:
        agent_tools.bind_tool_context(
            conn_str="postgresql://test/db",
            agent_id="agent-idv-test",
            agent_name="IDV Test Agent",
            strategy=agent_tools.RetrievalStrategy(),
            conversation_id="conv-idv-test",
            notify_fn=MagicMock(),
            tenant_id="tenant-idv-test",
            verified_session_token="tok_abc",
        )
        result.append(agent_tools._verified_session_token_var.get())

    ctx = contextvars.copy_context()
    ctx.run(_run)

    assert len(result) == 1, "bind_tool_context context block did not execute"
    assert result[0] == "tok_abc", (
        f"Expected ContextVar to carry 'tok_abc', got {result[0]!r}"
    )


def test_default_empty_when_omitted():
    """bind_tool_context WITHOUT verified_session_token leaves ContextVar as ''.

    Proves backward compatibility: existing 4-arg dispatches (job_id, agent_id,
    message, conversation_id) do not need to supply the new param; the
    ContextVar stays at its empty-string default so non-IDV tool calls are
    unaffected (IDV-05, Phase 17).
    """

    result: list[str] = []

    def _run() -> None:
        agent_tools.bind_tool_context(
            conn_str="postgresql://test/db",
            agent_id="agent-compat-test",
            agent_name="Compat Test Agent",
            strategy=agent_tools.RetrievalStrategy(),
            conversation_id="conv-compat-test",
            notify_fn=MagicMock(),
            tenant_id="tenant-compat-test",
            # verified_session_token intentionally omitted
        )
        result.append(agent_tools._verified_session_token_var.get())

    ctx = contextvars.copy_context()
    ctx.run(_run)

    assert len(result) == 1, "bind_tool_context context block did not execute"
    assert result[0] == "", (
        f"Expected ContextVar to be '' when arg omitted, got {result[0]!r}"
    )
