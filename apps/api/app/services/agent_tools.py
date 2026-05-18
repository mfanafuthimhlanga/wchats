"""
agent_tools — Four MCP tool definitions + build_tool_server factory.

Provides the tool layer that the Claude Agent SDK invokes inside a Celery task.
All four tools are defined at module level and reference module-level globals
for tenant-scoped state (safe for worker_pool=solo — sequential, single-process).

Tools:
    retrieve            — embed → rrf_fuse → rerank → return top MAX_CHUNKS chunks
    lookup_structured   — allowlisted psycopg2 SELECT with parameterised filters
    escalate_to_human   — writes escalation marker + calls notify_fn
    clarify             — returns the question text for the agent to surface

Security (G-04 hard block):
    ALLOWED_LOOKUP_TABLES is checked BEFORE any SQL assembly in lookup_structured_tool.
    Non-allowlisted tables receive is_error=True with NO SQL executed.

Design decisions:
    - Module-level globals (_conn_str, _agent_id, etc.) are a simple injection
      mechanism for worker_pool=solo. If concurrency is ever raised above 1,
      replace with contextvars.ContextVar.
    - retrieve calls retrieval_service functions directly (never apply_async)
      because this code already runs inside a Celery task.
    - Content is truncated at 2000 chars (≈ MAX_CHUNK_TOKENS * 4) before
      returning to the agent to limit context window consumption.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import psycopg2
import structlog

from claude_agent_sdk import create_sdk_mcp_server, tool

from app.services.retrieval_service import (
    RetrievalStrategy,
    embed_query,
    rerank,
    rrf_fuse,
)

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

ALLOWED_LOOKUP_TABLES: frozenset[str] = frozenset({"chunks", "documents", "chunk_metadata"})

_ALLOWED_FILTER_COLUMNS: dict[str, frozenset[str]] = {
    "chunks":         frozenset({"id", "document_id", "section", "chunk_order"}),
    "documents":      frozenset({"id", "name", "parse_status", "source_uri"}),
    "chunk_metadata": frozenset({"chunk_id", "entity_type", "entity_value"}),
}


def _validate_filter_columns(table: str, filters: dict) -> list[str]:
    """Returns list of rejected column names. Empty = all OK."""
    allowed = _ALLOWED_FILTER_COLUMNS.get(table, frozenset())
    return [col for col in filters if col not in allowed]


MAX_CHUNKS: int = 5
MAX_CHUNK_TOKENS: int = 500  # approximate; character proxy = MAX_CHUNK_TOKENS * 4 = 2000

_CONTENT_CHAR_LIMIT: int = MAX_CHUNK_TOKENS * 4  # 2000 chars

# ---------------------------------------------------------------------------
# Module-level globals (injected by build_tool_server — safe for worker_pool=solo)
# ---------------------------------------------------------------------------

_conn_str: str = ""
_agent_id: str = ""
_agent_name: str = ""
_strategy: RetrievalStrategy | None = None
_conversation_id: str = ""
_notify_fn = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _mark_conversation_escalated(
    conversation_id: str,
    reason: str,
    context: str,
    conn_str: str,
) -> None:
    """Write escalation marker to conversations table via jsonb_set UPDATE.

    Uses the module-level _conn_str if conn_str is not supplied separately.
    """
    sql = """
        UPDATE conversations
        SET metadata = jsonb_set(
            COALESCE(metadata, '{}'),
            '{escalated}',
            'true'::jsonb
        )
        WHERE id = %s
    """
    conn = psycopg2.connect(conn_str, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (conversation_id,))
        conn.commit()
    finally:
        conn.close()

    log.info(
        "conversation.escalated",
        conversation_id=conversation_id,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Tool 1: retrieve
# ---------------------------------------------------------------------------

@tool(
    "retrieve",
    (
        "Search the tenant knowledge base for content relevant to the customer query. "
        "Always call this before answering a factual question."
    ),
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The customer query to search for."},
            "filters": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Optional metadata filters (e.g. [{\"document_id\": \"abc\"}]).",
            },
        },
        "required": ["query"],
    },
)
async def retrieve_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Embed query, RRF-fuse, rerank; return top MAX_CHUNKS chunks truncated to 2000 chars each.

    Calls retrieval_service functions directly — no apply_async because this
    code already runs inside a Celery task.
    """
    query: str = args["query"]
    strategy = _strategy or RetrievalStrategy.model_validate({})

    log.debug("retrieve_tool.start", query=query[:80])

    # Run blocking retrieval calls in executor to keep the async tool cooperative.
    loop = asyncio.get_event_loop()

    query_vector: list[float] = await loop.run_in_executor(
        None, lambda: embed_query(query)
    )
    rrf_result: dict = await loop.run_in_executor(
        None, lambda: rrf_fuse(_conn_str, query_vector, query, strategy)
    )
    reranked: list[dict] = await loop.run_in_executor(
        None, lambda: rerank(query, rrf_result["fused"], strategy)
    )

    # Truncate to MAX_CHUNKS and cap content at _CONTENT_CHAR_LIMIT chars each.
    chunks = reranked[:MAX_CHUNKS]
    for chunk in chunks:
        if isinstance(chunk.get("content"), str) and len(chunk["content"]) > _CONTENT_CHAR_LIMIT:
            chunk["content"] = chunk["content"][:_CONTENT_CHAR_LIMIT]

    citations = [
        {
            "document_name": chunk.get("document_id", "unknown"),
            "section": chunk.get("section", "general"),
        }
        for chunk in chunks
    ]

    log.debug("retrieve_tool.done", chunk_count=len(chunks))

    return {
        "content": [{"type": "text", "text": str(chunks)}],
        "_citations": citations,
    }


