"""
run_agent_turn — Celery task: Orchestrates a single Claude Agent SDK turn.

Position in M4 runtime flow:
    API route /v1/chat/agent/{agent_id}/message
      → dispatch run_agent_turn (runtime queue)
      → SSE stream back to widget client

Idempotency mechanism:
    READ guard on job_events: if an "agent.response" row already exists for this
    job_id, the task returns immediately — safe to retry without duplicate SDK
    calls or duplicate SSE events.

Security constraints (CLAUDE.md non-negotiable rules):
    - Task args: (job_id, agent_id, message, conversation_id) ONLY.
      NO conn_str, NO API keys in task args (CTL-08 / T-04-03-04).
    - conn_str fetched via fernet_decrypt(agent.neon_connection_string) at runtime.
    - message text is NEVER logged (T-04-03-05).
    - structlog lines reference only job_id, agent_id, conversation_id, counts.

SSE event sequence:
    agent.thinking      ← task begins, SDK turn starting
    agent.tool_call     ← each MCP tool invocation observed in stream
    agent.tool_result   ← each MCP tool result observed in stream
    agent.escalated     ← (optional) escalate_to_human ToolUseBlock detected
    agent.response      ← terminal event with text, citations, conversation_id
    agent.failed        ← only on final retry exhaustion

Queue: runtime (CLAUDE.md non-negotiable: both Celery queues always present)
"""

import asyncio
import json
import os
import re
import ssl
import time
import uuid
from datetime import datetime, timezone

import psycopg2
import redis as redis_lib
import structlog
from celery import chain as celery_chain
from langfuse import Langfuse
from sqlalchemy import text as sa_text

from app.core.config import AGENT_TURN_MODEL, settings
from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.models.job import Job
from app.models.prompt_version import PromptVersion
from app.services.agent_prompt import build_system_prompt
from app.services.agent_tools import build_tool_server, RetrievalStrategy
from app.services.escalation import send_escalation_email
from app.services.events import emit
from app.services.prompt_version_service import resolve_prompt_version
from app.utils.pii_firewall import scan_response
from app.worker.celery_app import celery_app
from app.worker.tasks.runtime.validators import run_gatekeeper, run_auditor, run_strategist
from app.worker.tasks.runtime.retrieval_eval import run_retrieval_faithfulness
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
)

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level sync Redis client (copied verbatim from retrieve.py lines 59-62)
# Strip query params; pass ssl_cert_reqs as Python constant.
# ---------------------------------------------------------------------------
_url_clean = settings.REDIS_URL.split("?")[0] if "?" in settings.REDIS_URL else settings.REDIS_URL
_ssl_opts: dict = {"ssl_cert_reqs": ssl.CERT_NONE} if _url_clean.startswith("rediss://") else {}
_redis = redis_lib.from_url(_url_clean, **_ssl_opts)

# ---------------------------------------------------------------------------
# OPS-04: module-level Langfuse client — mirrors validation_service.py's
# `_langfuse is None` no-op guard exactly. Optional at runtime: if
# LANGFUSE_PUBLIC_KEY is unset (or client construction fails), every call
# site below no-ops rather than raising (CLAUDE.md Rule 6 — v4 API only).
# ---------------------------------------------------------------------------
_langfuse: Langfuse | None = None
try:
    if os.environ.get("LANGFUSE_PUBLIC_KEY"):
        _langfuse = Langfuse()
except Exception:
    pass  # Langfuse unavailable — the agent turn still runs, just not traced

# ---------------------------------------------------------------------------
# Citation regex — parses CITATIONS block appended by system prompt instruction.
# ---------------------------------------------------------------------------
CITATIONS_REGEX = re.compile(r"CITATIONS:\n((?:- Document: .+ \| Section: .+\n?)+)")
_CITATION_ENTRY = re.compile(r"- Document: (.+) \| Section: (.+)")


# ---------------------------------------------------------------------------
# Module-level helpers — tenant DB writes via psycopg2 (parameterised only)
# ---------------------------------------------------------------------------

def _create_conversation_row(conn, agent_id: str) -> str:
    """Insert a new conversations row and return its UUID as a string.

    Uses psycopg2 directly (not SQLAlchemy) because the tenant DB is a
    separate Neon project — not the control DB that get_sync_db() connects to.

    The caller owns the connection lifecycle — this helper does NOT open or
    close the connection (PROD-05: one pooled connection per turn).

    Returns:
        UUID string of the newly created conversation row.
    """
    new_id = str(uuid.uuid4())
    sql = """
        INSERT INTO conversations (id, agent_id, created_at, metadata)
        VALUES (%s, %s, NOW(), %s::jsonb)
    """
    with conn.cursor() as cur:
        cur.execute(sql, (new_id, agent_id, "{}"))
    conn.commit()

    log.debug("_create_conversation_row.done", conversation_id=new_id)
    return new_id


