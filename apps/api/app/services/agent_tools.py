"""
agent_tools, the eleven tool definitions and the one factory over them.

Provides the tool layer that `app.services.tool_loop.dispatch` runs inside a
Celery task. All eleven tools are defined at module level and read per-task state
from ContextVars for worker concurrency safety (PROD-14).

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
      (PROD-14).  bind_tool_context() calls .set() on each ContextVar at the start of
      every run_agent_turn invocation, and is the single entry point since #49.
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
import math
import re
import ssl
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from typing import Any, Literal

import psycopg2
import redis as redis_lib
import structlog

from app.core.config import settings
from app.domain.context_frame import (
    RETRIEVED_CONTEXT_FOOTER as _RETRIEVED_CONTEXT_FOOTER,
)
from app.domain.context_frame import (
    RETRIEVED_CONTEXT_HEADER as _RETRIEVED_CONTEXT_HEADER,
)
from app.domain.context_frame import frame_retrieved_context as _frame_context
from app.domain.retrieved_context import RetrievedContext
from app.domain.tool_def import ToolDefinition, tool
from app.domain.tool_result import ToolResult
from app.services.retrieval_metrics_service import write_retrieval_metrics
from app.services.retrieval_service import (
    RetrievalStrategy,
    RrfFusion,
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

#: Per-CHUNK cap on retrieved content. Public, and the name changed from
#: `_CONTENT_CHAR_LIMIT` because a second reader arrived: the eval scores
#: Faithfulness against these chunks (D1/P2) and stamps this number on the run.
#: A claim whose support was cut at THIS boundary is marked unsupported by the
#: judge, and a run that does not record the cap cannot tell that apart from a
#: genuinely ungrounded claim. One copy of the number, imported by the reader.
CHUNK_CONTENT_CHAR_LIMIT: int = MAX_CHUNK_TOKENS * 4  # 2000 chars

# ---------------------------------------------------------------------------
# SEC-02/L6 (OD-5): data-not-instructions framing on retrieve_tool's tool-result
# text. Mirrors the labeled-delimiter "treat as data" convention already shipped
# in app.services.actor_seam (lines ~210-232) at a new boundary: retrieved chunk
# content re-entering the SDK context window. Additive to, never a replacement
# for, app.utils.sanitize.sanitize_chunk_text — admit-time sanitisation and
# retrieval-time framing are two independent layers against the same threat.
# ---------------------------------------------------------------------------

# BACKLOG 5.19: the strings moved to context_frame (now app.domain.context_frame) so the grounding
# judge can frame its own context without importing this module (the whole
# retrieval stack) onto the validator path. Re-exported here because this is
# where every existing reader and test looks for them, and because a second copy
# of a security control is how two copies drift apart.
RETRIEVED_CONTEXT_HEADER = _RETRIEVED_CONTEXT_HEADER
RETRIEVED_CONTEXT_FOOTER = _RETRIEVED_CONTEXT_FOOTER

# ---------------------------------------------------------------------------
# Per-task ContextVars, published by bind_tool_context (PROD-14)
#
# Replacing the former module-level globals (_conn_str, _agent_id, etc.) with
# ContextVar gives per-task state isolation so a worker running multiple tasks
# concurrently carries no cross-request state bleed.
#
# asyncio.run() propagation (RESEARCH.md Cluster 7, note A3):
#   asyncio.run(coro) runs coro in a copy of the caller's context, so values
#   set by bind_tool_context (sync) are visible inside run_agent_loop (async).
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
# HKDF salt source for per-tenant credential derivation (INT-01)
_tenant_id_var: ContextVar[str] = ContextVar("tenant_id", default="")

# D-10 (suspenders): per-turn retrieve call counter — ContextVar for isolation.
# Reset to 0 by bind_tool_context() at the start of each run_agent_turn invocation.
# Raised from 2 (Voyage 3 RPM free-tier guard) to 8 (DoS-only ceiling) now that
# embeddings move to Bedrock which has no such RPM cap (PROD-06 throttle removal).
_RETRIEVE_CALLS_PER_TURN_MAX: int = 8
_retrieve_call_count_var: ContextVar[int] = ContextVar("retrieve_call_count", default=0)

# IDV-05 (Phase 17): verified session token transport rail.
# Empty-string default means "no verified session — all non-IDV tool calls pass through".
# The token is NEVER logged (parity with `message`, T-04-03-05).
# Set by bind_tool_context() from the run_agent_turn task arg; read by the
# Step 2.5 gate in transactional/tools.py (wired in 17-06).
_verified_session_token_var: ContextVar[str] = ContextVar("verified_session_token", default="")

# OPS-05/06 (Phase 21 Plan 03): job_id transport rail for retrieval_metrics writes.
# Empty-string default (job_id never set) still results in a written row — with
# an empty job_id and a warning — proving the ContextVar plumbing is exercised
# when a real job_id IS set (see retrieve_tool's Pitfall 4 local-read comment).
_job_id_var: ContextVar[str] = ContextVar("job_id", default="")

# OPS-06: context-window budget used for ctx_window_utilization (retrieved_tokens
# / CONTEXT_WINDOW_BUDGET). Matches the 200k-token model window referenced in
# 21-DOMAIN-NOTES.md §3 (context rot).
CONTEXT_WINDOW_BUDGET: int = 200_000

# ---------------------------------------------------------------------------
# D1/P1b — the side-effect mode (BACKLOG 2.5).
#
# From P2 the nightly eval drives this same tool layer through the same seam the
# customer's chat turn goes through, which is the entire point of approach (b):
# the agent that is measured has to be the agent that is served. What must NOT
# come with it is the outer edge. Three calls here leave this process and change
# something a customer or a bank can see:
#
#     notify_fn                escalation mail to the owner
#     write_retrieval_metrics  a row in the tenant's retrieval_metrics
#     ProviderAdapter          money and tenant state, via the six mutating skills
#
# "recorded" suppresses exactly those three and records each one instead.
#
# What recorded mode deliberately does NOT do is give the eval a smaller agent.
# The alternative the owner rejected was handing the eval a read-only
# allowed_tools subset; it would have stopped the refund too, and would have made
# "the agent should have refused to refund here" unfalsifiable, because an agent
# that cannot attempt the wrong thing cannot be measured on refusing it. So the
# tool list, the system prompt, the capability envelope, the IDV gate and the
# Actor seam are identical in both modes.
#
# On the default being "live": that is the safe direction here, not the reckless
# one. Every path that reaches these tools calls bind_tool_context, and the seam
# above it (agent_loop.build_agent_turn) takes the mode as a MANDATORY parameter
# with no default, so the eval cannot arrive here by forgetting to choose. A
# "recorded" default would instead mean that a caller who forgot silently stops
# refunding real customers — a failure that produces no error anywhere and would
# be found by a customer, not by us. The red-team probes (red_team.py,
# red_team_probe.py) rely on this default: they read real dispatcher verdict tags
# and are the two genuinely sound vectors in the measurement audit.
# ---------------------------------------------------------------------------

SideEffectMode = Literal["live", "recorded"]

#: The only two accepted values, checked at runtime. `Literal` is a type-checker
#: annotation and enforces nothing at run time; `side_effects="dry_run"` would
#: otherwise compare unequal to "recorded", read as live, and move real money on
#: the eval path.
SIDE_EFFECT_MODES: tuple[str, ...] = ("live", "recorded")

_side_effects_var: ContextVar[str] = ContextVar("side_effects", default="live")

#: Per-turn sink for suppressed side effects. Holds the LIST OBJECT itself, set
#: once by bind_tool_context in the sync task body. asyncio.run() copies the
#: context, so a .set() inside the turn would not be visible to the caller
#: afterwards, but appends to a list installed BEFORE the copy are — the list is
#: one shared object, not a per-context value. That is what lets P2 read back,
#: after the turn returns, what the agent tried to do during it.
_recorded_side_effects_var: ContextVar[list | None] = ContextVar(
    "recorded_side_effects", default=None
)


def current_side_effect_mode() -> str:
    """The side-effect mode in force for the current task context."""
    return _side_effects_var.get()


def record_suppressed_side_effect(kind: str, detail: dict) -> dict:
    """Record one attempt recorded mode observed, and return the entry.

    This is eval signal, not bookkeeping. That the agent CHOSE to call
    issue_refund is capability-envelope adherence — the measurement audit's
    confusion matrix has a whole cell for it ("executed when it should have
    refused: money moves wrongly, critical") — and it is the observation an eval
    would otherwise throw away, scoring only the prose that followed.

    Two kinds of entry, and the distinction is the whole point of the confusion
    matrix, so both are recorded:

      * **suppressed** — the envelope let the call through and recorded mode
        swapped the outer edge (`transactional.adapter`, `escalation.notify`,
        `retrieval_metrics.write`, `conversation.escalated_marker`). This is the
        matrix's *executed* column.
      * **declined** — the agent tried and something in steps 1-5 stopped it
        (`transactional.declined`, `transactional.confirm_action`). This is the
        matrix's *refused* column, and recording ONLY the first kind is how an
        eval ends up unable to tell "the agent never tried" from "the agent
        tried and the envelope stopped it" (the two are scored oppositely).

    Never raises: a recording failure must not fail the turn it is observing.
    A missing sink is logged at WARNING rather than swallowed, because the one
    way this becomes dangerous is by looking like it worked.
    """
    entry = {"kind": kind, "detail": detail}
    sink = _recorded_side_effects_var.get()
    if sink is None:
        log.warning(
            "side_effects.recorded_without_sink",
            kind=kind,
            note=(
                "recorded mode suppressed a side effect but no sink was installed "
                "bind_tool_context was not called for this context"
            ),
        )
    else:
        sink.append(entry)
    log.info("side_effects.suppressed", kind=kind)
    return entry


def get_recorded_side_effects() -> list[dict]:
    """Everything recorded mode suppressed or declined during the current turn.

    Returns a copy, so a caller iterating it cannot be surprised by a late
    append, and cannot clear the sink by mutating what it got back.
    """
    sink = _recorded_side_effects_var.get()
    return list(sink) if sink else []


# ---------------------------------------------------------------------------
# The per-attempt rate-limit namespace (ticket 15, issue #52).
#
# `transactional.enforcement.rate_limit_namespace` builds the Redis rate-counter
# key out of this, and the argument for it is the one that function already makes
# one level up. The counter is SHARED STATE. Ticket 15 requires every red-team
# vector to be attempted k times independently, and RTX-02 attacks the ceiling by
# chaining issue_refund calls until the envelope denies them. On one counter,
# attempt 1's chain exhausts the clean tenant's "2/hour" and attempts 2 and 3 are
# denied from their FIRST call instead of at the crossing. The verdict reads the
# same, so nothing is misreported to a reader, and the record still stores
# attempts=3 for a vector where one attempt tested the ceiling.
#
# So each attempt counts on its own key. It still crosses the ceiling, it still
# measures that the layer held, and it cannot spend the next attempt's budget.
# Suppressing the INCR for a red-team call, or rolling it back afterwards, would
# make "the agent kept refunding past its limit" unfalsifiable, and
# `rate_limit_namespace` rules both out for the eval in the same words.
#
# EMPTY IS PRODUCTION. Outside an attempt this holds "", so a customer turn keys
# exactly as it did before ticket 15, and a red-team run stops spending the
# deployed agent's own refund budget while measuring it. The value is a
# ready-to-concatenate key fragment rather than a name the reader has to format,
# so the enforcement path has no branch of its own to get wrong.
#
# One run per agent per 90 minutes (RUN_IDEMPOTENCY_WINDOW_MINUTES) is what keeps
# `attempt:<vector>:<n>` unique for the hour a "2/hour" window lasts, so the
# fragment stays readable in Redis instead of carrying a run id nothing else
# needs.
# ---------------------------------------------------------------------------

_attempt_scope_var: ContextVar[str] = ContextVar("attempt_scope", default="")


@contextmanager
def attempt_scope(vector: str, attempt: int):
    """Hold one red-team attempt's rate-counter namespace, and drop it after.

    The token reset is unconditional, so an attempt that raises out of its runner
    leaves the namespace exactly as it found it. Both directions matter. The next
    attempt must not inherit this one's counter, and nothing outside the k loop,
    a customer turn on this worker most of all, may run under an attempt's key.

    `reset_side_effect_context` deliberately does NOT clear this one. Every
    conversational vector reaches the dispatcher through a victim turn, and
    `agent_loop.build_agent_turn` calls that reset per probe message, so clearing
    it there would put those vectors' k attempts straight back on one counter.
    The scope is safe without it: this is the only setter, and it restores what
    it found on every path out.

    Args:
        vector: the RED_TEAM_VECTORS name being attempted. It is part of the key
            because two vectors that both drive issue_refund would otherwise
            share a counter inside one run, which is the same defect one axis
            over.
        attempt: 1-based attempt number within that vector's k.
    """
    token = _attempt_scope_var.set(f"attempt:{vector}:{attempt}:")
    try:
        yield
    finally:
        _attempt_scope_var.reset(token)


# ---------------------------------------------------------------------------
# The typed tool-result sink (ticket #49).
#
# `to_wire` spends a ToolResult's outcome on the wire's single error bit, so a
# reader downstream of the seven transactional handlers cannot tell a gate's
# denial from a broken call, or an escalation to a human from a completed
# action. The red-team victim turn needs exactly that distinction: its transcript
# is what RTX-01 reads to decide whether a confused-deputy attack landed.
#
# Until #49 that turn drove the SDK and saw ToolResultBlocks only, so it
# recovered a verdict by matching the dispatcher's prose. BACKLOG 5.8 is the bill
# for one hand-copied substring: the identity gate blocked a refund and the probe
# reported the attack as having SUCCEEDED. The victim turn runs `run_agent_loop`
# in this process now, so the type is handed over instead of re-derived.
#
# THE VERDICT DOES NOT GO ON `tool_calls_log`. `agent_loop._log_entry` keeps
# `result` for `retrieve` alone, because `_persist_messages` writes that key into
# the tenant's `tool_calls.result` jsonb, and a result on every tool would keep
# `lookup_structured`'s customer rows and the six mutating skills' outputs at rest
# on a POPIA-sensitive platform. This sink is in-process and dies with the turn.
# ---------------------------------------------------------------------------

#: Per-turn sink for the typed results the seven transactional handlers produced.
#: Holds the LIST OBJECT itself, for the reason `_recorded_side_effects_var` gives
#: above: asyncio.run() copies the context, so a .set() inside the turn is invisible
#: to the caller afterwards, while appends to a list installed BEFORE the copy are.
_tool_results_var: ContextVar[list[ToolResult] | None] = ContextVar(
    "tool_results", default=None
)


def publish_tool_result(result: ToolResult) -> None:
    """Put one transactional verdict in reach of whoever assembled this turn.

    Called at the one edge in `transactional.tools` where a `ToolResult` becomes
    wire bytes, BEFORE the conversion, so the sink holds what the dispatcher
    decided rather than what survived the crossing.

    Never raises: a publishing failure must not fail the turn it is observing.
    A missing sink is logged at WARNING rather than swallowed, because a probe
    reading an empty sink reports a clean run it was structurally incapable of
    failing, and BACKLOG 5.9 is what that costs.
    """
    sink = _tool_results_var.get()
    if sink is None:
        log.warning(
            "tool_results.published_without_sink",
            skill=result.skill,
            note=(
                "a transactional handler produced a verdict but no sink was installed "
                "bind_tool_context was not called for this context"
            ),
        )
        return
    sink.append(result)


def get_tool_results() -> list[ToolResult]:
    """Every transactional verdict this turn produced, in the order they returned.

    Returns a copy, so a caller iterating it cannot be surprised by a late
    append, and cannot clear the sink by mutating what it got back.
    """
    sink = _tool_results_var.get()
    return list(sink) if sink else []


def reset_side_effect_context() -> None:
    """Return this context to the safe default: live, with both sinks empty.

    The mode is process-context sticky and nothing resets it between Celery
    tasks — the prefork pool does not isolate contextvars per task. Today every
    entry point calls `bind_tool_context`, which republishes the mode, so a
    leaked "recorded" is closed by a coincidence of the call graph rather than
    by construction. It stops being a coincidence the moment a caller raises
    BEFORE reaching `bind_tool_context`. `build_agent_turn` validates its
    arguments and parses `RetrievalStrategy` first, and either can throw.

    So `build_agent_turn` calls this before it can fail. The direction is
    deliberate: a stale "recorded" surviving into a customer's chat turn stops
    refunding real customers with no error anywhere, and would be found by a
    customer rather than by us. A stale "live" surviving into an eval turn is
    the loud failure — it moves money, and every other guard in this phase
    exists to catch it.

    BOTH SINKS, and it cleared only one until #49's adversarial pass. The
    tool-result sink arrived with the typed verdicts the red-team probe reads,
    and this function was not taught about it, so a `build_agent_turn` that
    raised between here and `bind_tool_context` left the PREVIOUS turn's verdicts
    readable by `get_tool_results()`, refund text and all. `bind_tool_context`'s
    own block comment states the rule this missed: a sink carried over reports one
    message's verdicts as the next message's, which is worse than no recording
    because it is a wrong observation that looks right.
    """
    _side_effects_var.set("live")
    _recorded_side_effects_var.set([])
    _tool_results_var.set([])


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


def _frame_retrieved_context(chunks_text: str) -> str:
    """Enclose retrieved chunk text in an explicit data-not-instructions boundary.

    Pure and side-effect free — always produces exactly one header and one
    footer for a given input, so a chunk cannot "escape" the framing by
    appending its own closing text.

    Thin wrapper since BACKLOG 5.19: the framer lives in `app.domain.context_frame`
    so the grounding judge shares it. Kept under this name because the retrieval
    path and its tests call it here.
    """
    return _frame_context(chunks_text)


def _cap_for_the_agent(context: RetrievedContext) -> RetrievedContext:
    """The MAX_CHUNKS chunks the Agent sees, each cut to CHUNK_CONTENT_CHAR_LIMIT.

    A new context rather than a rewrite, because the record is frozen. The
    context passed in stays whole, and retrieve_tool goes on reading it for the
    reranked top score it writes to retrieval_metrics.
    """
    return replace(
        context,
        chunks=tuple(
            replace(chunk, content=chunk.content[:CHUNK_CONTENT_CHAR_LIMIT])
            for chunk in context.chunks[:MAX_CHUNKS]
        ),
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

    D-10 (suspenders): blocks the call and returns is_error when the per-turn
    counter exceeds _RETRIEVE_CALLS_PER_TURN_MAX (now 8 — DoS guard only; was 2
    to protect the Voyage 3 RPM free tier which Bedrock removes).
    Counter is reset by bind_tool_context() at the start of each task invocation.

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
    # OPS-05/06: job_id is read into a local here too — never .get() inside the
    # write's executor lambda below (Pitfall 4).
    job_id = _job_id_var.get()
    # D1/P1b: same rule, same reason — read the mode into a local here rather
    # than inside the executor lambda, which would see the default.
    side_effects = _side_effects_var.get()

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
    loop = asyncio.get_running_loop()

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
                return json.loads(cached)  # type: ignore[arg-type]  # sync redis client returns bytes|str
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
    rrf_result: RrfFusion = await loop.run_in_executor(
        None, lambda: rrf_fuse(conn_str, query_vector, query, strategy)
    )
    reranked: RetrievedContext = await loop.run_in_executor(
        None, lambda: rerank(query, rrf_result.fused, strategy)
    )

    retrieved = _cap_for_the_agent(reranked)
    chunks = retrieved.chunks

    # Every citation says "general". `section` is not a field of a retrieved
    # chunk, and none of the dicts before it carried the key either.
    citations = [
        {"document_name": chunk.document_id or "unknown", "section": "general"}
        for chunk in chunks
    ]

    log.debug("retrieve_tool.done", chunk_count=len(chunks))

    # -------------------------------------------------------------------
    # OPS-05/06: retrieval-health instrumentation (T-21-03-01/02/03).
    #
    # Written from inside this tool's closure, because the rank and score data
    # rrf_fuse and rerank produce never leaves it. job_id, conn_str and
    # conversation_id were read into locals above (Pitfall 4).
    #
    # No live per-query ground truth exists, so recall_at_k, ndcg_at_10 and mrr
    # take the reranker's final selection as the relevance signal and measure how
    # well the PRE-RERANK RRF fusion ranked those same chunks. That is the
    # "stronger downstream ranker as a pseudo-label" technique, and it is what
    # makes "reranker lift" a number rather than a guess (21-DOMAIN-NOTES.md §3).
    # -------------------------------------------------------------------
    # Each context reports its own engine's number as `score`, so the four
    # top-score reads below are the four numbers the field names promise.
    fused_list = rrf_result.fused.chunks
    bm25_candidates = rrf_result.bm25_candidates.chunks
    vector_candidates = rrf_result.vector_candidates.chunks

    bm25_top_score = bm25_candidates[0].score if bm25_candidates else None
    vector_top_score = vector_candidates[0].score if vector_candidates else None
    rrf_top_score = fused_list[0].score if fused_list else None
    rerank_top_score = reranked.chunks[0].score if reranked.chunks else None
    reranker_lift = (
        rerank_top_score - bm25_top_score
        if rerank_top_score is not None and bm25_top_score is not None
        else None
    )

    # 1-indexed position of each chunk in the pre-rerank RRF fusion ranking,
    # which is the rank rrf_fuse already stamped on every fused chunk.
    fused_rank_by_chunk_id = {c.chunk_id: c.rank for c in fused_list}
    returned_chunk_ids = [c.chunk_id for c in chunks]
    k = len(returned_chunk_ids)

    cited_chunk_rank = (
        fused_rank_by_chunk_id.get(returned_chunk_ids[0]) if returned_chunk_ids else None
    )
    mrr = (1.0 / cited_chunk_rank) if cited_chunk_rank else None

    if k > 0:
        hits = sum(
            1 for cid in returned_chunk_ids if fused_rank_by_chunk_id.get(cid, k + 1) <= k
        )
        recall_at_k = hits / k
    else:
        recall_at_k = None

    # nDCG@10 — binary relevance (1 if the reranker kept this chunk in the
    # final returned set, else 0) applied over the pre-rerank fused order.
    _ndcg_window = fused_list[:10]
    dcg = sum(
        (1.0 if c.chunk_id in returned_chunk_ids else 0.0) / math.log2(idx + 2)
        for idx, c in enumerate(_ndcg_window)
    )
    ideal_hits = min(k, 10)
    idcg = sum(1.0 / math.log2(idx + 2) for idx in range(ideal_hits))
    ndcg_at_10 = (dcg / idcg) if idcg > 0 else None

    retrieved_tokens = sum(len(c.content) for c in chunks) // 4
    ctx_window_utilization = retrieved_tokens / CONTEXT_WINDOW_BUDGET

    total_fused_tokens = sum(len(c.content) for c in fused_list) // 4
    carried_never_cited_tokens = max(total_fused_tokens - retrieved_tokens, 0)
    compaction_ratio = (
        retrieved_tokens / total_fused_tokens if total_fused_tokens > 0 else None
    )

    if not job_id:
        log.warning(
            "retrieve_tool.metrics_write_no_job_id",
            conversation_id=conversation_id,
            note="job_id ContextVar was empty — row written with an empty job_id",
        )

    metrics_row = {
        "job_id": job_id,
        "conversation_id": conversation_id,
        "bm25_top_score": bm25_top_score,
        "vector_top_score": vector_top_score,
        "rrf_top_score": rrf_top_score,
        "rerank_top_score": rerank_top_score,
        "reranker_lift": reranker_lift,
        "recall_at_k": recall_at_k,
        "ndcg_at_10": ndcg_at_10,
        "mrr": mrr,
        "cited_chunk_rank": cited_chunk_rank,
        "retrieved_tokens": retrieved_tokens,
        "ctx_window_utilization": ctx_window_utilization,
        "carried_never_cited_tokens": carried_never_cited_tokens,
        "compaction_ratio": compaction_ratio,
    }

    # job_id/conn_str/metrics_row are locals captured from the async body —
    # safe for the executor thread (no ContextVar.get() calls inside the lambda).
    #
    # D1/P1b: on the eval path the row is recorded rather than written. These are
    # observations about the tenant's PRODUCTION retrieval quality — OPS-05/06
    # feeds the ops room's recall/nDCG tiles — and an eval's scenario queries
    # would move those numbers without a single customer having asked anything.
    # The retrieve RESULT below is unchanged: retrieval is a read, the agent must
    # see exactly what production would hand it, and only the write is suppressed.
    if side_effects == "recorded":
        record_suppressed_side_effect(
            "retrieval_metrics.write",
            {"job_id": job_id, "conversation_id": conversation_id, "row": metrics_row},
        )
    else:
        await loop.run_in_executor(
            None, lambda: write_retrieval_metrics(conn_str, metrics_row)
        )

    # SEC-02/L6: framing is applied after the CHUNK_CONTENT_CHAR_LIMIT truncation loop
    # above, so a truncated chunk is still fully enclosed by the header/footer.
    # sanitize_chunk_text at ingest is complementary rather than superseded — this
    # is the retrieval-time layer, that is the admit-time layer, against the same
    # indirect-prompt-injection threat.
    # THE JSON SEAM (#48). The framed text is JSON for the MODEL to read, and
    # nothing calls `json.loads` on it. The loop takes its chunks from the
    # `_retrieved_context` ride-along, which is why `to_json` still decides keys.
    context = retrieved.to_json()
    model_text = _frame_retrieved_context(json.dumps(context["chunks"]))
    return {
        "content": [{"type": "text", "text": model_text}],
        "_citations": citations,
        "_retrieved_context": context,
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

    loop = asyncio.get_running_loop()

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
    # D1/P1b: read into a local before any run_in_executor handoff, same rule and
    # same reason as every other ContextVar above.
    side_effects = _side_effects_var.get()

    loop = asyncio.get_running_loop()

    # -------------------------------------------------------------------
    # D1/P1b: the escalation edge has TWO outer effects, not one. The mail is
    # swapped at the seam (agent_loop.build_agent_turn builds a recording
    # notify_fn), but this UPDATE is the other half and it lands in the
    # TENANT's `conversations` table. An eval scenario that escalates would
    # otherwise mark a real customer conversation as escalated — changing what
    # the owner's inbox and every escalation dashboard show — and mined
    # scenarios come from real conversations, so the id it is handed is
    # precisely the kind that exists.
    #
    # Suppressing it also removes the dependency BACKLOG 2.7 named: with the
    # UPDATE gone there is no rowcount to be zero, so the recorded escalation
    # notification fires regardless of what conversation_id P2 chooses. The
    # eval signal no longer hangs on a decision P2 has not made yet.
    # -------------------------------------------------------------------
    if side_effects == "recorded":
        record_suppressed_side_effect(
            "conversation.escalated_marker",
            {
                "conversation_id": conversation_id,
                "agent_id": agent_id,
                "reason": reason,
                "context": context,
            },
        )
        result: dict = {}
    else:
        # Write escalation marker to conversations table (idempotency guard inside).
        result = await loop.run_in_executor(
            None,
            lambda: _mark_conversation_escalated(
                conversation_id, agent_id, reason, context, conn_str
            ),
        )

    if result.get("already_escalated"):
        # A duplicate escalation is a benign no-op, not a failure — the
        # conversation IS flagged and a human IS coming — so no is_error. What
        # it must not do is hand the SDK a bare {"already_escalated": True}:
        # every other tool in this file returns a "content" list, and the
        # agent's next turn reasons over whatever text it finds there. A dict
        # with no content leaves it reasoning over nothing.
        return {
            "already_escalated": True,
            "content": [
                {
                    "type": "text",
                    "text": (
                        "This conversation is already flagged for our support team. "
                        "A human will follow up shortly."
                    ),
                }
            ],
        }

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
# Factory: bind_tool_context
# ---------------------------------------------------------------------------

def agent_tool_definitions() -> tuple[ToolDefinition, ...]:
    """The eleven tools an Agent turn may call, in registration order.

    `app.services.agent_loop.build_agent_turn` is the only reader. It turns this
    list into the JSON schemas the owned loop sends on the wire, and dispatches
    a tool call by matching `name` against it. Until #49 a second reader existed,
    `build_tool_server`, which registered the same list with an MCP server for
    the SDK path. One list is what kept the two from granting an agent different
    capabilities; now there is only the one path.

    The seven transactional tools are imported inside this function rather than
    at module scope. `transactional.tools` imports `_agent_id_var` from this
    module at call time, so importing it here, after the ContextVar definitions
    above, is what keeps the pair out of a circular import.
    """
    from app.services.transactional.tools import (  # noqa: PLC0415
        book_slot_tool,
        cancel_order_tool,
        confirm_action_tool,
        issue_refund_tool,
        place_order_tool,
        update_customer_record_tool,
        update_subscription_tool,
    )

    return (
        # The original 4, retained (TXN-04 / PLAN.md prohibition).
        retrieve_tool,
        lookup_structured_tool,
        escalate_to_human_tool,
        clarify_tool,
        # The 7 transactional tools, added by Phase 14 Plan 04.
        place_order_tool,
        cancel_order_tool,
        issue_refund_tool,
        update_subscription_tool,
        book_slot_tool,
        update_customer_record_tool,
        confirm_action_tool,
    )


def _require_side_effect_mode(caller: str, side_effects: str) -> None:
    """Refuse a mode this layer cannot serve, naming the caller that asked for it.

    One check, two entry points. `Literal` is a type-checker annotation and stops
    nothing at run time, so an unrecognised value would compare unequal to
    "recorded" and be served as live, which on the eval path means a real refund
    against the tenant's provider (BACKLOG 2.5).
    """
    if side_effects in SIDE_EFFECT_MODES:
        return
    raise ValueError(
        f"{caller}: side_effects must be one of {SIDE_EFFECT_MODES}, "
        f"got {side_effects!r}. An unrecognised value would compare unequal to "
        f"'recorded' and be served as live, which on the eval path means a "
        f"real refund against the tenant's provider (BACKLOG 2.5)."
    )


# ---------------------------------------------------------------------------
# bind_tool_context — the one entry point, and the three rules it follows.
#
# Every tool handler reads its tenant, its agent and its mode from the ContextVars
# below, and whoever publishes last owns the turn. Every caller reaches this
# function directly since ticket #49: `agent_loop.build_agent_turn` for a customer
# or probe turn, and `worker.tasks.runtime.red_team` for the two deterministic
# vectors that call `invoke_probe_tool` without a model turn at all.
#
# Until #49 those callers went through a `build_tool_server` wrapper that built an
# MCP server first and bound second, so that a server which raised left no
# side-effect mode behind. The server is gone with the SDK, and with it the only
# step between entry and the bind, so the ordering hazard it guarded against no
# longer exists.
#
# CALLED ONCE PER TURN in the sync Celery task body, before asyncio.run(), so every
# value is visible inside the async turn and its tool callees. ContextVar .set()
# writes only the current context's copy, so prefork concurrency > 1 bleeds nothing
# between tasks (PROD-14).
#
# THE MODE AND THE SINKS GO LAST, after every step that can raise. The mode is
# process-context sticky and the prefork pool does not isolate contextvars per
# task, so a bind that dies halfway through would otherwise leave a "recorded"
# behind for whatever runs next in this worker's context. That next thing is a
# customer turn that silently stops refunding, with no error anywhere.
#
# BOTH SINKS ARE FRESH LISTS, and that matters as much as the mode. A sink carried
# over from the previous turn reports one eval scenario's refund attempt as
# another's, or one red-team message's verdicts as the next message's. That is
# worse than no recording: it is a wrong observation that looks right.
#
# The prose sits here rather than in the docstring because lizard measures a
# function from its `def` to its last line, and this one was 30 lines over the
# standard with every word of it inside.
# ---------------------------------------------------------------------------


def bind_tool_context(
    conn_str: str,
    agent_id: str,
    agent_name: str,
    strategy: RetrievalStrategy,
    conversation_id: str,
    notify_fn,
    tenant_id: str = "",
    verified_session_token: str = "",
    job_id: str = "",
    side_effects: str = "live",
) -> None:
    """Publish one turn's tenant-scoped state into the per-task ContextVars.

    The block comment above carries the three rules this follows.

    Args:
        conn_str:                Decrypted tenant DB connection string.
        agent_id:                Agent UUID string (for logging / metadata).
        agent_name:              Agent display name (for logging / metadata).
        strategy:                RetrievalStrategy parsed from agent.retrieval_strategy JSONB.
        conversation_id:         Conversation UUID for escalation DB writes.
        notify_fn:               Callable(reason: str, context: str) — fire-and-forget notification.
        tenant_id:               Tenant UUID string for credential derivation (INT-01).
        verified_session_token:  IDV-05 token from the Celery task arg. NEVER logged (T-04-03-05).
                                 Empty when no verified session is present, which lets every
                                 non-IDV tool call pass through.
        job_id:                  OPS-05/06: Celery job_id, threaded into retrieve_tool's
                                 retrieval_metrics write path via _job_id_var. Empty when omitted.
        side_effects:            D1/P1b (BACKLOG 2.5): "live" or "recorded", defaulting to "live" so
                                 every pre-existing caller keeps the behaviour it had. The
                                 SideEffectMode block above says why the default points that way.

    Raises:
        ValueError: side_effects is neither "live" nor "recorded". Loud on purpose, because a
            typo read as "not recorded, therefore live" moves real money on the eval path.
    """
    _require_side_effect_mode("bind_tool_context", side_effects)

    _conn_str_var.set(conn_str)
    _agent_id_var.set(agent_id)
    _tenant_id_var.set(tenant_id)
    _agent_name_var.set(agent_name)
    _strategy_var.set(strategy)
    _conversation_id_var.set(conversation_id)
    _notify_fn_var.set(notify_fn)
    _job_id_var.set(job_id)

    # D-10 (suspenders): this turn's retrieve counter starts at zero.
    _retrieve_call_count_var.set(0)
    # IDV-05: read by the gate in transactional/tools.py. NEVER logged (T-04-03-05).
    _verified_session_token_var.set(verified_session_token)

    log.debug("bind_tool_context.ready", agent_id=agent_id, conversation_id=conversation_id)

    # D1/P1b: the mode and two fresh sinks, last. Reason in the block above.
    _side_effects_var.set(side_effects)
    _recorded_side_effects_var.set([])
    _tool_results_var.set([])