# ---------------------------------------------------------------------------
# Tool 2: lookup_structured
# ---------------------------------------------------------------------------

@tool(
    "lookup_structured",
    (
        "Query structured tenant data (documents list, chunk metadata). "
        "Use only for metadata queries, not for semantic search. "
        f"Allowed tables: {sorted(ALLOWED_LOOKUP_TABLES)}."
    ),
    {
        "type": "object",
        "properties": {
            "table": {
                "type": "string",
                "description": f"Table to query. Must be one of: {sorted(ALLOWED_LOOKUP_TABLES)}.",
            },
            "filters": {
                "type": "object",
                "description": "Key-value filters applied as WHERE col = val (AND-joined).",
            },
        },
        "required": ["table"],
    },
)
async def lookup_structured_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Allowlisted psycopg2 SELECT with parameterised filters.

    Security (G-04): table is checked against ALLOWED_LOOKUP_TABLES BEFORE any
    SQL assembly. Non-allowlisted table → immediate is_error return, no SQL run.
    Filter values are passed as psycopg2 %s parameters — never f-string interpolated.
    """
    table: str = args.get("table", "")
    filters: dict = args.get("filters", {})

    # ---- G-04 hard block ----
    if table not in ALLOWED_LOOKUP_TABLES:
        log.warning("lookup_structured.rejected", table=table)
        return {
            "content": [{"type": "text", "text": f"Table '{table}' is not accessible."}],
            "is_error": True,
        }

    # Table name validated — safe to embed in SQL (table name from allowlist, not user input).
    # Column names validated against per-table allowlist; quoted with pgsql.Identifier.
    rejected = _validate_filter_columns(table, filters)
    if rejected:
        log.warning("lookup_structured.column_rejected", table=table, rejected=rejected)
        return {
            "content": [{"type": "text", "text": f"Column(s) {rejected!r} are not allowed for table '{table}'."}],
            "is_error": True,
        }

    from psycopg2 import sql as pgsql

    where_clauses: list = []
    params: list[Any] = []
    for col, val in filters.items():
        where_clauses.append(pgsql.SQL("{} = %s").format(pgsql.Identifier(col)))
        params.append(val)

    if where_clauses:
        where_sql = pgsql.SQL("WHERE ") + pgsql.SQL(" AND ").join(where_clauses)
        sql = pgsql.SQL("SELECT * FROM {} ").format(pgsql.Identifier(table)) + where_sql + pgsql.SQL(" LIMIT 100")
    else:
        sql = pgsql.SQL("SELECT * FROM {} LIMIT 100").format(pgsql.Identifier(table))  # noqa: S608 — table from allowlist

    log.debug("lookup_structured.query", table=table, filter_count=len(filters))

    loop = asyncio.get_event_loop()

    def _run_query() -> list:
        conn = psycopg2.connect(_conn_str, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        finally:
            conn.close()
        return rows

    rows = await loop.run_in_executor(None, _run_query)
    return {"content": [{"type": "text", "text": str(rows)}]}


# ---------------------------------------------------------------------------
# Tool 3: escalate_to_human
# ---------------------------------------------------------------------------

@tool(
    "escalate_to_human",
    (
        "Escalate the conversation to a human agent. Call when: "
        "(a) customer is frustrated or angry, "
        "(b) query is outside the knowledge base after retrieval, "
        "(c) customer explicitly asks to speak to a human, or "
        "(d) the same question has been sent three or more times. "
        "Do not call more than once per conversation."
    ),
    {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Brief reason for escalation (shown to support team).",
            },
            "context": {
                "type": "string",
                "description": "Summary of the conversation context for the human agent.",
            },
        },
        "required": ["reason", "context"],
    },
)
async def escalate_to_human_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Write escalation marker and fire-and-forget notify_fn."""
    reason: str = args["reason"]
    context: str = args["context"]

    log.info("escalate_to_human_tool.called", reason=reason)

    loop = asyncio.get_event_loop()

    # Write escalation marker to conversations table.
    await loop.run_in_executor(
        None,
        lambda: _mark_conversation_escalated(_conversation_id, reason, context, _conn_str),
    )

    # Fire-and-forget notification (email / webhook / slack — injected by task).
    if _notify_fn is not None:
        _notify_fn(reason, context)

    return {
        "content": [
            {
                "type": "text",
                "text": (
                    "I've flagged this conversation for our support team. "
                    f"Reason: {reason}. A human will follow up shortly."
                ),
            }
        ]
    }


