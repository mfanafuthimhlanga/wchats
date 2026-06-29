"""
agent_tools — Four MCP tool definitions + build_tool_server factory.

Provides the tool layer that the Claude Agent SDK invokes inside a Celery task.
All four tools are defined at module level and read per-task state from ContextVars
for worker concurrency safety (PROD-14).

Tools:
    retrieve            — embed → rrf_fuse → rerank → return top MAX_CHUNKS chunks
    lookup_structured   — allowlisted psycopg2 SELECT with parameterised filters
    escalate_to_human   — writes escalation marker + calls notify_fn
    clarify             — returns the question text for the agent to surface

Security (G-04 hard block):
    ALLOWED_LOOKUP_TABLES is checked BEFORE any SQL assembly in lookup_structured_tool.
    Non-allowlisted tables receive is_error=True with NO SQL executed.

Design decisions:
    - Per-task state (_conn_str, _agent_id, etc.) is stored in contextvars.ContextVar
      so workers running multiple tasks concurrently carry no cross-request state bleed
      (PROD-14).  build_tool_server() calls .set() on each ContextVar at the start of
      every run_agent_turn invocation.
    - retrieve_tool reads all ContextVars into local variables in the async body BEFORE
      passing lambdas to run_in_executor — executor threads do not inherit the asyncio
      context automatically, so ContextVar.get() calls inside executor lambdas would
      read stale/empty values.
    - retrieve calls retrieval_service functions directly (never apply_async)
      because this code already runs inside a Celery task.
    - Content is truncated at 2000 chars (≈ MAX_CHUNK_TOKENS * 4) before
      returning to the agent to limit context window consumption.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import ssl
from contextvars import ContextVar
from typing import Any

import psycopg2
import redis as redis_lib
import structlog

from claude_agent_sdk import create_sdk_mcp_server, tool

from app.core.config import settings
from app.services.retrieval_service import (
    RetrievalStrategy,
    embed_query,
    rerank,
    rrf_fuse,
)

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# D-13: Module-level sync Redis client for query-embedding cache.
# Reuses the same Upstash rediss:// connection as agent.py (lines 77-79).
# Built lazily to avoid import-time failures in test environments where
# REDIS_URL may not be set.
# ---------------------------------------------------------------------------

_qembed_redis: redis_lib.Redis | None = None


def _get_qembed_redis() -> redis_lib.Redis:
    """Return (and lazily create) the module-level sync Redis client."""
    global _qembed_redis
    if _qembed_redis is None:
        _url_clean = (
            settings.REDIS_URL.split("?")[0]
            if "?" in settings.REDIS_URL
            else settings.REDIS_URL
        )
        _ssl_opts: dict = (
            {"ssl_cert_reqs": ssl.CERT_NONE}
            if _url_clean.startswith("rediss://")
            else {}
        )
        _qembed_redis = redis_lib.from_url(_url_clean, **_ssl_opts)
    return _qembed_redis

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


# ---------------------------------------------------------------------------
# F3 — Control character sanitiser for escalation fields
# ---------------------------------------------------------------------------

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


def _sanitise_escalation_field(value: str, max_len: int = 500) -> str:
    """Strip control characters and enforce max_len cap on escalation fields."""
    return _CONTROL_CHAR_RE.sub(" ", value[:max_len]).strip()


MAX_CHUNKS: int = 5
MAX_CHUNK_TOKENS: int = 500  # approximate; character proxy = MAX_CHUNK_TOKENS * 4 = 2000

_CONTENT_CHAR_LIMIT: int = MAX_CHUNK_TOKENS * 4  # 2000 chars

# ---------------------------------------------------------------------------
# Per-task ContextVars — injected by build_tool_server (PROD-14)
#
# Replacing the former module-level globals (_conn_str, _agent_id, etc.) with
# ContextVar gives per-task state isolation so a worker running multiple tasks
# concurrently carries no cross-request state bleed.
#
# asyncio.run() propagation (RESEARCH.md Cluster 7, note A3):
#   asyncio.run(coro) runs coro in a copy of the caller's context, so values
#   set by build_tool_server (sync) are visible inside _run_sdk_turn (async).
#
# Executor-thread caveat:
#   run_in_executor() threads do NOT automatically receive the asyncio context.
#   All tools read ContextVars into local variables in the async body and pass
#   those locals to the executor lambdas — not ContextVar.get() inside lambdas.
# ---------------------------------------------------------------------------

_conn_str_var: ContextVar[str] = ContextVar("conn_str", default="")
_agent_id_var: ContextVar[str] = ContextVar("agent_id", default="")
_agent_name_var: ContextVar[str] = ContextVar("agent_name", default="")
_strategy_var: ContextVar[RetrievalStrategy | None] = ContextVar("strategy", default=None)
_conversation_id_var: ContextVar[str] = ContextVar("conversation_id", default="")
_notify_fn_var: ContextVar = ContextVar("notify_fn", default=None)

# D-10 (suspenders): per-turn retrieve call counter — ContextVar for isolation.
# Reset to 0 by build_tool_server() at the start of each run_agent_turn invocation.
# Raised from 2 (Voyage 3 RPM free-tier guard) to 8 (DoS-only ceiling) now that
# embeddings move to Bedrock which has no such RPM cap (PROD-06 throttle removal).
_RETRIEVE_CALLS_PER_TURN_MAX: int = 8
_retrieve_call_count_var: ContextVar[int] = ContextVar("retrieve_call_count", default=0)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _mark_conversation_escalated(
    conversation_id: str,
    agent_id: str,
    reason: str,
    context: str,
    conn_str: str,
) -> dict:
    """Write escalation marker to conversations table via jsonb_set UPDATE.

    Idempotency guard: only updates when metadata->>'escalated' IS DISTINCT FROM 'true'.
    Returns {"already_escalated": True} if the row was already escalated (rowcount==0).

    Args:
        conversation_id: UUID of the conversation to escalate.
        agent_id:        UUID of the agent (used as additional WHERE guard).
        reason:          Sanitised escalation reason string (for logging).
        context:         Sanitised conversation context string.
        conn_str:        Decrypted tenant DB connection string.
    """
    sql = """
        UPDATE conversations
        SET metadata = jsonb_set(
            COALESCE(metadata, '{}'),
            '{escalated}',
            'true'::jsonb
        )
        WHERE id = %s AND agent_id = %s
          AND (metadata->>'escalated') IS DISTINCT FROM 'true'
    """
    conn = psycopg2.connect(conn_str, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (conversation_id, agent_id))
            if cur.rowcount == 0:
                log.info(
                    "escalate_to_human.already_escalated",
                    conversation_id=conversation_id,
                )
                return {"already_escalated": True}
        conn.commit()
    finally:
        conn.close()

    log.info(
        "conversation.escalated",
        conversation_id=conversation_id,
        reason=reason,
    )
    return {}


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

    D-10 (suspenders): blocks the call and returns is_error when the per-turn
    counter exceeds _RETRIEVE_CALLS_PER_TURN_MAX (now 8 — DoS guard only; was 2
    to protect the Voyage 3 RPM free tier which Bedrock removes).
    Counter is reset by build_tool_server() at the start of each task invocation.

    Executor-thread safety: all ContextVars are read into local variables at the
    TOP of this async body before any run_in_executor call.  Executor threads do
    not inherit the asyncio context, so ContextVar.get() calls inside lambdas
    would return default (empty/None) values instead of the task's values.
    """
    # Increment per-turn counter.  ContextVar-backed: mutations here are visible
    # only in this task's context (no bleed across concurrent tasks).
    count = _retrieve_call_count_var.get() + 1
    _retrieve_call_count_var.set(count)

    # Read remaining ContextVars into locals BEFORE any run_in_executor handoff.
    conversation_id = _conversation_id_var.get()
    conn_str = _conn_str_var.get()
    strategy = _strategy_var.get() or RetrievalStrategy.model_validate({})

    if count > _RETRIEVE_CALLS_PER_TURN_MAX:
        log.warning(
            "retrieve_tool.rate_cap_hit",
            call_count=count,
            max_allowed=_RETRIEVE_CALLS_PER_TURN_MAX,
            conversation_id=conversation_id,
            note="DoS guard: retrieve call cap exceeded for this turn",
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Retrieve quota exceeded for this turn "
                        f"(max {_RETRIEVE_CALLS_PER_TURN_MAX} calls allowed). "
                        "Please synthesize an answer from the results already retrieved."
                    ),
                }
            ],
            "is_error": True,
        }

    query: str = args["query"]
    # F7: filters is a no-op. Emit warning so any LLM-supplied filter value is visible
    # in observability before it silently becomes load-bearing in a future change.
    # Enforcement tracked as TODO-RET-01 (M5 milestone).
    filters = args.get("filters", [])
    if filters:
        log.warning(
            "retrieve_tool.filters_ignored",
            filter_count=len(filters),
            conversation_id=conversation_id,
            note="filters parameter is not yet enforced; upgrade to M5 allowlist before activation",
        )
    # filters intentionally not applied — see AR-03-07 / TODO-RET-01

    log.debug("retrieve_tool.start", query=query[:80], call_count=count)

    # Run blocking retrieval calls in executor to keep the async tool cooperative.
    loop = asyncio.get_event_loop()

    # D-13: Redis read-through cache for query embeddings.
    # Key: qembed:<sha256-hex-of-query-utf8>  |  TTL: 3600s (1 hour)
    # Repeat questions cost zero Voyage calls (supports D-09 $0 path).
    # Any cache error falls back to a direct embed_query call — the cache is
    # an optimisation, never a correctness dependency.
    def _embed_with_cache(q: str) -> list[float]:
        cache_key = "qembed:" + hashlib.sha256(q.encode("utf-8")).hexdigest()
        try:
            rc = _get_qembed_redis()
            cached = rc.get(cache_key)
            if cached is not None:
                log.debug("retrieve_tool.cache_hit", key_prefix="qembed:")
                return json.loads(cached)
            vector = embed_query(q)
            rc.setex(cache_key, 3600, json.dumps(vector))
            return vector
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "retrieve_tool.cache_error",
                exc=str(exc),
                note="falling back to direct embed_query",
            )
            return embed_query(q)

    query_vector: list[float] = await loop.run_in_executor(
        None, lambda: _embed_with_cache(query)
    )
    # conn_str and strategy are locals captured from the async body — safe for
    # executor threads (no ContextVar.get() calls inside the lambdas).
    rrf_result: dict = await loop.run_in_executor(
        None, lambda: rrf_fuse(conn_str, query_vector, query, strategy)
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

    Executor-thread safety: conn_str is read from _conn_str_var into a local
    before _run_query is defined — the nested function captures the local via
    closure, not via ContextVar.get() inside the thread.
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

    # Read conn_str from ContextVar into a local so the executor thread closure
    # captures a plain string value — not a ContextVar.get() call that would
    # run in the wrong context.
    conn_str = _conn_str_var.get()

    def _run_query() -> list:
        conn = psycopg2.connect(conn_str, connect_timeout=5)
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
    """Write escalation marker and fire-and-forget notify_fn.

    F3 hardening:
      - reason and context are sanitised (control chars stripped, 500-char cap).
      - _mark_conversation_escalated returns already_escalated=True on duplicate call;
        in that case _notify_fn is NOT called.
      - Notification payload is prefixed with [AGENT-DETECTED — UNVERIFIED].

    Executor-thread safety: all ContextVars are read into locals before the
    run_in_executor call so the executor closure captures plain values.
    """
    reason: str = _sanitise_escalation_field(args.get("reason", ""))
    context: str = _sanitise_escalation_field(args.get("context", ""))

    log.info("escalate_to_human_tool.called", reason=reason)

    # Read ContextVars into locals BEFORE run_in_executor — executor threads do not
    # inherit the asyncio context (PROD-14 executor-thread caveat).
    conversation_id = _conversation_id_var.get()
    agent_id = _agent_id_var.get()
    conn_str = _conn_str_var.get()
    notify_fn = _notify_fn_var.get()

    loop = asyncio.get_event_loop()

    # Write escalation marker to conversations table (idempotency guard inside).
    result = await loop.run_in_executor(
        None,
        lambda: _mark_conversation_escalated(
            conversation_id, agent_id, reason, context, conn_str
        ),
    )

    if result.get("already_escalated"):
        return result

    # Fire-and-forget notification (email / webhook / slack — injected by task).
    # Prefix with [AGENT-DETECTED — UNVERIFIED] so recipients know this is LLM-sourced.
    if notify_fn is not None:
        prefixed_reason = f"[AGENT-DETECTED — UNVERIFIED] {reason}"
        notify_fn(prefixed_reason, context)

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
    """Inject tenant-scoped state into ContextVars and return the MCP server.

    Called once per ``run_agent_turn`` invocation in the sync Celery task body
    (before asyncio.run()).  Sets all six ContextVars so they are visible inside
    the async SDK turn and its tool callees (Python 3.7+ asyncio.run propagation
    guarantee — see RESEARCH.md Cluster 7, note A3).

    Concurrency safety (PROD-14):
        ContextVar.set() mutates only the *current context's* copy of the variable.
        With prefork concurrency > 1, each worker process has its own context per
        task, so no cross-request state bleed occurs.

    Args:
        conn_str:        Decrypted tenant DB connection string.
        agent_id:        Agent UUID string (for logging / metadata).
        agent_name:      Agent display name (for logging / metadata).
        strategy:        RetrievalStrategy parsed from agent.retrieval_strategy JSONB.
        conversation_id: Conversation UUID for escalation DB writes.
        notify_fn:       Callable(reason: str, context: str) — fire-and-forget notification.

    Returns:
        MCP server object (create_sdk_mcp_server result) registering all 11 tools:
        the original 4 (retrieve, lookup_structured, escalate_to_human, clarify) plus the
        7 transactional tools added in Phase 14 Plan 04 (place_order, cancel_order,
        issue_refund, update_subscription, book_slot, update_customer_record, confirm_action).
    """
    _conn_str_var.set(conn_str)
    _agent_id_var.set(agent_id)
    _agent_name_var.set(agent_name)
    _strategy_var.set(strategy)
    _conversation_id_var.set(conversation_id)
    _notify_fn_var.set(notify_fn)

    # D-10 (suspenders): reset per-turn retrieve counter for this new task invocation.
    # ContextVar.set() ensures the reset is scoped to this task's context only.
    _retrieve_call_count_var.set(0)

    log.debug(
        "build_tool_server.ready",
        agent_id=agent_id,
        conversation_id=conversation_id,
    )

    # Phase 14 Plan 04: append the 7 transactional tools to the existing 4.
    # The import is deferred to avoid circular-import issues at module level:
    # tools.py imports _agent_id_var from this module at function call time, so
    # importing tools here (after the ContextVar definitions above) is safe.
    from app.services.transactional.tools import (  # noqa: PLC0415
        place_order_tool,
        cancel_order_tool,
        issue_refund_tool,
        update_subscription_tool,
        book_slot_tool,
        update_customer_record_tool,
        confirm_action_tool,
    )

    return create_sdk_mcp_server(
        name="customer-tools",
        version="1.0.0",
        tools=[
            # Original 4 tools — must be retained (TXN-04 / PLAN.md prohibition)
            retrieve_tool,
            lookup_structured_tool,
            escalate_to_human_tool,
            clarify_tool,
            # Phase 14 Plan 04 — 7 transactional tools
            place_order_tool,
            cancel_order_tool,
            issue_refund_tool,
            update_subscription_tool,
            book_slot_tool,
            update_customer_record_tool,
            confirm_action_tool,
        ],
    )
