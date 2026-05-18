"""
Unit tests for app.services.agent_tools.

Monkeypatches ``claude_agent_sdk`` before module import so tests run without
the SDK binary present. The tool functions are async so each test uses
``asyncio.run()``.

Test coverage:
  1. test_lookup_structured_rejects_non_allowlist_table
  2. test_lookup_structured_accepts_allowlist_table
  3. test_retrieve_truncates_to_max_chunks
  4. test_escalate_calls_notify_fn
  5. test_clarify_returns_question_text
  6. test_build_tool_server_sets_globals
  7. test_allowed_lookup_tables_is_frozenset
"""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Monkeypatch claude_agent_sdk BEFORE importing agent_tools.
# The module uses ``from claude_agent_sdk import tool, create_sdk_mcp_server``
# at import time, so the fake must be installed in sys.modules first.
# ---------------------------------------------------------------------------

def _make_passthrough_tool_decorator():
    """Return a @tool decorator that wraps the async function transparently."""
    def tool_decorator(name: str, description: str, input_schema: dict):
        def wrapper(fn):
            # Attach metadata so tests can inspect if needed; return fn as-is.
            fn._tool_name = name
            fn._tool_description = description
            fn._tool_schema = input_schema
            return fn
        return wrapper
    return tool_decorator


def _make_fake_sdk():
    """Build a minimal fake claude_agent_sdk module."""
    fake = types.ModuleType("claude_agent_sdk")
    fake.tool = _make_passthrough_tool_decorator()
    fake.create_sdk_mcp_server = MagicMock(return_value=MagicMock(name="mcp_server"))
    return fake


# Install the fake only if the real SDK is not installed.
if "claude_agent_sdk" not in sys.modules:
    sys.modules["claude_agent_sdk"] = _make_fake_sdk()