def _validate_conversation_owner(conn, conv_id: str, agent_id: str) -> dict | None:
    """Fetch conversation row for (conv_id, agent_id) — ownership guard.

    Returns:
        dict with keys "id" and "metadata" if the conversation belongs to agent_id.
        None if no matching row is found (ownership violation or not-found).

    Security (T-04-03-01): The AND agent_id = %s clause prevents cross-agent
    conversation hijacking via a guessed UUID.
    The caller owns the connection lifecycle — this helper does NOT open or
    close the connection (PROD-05: one pooled connection per turn).
    """
    sql = """
        SELECT id, metadata
        FROM conversations
        WHERE id = %s AND agent_id = %s
        LIMIT 1
    """
    with conn.cursor() as cur:
        cur.execute(sql, (conv_id, agent_id))
        row = cur.fetchone()

    if row is None:
        return None

    row_id, metadata = row
    return {"id": str(row_id), "metadata": metadata or {}}


def _set_sdk_session_id(conn, conv_id: str, sdk_session_id: str) -> None:
    """Store the SDK's internal session_id in conversations.metadata.

    Resolves R-02: SDK session_id captured from ResultMessage and persisted
    so subsequent turns can pass resume=sdk_session_id to ClaudeAgentOptions.

    Security (T-04-03-07): jsonb_set update uses parameterised %s values —
    sdk_session_id is never string-concatenated into SQL.
    The caller owns the connection lifecycle — this helper does NOT open or
    close the connection (PROD-05: one pooled connection per turn).
    """
    sql = """
        UPDATE conversations
        SET metadata = jsonb_set(
            COALESCE(metadata, '{}'),
            '{sdk_session_id}',
            to_jsonb(%s::text)
        )
        WHERE id = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (sdk_session_id, conv_id))
    conn.commit()

    log.debug("_set_sdk_session_id.done", conversation_id=conv_id)


def _set_prompt_version_id(conn, conv_id: str, prompt_version_id: str) -> None:
    """Store the resolved prompt_version_id in conversations.metadata (OPS-16).

    Mirrors _set_sdk_session_id's jsonb_set path exactly, so subsequent turns
    on this conversation can read it back and reuse it without re-rolling
    (A-CANARY: canary is sticky per-conversation — no mid-conversation persona
    flip). Called exactly once, on the turn that first resolves a version for
    a conversation (see _resolve_turn_prompt_version below).

    Security (T-04-03-07): jsonb_set update uses parameterised %s values —
    prompt_version_id is never string-concatenated into SQL.
    The caller owns the connection lifecycle — this helper does NOT open or
    close the connection (PROD-05: one pooled connection per turn).
    """
    sql = """
        UPDATE conversations
        SET metadata = jsonb_set(
            COALESCE(metadata, '{}'),
            '{prompt_version_id}',
            to_jsonb(%s::text)
        )
        WHERE id = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (prompt_version_id, conv_id))
    conn.commit()

    log.debug("_set_prompt_version_id.done", conversation_id=conv_id)


def _resolve_turn_prompt_version(
    db,
    tenant_conn,
    *,
    agent_id: str,
    local_conversation_id: str,
    existing_prompt_version_id: str | None,
) -> tuple[str | None, dict | None]:
    """Resolve the prompt version to serve this turn, sticky per conversation (OPS-16).

    First turn of a conversation (existing_prompt_version_id is None): calls
    resolve_prompt_version (weighted pick, control DB) and — if a version was
    found — persists the choice on conversations.metadata (tenant DB) so every
    subsequent turn on this conversation reuses it (A-CANARY: no mid-
    conversation persona flip; the version is never re-rolled).

    Subsequent turns (existing_prompt_version_id provided): re-fetches that
    EXACT version's soul fields by id — never re-rolls, never re-picks.

    T-21-09-05 (never fails a turn): any exception here is caught and treated
    as "no version resolved" — the caller falls back to the agent's live
    soul_* columns unchanged (soul_override=None passed to
    build_system_prompt), exactly like an agent with zero prompt_versions
    rows. A resolution failure degrades canary correlation only; it never
    blocks or fails the served turn.

    Returns:
        (prompt_version_id, soul_override) — both None on no-version-exists,
        resolution failure, or a stale/deleted stored version id.
    """
    try:
        if existing_prompt_version_id:
            pv = db.get(PromptVersion, existing_prompt_version_id)
            if pv is None:
                return None, None
            return str(pv.id), {
                "soul_role": pv.soul_role,
                "soul_voice": pv.soul_voice,
                "soul_do_list": pv.soul_do_list,
                "soul_donot_list": pv.soul_donot_list,
            }

        resolved_id, soul_override = resolve_prompt_version(db, agent_id)
        if resolved_id is None:
            return None, None

        _set_prompt_version_id(tenant_conn, local_conversation_id, resolved_id)
        return resolved_id, soul_override
    except Exception as exc:
        log.warning(
            "run_agent_turn.prompt_version_resolve_failed",
            agent_id=agent_id,
            conversation_id=local_conversation_id,
            error=str(exc),
        )
        return None, None


