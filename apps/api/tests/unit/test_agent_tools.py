"""
Unit tests for app.services.agent_tools.

The tool functions are async, so each test uses ``asyncio.run()``.

WHAT THE FAKE SDK USED TO BE FOR
    This module installed a fake `claude_agent_sdk` before importing agent_tools,
    because agent_tools took its `@tool` decorator and `create_sdk_mcp_server`
    from that package at import time. Three other test modules point here for the
    rule that guard follows: install the fake only when the REAL package is
    absent, since `not in sys.modules` let the fake win on pytest file order and
    `agent_tool_definitions()` then handed the loop objects with no `.name` (#48).
    #49 removed the import it protected. `app.domain.tool_def.tool` returns a
    ToolDefinition in every run order, so there is nothing left to install.

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
  6. test_bind_tool_context_sets_globals
  7. test_allowed_lookup_tables_is_frozenset
  ...
  15. test_retrieve_tool_blocked_on_third_call  (D-10 suspenders)
  16. test_retrieve_tool_counter_reset_by_bind_tool_context  (D-10 suspenders)
  17. the typed tool-result sink (ticket #49) — see the block at the end
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import app.services.agent_tools as agent_tools
from app.services.agent_tools import (
    ALLOWED_LOOKUP_TABLES,
    MAX_CHUNKS,
    bind_tool_context,
    get_tool_results,
    publish_tool_result,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run a coroutine synchronously — pytest-asyncio not needed."""
    return asyncio.run(coro)


def _fn(tool_obj):
    """Resolve a @tool-declared tool to its async callable.

    `app.domain.tool_def.tool` returns a frozen ToolDefinition and the coroutine
    lives on ``.handler``. The `getattr` fallback is what absorbed the SDK-era
    ambiguity, when a passthrough fake returned the function itself and the real
    decorator returned an ``SdkMcpTool``, so which shape a test got depended on
    pytest's file order.
    """
    return getattr(tool_obj, "handler", tool_obj)


def _empty_fused_context():
    """A fused context that matched nothing, which retrieve_tool still renders."""
    from app.domain.retrieved_context import RetrievedContext

    return RetrievedContext(query="test query", chunks=(), strategy="rrf")


def _long_chunk_context(content: str, n: int = 20):
    """n fused chunks sharing one long body, the truncation fixture."""
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
    """The RrfFusion rrf_fuse returns, with both candidate lists empty."""
    from app.domain.retrieved_context import RetrievedContext
    from app.services.retrieval_service import RrfFusion

    return RrfFusion(
        fused=fused,
        vector_candidates=RetrievedContext(
            query=fused.query, chunks=(), strategy="vector"
        ),
        bm25_candidates=RetrievedContext(
            query=fused.query, chunks=(), strategy="bm25"
        ),
    )


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
    assert '"chunk_id": "0"' in body

    # _citations is unchanged in length and shape versus the chunk list.
    assert len(citations) == min(len(fake_chunks), MAX_CHUNKS)
    for citation in citations:
        assert set(citation.keys()) == {"document_name", "section"}


# ---------------------------------------------------------------------------
# Test 3c: ticket #48, the model-facing string, pinned byte for byte.
#
# retrieve_tool renders the retrieved chunks as JSON, and `app.services.agent_loop`
# reads that string back with json.loads. #44 put RetrievedContext at the retrieval
# seam and left the repr in place; #48 replaced the repr with json.dumps of the same
# `to_json` chunks, so the keys and their order are still `to_json`'s decision.
#
# The literal below is written by hand from the five keys RetrievedChunk names.
# The parser test beside this one proves the reader on the other side still reads
# the string this pin fixes.
# ---------------------------------------------------------------------------