# Now it's safe to import agent_tools.
import app.services.agent_tools as agent_tools  # noqa: E402  (after monkeypatch)
from app.services.agent_tools import (  # noqa: E402
    ALLOWED_LOOKUP_TABLES,
    MAX_CHUNKS,
    MAX_CHUNK_TOKENS,
    build_tool_server,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run a coroutine synchronously — pytest-asyncio not needed."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Test 1: lookup_structured rejects non-allowlisted table
# ---------------------------------------------------------------------------


def test_lookup_structured_rejects_non_allowlist_table():
    """Table 'users' is not in ALLOWED_LOOKUP_TABLES — must return is_error=True
    and must NOT call psycopg2.connect."""
    with patch("psycopg2.connect") as mock_connect:
        result = _run(agent_tools.lookup_structured_tool({"table": "users", "filters": {}}))

    assert result.get("is_error") is True
    content_text = result["content"][0]["text"]
    assert "users" in content_text
    assert "not accessible" in content_text.lower() or "not allowed" in content_text.lower() or "not accessible" in content_text.lower()
    mock_connect.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: lookup_structured accepts allowlisted table
# ---------------------------------------------------------------------------


def test_lookup_structured_accepts_allowlist_table():
    """Table 'chunks' IS in ALLOWED_LOOKUP_TABLES — psycopg2.connect must be called."""
    # Set a dummy connection string so the code can try to connect.
    agent_tools._conn_str = "postgresql://test:test@localhost/testdb"

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchall.return_value = [(1, "chunk content")]
    mock_conn.cursor.return_value = mock_cursor

    with patch("psycopg2.connect", return_value=mock_conn) as mock_connect:
        result = _run(
            agent_tools.lookup_structured_tool({"table": "chunks", "filters": {"document_id": "abc"}})
        )

    mock_connect.assert_called_once()
    assert result.get("is_error") is not True


# ---------------------------------------------------------------------------
# Test 3: retrieve truncates to MAX_CHUNKS with content cap
# ---------------------------------------------------------------------------


def test_retrieve_truncates_to_max_chunks():
    """retrieve tool must return at most MAX_CHUNKS chunks, each content ≤ 2000 chars."""
    # Produce 20 chunks with 5000-char content each.
    long_content = "x" * 5000
    fake_chunks = [
        {
            "chunk_id": str(i),
            "content": long_content,
            "document_id": "doc-1",
            "rrf_score": 0.9 - i * 0.01,
        }
        for i in range(20)
    ]

    fake_rrf_result = {
        "fused": fake_chunks,
        "vector_candidates": [],
        "bm25_candidates": [],
    }

    with (
        patch("app.services.agent_tools.embed_query", return_value=[0.1] * 1024),
        patch("app.services.agent_tools.rrf_fuse", return_value=fake_rrf_result),
        patch("app.services.agent_tools.rerank", return_value=fake_chunks),
    ):
        result = _run(agent_tools.retrieve_tool({"query": "test query", "filters": []}))

    # Extract returned chunks from the text field.
    text = result["content"][0]["text"]

    # The result must have at most MAX_CHUNKS chunks.
    # Verify by checking _citations field (one per chunk).
    citations = result.get("_citations", [])
    assert len(citations) <= MAX_CHUNKS, (
        f"Expected at most {MAX_CHUNKS} chunks, got {len(citations)}"
    )

    # Each content string must be ≤ 2000 chars.
    # The truncation is applied before returning; verify via the raw text length.
    # We use the "x"*5000 sentinel: if truncation works, it becomes "x"*2000.
    assert "x" * 2001 not in text, "Content was not truncated to 2000 chars"


# ---------------------------------------------------------------------------
# Test 4: escalate calls notify_fn
# ---------------------------------------------------------------------------


def test_escalate_calls_notify_fn():
    """escalate_to_human must call _notify_fn with reason and context."""
    notify_fn = MagicMock()

    # Set module globals directly (same as build_tool_server does).
    agent_tools._notify_fn = notify_fn
    agent_tools._conversation_id = "conv-test-123"
    agent_tools._conn_str = "postgresql://test:test@localhost/testdb"

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    with patch("psycopg2.connect", return_value=mock_conn):
        result = _run(
            agent_tools.escalate_to_human_tool(
                {"reason": "Customer frustrated", "context": "Order delayed 3 weeks"}
            )
        )

    # F3: reason is now prefixed with [AGENT-DETECTED — UNVERIFIED] before notify_fn.
    notify_fn.assert_called_once_with(
        "[AGENT-DETECTED — UNVERIFIED] Customer frustrated",
        "Order delayed 3 weeks",
    )
    assert "content" in result


# ---------------------------------------------------------------------------
# Test 5: clarify returns the question text
# ---------------------------------------------------------------------------


def test_clarify_returns_question_text():
    """clarify tool must return the question verbatim in content text."""
    result = _run(agent_tools.clarify_tool({"question": "Which size?"}))

    assert result["content"][0]["text"] == "Which size?"


# ---------------------------------------------------------------------------
# Test 6: build_tool_server sets module globals
# ---------------------------------------------------------------------------


def test_build_tool_server_sets_globals():
    """build_tool_server must propagate all six arguments to module-level globals."""
    from app.services.retrieval_service import RetrievalStrategy

    sentinel_conn = "postgresql://sentinel:pass@host/db"
    sentinel_agent_id = "agent-sentinel-id"
    sentinel_agent_name = "Sentinel Bot"
    sentinel_strategy = RetrievalStrategy.model_validate({})
    sentinel_conv_id = "conv-sentinel-456"
    sentinel_notify = MagicMock()

    build_tool_server(
        conn_str=sentinel_conn,
        agent_id=sentinel_agent_id,
        agent_name=sentinel_agent_name,
        strategy=sentinel_strategy,
        conversation_id=sentinel_conv_id,
        notify_fn=sentinel_notify,
    )

    assert agent_tools._conn_str == sentinel_conn
    assert agent_tools._agent_id == sentinel_agent_id
    assert agent_tools._agent_name == sentinel_agent_name
    assert agent_tools._strategy is sentinel_strategy
    assert agent_tools._conversation_id == sentinel_conv_id
    assert agent_tools._notify_fn is sentinel_notify


# ---------------------------------------------------------------------------
# Test 7: ALLOWED_LOOKUP_TABLES is a frozenset with exact membership
# ---------------------------------------------------------------------------


def test_allowed_lookup_tables_is_frozenset():
    """ALLOWED_LOOKUP_TABLES must be a frozenset with exactly the three allowed tables."""
    assert isinstance(ALLOWED_LOOKUP_TABLES, frozenset)
    assert ALLOWED_LOOKUP_TABLES == frozenset({"chunks", "documents", "chunk_metadata"})


# ---------------------------------------------------------------------------
# Test 8: F1 — lookup_structured rejects unknown column (SQL injection block)
# ---------------------------------------------------------------------------


def test_lookup_structured_rejects_unknown_column():
    """Unknown/injected column names must return is_error=True with no DB call."""
    with patch("psycopg2.connect") as mock_connect:
        result = _run(
            agent_tools.lookup_structured_tool(
                {"table": "chunks", "filters": {"evil_col; DROP TABLE": "x"}}
            )
        )

    assert result.get("is_error") is True
    content_text = result["content"][0]["text"]
    assert "not allowed" in content_text.lower()
    mock_connect.assert_not_called()


# ---------------------------------------------------------------------------
# Test 9: F1 — lookup_structured allows known columns
# ---------------------------------------------------------------------------


def test_lookup_structured_allows_known_columns():
    """Filters with known column names must reach the DB (no is_error)."""
    agent_tools._conn_str = "postgresql://test:test@localhost/testdb"

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchall.return_value = [("row1",)]
    mock_conn.cursor.return_value = mock_cursor

    with patch("psycopg2.connect", return_value=mock_conn) as mock_connect:
        result = _run(
            agent_tools.lookup_structured_tool(
                {"table": "chunks", "filters": {"id": "abc"}}
            )
        )

    mock_connect.assert_called_once()
    assert result.get("is_error") is not True


# ---------------------------------------------------------------------------
# Test 10: F1 — lookup_structured uses psycopg2.sql.Identifier for quoting
# ---------------------------------------------------------------------------


def test_lookup_structured_sql_identifier_quoting():
    """Allowed columns must be quoted via pgsql.Identifier, not f-string interpolation."""
    from psycopg2 import sql as pgsql

    agent_tools._conn_str = "postgresql://test:test@localhost/testdb"

    # Track the sql arg passed to cur.execute
    executed_sqls = []

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchall.return_value = []

    def capture_execute(sql, params=None):
        executed_sqls.append(sql)

    mock_cursor.execute.side_effect = capture_execute
    mock_conn.cursor.return_value = mock_cursor

    with patch("psycopg2.connect", return_value=mock_conn):
        result = _run(
            agent_tools.lookup_structured_tool(
                {"table": "documents", "filters": {"name": "test"}}
            )
        )

    assert result.get("is_error") is not True
    assert len(executed_sqls) > 0, "cur.execute was never called"
    # The WHERE clause must include at least one pgsql.Composed or pgsql.SQL object,
    # i.e. not a plain raw f-string with the column name embedded literally.
    sql_arg = executed_sqls[0]
    assert isinstance(sql_arg, (pgsql.Composed, pgsql.SQL)), (
        f"Expected psycopg2.sql.Composed or SQL, got {type(sql_arg)}: {sql_arg!r}"
    )


# ---------------------------------------------------------------------------
# Test 11: F3 — escalate_to_human is idempotent (second call skips notify_fn)
# ---------------------------------------------------------------------------


def test_escalate_to_human_idempotent():
    """Second escalation call on already-escalated conversation must not fire _notify_fn again."""
    notify_fn = MagicMock()

    agent_tools._notify_fn = notify_fn
    agent_tools._conversation_id = "conv-idempotent-1"
    agent_tools._conn_str = "postgresql://test:test@localhost/testdb"
    agent_tools._agent_id = "agent-abc"

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    # First call: rowcount=1 (row updated — not yet escalated)
    mock_cursor.rowcount = 1
    with patch("psycopg2.connect", return_value=mock_conn):
        _run(
            agent_tools.escalate_to_human_tool(
                {"reason": "Angry customer", "context": "Some context"}
            )
        )

    # Second call: rowcount=0 (already escalated — idempotency guard fires)
    mock_cursor.rowcount = 0
    with patch("psycopg2.connect", return_value=mock_conn):
        result = _run(
            agent_tools.escalate_to_human_tool(
                {"reason": "Angry customer", "context": "Some context"}
            )
        )

    assert result.get("already_escalated") is True, (
        "Expected already_escalated=True on second call"
    )
    assert notify_fn.call_count == 1, (
        f"_notify_fn should be called exactly once, got {notify_fn.call_count}"
    )


# ---------------------------------------------------------------------------
# Test 12: F3 — escalate_to_human sanitises control characters in reason
# ---------------------------------------------------------------------------


def test_escalate_to_human_sanitises_reason():
    """Control characters in reason must be stripped before passing to _notify_fn."""
    notify_fn = MagicMock()

    agent_tools._notify_fn = notify_fn
    agent_tools._conversation_id = "conv-sanitise-1"
    agent_tools._conn_str = "postgresql://test:test@localhost/testdb"
    agent_tools._agent_id = "agent-abc"

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.rowcount = 1
    mock_conn.cursor.return_value = mock_cursor

    dirty_reason = f"Angry{chr(0)}Customer{chr(31)}Now"

    with patch("psycopg2.connect", return_value=mock_conn):
        _run(
            agent_tools.escalate_to_human_tool(
                {"reason": dirty_reason, "context": "Normal context"}
            )
        )

    notify_fn.assert_called_once()
    called_reason = notify_fn.call_args[0][0]  # first positional arg
    assert chr(0) not in called_reason, "NULL byte must be stripped from reason"
    assert chr(31) not in called_reason, "Control char 0x1f must be stripped from reason"


# ---------------------------------------------------------------------------
# Test 13: F3 — escalate_to_human truncates reason at 500 chars
# ---------------------------------------------------------------------------


def test_escalate_to_human_reason_truncated_at_500():
    """Reason longer than 500 chars must be truncated; notify_fn payload ≤ prefix + 500."""
    notify_fn = MagicMock()

    agent_tools._notify_fn = notify_fn
    agent_tools._conversation_id = "conv-truncate-1"
    agent_tools._conn_str = "postgresql://test:test@localhost/testdb"
    agent_tools._agent_id = "agent-abc"

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.rowcount = 1
    mock_conn.cursor.return_value = mock_cursor

    long_reason = "A" * 600

    with patch("psycopg2.connect", return_value=mock_conn):
        _run(
            agent_tools.escalate_to_human_tool(
                {"reason": long_reason, "context": "Normal context"}
            )
        )

    notify_fn.assert_called_once()
    called_reason = notify_fn.call_args[0][0]
    # The prefix is "[AGENT-DETECTED — UNVERIFIED] " — extract the payload portion after it
    prefix = "[AGENT-DETECTED — UNVERIFIED] "
    assert called_reason.startswith(prefix), (
        f"Reason must be prefixed with {prefix!r}, got: {called_reason[:80]!r}"
    )
    payload = called_reason[len(prefix):]
    assert len(payload) <= 500, (
        f"Payload segment must be at most 500 chars, got {len(payload)}"
    )


# ---------------------------------------------------------------------------
# Test 14: F7 — retrieve_tool logs warning when filters are present
# ---------------------------------------------------------------------------


def test_retrieve_tool_logs_warning_on_unused_filters():
    """retrieve_tool must emit log.warning('retrieve_tool.filters_ignored') when filters given."""
    with (
        patch("app.services.agent_tools.embed_query", return_value=[0.1] * 1024),
        patch(
            "app.services.agent_tools.rrf_fuse",
            return_value={"fused": [], "vector_candidates": [], "bm25_candidates": []},
        ),
        patch("app.services.agent_tools.rerank", return_value=[]),
        patch("app.services.agent_tools.log") as mock_log,
    ):
        _run(
            agent_tools.retrieve_tool(
                {"query": "test", "filters": [{"document_id": "abc"}]}
            )
        )

    # Find the warning call with event="retrieve_tool.filters_ignored"
    warning_events = [
        call.args[0]
        for call in mock_log.warning.call_args_list
        if call.args and call.args[0] == "retrieve_tool.filters_ignored"
    ]
    assert len(warning_events) >= 1, (
        "Expected log.warning('retrieve_tool.filters_ignored') to be called"
    )