def _persist_messages(
    conn,
    conv_id: str,
    user_msg: str,
    assistant_msg: str,
    tool_calls_log: list[dict],
) -> str:
    """Insert user message, assistant message, and tool_call rows.

    Inserts in a single transaction:
      1. user message row in messages table
      2. assistant message row in messages table
      3. one tool_calls row per ToolUseBlock observed during the turn

    All values are passed as %s parameters (T-04-03-07).
    The caller owns the connection lifecycle — this helper does NOT open or
    close the connection (PROD-05: one pooled connection per turn).

    Returns the assistant message's id (WIRE-05): the caller needs it to put
    on the terminal agent.response event's payload, because the customer
    widget cannot submit feedback for a reply it has no way to name.
    """
    with conn.cursor() as cur:
        # Insert user message
        user_msg_id = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO messages (id, conversation_id, role, content, created_at)
            VALUES (%s, %s, 'user', %s, NOW())
            """,
            (user_msg_id, conv_id, user_msg),
        )

        # Insert assistant message
        assistant_msg_id = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO messages (id, conversation_id, role, content, created_at)
            VALUES (%s, %s, 'assistant', %s, NOW())
            """,
            (assistant_msg_id, conv_id, assistant_msg),
        )

        # Insert tool_calls rows (one per ToolUseBlock observed)
        for tc in tool_calls_log:
            cur.execute(
                """
                INSERT INTO tool_calls (id, message_id, tool_name, arguments, result, created_at)
                VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, NOW())
                """,
                (
                    str(uuid.uuid4()),
                    assistant_msg_id,
                    tc.get("tool_name", ""),
                    json.dumps(tc.get("input", {})),
                    json.dumps(tc.get("result", {})),
                ),
            )
    conn.commit()

    log.debug(
        "_persist_messages.done",
        conversation_id=conv_id,
        tool_call_count=len(tool_calls_log),
    )

    return assistant_msg_id


