"""
Unit tests for app.services.agent_tools.

Monkeypatches ``claude_agent_sdk`` before module import so tests run without
the SDK binary present. The tool functions are async so each test uses
``asyncio.run()``.

Updated for PROD-14 ContextVar refactor: module-level globals (_conn_str,
_agent_id, etc.) are now ContextVars (_conn_str_var, _agent_id_var, etc.).
Tests that previously set globals directly now call .set() on the ContextVars;
assertions that previously read globals now call .get() on the ContextVars.

Test coverage:
  1. test_lookup_structured_rejects_non_allowlist_table
  2. test_lookup_structured_accepts_allowlist_table
  3. test_retrieve_truncates_to_max_chunks
  4. test_escalate_calls_notify_fn
  5. test_clarify_returns_question_text
  6. test_build_tool_server_sets_globals
  7. test_allowed_lookup_tables_is_frozenset
  ...
  15. test_retrieve_tool_blocked_on_third_call  (D-10 suspenders)
  16. test_retrieve_tool_counter_reset_by_build_tool_server  (D-10 suspenders)
"""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import MagicMock, patch

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
    """Build a fake claude_agent_sdk module with all attrs needed by agent_tools and agent.py."""
    fake = types.ModuleType("claude_agent_sdk")
    fake.tool = _make_passthrough_tool_decorator()
    fake.create_sdk_mcp_server = MagicMock(return_value=MagicMock(name="mcp_server"))
    # Attributes required by app.worker.tasks.runtime.agent at module import time.
    # Without these, any test that indirectly imports agent.py after this fake is
    # installed will fail with ImportError: cannot import name 'ClaudeAgentOptions'.
    fake.ClaudeAgentOptions = MagicMock(name="ClaudeAgentOptions")
    fake.ClaudeSDKClient = MagicMock(name="ClaudeSDKClient")
    fake.AssistantMessage = MagicMock(name="AssistantMessage")
    fake.ResultMessage = MagicMock(name="ResultMessage")
    fake.TextBlock = MagicMock(name="TextBlock")
    fake.ToolUseBlock = MagicMock(name="ToolUseBlock")
    fake.ToolResultBlock = MagicMock(name="ToolResultBlock")
    fake.ClaudeSDKError = type("ClaudeSDKError", (Exception,), {})
    fake.CLINotFoundError = type("CLINotFoundError", (Exception,), {})
    fake.CLIConnectionError = type("CLIConnectionError", (Exception,), {})
    fake.ProcessError = type("ProcessError", (Exception,), {})
    fake.CLIJSONDecodeError = type("CLIJSONDecodeError", (Exception,), {})
    return fake


# Install the fake only if the real SDK is not installed.
if "claude_agent_sdk" not in sys.modules:
    sys.modules["claude_agent_sdk"] = _make_fake_sdk()