PINNED_MODEL_FACING_CHUNKS = (
    '[{"chunk_id": "c1", "document_id": "d1", "content": "Unopened bags, 14 days.", '
    '"score": 0.95, "rank": 1}, {"chunk_id": "c2", "document_id": "d2", "content": '
    '"Refunds take 5 days.", "score": 0.8, "rank": 2}]'
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
    return query, _rrf_result(fused), reranked


def test_retrieve_tool_model_string_is_byte_for_byte_the_pinned_json():
    """A fixed retrieval result renders the pinned string, key for key."""
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


def _framed_body(text: str) -> str:
    """The payload between the header and the footer of a framed retrieve result."""
    header_end = text.index(agent_tools.RETRIEVED_CONTEXT_HEADER) + len(
        agent_tools.RETRIEVED_CONTEXT_HEADER
    )
    return text[header_end:text.rindex(agent_tools.RETRIEVED_CONTEXT_FOOTER)]


def test_the_model_facing_json_carries_the_chunks_the_loop_captured():
    """The framed text the MODEL reads is well formed JSON, carrying the same records.

    Nothing on the other side parses it. `_run_tool_call` joins the wire's text
    for the tool message and `_attach_retrieve_capture` takes the chunks off the
    `_retrieved_context` ride-along structurally, so no reader in this tree turns
    this string back into objects.

    What the assertion establishes is that the two renderings of one retrieval
    agree. `json.loads` here is the test's own instrument. It fails if the framed
    body is not valid JSON, which is what the model is being asked to read, and
    the comparison then pins that the records in it are the chunks the loop
    captured. A chunk the loop stored is a chunk the model was shown.
    """
    import json

    agent_tools._retrieve_call_count_var.set(0)
    query, rrf_result, reranked = _pinned_retrieval()

    with (
        patch("app.services.agent_tools.embed_query", return_value=[0.1] * 8),
        patch("app.services.agent_tools.rrf_fuse", return_value=rrf_result),
        patch("app.services.agent_tools.rerank", return_value=reranked),
        patch("app.services.agent_tools.write_retrieval_metrics"),
    ):
        result = _run(_fn(agent_tools.retrieve_tool)({"query": query}))

    records = json.loads(_framed_body(result["content"][0]["text"]).strip())
    assert records == result["_retrieved_context"]["chunks"]
    assert [record["content"] for record in records] == [
        "Unopened bags, 14 days.",
        "Refunds take 5 days.",
    ]
    assert [record["document_id"] for record in records] == ["d1", "d2"]


def test_retrieve_tool_rides_the_retrieved_context_along_beside_the_citations():
    """The loop captures the chunks structurally rather than re-parsing the text."""
    agent_tools._retrieve_call_count_var.set(0)
    query, rrf_result, reranked = _pinned_retrieval()

    with (
        patch("app.services.agent_tools.embed_query", return_value=[0.1] * 8),
        patch("app.services.agent_tools.rrf_fuse", return_value=rrf_result),
        patch("app.services.agent_tools.rerank", return_value=reranked),
        patch("app.services.agent_tools.write_retrieval_metrics"),
    ):
        result = _run(_fn(agent_tools.retrieve_tool)({"query": query}))

    context = result["_retrieved_context"]
    assert context == reranked.to_json()
    assert context["query"] == query
    assert [chunk["chunk_id"] for chunk in context["chunks"]] == ["c1", "c2"]


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
# Test 6: bind_tool_context sets ContextVars
# ---------------------------------------------------------------------------


def test_bind_tool_context_sets_globals():
    """bind_tool_context must propagate all six arguments to ContextVars (PROD-14)."""
    from app.services.retrieval_service import RetrievalStrategy

    sentinel_conn = "postgresql://sentinel:<redacted>@host/db"
    sentinel_agent_id = "agent-sentinel-id"
    sentinel_agent_name = "Sentinel Bot"
    sentinel_strategy = RetrievalStrategy.model_validate({})
    sentinel_conv_id = "conv-sentinel-456"
    sentinel_notify = MagicMock()

    bind_tool_context(
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
# Test 6b: agent_tool_definitions, the one list the loop reads (ticket #48)
#
# `app.services.agent_loop` turns it into the JSON schemas the owned loop sends and
# dispatches a tool call by matching against it. Until #49 a second reader existed,
# `build_tool_server`, which registered the same list with an MCP server; two
# literals would have let the two callers disagree about which tools an agent has,
# and only one of them serves a customer.
# ---------------------------------------------------------------------------

#: The eleven tools an Agent turn may call, in registration order.
PINNED_TOOL_ORDER = [
    "retrieve",
    "lookup_structured",
    "escalate_to_human",
    "clarify",
    "place_order",
    "cancel_order",
    "issue_refund",
    "update_subscription",
    "book_slot",
    "update_customer_record",
    "confirm_action",
]


def _tool_name(tool_obj) -> str:
    """The tool's name, under the real SDK's SdkMcpTool or the fake decorator.

    Same reason as `_fn` above: which shape this module gets depends on whether
    another test module imported the real SDK first.
    """
    return getattr(tool_obj, "name", None) or tool_obj._tool_name


def test_agent_tool_definitions_returns_the_eleven_tools_in_order():
    definitions = agent_tools.agent_tool_definitions()

    assert len(definitions) == 11
    assert [_tool_name(t) for t in definitions] == PINNED_TOOL_ORDER


def test_agent_tool_definitions_returns_a_tuple():
    """A caller that appended to a list would change what the other caller registers."""
    assert isinstance(agent_tools.agent_tool_definitions(), tuple)


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
            return_value=_rrf_result(_empty_fused_context()),
        ),
        patch(
            "app.services.agent_tools.rerank",
            return_value=_empty_fused_context(),
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
# Test 16: D-10 (suspenders) — bind_tool_context resets the retrieve counter
# ---------------------------------------------------------------------------


def test_retrieve_tool_counter_reset_by_bind_tool_context():
    """D-10 suspenders: bind_tool_context must reset _retrieve_call_count_var to 0.

    This ensures each new run_agent_turn invocation starts with a fresh counter,
    so the per-turn DoS guard does not accumulate across multiple Celery task
    invocations.
    """
    from app.services.retrieval_service import RetrievalStrategy

    # Simulate counter left over from a previous turn.
    # PROD-14: ContextVar-backed, so use .set() not direct assignment.
    agent_tools._retrieve_call_count_var.set(99)

    bind_tool_context(
        conn_str="postgresql://test:test@localhost/testdb",
        agent_id="agent-reset-test",
        agent_name="Reset Bot",
        strategy=RetrievalStrategy.model_validate({}),
        conversation_id="conv-reset-test",
        notify_fn=None,
    )

    assert agent_tools._retrieve_call_count_var.get() == 0, (
        f"bind_tool_context must reset _retrieve_call_count_var to 0, "
        f"got {agent_tools._retrieve_call_count_var.get()}"
    )


# ---------------------------------------------------------------------------
# Test 17: the typed tool-result sink (ticket #49)
#
# `to_wire` spends a ToolResult's outcome on one bit, so the red-team victim turn
# used to recover a verdict by matching the dispatcher's prose off the SDK's
# ToolResultBlocks. BACKLOG 5.8 is the bill for one hand-copied substring, and
# BACKLOG 5.9 is the bill for the transcript being empty for a whole milestone.
# The sink is what makes the type reach that turn instead.
# ---------------------------------------------------------------------------


def _bind(conversation_id: str = "conv-sink-001") -> None:
    from app.services.retrieval_service import RetrievalStrategy

    bind_tool_context(
        conn_str="postgresql://test:test@localhost/testdb",
        agent_id="agent-sink-001",
        agent_name="Sink Bot",
        strategy=RetrievalStrategy.model_validate({}),
        conversation_id=conversation_id,
        notify_fn=None,
    )


def _verdict(skill: str, outcome=None, text: str = "done"):
    from app.domain.tool_result import Outcome, ToolResult

    return ToolResult(skill=skill, outcome=outcome or Outcome.ok, text=text)


def test_publish_tool_result_reaches_get_tool_results_in_order():
    _bind()

    publish_tool_result(_verdict("issue_refund"))
    publish_tool_result(_verdict("place_order"))

    assert [r.skill for r in get_tool_results()] == ["issue_refund", "place_order"]


def test_the_outcome_survives_the_sink():
    """The whole reason the sink exists. The wire cannot carry these apart."""
    from app.domain.tool_result import Outcome

    _bind()
    publish_tool_result(_verdict("issue_refund", Outcome.denied, "Access denied"))
    publish_tool_result(_verdict("confirm_action", Outcome.requires_human, "Awaiting"))

    assert [r.outcome for r in get_tool_results()] == [
        Outcome.denied,
        Outcome.requires_human,
    ]


def test_bind_tool_context_installs_a_fresh_sink():
    """A sink carried over reports one attack's refund attempt as the next one's.

    Worse than no recording, because it is a wrong observation that looks right.
    """
    _bind("conv-sink-first")
    publish_tool_result(_verdict("issue_refund"))
    assert len(get_tool_results()) == 1

    _bind("conv-sink-second")

    assert get_tool_results() == [], (
        "the previous turn's verdicts survived into this one — a red-team "
        "transcript would report the last attack's refund as this attack's"
    )


def test_get_tool_results_returns_a_copy():
    """A caller cannot empty the sink by mutating what it got back."""
    _bind()
    publish_tool_result(_verdict("issue_refund"))

    got = get_tool_results()
    got.clear()

    assert [r.skill for r in get_tool_results()] == ["issue_refund"]


def test_publishing_without_a_sink_does_not_raise():
    """A recording failure must not fail the turn it is observing."""
    agent_tools._tool_results_var.set(None)

    publish_tool_result(_verdict("issue_refund"))

    assert get_tool_results() == []
