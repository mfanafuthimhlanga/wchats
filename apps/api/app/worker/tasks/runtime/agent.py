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
import re
import ssl
import uuid
from datetime import datetime, timezone

import psycopg2
import redis as redis_lib
import structlog
from celery import chain as celery_chain
from sqlalchemy import text as sa_text

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.models.job import Job
from app.services.agent_prompt import build_system_prompt
from app.services.agent_tools import build_tool_server, RetrievalStrategy
from app.services.escalation import send_escalation_email
from app.services.events import emit
from app.worker.celery_app import celery_app
from app.worker.tasks.runtime.validators import run_gatekeeper, run_auditor, run_strategist
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ClaudeSDKError,
    CLINotFoundError,
    CLIConnectionError,
    ProcessError,
    CLIJSONDecodeError,
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


def _persist_messages(
    conn,
    conv_id: str,
    user_msg: str,
    assistant_msg: str,
    tool_calls_log: list[dict],
) -> None:
    """Insert user message, assistant message, and tool_call rows.

    Inserts in a single transaction:
      1. user message row in messages table
      2. assistant message row in messages table
      3. one tool_calls row per ToolUseBlock observed during the turn

    All values are passed as %s parameters (T-04-03-07).
    The caller owns the connection lifecycle — this helper does NOT open or
    close the connection (PROD-05: one pooled connection per turn).
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

    return {
        "response_text": response_text,
        "tool_calls_log": tool_calls_log,
        "escalated": escalated,
        "escalation_reason": escalation_reason,
        "escalation_context": escalation_context,
        "sdk_session_id": sdk_session_id_out,
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
            )

            system_prompt = build_system_prompt(agent)

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
                model="claude-haiku-4-5-20251001",
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
            # --------------------------------------------------------------
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

            response_text: str = result["response_text"]
            tool_calls_log: list[dict] = result["tool_calls_log"]
            escalated: bool = result["escalated"]
            escalation_reason: str | None = result["escalation_reason"]
            escalation_context: str | None = result["escalation_context"]
            sdk_session_id_out: str | None = result["sdk_session_id"]

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
            _persist_messages(
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
                },
                db,
                _redis,
            )

            # Mark job complete
            job.status = "complete"
            job.finished_at = datetime.now(timezone.utc)
            db.commit()

            # Dispatch validation chain (M5 — VAL-04)
            retrieve_results = [tc.get("result") for tc in tool_calls_log
                                 if tc.get("tool_name") == "retrieve" and tc.get("result")]
            retrieved_context_json = json.dumps([str(r)[:600] for r in retrieve_results][:3])
            celery_chain(
                run_gatekeeper.si(str(agent_id), job_id, response_text, message),
                run_auditor.si(str(agent_id), job_id, response_text, message,
                               retrieved_context_json, str(local_conversation_id)),
                run_strategist.si(str(agent_id), job_id, response_text, message),
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