# Now it's safe to import agent_tools.
import app.services.agent_tools as agent_tools  # noqa: E402  (after monkeypatch)
from app.services.agent_tools import (  # noqa: E402
    ALLOWED_LOOKUP_TABLES,
    MAX_CHUNKS,
    build_tool_server,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run a coroutine synchronously — pytest-asyncio not needed."""
    return asyncio.run(coro)


def _fn(tool_obj):
    """Resolve a @tool-decorated tool to its async callable.

    The fake SDK's passthrough decorator returns the function itself, but the
    REAL claude_agent_sdk's @tool returns an ``SdkMcpTool`` dataclass, which is
    not callable — its async function lives on ``.handler``. Which shape we get
    depends on test-module ordering: the ``if "claude_agent_sdk" not in
    sys.modules`` guard above deliberately does not clobber an already-imported
    real SDK, and modules such as test_agent_chat_routes.py import ``app.main``
    (pulling in the real SDK) before this module loads. Resolving both shapes
    here makes these tests order-independent instead of silently depending on
    winning that race.
    """
    return getattr(tool_obj, "handler", tool_obj)


def _long_chunk_context(content: str, n: int = 20):
    """20 fused chunks sharing one long body, the truncation fixture."""
    from app.domain.retrieved_context import RetrievedChunk, RetrievedContext

    return RetrievedContext(
        query="test query",
        chunks=tuple(
            RetrievedChunk(
                chunk_id=str(i),
                document_id="doc-1",
                content=content,
                score=0.9 - i * 0.01,
                rank=i + 1,
            )
            for i in range(n)
        ),
        strategy="rrf",
    )


def _rrf_result(fused):
    """The three-key dict rrf_fuse returns, with both candidate lists empty."""
    from app.domain.retrieved_context import RetrievedContext

    return {
        "fused": fused,
        "vector_candidates": RetrievedContext(
            query=fused.query, chunks=(), strategy="vector"
        ),
        "bm25_candidates": RetrievedContext(
            query=fused.query, chunks=(), strategy="bm25"
        ),
    }


# ---------------------------------------------------------------------------
# Test 1: lookup_structured rejects non-allowlisted table
# ---------------------------------------------------------------------------


def test_lookup_structured_rejects_non_allowlist_table():
    """Table 'users' is not in ALLOWED_LOOKUP_TABLES — must return is_error=True
    and must NOT call psycopg2.connect."""
    with patch("psycopg2.connect") as mock_connect:
        result = _run(_fn(agent_tools.lookup_structured_tool)({"table": "users", "filters": {}}))

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
    # Set conn_str via ContextVar (PROD-14: replaced module global).
    agent_tools._conn_str_var.set("postgresql://test:test@localhost/testdb")

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchall.return_value = [(1, "chunk content")]
    mock_conn.cursor.return_value = mock_cursor

    with patch("psycopg2.connect", return_value=mock_conn) as mock_connect:
        result = _run(
            _fn(agent_tools.lookup_structured_tool)({"table": "chunks", "filters": {"document_id": "abc"}})
        )

    mock_connect.assert_called_once()
    assert result.get("is_error") is not True


# ---------------------------------------------------------------------------
# Test 3: retrieve truncates to MAX_CHUNKS with content cap
# ---------------------------------------------------------------------------


def test_retrieve_truncates_to_max_chunks():
    """retrieve tool must return at most MAX_CHUNKS chunks, each content <= 2000 chars."""
    # Reset counter so DoS guard does not interfere (PROD-14: ContextVar-backed).
    agent_tools._retrieve_call_count_var.set(0)

    # Produce 20 chunks with 5000-char content each.
    long_content = "x" * 5000
    fake_context = _long_chunk_context(long_content)
    fake_chunks = fake_context.chunks

    fake_rrf_result = _rrf_result(fake_context)

    with (
        patch("app.services.agent_tools.embed_query", return_value=[0.1] * 1024),
        patch("app.services.agent_tools.rrf_fuse", return_value=fake_rrf_result),
        patch("app.services.agent_tools.rerank", return_value=fake_context),
        # OPS-05/06 (21-03): retrieve_tool now writes a retrieval_metrics row;
        # patch it out so this test never attempts a real DB connection.
        patch("app.services.agent_tools.write_retrieval_metrics"),
    ):
        result = _run(_fn(agent_tools.retrieve_tool)({"query": "test query", "filters": []}))

    # Extract returned chunks from the text field.
    text = result["content"][0]["text"]

    # The result must have at most MAX_CHUNKS chunks.
    # Verify by checking _citations field (one per chunk).
    citations = result.get("_citations", [])
    assert len(citations) <= MAX_CHUNKS, (
        f"Expected at most {MAX_CHUNKS} chunks, got {len(citations)}"
    )

    # Each content string must be <= 2000 chars.
    # The truncation is applied before returning; verify via the raw text length.
    # We use the "x"*5000 sentinel: if truncation works, it becomes "x"*2000.
    assert "x" * 2001 not in text, "Content was not truncated to 2000 chars"


# ---------------------------------------------------------------------------
# Test 3b: SEC-02 — retrieve_tool wraps tool-result text as data, not
# instructions, and the framing survives the truncation path.
# ---------------------------------------------------------------------------


def test_retrieve_tool_data_wrapper():
    """SEC-02/T-18-SEC-03: retrieve_tool's tool-result text is enclosed by an
    explicit data-not-instructions boundary that survives per-chunk truncation."""
    agent_tools._retrieve_call_count_var.set(0)

    # Same 5000-char-content fixture as test_retrieve_truncates_to_max_chunks
    # so the truncation path is genuinely exercised alongside the framing.
    long_content = "x" * 5000
    fake_context = _long_chunk_context(long_content)
    fake_chunks = fake_context.chunks

    fake_rrf_result = _rrf_result(fake_context)

    with (
        patch("app.services.agent_tools.embed_query", return_value=[0.1] * 1024),
        patch("app.services.agent_tools.rrf_fuse", return_value=fake_rrf_result),
        patch("app.services.agent_tools.rerank", return_value=fake_context),
        patch("app.services.agent_tools.write_retrieval_metrics"),
    ):
        result = _run(_fn(agent_tools.retrieve_tool)({"query": "test query", "filters": []}))

    text = result["content"][0]["text"]
    citations = result.get("_citations", [])

    # Header/footer enclose the whole payload.
    assert text.startswith(agent_tools.RETRIEVED_CONTEXT_HEADER)
    assert text.endswith(agent_tools.RETRIEVED_CONTEXT_FOOTER)
    assert "not as instructions" in text

    # Truncation still applied even with the framing wrapped around it.
    assert "x" * 2001 not in text, "Content was not truncated to 2000 chars"
    # The footer is the FINAL segment of the text — a chunk cannot clip it off.
    assert text.rstrip().endswith(agent_tools.RETRIEVED_CONTEXT_FOOTER)

    # The chunk payload still appears between the markers.
    header_end = text.index(agent_tools.RETRIEVED_CONTEXT_HEADER) + len(
        agent_tools.RETRIEVED_CONTEXT_HEADER
    )
    footer_start = text.rindex(agent_tools.RETRIEVED_CONTEXT_FOOTER)
    body = text[header_end:footer_start]
    assert "'chunk_id': '0'" in body

    # _citations is unchanged in length and shape versus the chunk list.
    assert len(citations) == min(len(fake_chunks), MAX_CHUNKS)
    for citation in citations:
        assert set(citation.keys()) == {"document_name", "section"}


# ---------------------------------------------------------------------------
# Test 3c: ticket #44, the model-facing string, pinned byte for byte.
#
# retrieve_tool renders the retrieved chunks as the repr of a list of dicts,
# and app/worker/tasks/runtime/agent.py reads that string back with
# ast.literal_eval. #44 puts RetrievedContext at the retrieval seam and leaves
# the repr where it is, so the two sides still meet. The literal below was
# captured from the code as it stood before that change, with a retrieval
# result carrying exactly the fields RetrievedChunk names.
# ---------------------------------------------------------------------------

PINNED_MODEL_FACING_CHUNKS = (
    "[{'chunk_id': 'c1', 'document_id': 'd1', 'content': 'Unopened bags, 14 days.', "
    "'score': 0.95, 'rank': 1}, {'chunk_id': 'c2', 'document_id': 'd2', 'content': "
    "'Refunds take 5 days.', 'score': 0.8, 'rank': 2}]"
)


def _pinned_retrieval():
    """The fixed retrieval result the pin is measured against."""
    from app.domain.retrieved_context import RetrievedChunk, RetrievedContext

    query = "what is the return window?"
    chunks = (
        RetrievedChunk(
            chunk_id="c1",
            document_id="d1",
            content="Unopened bags, 14 days.",
            score=0.95,
            rank=1,
        ),
        RetrievedChunk(
            chunk_id="c2",
            document_id="d2",
            content="Refunds take 5 days.",
            score=0.8,
            rank=2,
        ),
    )
    fused = RetrievedContext(query=query, chunks=chunks, strategy="rrf")
    reranked = RetrievedContext(query=query, chunks=chunks, strategy="rerank")
    rrf_result = {
        "fused": fused,
        "vector_candidates": RetrievedContext(query=query, chunks=(), strategy="vector"),
        "bm25_candidates": RetrievedContext(query=query, chunks=(), strategy="bm25"),
    }
    return query, rrf_result, reranked


def test_retrieve_tool_model_string_is_byte_for_byte_the_pinned_repr():
    """A fixed retrieval result renders the exact string it rendered before #44."""
    agent_tools._retrieve_call_count_var.set(0)
    query, rrf_result, reranked = _pinned_retrieval()

    with (
        patch("app.services.agent_tools.embed_query", return_value=[0.1] * 8),
        patch("app.services.agent_tools.rrf_fuse", return_value=rrf_result),
        patch("app.services.agent_tools.rerank", return_value=reranked),
        patch("app.services.agent_tools.write_retrieval_metrics"),
    ):
        result = _run(_fn(agent_tools.retrieve_tool)({"query": query}))

    text = result["content"][0]["text"]
    header_end = text.index(agent_tools.RETRIEVED_CONTEXT_HEADER) + len(
        agent_tools.RETRIEVED_CONTEXT_HEADER
    )
    body = text[header_end:text.rindex(agent_tools.RETRIEVED_CONTEXT_FOOTER)]

    assert body.strip() == PINNED_MODEL_FACING_CHUNKS
    assert text == agent_tools._frame_retrieved_context(PINNED_MODEL_FACING_CHUNKS)


def test_retrieve_tool_model_string_still_parses_the_way_agent_py_parses_it():
    """ast.literal_eval is the parser on the other side; it must still read this."""
    import ast

    agent_tools._retrieve_call_count_var.set(0)
    query, rrf_result, reranked = _pinned_retrieval()

    with (
        patch("app.services.agent_tools.embed_query", return_value=[0.1] * 8),
        patch("app.services.agent_tools.rrf_fuse", return_value=rrf_result),
        patch("app.services.agent_tools.rerank", return_value=reranked),
        patch("app.services.agent_tools.write_retrieval_metrics"),
    ):
        result = _run(_fn(agent_tools.retrieve_tool)({"query": query}))

    text = result["content"][0]["text"]
    header_end = text.index(agent_tools.RETRIEVED_CONTEXT_HEADER) + len(
        agent_tools.RETRIEVED_CONTEXT_HEADER
    )
    body = text[header_end:text.rindex(agent_tools.RETRIEVED_CONTEXT_FOOTER)]

    records = ast.literal_eval(body.strip())
    assert [record["content"] for record in records] == [
        "Unopened bags, 14 days.",
        "Refunds take 5 days.",
    ]
    assert [record["document_id"] for record in records] == ["d1", "d2"]


def test_frame_retrieved_context_idempotent_safe():
    """_frame_retrieved_context always produces exactly one header and one footer."""
    framed = agent_tools._frame_retrieved_context("some retrieved chunk text")
    assert framed.count(agent_tools.RETRIEVED_CONTEXT_HEADER) == 1
    assert framed.count(agent_tools.RETRIEVED_CONTEXT_FOOTER) == 1
    assert framed.startswith(agent_tools.RETRIEVED_CONTEXT_HEADER)
    assert framed.endswith(agent_tools.RETRIEVED_CONTEXT_FOOTER)


# ---------------------------------------------------------------------------
# Test 4: escalate calls notify_fn
# ---------------------------------------------------------------------------


def test_escalate_calls_notify_fn():
    """escalate_to_human must call _notify_fn with reason and context."""
    notify_fn = MagicMock()

    # Set module state via ContextVars (PROD-14: replaced module globals).
    agent_tools._notify_fn_var.set(notify_fn)
    agent_tools._conversation_id_var.set("conv-test-123")
    agent_tools._conn_str_var.set("postgresql://test:test@localhost/testdb")

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    with patch("psycopg2.connect", return_value=mock_conn):
        result = _run(
            _fn(agent_tools.escalate_to_human_tool)(
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
    result = _run(_fn(agent_tools.clarify_tool)({"question": "Which size?"}))

    assert result["content"][0]["text"] == "Which size?"


# ---------------------------------------------------------------------------
# Test 6: build_tool_server sets ContextVars
# ---------------------------------------------------------------------------


def test_build_tool_server_sets_globals():
    """build_tool_server must propagate all six arguments to ContextVars (PROD-14)."""
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

    # Read back via ContextVar.get() (PROD-14: replaced direct module global reads).
    assert agent_tools._conn_str_var.get() == sentinel_conn
    assert agent_tools._agent_id_var.get() == sentinel_agent_id
    assert agent_tools._agent_name_var.get() == sentinel_agent_name
    assert agent_tools._strategy_var.get() is sentinel_strategy
    assert agent_tools._conversation_id_var.get() == sentinel_conv_id
    assert agent_tools._notify_fn_var.get() is sentinel_notify


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
            _fn(agent_tools.lookup_structured_tool)(
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
    agent_tools._conn_str_var.set("postgresql://test:test@localhost/testdb")

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchall.return_value = [("row1",)]
    mock_conn.cursor.return_value = mock_cursor

    with patch("psycopg2.connect", return_value=mock_conn) as mock_connect:
        result = _run(
            _fn(agent_tools.lookup_structured_tool)(
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

    agent_tools._conn_str_var.set("postgresql://test:test@localhost/testdb")

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
            _fn(agent_tools.lookup_structured_tool)(
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

    agent_tools._notify_fn_var.set(notify_fn)
    agent_tools._conversation_id_var.set("conv-idempotent-1")
    agent_tools._conn_str_var.set("postgresql://test:test@localhost/testdb")
    agent_tools._agent_id_var.set("agent-abc")

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    # First call: rowcount=1 (row updated — not yet escalated)
    mock_cursor.rowcount = 1
    with patch("psycopg2.connect", return_value=mock_conn):
        _run(
            _fn(agent_tools.escalate_to_human_tool)(
                {"reason": "Angry customer", "context": "Some context"}
            )
        )

    # Second call: rowcount=0 (already escalated — idempotency guard fires)
    mock_cursor.rowcount = 0
    with patch("psycopg2.connect", return_value=mock_conn):
        result = _run(
            _fn(agent_tools.escalate_to_human_tool)(
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

    agent_tools._notify_fn_var.set(notify_fn)
    agent_tools._conversation_id_var.set("conv-sanitise-1")
    agent_tools._conn_str_var.set("postgresql://test:test@localhost/testdb")
    agent_tools._agent_id_var.set("agent-abc")

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.rowcount = 1
    mock_conn.cursor.return_value = mock_cursor

    dirty_reason = f"Angry{chr(0)}Customer{chr(31)}Now"

    with patch("psycopg2.connect", return_value=mock_conn):
        _run(
            _fn(agent_tools.escalate_to_human_tool)(
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
    """Reason longer than 500 chars must be truncated; notify_fn payload <= prefix + 500."""
    notify_fn = MagicMock()

    agent_tools._notify_fn_var.set(notify_fn)
    agent_tools._conversation_id_var.set("conv-truncate-1")
    agent_tools._conn_str_var.set("postgresql://test:test@localhost/testdb")
    agent_tools._agent_id_var.set("agent-abc")

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.rowcount = 1
    mock_conn.cursor.return_value = mock_cursor

    long_reason = "A" * 600

    with patch("psycopg2.connect", return_value=mock_conn):
        _run(
            _fn(agent_tools.escalate_to_human_tool)(
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
    # Reset counter so DoS guard does not interfere (PROD-14: ContextVar-backed).
    agent_tools._retrieve_call_count_var.set(0)

    with (
        patch("app.services.agent_tools.embed_query", return_value=[0.1] * 1024),
        patch(
            "app.services.agent_tools.rrf_fuse",
            return_value=_rrf_result(_long_chunk_context("body", n=0)),
        ),
        patch(
            "app.services.agent_tools.rerank",
            return_value=_long_chunk_context("body", n=0),
        ),
        # OPS-05/06 (21-03): retrieve_tool now writes a retrieval_metrics row;
        # patch it out so this test never attempts a real DB connection.
        patch("app.services.agent_tools.write_retrieval_metrics"),
        patch("app.services.agent_tools.log") as mock_log,
    ):
        _run(
            _fn(agent_tools.retrieve_tool)(
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


# ---------------------------------------------------------------------------
# Test 15: D-10 (suspenders) — retrieve_tool blocks when cap is exceeded
# ---------------------------------------------------------------------------


def test_retrieve_tool_blocked_on_third_call():
    """D-10 suspenders: a retrieve call that exceeds _RETRIEVE_CALLS_PER_TURN_MAX must
    return is_error=True.

    The tool-level counter (_retrieve_call_count_var) is incremented on each call
    and blocks when it exceeds _RETRIEVE_CALLS_PER_TURN_MAX.  This ensures the DoS
    ceiling is enforced regardless of max_turns setting.
    """
    # Set the counter to the max so the next call is the blocked one.
    # PROD-14: ContextVar-backed counter, set via .set() not direct assignment.
    agent_tools._retrieve_call_count_var.set(agent_tools._RETRIEVE_CALLS_PER_TURN_MAX)

    with (
        patch("app.services.agent_tools.embed_query") as mock_embed,
        patch("app.services.agent_tools.rrf_fuse") as mock_rrf,
        patch("app.services.agent_tools.rerank") as mock_rerank,
    ):
        result = _run(_fn(agent_tools.retrieve_tool)({"query": "capped call query"}))

    # Must be blocked — embed/rrf/rerank must NOT have been called.
    mock_embed.assert_not_called()
    mock_rrf.assert_not_called()
    mock_rerank.assert_not_called()

    assert result.get("is_error") is True, (
        f"Expected is_error=True on capped retrieve call, got: {result}"
    )
    content_text = result["content"][0]["text"]
    assert "quota" in content_text.lower() or "cap" in content_text.lower() or "exceeded" in content_text.lower(), (
        f"Expected quota/cap message in blocked retrieve result, got: {content_text!r}"
    )


# ---------------------------------------------------------------------------
# Test 16: D-10 (suspenders) — build_tool_server resets the retrieve counter
# ---------------------------------------------------------------------------


def test_retrieve_tool_counter_reset_by_build_tool_server():
    """D-10 suspenders: build_tool_server must reset _retrieve_call_count_var to 0.

    This ensures each new run_agent_turn invocation starts with a fresh counter,
    so the per-turn DoS guard does not accumulate across multiple Celery task
    invocations.
    """
    from app.services.retrieval_service import RetrievalStrategy

    # Simulate counter left over from a previous turn.
    # PROD-14: ContextVar-backed, so use .set() not direct assignment.
    agent_tools._retrieve_call_count_var.set(99)

    build_tool_server(
        conn_str="postgresql://test:test@localhost/testdb",
        agent_id="agent-reset-test",
        agent_name="Reset Bot",
        strategy=RetrievalStrategy.model_validate({}),
        conversation_id="conv-reset-test",
        notify_fn=None,
    )

    assert agent_tools._retrieve_call_count_var.get() == 0, (
        f"build_tool_server must reset _retrieve_call_count_var to 0, "
        f"got {agent_tools._retrieve_call_count_var.get()}"
    )