def _write_turn_metrics(
    conn,
    *,
    job_id: str,
    conversation_id: str,
    agent_id: str,
    cost_usd: float | None,
    num_turns: int | None,
    latency_ms: int,
    escalated: bool,
    tool_count: int,
    stop_reason: str | None,
    prompt_version_id: str | None = None,
) -> None:
    """Insert one turn_metrics row for a completed production turn (OPS-01).

    Called AFTER the terminal agent.response emit and the idempotency-marking
    db.commit() in run_agent_turn — telemetry must never delay or block the
    served turn (must_haves prohibition).

    prompt_version_id (OPS-16): the version resolved by
    _resolve_turn_prompt_version for this turn (None when the agent has no
    prompt_versions rows yet, or resolution failed — see T-21-09-05). The
    column exists since migration 0009, reserved for exactly this write.

    Failure handling: any exception here is caught by the caller
    (run_agent_turn wraps this call in its own try/except) — a metrics write
    failure degrades observability only, it never fails a served turn
    (T-21-01-03).

    The caller owns the connection lifecycle — this helper does NOT open or
    close the connection (same PROD-05 convention as _persist_messages).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO turn_metrics
                (id, job_id, conversation_id, agent_id, cost_usd, num_turns,
                 latency_ms, escalated, tool_count, stop_reason,
                 prompt_version_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                str(uuid.uuid4()),
                job_id,
                conversation_id,
                agent_id,
                cost_usd,
                num_turns,
                latency_ms,
                escalated,
                tool_count,
                stop_reason,
                prompt_version_id,
            ),
        )
    conn.commit()

    log.debug(
        "_write_turn_metrics.done",
        job_id=job_id,
        conversation_id=conversation_id,
        latency_ms=latency_ms,
        tool_count=tool_count,
    )


def _emit_langfuse_turn_trace(
    *,
    job_id: str,
    agent_id: str,
    model: str,
    num_turns: int | None,
    total_cost_usd: float | None,
    latency_ms: int,
    stop_reason: str | None,
) -> None:
    """Emit one Langfuse v4 trace+generation for a completed agent turn (OPS-04).

    Mirrors validation_service.py's `_log_verdict` shape exactly:
    start_as_current_generation(...) context manager + create_score(trace_id=...)
    + a single flush() call. Correlated to the turn_metrics row by job_id
    (both use job_id as the Langfuse trace_id / SQL correlation key).

    No-ops when _langfuse is None (LANGFUSE_PUBLIC_KEY unset — Pitfall 3/5).
    flush() is called exactly ONCE per turn, not per generation/score, to
    avoid the synchronous-flush latency hang documented in RESEARCH.md
    Pitfall 3 (Phase 15 live-verification: 596s -> 52s after removing
    per-call flush on a hot path).

    Never raises: the caller (run_agent_turn) also wraps this in try/except,
    but the internal try/except here means a Langfuse outage degrades
    observability only — it can never fail or stall a served turn
    (T-21-01-02, CLAUDE.md Rule 6 — v4 API only, no start_span/start_generation).
    """
    if _langfuse is None:
        return
    try:
        with _langfuse.start_as_current_generation(
            name="agent-turn",
            model=model,
            metadata={"agent_id": agent_id, "job_id": job_id},
            output={
                "num_turns": num_turns,
                "total_cost_usd": total_cost_usd,
                "stop_reason": stop_reason,
            },
        ):
            pass  # generation data is set via context manager params

        if total_cost_usd is not None:
            _langfuse.create_score(
                name="turn_cost_usd",
                value=total_cost_usd,
                trace_id=job_id,
                data_type="NUMERIC",
            )
        _langfuse.create_score(
            name="turn_latency_ms",
            value=latency_ms,
            trace_id=job_id,
            data_type="NUMERIC",
        )
        _langfuse.flush()  # once per turn — never per-generation (Pitfall 3)
    except Exception as exc:
        log.warning("langfuse.agent_turn_trace_failed", job_id=job_id, error=str(exc))


# ---------------------------------------------------------------------------
# Citation extractor
# ---------------------------------------------------------------------------

def _extract_citations(text: str) -> list[dict]:
    """Parse the CITATIONS block from response text.

    Returns:
        List of {"document_name": str, "section": str} dicts.
        Empty list if no CITATIONS block is present (logs WARNING — not an error).
    """
    match = CITATIONS_REGEX.search(text)
    if not match:
        log.warning("citation_block_missing", response_length=len(text))
        return []

    citations = []
    for entry_match in _CITATION_ENTRY.finditer(match.group(1)):
        citations.append({
            "document_name": entry_match.group(1).strip(),
            "section": entry_match.group(2).strip(),
        })
    return citations


# ---------------------------------------------------------------------------
# Async SDK turn helper — bridged into sync Celery task via asyncio.run()
# ---------------------------------------------------------------------------

async def _run_sdk_turn(
    message: str,
    options: "ClaudeAgentOptions",
    job_id: str,
    local_conversation_id: str,
    conn_str: str,
    db,
    redis,
) -> dict:
    """Run one Claude Agent SDK turn and collect all streaming output.

    Opened and closed by the async context manager — do NOT close the client
    manually. Called via asyncio.run(asyncio.wait_for(_run_sdk_turn(...), timeout=90)).

    Returns:
        dict with keys:
            response_text       — accumulated text from TextBlock messages
            tool_calls_log      — list of dicts per ToolUseBlock observed
            escalated           — True if escalate_to_human ToolUseBlock was seen
            escalation_reason   — reason from escalate_to_human input, or None
            escalation_context  — context from escalate_to_human input, or None
            sdk_session_id      — ResultMessage.session_id, or None
            total_cost_usd      — ResultMessage.total_cost_usd, or None (OPS-01)
            num_turns           — ResultMessage.num_turns, or None (OPS-01)
            stop_reason         — ResultMessage.stop_reason, or None (OPS-01)

    Security (T-04-03-02, T-04-03-03):
        - Tool-call streaming is the evidence source for escalation detection.
          Escalation is set based on ToolUseBlock.name — not on parsed text prose.
        - system_prompt enforces persona lock via build_system_prompt().
    """
    response_text = ""
    tool_calls_log: list[dict] = []
    escalated = False
    escalation_reason: str | None = None
    escalation_context: str | None = None
    sdk_session_id_out: str | None = None
    total_cost_usd_out: float | None = None
    num_turns_out: int | None = None
    stop_reason_out: str | None = None

    async with ClaudeSDKClient(options=options) as client:
        await client.query(message)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        response_text += block.text

                    elif isinstance(block, ToolUseBlock):
                        tool_name_short = block.name.removeprefix("mcp__customer-tools__")
                        emit(
                            job_id,
                            "agent.tool_call",
                            {"tool_name": tool_name_short, "input": block.input},
                            db,
                            redis,
                        )
                        tool_calls_log.append({
                            "tool_name": tool_name_short,
                            "input": block.input,
                        })
                        # Escalation detection: based on ToolUseBlock evidence only (T-04-03-03)
                        if block.name.endswith("escalate_to_human"):
                            escalated = True
                            escalation_reason = block.input.get("reason")
                            escalation_context = block.input.get("context")

                    elif isinstance(block, ToolResultBlock):
                        tool_name_short = getattr(block, "name", "unknown").removeprefix(
                            "mcp__customer-tools__"
                        )
                        emit(
                            job_id,
                            "agent.tool_result",
                            {
                                "tool_name": tool_name_short,
                                "summary": str(getattr(block, "content", ""))[:200],
                            },
                            db,
                            redis,
                        )
                        # Capture retrieve result for Auditor (M5 — plan 05-04)
                        for tc in reversed(tool_calls_log):
                            if tc.get("tool_name") == "retrieve" and "result" not in tc:
                                tc["result"] = str(getattr(block, "content", ""))[:1800]
                                break

            elif isinstance(msg, ResultMessage):
                sdk_session_id_out = msg.session_id
                # D-10 fix phase 2: log the full stop reason so we can distinguish
                # error_max_turns / error_max_budget / error_during_execution / success.
                # These fields are the ONLY reliable disambiguator when response_text
                # is empty — all stop paths produce the same empty-text signature.
                # (See ResultMessage dataclass in claude_agent_sdk; field names are stable
                # in 0.1.81 and confirmed by inspection of the installed package.)
                log.info(
                    "_run_sdk_turn.result",
                    job_id=job_id,
                    subtype=msg.subtype,
                    is_error=msg.is_error,
                    num_turns=msg.num_turns,
                    total_cost_usd=msg.total_cost_usd,
                    stop_reason=msg.stop_reason,
                    api_error_status=msg.api_error_status,
                    response_length=len(response_text),
                )
                if msg.is_error:
                    log.warning(
                        "_run_sdk_turn.sdk_error",
                        job_id=job_id,
                        subtype=msg.subtype,
                        is_error=msg.is_error,
                        num_turns=msg.num_turns,
                        total_cost_usd=msg.total_cost_usd,
                    )
                # OPS-01: carry these through the return dict — previously logged
                # only, now persisted to turn_metrics by run_agent_turn (below).
                total_cost_usd_out = msg.total_cost_usd
                num_turns_out = msg.num_turns
                stop_reason_out = msg.stop_reason

    return {
        "response_text": response_text,
        "tool_calls_log": tool_calls_log,
        "escalated": escalated,
        "escalation_reason": escalation_reason,
        "escalation_context": escalation_context,
        "sdk_session_id": sdk_session_id_out,
        "total_cost_usd": total_cost_usd_out,
        "num_turns": num_turns_out,
        "stop_reason": stop_reason_out,
    }


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=5,
    queue="runtime",
    name="run_agent_turn",
)
def run_agent_turn(
    self,
    job_id: str,
    agent_id: str,
    message: str,
    conversation_id: str | None,
    verified_session_token: str = "",
) -> dict:
    """Orchestrate one agent conversational turn with full SSE event emission.

    Idempotent: returns {"status": "already_complete", "job_id": job_id}
    immediately if an "agent.response" event row already exists for this
    job_id (duplicate delivery / retry safety).

    Args:
        job_id:                  UUID string of the runtime chat job.
        agent_id:                UUID string of the agent handling this conversation.
        message:                 Raw user message text. NEVER logged (T-04-03-05).
        conversation_id:         UUID string of an existing conversation, or None for
                                 the first turn (triggers conversation row creation).
        verified_session_token:  IDV-05 verified session token. NEVER logged
                                 (parity with message, T-04-03-05). Empty string
                                 when no verified session — all non-IDV calls pass
                                 through. Added in Phase 17 Plan 03; 4-arg existing
                                 dispatches remain valid via the empty default.

    Returns:
        {"status": "already_complete", "job_id": job_id}  — idempotent path
        {}                                                  — all other paths

    Security:
        conn_str is decrypted at runtime from agent.neon_connection_string
        (Fernet) and NEVER appears in task args, logs, or return values (CTL-08).
    """
    with get_sync_db() as db:
        # ------------------------------------------------------------------
        # Idempotency guard — exit immediately if agent.response already exists
        # for this job_id. Prevents duplicate SDK calls and duplicate SSE events
        # on Celery retry or at-least-once redelivery from Redis.
        # ------------------------------------------------------------------
        existing = db.execute(
            sa_text(
                "SELECT 1 FROM job_events"
                " WHERE job_id = :jid AND event_type = 'agent.response' LIMIT 1"
            ),
            {"jid": job_id},
        ).fetchone()
        if existing:
            log.info("run_agent_turn.idempotent_skip", job_id=job_id)
            return {"status": "already_complete", "job_id": job_id}

        # ------------------------------------------------------------------
        # Fetch agent from control DB — required for soul fields, retrieval
        # strategy, and the encrypted neon_connection_string.
        # ------------------------------------------------------------------
        agent = db.get(Agent, agent_id)
        if agent is None:
            log.error(
                "run_agent_turn.agent_not_found",
                job_id=job_id,
                agent_id=agent_id,
            )
            return {}

        # ------------------------------------------------------------------
        # Fetch job from control DB — required to update status on completion.
        # ------------------------------------------------------------------
        job = db.get(Job, job_id)
        if job is None:
            log.error("run_agent_turn.job_not_found", job_id=job_id)
            return {}

        # ------------------------------------------------------------------
        # Decrypt connection string at runtime — NEVER in task args (CTL-08).
        # conn_str is intentionally not logged.
        # ------------------------------------------------------------------
        conn_str = fernet_decrypt(agent.neon_connection_string)

        # PROD-05: open ONE pooled tenant-DB connection for all per-turn write
        # helpers (_create_conversation_row, _validate_conversation_owner,
        # _set_sdk_session_id, _persist_messages).  Reduces connection churn
        # from 4 opens/closes per turn to 1.  Uses the pooled endpoint
        # (agent.neon_connection_string) — PgBouncer transaction-mode
        # compatible: no named prepared statements, no SET session vars.
        # Closed in finally even when _run_sdk_turn raises.
        tenant_conn = psycopg2.connect(conn_str, connect_timeout=5)
        try:
            # --------------------------------------------------------------
            # EVENT 1: agent.thinking — confirms task is running for this agent
            # --------------------------------------------------------------
            emit(job_id, "agent.thinking", {"agent_id": agent_id}, db, _redis)

            # --------------------------------------------------------------
            # Conversation branching (R-02 resolution):
            #   First turn  (conversation_id is None) → create row, resume=None
            #   Subsequent  (conversation_id provided) → validate ownership,
            #               sdk_resume = metadata.get("sdk_session_id")
            # --------------------------------------------------------------
            sdk_resume: str | None = None
            # OPS-16: only set on subsequent turns when the conversation already
            # carries a resolved prompt_version_id — see _resolve_turn_prompt_version.
            existing_prompt_version_id: str | None = None

            if conversation_id is None:
                # First turn: create conversation row in tenant DB
                local_conversation_id = _create_conversation_row(tenant_conn, agent_id)
                log.debug(
                    "run_agent_turn.first_turn",
                    job_id=job_id,
                    conversation_id=local_conversation_id,
                )
            else:
                # Subsequent turn: validate ownership (T-04-03-01)
                conv_row = _validate_conversation_owner(tenant_conn, conversation_id, agent_id)
                if conv_row is None:
                    log.warning(
                        "run_agent_turn.conversation_not_found",
                        job_id=job_id,
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                    )
                    emit(
                        job_id,
                        "agent.failed",
                        {"error": "conversation_not_found"},
                        db,
                        _redis,
                    )
                    return {}
                local_conversation_id = conv_row["id"]
                sdk_resume = conv_row["metadata"].get("sdk_session_id")
                existing_prompt_version_id = conv_row["metadata"].get("prompt_version_id")

            # --------------------------------------------------------------
            # Build retrieval strategy, tool server, system prompt, options
            # --------------------------------------------------------------
            strategy = RetrievalStrategy.model_validate(agent.retrieval_strategy or {})

            tool_server = build_tool_server(
                conn_str=conn_str,
                agent_id=str(agent.id),
                agent_name=agent.name,
                strategy=strategy,
                conversation_id=str(local_conversation_id),
                notify_fn=lambda r, c: send_escalation_email(agent, r, c),
                tenant_id=str(agent.tenant_id),
                verified_session_token=verified_session_token,
                job_id=job_id,
            )

            # ----------------------------------------------------------------
            # OPS-16: canary prompt-version resolution — sticky per conversation,
            # never fails a turn (T-21-09-05). See _resolve_turn_prompt_version's
            # own docstring for the first-turn-vs-subsequent-turn distinction.
            # ----------------------------------------------------------------
            prompt_version_id, soul_override = _resolve_turn_prompt_version(
                db,
                tenant_conn,
                agent_id=agent_id,
                local_conversation_id=str(local_conversation_id),
                existing_prompt_version_id=existing_prompt_version_id,
            )

            system_prompt = build_system_prompt(agent, soul_override=soul_override)

            # D-10 note (13-07): The Voyage 3 RPM free-tier prompt-level retrieve-cap
            # instruction was removed now that embeddings move to Bedrock (PROD-06).
            # Bedrock has no comparable RPM constraint; the per-turn retrieve counter in
            # agent_tools.retrieve_tool remains active as a DoS guard (ceiling raised to 8).

            # R-05: allowed_tools use full MCP namespace mcp__customer-tools__*
            # D-10 fix phase 1 (2026-06-01): max_turns raised from 3 to 6.
            #   Root cause: max_turns=3 cut the agent off after the retrieve tool
            #   round-trip (tool_use + tool_result = 2 turns), leaving no turn to
            #   compose the final text answer → empty response_text.
            #   The Voyage RPM guard is now enforced solely by the tool-level counter
            #   in agent_tools.retrieve_tool (blocks the 3rd call per turn), making
            #   max_turns free to cover the full retrieve → synthesis cycle.
            #   6 turns is sufficient for: thinking + retrieve + synthesis + any
            #   clarify/escalate follow-ups while still bounding DoS risk (T-04-03-06).
            # D-10 fix phase 2 (2026-06-01): max_budget_usd raised from 0.05 to
            #   settings.AGENT_MAX_BUDGET_USD (default 0.50).
            #   Root cause (additional): the 0.05 USD cap was too tight for a
            #   turn that uses extended thinking (~38s) + retrieved context + synthesis.
            #   A Haiku extended-thinking + retrieve + synthesis turn can exceed $0.05.
            #   When the budget is exceeded the CLI emits result{subtype:error_max_budget,
            #   is_error:true} → receive_response() terminates → response_text stays ""
            #   with no exception raised (identical empty-text signature to max_turns).
            #   0.50 USD gives headroom while still serving as a DoS guardrail.
            #   Configure via AGENT_MAX_BUDGET_USD env var for tighter prod limits.
            # R-05: allowed_tools suppresses SDK permission prompts only.
            # The capability envelope check inside each transactional tool handler
            # is the real access gate (fail-closed) — T-14-04-03.
            options = ClaudeAgentOptions(
                # AGENT_TURN_MODEL, not a literal — eval_runs.config.model_id
                # reads the same constant, so a score can never be attributed
                # to a model that did not serve the turn (migration 0013).
                model=AGENT_TURN_MODEL,
                system_prompt=system_prompt,
                mcp_servers={"customer-tools": tool_server},
                allowed_tools=[
                    # Original 4 tools — retained (TXN-04 requirement)
                    "mcp__customer-tools__retrieve",
                    "mcp__customer-tools__lookup_structured",
                    "mcp__customer-tools__escalate_to_human",
                    "mcp__customer-tools__clarify",
                    # Phase 14 Plan 04 — 7 transactional tools
                    # Listing here suppresses SDK permission prompts only;
                    # the capability envelope in each handler is the real gate.
                    "mcp__customer-tools__place_order",
                    "mcp__customer-tools__cancel_order",
                    "mcp__customer-tools__issue_refund",
                    "mcp__customer-tools__update_subscription",
                    "mcp__customer-tools__book_slot",
                    "mcp__customer-tools__update_customer_record",
                    "mcp__customer-tools__confirm_action",
                ],
                resume=sdk_resume,
                max_turns=6,   # D-10 fix: was 3 (too low — cut off synthesis after retrieve)
                max_budget_usd=settings.AGENT_MAX_BUDGET_USD,  # D-10 fix phase 2: was 0.05 (too low for thinking+retrieve+synthesis)
            )

            # --------------------------------------------------------------
            # Bridge async SDK into sync Celery worker.
            # asyncio.run() is the required pattern for Python 3.12 (see CLAUDE.md).
            # Wall-clock safety: asyncio.wait_for(timeout=90) is inside the run()
            # call. T-04-03-06 DoS guard: max_turns=6, max_budget_usd=settings.AGENT_MAX_BUDGET_USD.
            # D-11: raised from 30s to 90s — warm-but-not-hot Agent SDK subprocess
            # needs up to 90s on slower ARM VMs; SSE layer retains 120s (30s headroom).
            #
            # OPS-01: monotonic start captured immediately before the call so
            # latency_ms reflects only the SDK turn itself, not queueing/setup.
            # --------------------------------------------------------------
            _turn_start_monotonic = time.monotonic()
            result = asyncio.run(
                asyncio.wait_for(
                    _run_sdk_turn(
                        message=message,
                        options=options,
                        job_id=job_id,
                        local_conversation_id=local_conversation_id,
                        conn_str=conn_str,
                        db=db,
                        redis=_redis,
                    ),
                    timeout=90,
                )
            )
            latency_ms = int((time.monotonic() - _turn_start_monotonic) * 1000)

            response_text: str = result["response_text"]
            tool_calls_log: list[dict] = result["tool_calls_log"]
            escalated: bool = result["escalated"]
            escalation_reason: str | None = result["escalation_reason"]
            escalation_context: str | None = result["escalation_context"]
            sdk_session_id_out: str | None = result["sdk_session_id"]
            total_cost_usd: float | None = result.get("total_cost_usd")
            num_turns: int | None = result.get("num_turns")
            stop_reason: str | None = result.get("stop_reason")

            # --------------------------------------------------------------
            # SEC-01/L4: synchronous PII output firewall (T-18-SEC-01, T-18-SEC-02).
            # Must run here, synchronously, because the Gatekeeper/Auditor/Strategist
            # validators dispatch ASYNCHRONOUSLY after the response has already
            # streamed to the customer (see the validator chord below) — a leak
            # cannot wait for a post-hoc judge. Rebinding response_text here, before
            # any consumer reads it, guarantees the served text (SSE emit), the
            # persisted text (_persist_messages), the cited text (_extract_citations)
            # and the judged text (the validator chord) can never diverge — a single
            # substitution covers all four. The firewall call below takes no flag and
            # reads no config, so nothing in the response text, the agent soul, or an
            # ingested document can disable it. Citations are extracted from the deflection
            # when a flag fires, which correctly yields an empty citation list — a
            # deflection cites nothing.
            filtered_text, pii_detector = scan_response(response_text)
            if pii_detector is not None:
                log.warning(
                    "pii_firewall.response_deflected",
                    job_id=job_id,
                    agent_id=agent_id,
                    conversation_id=str(local_conversation_id),
                    detector=pii_detector,
                    original_length=len(response_text),
                )
            response_text = filtered_text

            # --------------------------------------------------------------
            # Citation extraction — missing block yields [] + warning (not failure)
            # --------------------------------------------------------------
            citations_list = _extract_citations(response_text)

            # --------------------------------------------------------------
            # R-02 resolution: persist SDK session_id for next-turn resume
            # Only on first turn; only when sdk_session_id was returned
            # --------------------------------------------------------------
            if conversation_id is None and sdk_session_id_out:
                _set_sdk_session_id(tenant_conn, local_conversation_id, sdk_session_id_out)

            # --------------------------------------------------------------
            # Persist messages and tool calls to tenant DB
            # --------------------------------------------------------------
            assistant_msg_id = _persist_messages(
                conn=tenant_conn,
                conv_id=local_conversation_id,
                user_msg=message,
                assistant_msg=response_text,
                tool_calls_log=tool_calls_log,
            )

            # --------------------------------------------------------------
            # EVENT (optional): agent.escalated — fires BEFORE agent.response
            # --------------------------------------------------------------
            if escalated:
                emit(
                    job_id,
                    "agent.escalated",
                    {
                        "reason": escalation_reason,
                        "context": escalation_context,
                        "conversation_id": str(local_conversation_id),
                    },
                    db,
                    _redis,
                )

            # --------------------------------------------------------------
            # EVENT (terminal): agent.response — widget renders this payload
            # --------------------------------------------------------------
            emit(
                job_id,
                "agent.response",
                {
                    "text": response_text,
                    "citations": citations_list,
                    "conversation_id": str(local_conversation_id),
                    # WIRE-05: the assistant message's own id — the non-secret
                    # correlation key the widget feedback route already
                    # requires as a body field. Useless to a caller who does
                    # not also hold a valid widget token scoped to this agent
                    # (T-23-GA-01).
                    "message_id": assistant_msg_id,
                },
                db,
                _redis,
            )

            # Mark job complete
            job.status = "complete"
            job.finished_at = datetime.now(timezone.utc)
            db.commit()

            # --------------------------------------------------------------
            # OPS-01/OPS-04: telemetry — runs AFTER the terminal agent.response
            # emit and the idempotency-marking commit above, so a telemetry
            # failure can never delay or fail the served turn (must_haves
            # prohibition: "the turn_metrics write NEVER blocks or precedes
            # the terminal agent.response SSE emit"). The INSERT gets its own
            # try/except (separate from _emit_langfuse_turn_trace's internal
            # guard) — a turn_metrics write failure must never fail or retry
            # an already-served turn (T-21-01-03).
            # --------------------------------------------------------------
            try:
                _write_turn_metrics(
                    tenant_conn,
                    job_id=job_id,
                    conversation_id=local_conversation_id,
                    agent_id=agent_id,
                    cost_usd=total_cost_usd,
                    num_turns=num_turns,
                    latency_ms=latency_ms,
                    escalated=escalated,
                    tool_count=len(tool_calls_log),
                    stop_reason=stop_reason,
                    prompt_version_id=prompt_version_id,
                )
            except Exception as metrics_exc:
                log.warning(
                    "run_agent_turn.turn_metrics_write_failed",
                    job_id=job_id,
                    error=str(metrics_exc),
                )
            _emit_langfuse_turn_trace(
                job_id=job_id,
                agent_id=agent_id,
                model=AGENT_TURN_MODEL,
                num_turns=num_turns,
                total_cost_usd=total_cost_usd,
                latency_ms=latency_ms,
                stop_reason=stop_reason,
            )

            # Dispatch validation chain (M5 — VAL-04)
            retrieve_results = [tc.get("result") for tc in tool_calls_log
                                 if tc.get("tool_name") == "retrieve" and tc.get("result")]
            retrieved_context_json = json.dumps([str(r)[:600] for r in retrieve_results][:3])
            # OPS-07: run_retrieval_faithfulness is appended as the LAST step —
            # it must run strictly after run_auditor commits its verdict, since
            # the sample-rate-OR-auditor-flag gate is evaluated inside the task
            # itself (Auditor's verdict does not exist yet at this dispatch
            # point — see retrieval_eval.py module docstring).
            celery_chain(
                run_gatekeeper.si(str(agent_id), job_id, response_text, message),
                run_auditor.si(str(agent_id), job_id, response_text, message,
                               retrieved_context_json, str(local_conversation_id)),
                run_strategist.si(str(agent_id), job_id, response_text, message),
                run_retrieval_faithfulness.si(str(agent_id), job_id),
            ).apply_async(queue="runtime")

            log.info(
                "run_agent_turn.complete",
                job_id=job_id,
                agent_id=agent_id,
                conversation_id=local_conversation_id,
                citation_count=len(citations_list),
                escalated=escalated,
            )

        except Exception as exc:
            log.error(
                "run_agent_turn.failed",
                job_id=job_id,
                agent_id=agent_id,
                error=str(exc),
            )

            # On final retry exhaustion, mark job failed and emit failure event
            if self.request.retries >= self.max_retries:
                try:
                    with get_sync_db() as db2:
                        job2 = db2.get(Job, job_id)
                        if job2:
                            job2.status = "failed"
                            job2.finished_at = datetime.now(timezone.utc)
                            db2.commit()
                            emit(
                                job_id,
                                "agent.failed",
                                {"error": str(exc)},
                                db2,
                                _redis,
                            )
                except Exception:
                    pass
            else:
                countdown = 2 ** self.request.retries
                raise self.retry(exc=exc, countdown=countdown)
        finally:
            # PROD-05: close the single pooled tenant-DB connection.
            # Runs even when _run_sdk_turn raises or self.retry() re-raises,
            # preventing connection leaks on the exception paths.
            tenant_conn.close()

    return {}
