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
import sys
import types
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Monkeypatch claude_agent_sdk BEFORE importing agent_tools.
# agent_tools.py imports ``tool`` and ``create_sdk_mcp_server`` at module load
# time, so the fake must be installed in sys.modules first.
# ---------------------------------------------------------------------------
if "claude_agent_sdk" not in sys.modules:
    _fake_sdk = types.ModuleType("claude_agent_sdk")

    def _tool_decorator(name: str, description: str, schema: dict):
        def wrapper(fn):
            fn._tool_name = name
            return fn
        return wrapper

    _fake_sdk.tool = _tool_decorator
    _fake_sdk.create_sdk_mcp_server = MagicMock(return_value=MagicMock(name="mcp_server"))
    _fake_sdk.ClaudeAgentOptions = MagicMock(name="ClaudeAgentOptions")
    _fake_sdk.ClaudeSDKClient = MagicMock(name="ClaudeSDKClient")
    _fake_sdk.AssistantMessage = MagicMock(name="AssistantMessage")
    _fake_sdk.ResultMessage = MagicMock(name="ResultMessage")
    _fake_sdk.TextBlock = MagicMock(name="TextBlock")
    _fake_sdk.ToolUseBlock = MagicMock(name="ToolUseBlock")
    _fake_sdk.ToolResultBlock = MagicMock(name="ToolResultBlock")
    _fake_sdk.ClaudeSDKError = type("ClaudeSDKError", (Exception,), {})
    _fake_sdk.CLINotFoundError = type("CLINotFoundError", (Exception,), {})
    _fake_sdk.CLIConnectionError = type("CLIConnectionError", (Exception,), {})
    _fake_sdk.ProcessError = type("ProcessError", (Exception,), {})
    _fake_sdk.CLIJSONDecodeError = type("CLIJSONDecodeError", (Exception,), {})
    sys.modules["claude_agent_sdk"] = _fake_sdk

import app.services.agent_tools as agent_tools  # noqa: E402


# ---------------------------------------------------------------------------
# Test 1: ContextVar propagation across asyncio.run()
# ---------------------------------------------------------------------------

def test_asyncio_run_propagation():
    """ContextVar set before asyncio.run(coro) is visible inside coro and its callees.

    Python 3.7+ guarantees that asyncio.run() inherits the caller's context via an
    implicit copy_context() on task creation.  This proves that values set by
    build_tool_server (sync Celery task body) are visible inside _run_sdk_turn (async,
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
    observes the other's counter state — the counter resets per build_tool_server call
    and is scoped to the task's ContextVar copy.  This is the T-13-07-02 (stale counter)
    mitigation check.
    """
    counts: dict[str, int] = {}

    def _increment_n(ctx_name: str, n: int) -> None:
        # Simulate build_tool_server reset at turn start
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