# ---------------------------------------------------------------------------
# Tool 4: clarify
# ---------------------------------------------------------------------------

@tool(
    "clarify",
    (
        "Ask the customer a clarifying question when the query is ambiguous. "
        "Use at most twice per conversation before escalating to a human."
    ),
    {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The clarifying question to ask the customer.",
            }
        },
        "required": ["question"],
    },
)
async def clarify_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Return the clarifying question as the agent's response text."""
    return {"content": [{"type": "text", "text": args["question"]}]}


# ---------------------------------------------------------------------------
# Factory: build_tool_server
# ---------------------------------------------------------------------------

def build_tool_server(
    conn_str: str,
    agent_id: str,
    agent_name: str,
    strategy: RetrievalStrategy,
    conversation_id: str,
    notify_fn,
) -> object:
    """Inject tenant-scoped state into module globals and return the MCP server.

    Called once per ``run_agent_turn`` invocation. The module globals are safe
    because ``worker_pool=solo`` executes tasks sequentially in the main process.

    Args:
        conn_str:        Decrypted tenant DB connection string.
        agent_id:        Agent UUID string (for logging / metadata).
        agent_name:      Agent display name (for logging / metadata).
        strategy:        RetrievalStrategy parsed from agent.retrieval_strategy JSONB.
        conversation_id: Conversation UUID for escalation DB writes.
        notify_fn:       Callable(reason: str, context: str) — fire-and-forget notification.

    Returns:
        MCP server object (create_sdk_mcp_server result) registering all four tools.
    """
    global _conn_str, _agent_id, _agent_name, _strategy, _conversation_id, _notify_fn

    _conn_str = conn_str
    _agent_id = agent_id
    _agent_name = agent_name
    _strategy = strategy
    _conversation_id = conversation_id
    _notify_fn = notify_fn

    log.debug(
        "build_tool_server.ready",
        agent_id=agent_id,
        conversation_id=conversation_id,
    )

    return create_sdk_mcp_server(
        name="customer-tools",
        version="1.0.0",
        tools=[retrieve_tool, lookup_structured_tool, escalate_to_human_tool, clarify_tool],
    )
