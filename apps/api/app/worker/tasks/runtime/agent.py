"""
run_agent_turn, the Celery task that runs one customer turn of the owned loop.

Position in M4 runtime flow:
    API route /v1/chat/agent/{agent_id}/message
      → dispatch run_agent_turn (runtime queue)
      → SSE stream back to widget client

ADR 0008 took this turn off the Agent SDK harness. `app.services.agent_loop` owns
the assembly seam and the bounded tool loop now, and this module is what a Celery
task adds around them: the tenant connection, the conversation row, the history
the loop resumes from, the PII firewall, the persisted messages, the telemetry
and the validation chain.

Idempotency mechanism:
    READ guard on job_events: if an "agent.response" row already exists for this
    job_id, the task returns immediately, so a retry costs no duplicate model
    calls and no duplicate SSE events.

Security constraints (CLAUDE.md non-negotiable rules):
    - Task args: (job_id, agent_id, message, conversation_id) ONLY.
      NO conn_str, NO API keys in task args (CTL-08 / T-04-03-04).
    - conn_str fetched via fernet_decrypt(agent.neon_connection_string) at runtime.
    - message text is NEVER logged (T-04-03-05).
    - structlog lines reference only job_id, agent_id, conversation_id, counts.

SSE event sequence:
    agent.thinking      ← task begins, the turn is starting
    agent.tool_call     ← each tool the model calls (emitted by the loop)
    agent.tool_result   ← each tool result (emitted by the loop)
    agent.escalated     ← (optional) the model called escalate_to_human
    agent.response      ← terminal event with text, citations, conversation_id
    agent.failed        ← terminal failure only (retries exhausted). Payload
                          carries error_type beside error, because str() of
                          several exceptions on this path is empty (BACKLOG 1.30).

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
from app.core.model_client import ledger_recorder
from app.core.security import fernet_decrypt, require_ciphertext
from app.domain.pii_firewall import detect_pii, scan_response
from app.domain.pricing import UnknownPrice, cost_usd
from app.models.agent import Agent
from app.models.job import Job
from app.models.prompt_version import PromptVersion
from app.services.agent_loop import (
    RETRIEVE_CHUNKS_KEY,
    RETRIEVE_CHUNKS_SOURCE_KEY,
    RETRIEVE_CHUNKS_UNPARSED,
    RETRIEVE_JUDGE_CHUNKS_KEY,
    RETRIEVE_RESULT_IS_ERROR_KEY,
    build_agent_turn,
    record_turn_calls,
    run_agent_loop,
)
from app.services.events import emit
from app.services.prompt_version_service import resolve_prompt_version
from app.worker.celery_app import celery_app
from app.worker.tasks.runtime.retrieval_eval import run_retrieval_faithfulness
from app.worker.tasks.runtime.validators import run_auditor, run_gatekeeper, run_strategist

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
# The one bound this module still owns. The retrieve-capture keys and the
# per-turn ceilings live in `app.services.agent_loop`, which is where the turn is
# assembled and run; this file imports them rather than keeping a second copy,
# because a second copy of either is the audit's D3 defect wearing new clothes.
# One call site kept its own copy of a column name and the deploy gate's eval
# query fails open to this day.
#
# The eval task drives the same loop with the same wall-clock ceiling and stamps
# it on the run's provenance, so there is one copy, here, and the other reader
# imports it.
# ---------------------------------------------------------------------------

#: Wall-clock ceiling on one turn, enforced by asyncio.wait_for.
#: D-11 raised it from 30s to 90s. The number outlived the subprocess it was
#: chosen for. The turn now runs in this process against a provider API, and 90s
#: still covers a slow retrieve, a synthesis and a follow-up while staying under
#: the SSE layer's 120s (30s headroom). The eval's per-run cost ceiling is
#: derived from this value rather than from a guess about it.
AGENT_TURN_TIMEOUT_S = 90


def _judge_retrieved_context(tool_calls_log: list[dict]) -> tuple[list[str], int]:
    """The retrieved context the Auditor is judged against — what the AGENT saw.

    BACKLOG 5.16. This used to be
    `json.dumps([str(r)[:600] for r in retrieve_results][:3])`, which cut the
    grounding judge's evidence three separate ways at once:

        [:600]        600 chars per call, against MAX_CHUNKS(5) *
                      CHUNK_CONTENT_CHAR_LIMIT(2000) = 10,000 the agent was shown
        [:3]          retrieve calls 4+ dropped entirely (the cap is 8)
        tc["result"]  the tool result's own text, already cut at
                      RETRIEVE_RESULT_CAPTURE_CHARS, a bound that exists because
                      it reaches a jsonb column and not because 1800 is evidence

    So the judge was asked "is this answer supported by its context?" while being
    shown roughly half of it. The first valid verdict in the platform's history
    (E2E-3, 2026-08-13) marked the agent's price claims unsupported and said so
    in its reason — *because it was never shown the price rows*. That `partial`
    is an artefact of this function's predecessor, not a judgement about the
    response, and `verified_qa_candidates` (confidence >= 0.90) is starved by it.

    THE RULE IS "the judge sees exactly what the agent saw", NOT a bigger number.
    No cap is applied here, because the bound already exists upstream and belongs
    to the retrieval layer:

        MAX_CHUNKS(5) * CHUNK_CONTENT_CHAR_LIMIT(2000)
                      * _RETRIEVE_CALLS_PER_TURN_MAX(8)   = 80,000 chars

    all three in agent_tools.py. A ceiling chosen at this call site would drift
    away from that contract the first time any of them moved, which is 2.13's
    history exactly.

    **The bounding constant is the retrieve-call cap, NOT the model-call ceiling.**
    `MAX_MODEL_CALLS_PER_TURN` is 6 and parallel tool use puts several retrieves
    into one call, so citing it here understated the worst case by a third.

    The scope of what travels is deliberately narrow: only `retrieve` results.
    `lookup_structured` returns customer rows and has never been in this channel,
    which is what keeps BACKLOG 0.4's egress question separate from this one.

    THREE LIVE STATES AND ONE RETIRED, counted separately, because collapsing
    them is how the first version of this function was wrong. A retrieve call is:

        chunks    decoded, with hits    -> every chunk, untruncated
        empty     decoded, zero hits    -> contributes NOTHING, and that is the
                                           honest answer. The corpus had no
                                           match, so there is no evidence
        errored   is_error on the entry -> contributes NOTHING
        unparsed  RETIRED. See below

    `unparsed` NO LONGER REACHES THIS FUNCTION. `agent_tools.retrieve_tool`
    attaches its `_retrieved_context` ride-along on its one success path, and
    every other producer of a retrieve wire sets `is_error`: a raising handler,
    an unknown tool, unreadable arguments, the DoS-guard refusal. The error check
    below runs first and skips, so the unparsed branch never runs and
    `counts["unparsed"]` reads 0 in production. It stays as a guard, because a
    future ride-along-less SUCCESS would otherwise be reported as a retrieval
    that found nothing, and that is a verdict about the reader.

    `errored` matters more than it looks. A turn that trips the DoS guard gets
    "Retrieve quota exceeded for this turn" back with is_error set. That is a
    control message the AGENT read as a failure, and feeding it to the judge as
    evidence puts a sentence about quotas into the RETRIEVED CONTEXT block of the
    turn least likely to be well grounded.

    THE DEGRADED FALLBACK IN THAT BRANCH hands over the audit `result` text
    rather than nothing, because the judge chain has no "unscorable" verdict and
    an empty context makes every claim unsupported. `run_eval_suite` can abstain
    and does, which is why it excludes such a row instead of degrading it.

    Returns:
        (contexts, counts). One string per CHUNK in retrieval order, and a
        per-state tally that tells a short context apart from a broken one.
    """
    contexts: list[str] = []
    counts = {"calls": 0, "chunks": 0, "empty": 0, "unparsed": 0, "errored": 0}
    for tc in tool_calls_log:
        if tc.get("tool_name") != "retrieve" or "result" not in tc:
            continue
        counts["calls"] += 1
        if tc.get(RETRIEVE_RESULT_IS_ERROR_KEY):
            counts["errored"] += 1
            continue
        # The SOURCE key, never an inference from an empty chunk list. A corpus
        # miss and a result carrying no retrieval are different observations.
        if tc.get(RETRIEVE_CHUNKS_SOURCE_KEY) == RETRIEVE_CHUNKS_UNPARSED:
            counts["unparsed"] += 1
            contexts.append(str(tc.get("result") or ""))
            continue
        # RETRIEVE_JUDGE_CHUNKS_KEY, not RETRIEVE_CHUNKS_KEY: the judge needs the
        # provenance the agent saw as well as the text (BACKLOG 5.18).
        chunks = [str(c) for c in (tc.get(RETRIEVE_JUDGE_CHUNKS_KEY) or []) if c]
        if not chunks:
            counts["empty"] += 1
            continue
        counts["chunks"] += len(chunks)
        contexts.extend(chunks)
    return contexts, counts


def _published_context(tool_calls_log: list[dict]) -> list[str]:
    """What the TENANT published, for the PII firewall's exemption (BACKLOG 7.29).

    The firewall stops the agent leaking a CUSTOMER's personal data. It is not
    meant to stop it repeating the BUSINESS's own published contact details, and
    it was doing exactly that: three of twenty E2E-6 responses came back as the
    deflection, because the best-matching chunk was the corpus's "Contact and
    Escalation" section and a correct answer quotes the address in it.

    A THIRD READING OF ONE CAPTURE, joining RETRIEVE_CHUNKS_KEY (what the eval
    scores) and RETRIEVE_JUDGE_CHUNKS_KEY (what the Auditor judges). It differs
    from `_judge_retrieved_context` in the two places that decide whether an
    exemption is safe, so it cannot borrow that function:

        content only   RETRIEVE_CHUNKS_KEY, not RETRIEVE_JUDGE_CHUNKS_KEY. The
                       judge needs provenance; an allowlist needs the smallest
                       surface that answers the question.
        unparsed -> nothing   RETIRED, and kept as a guard. `retrieve_tool`
                       attaches its ride-along on its one success path, so an
                       unparsed entry always carries is_error and the check above
                       has already skipped it. Fail closed on both routes.

    WHAT IS DELIBERATELY NOT IN HERE, because each would be a bypass:

        the framed payload   it echoes the retrieve QUERY, so a customer who
                             types their own address into the chat would see it
                             come back exempted
        the customer message and history   the same bypass, one step shorter
        the agent soul       `pii_firewall`'s stated property is that nothing in
                             the soul reaches its behaviour (T-18-SEC-02)
        errored retrieves    `retrieve_tool` returns its DoS-guard refusal as
                             ordinary text with is_error set, and a refusal is
                             not published material

    THE ONE THING THAT WOULD WIDEN THIS SILENTLY, checked 2026-08-18 and clear:
    `verified_qa`. Those rows are answers derived from CONVERSATIONS, not material
    the tenant published, so an address a customer typed could reach the allowlist
    through them. It cannot today, because `verified_qa_lookup` is called from
    `app.worker.tasks.runtime.retrieve`, not from `agent_tools.retrieve_tool`,
    which builds its chunks straight from reranked hybrid search. Promotion is
    also off by the owner's decision of 2026-08-08. **Routing the agent's retrieve
    through that cache, or adding the lookup to `agent_tools`, widens a security
    control without touching this file.** If that day comes, filter here on the
    chunk's provenance rather than trusting the tool name.

    Returns one string per retrieved chunk, in retrieval order.
    """
    published: list[str] = []
    for tc in tool_calls_log:
        if tc.get("tool_name") != "retrieve" or "result" not in tc:
            continue
        if tc.get(RETRIEVE_RESULT_IS_ERROR_KEY):
            continue
        if tc.get(RETRIEVE_CHUNKS_SOURCE_KEY) == RETRIEVE_CHUNKS_UNPARSED:
            continue
        published.extend(str(c) for c in (tc.get(RETRIEVE_CHUNKS_KEY) or []) if c)
    return published


def _dispatch_validation_chain(
    *,
    agent_id: str,
    job_id: str,
    response_text: str,
    message: str,
    conversation_id: str,
    tool_calls_log: list[dict],
) -> str:
    """Build the judge's context and dispatch the validation chain. Returns it.

    THIS FUNCTION EXISTS TO BE TESTABLE, and that is the whole point of the seam.
    An adversarial review of the first version of BACKLOG 5.16 reintroduced the
    defect five different ways. A truncating helper, a differently named
    variable, `itertools.islice`, a second assignment on the next line, and
    rebuilding the old capture while still calling `_judge_retrieved_context`.
    The guards stayed green through all five, because they inspected the SHAPE OF
    A LINE rather than the VALUE `run_auditor` receives. Nothing in the repo
    observed that value: `retrieved_context_json` was built inside a 400-line
    task body no test could reach.

    So the rule this seam encodes: **guard the argument the consumer is handed,
    not the syntax that produces it.** A text-shaped guard only ever bans the one
    spelling its author thought of.

    Returns the JSON handed to run_auditor, so a caller (and a test) can assert
    on exactly what the judge will see.
    """
    contexts, counts = _judge_retrieved_context(tool_calls_log)
    retrieved_context_json = json.dumps(contexts)
    # Recorded, not inferred: E2E-6 calibrates this judge, and a calibration run
    # has to read what the judge was actually shown rather than reconstruct it
    # from whichever version of the code was current.
    log.info(
        "run_agent_turn.judge_context",
        job_id=job_id,
        agent_id=agent_id,
        chars=len(retrieved_context_json),
        **counts,
    )
    # OPS-07: run_retrieval_faithfulness is appended as the LAST step — it must
    # run strictly after run_auditor commits its verdict, since the
    # sample-rate-OR-auditor-flag gate is evaluated inside the task itself
    # (the Auditor's verdict does not exist yet at this dispatch point — see
    # retrieval_eval.py module docstring).
    celery_chain(
        run_gatekeeper.si(agent_id, job_id, response_text, message),
        run_auditor.si(agent_id, job_id, response_text, message,
                       retrieved_context_json, conversation_id),
        run_strategist.si(agent_id, job_id, response_text, message),
        run_retrieval_faithfulness.si(agent_id, job_id),
    ).apply_async(queue="runtime")
    return retrieved_context_json


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


#: How many `messages` rows one turn resumes from.
#:
#: Session state lives in `conversations` and `messages` (ADR 0008), so the
#: history a turn is given is a query rather than an SDK session file. Forty rows
#: is twenty exchanges, which bounds a long conversation's context cost. Every
#: row travels on every model call of the turn, so an uncapped read makes the
#: hundredth turn of a conversation cost more than the first hundred put
#: together.
TURN_HISTORY_MAX_MESSAGES = 40


def _read_turn_history(conn, conv_id: str) -> list[dict]:
    """The conversation so far, oldest first, as the loop's `history` argument.

    The caller owns the connection lifecycle — this helper does NOT open or
    close the connection (PROD-05: one pooled connection per turn).
    """
    sql = """
        SELECT role, content
        FROM messages
        WHERE conversation_id = %s AND role IN ('user', 'assistant')
        ORDER BY created_at DESC,
                 CASE role WHEN 'assistant' THEN 0 ELSE 1 END
        LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (conv_id, TURN_HISTORY_MAX_MESSAGES))
        rows = list(cur.fetchall())

    # THE CASE IS LOAD-BEARING (issue #79). `_persist_messages` writes a turn's
    # user row and assistant row in ONE transaction, so both carry the same
    # `transaction_timestamp()` and `created_at` alone leaves their order to the
    # plan. Within one timestamp the user row precedes the assistant row, so a
    # DESC scan has to take the assistant row FIRST, which is what the CASE
    # says. Without it a turn's own answer can come back before its question and
    # the model reads the conversation inside out.
    rows.reverse()
    # An assistant row with no text is what a turn that exhausted
    # `max_model_calls` persists. The loop ran out of calls while the model was
    # still asking for tools, so `response_text` joined to "". Replaying it puts
    # an empty assistant message into the next turn's context, where the model
    # reads it as a turn in which it chose to say nothing. The row stays in the
    # table, because it is the record of what happened; it just does not travel.
    return [
        {"role": role, "content": content}
        for role, content in rows
        if content and str(content).strip()
    ]


def _set_prompt_version_id(conn, conv_id: str, prompt_version_id: str) -> None:
    """Store the resolved prompt_version_id in conversations.metadata (OPS-16).

    A `jsonb_set` update, so subsequent turns
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
    *,
    agent_id: str,
    local_conversation_id: str,
    existing_prompt_version_id: str | None,
) -> tuple[str | None, dict | None, bool]:
    """Resolve the prompt version to serve this turn, sticky per conversation (OPS-16).

    READ ONLY — control DB. This function used to also WRITE the resolved id to
    conversations.metadata, and P1 moved the whole thing ahead of the seam
    because the soul fields it returns are an input to the system prompt. The
    write came along for the ride, so a turn that then died in
    the seam left the conversation permanently sticky to a version that never
    served it, where the Celery retry previously re-rolled (BACKLOG 2.6). Settled
    2026-08-07: resolve before, commit after. The read stays here; the write is
    the caller's, behind a successful seam call.

    First turn of a conversation (existing_prompt_version_id is None): calls
    resolve_prompt_version (weighted pick, control DB) and reports back that the
    caller must persist the choice, so every subsequent turn on this
    conversation reuses it (A-CANARY: no mid-conversation persona flip; the
    version is never re-rolled).

    Subsequent turns (existing_prompt_version_id provided): re-fetches that
    EXACT version's soul fields by id — never re-rolls, never re-picks, and
    nothing to persist because the id is already stored.

    T-21-09-05 (never fails a turn): any exception here is caught and treated
    as "no version resolved" — the caller falls back to the agent's live
    soul_* columns unchanged (soul_override=None passed to
    build_system_prompt), exactly like an agent with zero prompt_versions
    rows. A resolution failure degrades canary correlation only; it never
    blocks or fails the served turn.

    Returns:
        (prompt_version_id, soul_override, needs_persist).

        needs_persist is True only for a first turn that actually resolved a
        version — the one case where conversations.metadata does not yet hold
        the id. It is returned rather than re-derived by the caller from
        `existing_prompt_version_id is None` so that a future change to the
        resolution rules (a stale id that re-rolls, say) cannot leave the
        caller's copy of the logic silently disagreeing with this one.
    """
    try:
        if existing_prompt_version_id:
            pv = db.get(PromptVersion, existing_prompt_version_id)
            if pv is None:
                return None, None, False
            return str(pv.id), {
                "soul_role": pv.soul_role,
                "soul_voice": pv.soul_voice,
                "soul_do_list": pv.soul_do_list,
                "soul_donot_list": pv.soul_donot_list,
            }, False

        resolved_id, soul_override = resolve_prompt_version(db, agent_id)
        if resolved_id is None:
            return None, None, False

        return resolved_id, soul_override, True
    except Exception as exc:
        log.warning(
            "run_agent_turn.prompt_version_resolve_failed",
            agent_id=agent_id,
            conversation_id=local_conversation_id,
            error=str(exc),
        )
        return None, None, False


def _persisted_chunks(tc: dict) -> str | None:
    """The judge chunks to store for one tool call, or None for SQL NULL.

    BACKLOG 7.34. `grounding_fidelity` asks whether a claim is traceable to a
    chunk "provided in the tool_calls log", and until this column existed nothing
    outside the worker could provide one, so the rubric's PASS branch was
    unreachable and every grounding verdict had to FAIL.

    RETRIEVE_JUDGE_CHUNKS_KEY, not RETRIEVE_CHUNKS_KEY: the reader is a judge
    asked whether a claim is SUPPORTED, and BACKLOG 5.18 is the finding that a
    claim naming a document or a section cannot be supported by a context that
    contains neither. Ragas wants the content-only rendering and reads it
    elsewhere; one parse, two renderings, one reader each.

    NULL AND `[]` ARE DIFFERENT OBSERVATIONS and this function is where they
    stay apart:

        None  this call is not a retrieve, or the retrieve errored and its
              "refusal" text is not evidence, or its capture could not be decoded
        []    a retrieve ran and the corpus matched nothing

    Collapsing them is BACKLOG 5.16 one level down. An empty context makes every
    claim unsupported, so reporting a decode failure as "retrieved nothing"
    manufactures an ungrounded verdict about the decoder rather than the answer.

    THE UNPARSED CHECK BELOW IS RETIRED AND KEPT. `retrieve_tool` attaches its
    ride-along on its one success path, so an unparsed entry always carries
    is_error and the check above it has already returned None. It stays because a
    future ride-along-less success would otherwise be stored as `[]`, which is
    the collapse this docstring exists to prevent.
    """
    if tc.get("tool_name") != "retrieve":
        return None
    if tc.get(RETRIEVE_RESULT_IS_ERROR_KEY):
        return None
    if tc.get(RETRIEVE_CHUNKS_SOURCE_KEY) == RETRIEVE_CHUNKS_UNPARSED:
        return None
    if RETRIEVE_JUDGE_CHUNKS_KEY not in tc:
        return None
    return json.dumps(tc.get(RETRIEVE_JUDGE_CHUNKS_KEY) or [])


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
                INSERT INTO tool_calls
                    (id, message_id, tool_name, arguments, result, retrieved_chunks, created_at)
                VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, NOW())
                """,
                (
                    str(uuid.uuid4()),
                    assistant_msg_id,
                    tc.get("tool_name", ""),
                    json.dumps(tc.get("input", {})),
                    json.dumps(tc.get("result", {})),
                    # BACKLOG 7.34. `result` is unchanged beside it: that column
                    # is the 1800-char audit capture, and one column holding
                    # either that or a chunk list depending on when the row was
                    # written gets read as whichever the reader had in mind.
                    _persisted_chunks(tc),
                ),
            )
    conn.commit()

    log.debug(
        "_persist_messages.done",
        conversation_id=conv_id,
        tool_call_count=len(tool_calls_log),
    )

    return assistant_msg_id


def _turn_cost_usd(calls: list, *, job_id: str, agent_id: str) -> float | None:
    """What one turn cost, priced from its own `model_calls` rows (OPS-01).

    The rows are the ones the ledger recorder teed into `AgentTurn.calls`, and
    the price comes from the versioned book. The SDK's `total_cost_usd` went with
    the harness, because it priced calls from a book nobody here controls.

    None, never zero, in the two cases where the cost is unknown. The book cannot
    price a call, or there is no call to price. Zero would report the turn as
    free, which is the claim #46 exists to stop making. `turn_metrics` already
    reads a NULL cost as unknown.

    An empty `calls` is not an unbilled turn. The ledger hook fails open, so it
    skips a response it cannot read, a streamed body, any status >= 400, and
    anything that raised inside it. A turn that asked the model six times and
    recorded none of them costs exactly as much as one that recorded all six.
    """
    if not calls:
        log.warning(
            "run_agent_turn.turn_cost_unrecorded",
            job_id=job_id,
            agent_id=agent_id,
            detail="no model_calls rows for this turn; the cost is unknown, not zero",
        )
        return None
    try:
        return float(sum(cost_usd(call)[0] for call in calls))
    except UnknownPrice as exc:
        log.warning(
            "run_agent_turn.turn_cost_unpriced",
            job_id=job_id,
            agent_id=agent_id,
            error=str(exc),
        )
        return None


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
    turn_cost_usd: float | None,
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
        with _langfuse.start_as_current_observation(
            as_type="generation",
            name="agent-turn",
            model=model,
            metadata={"agent_id": agent_id, "job_id": job_id},
            output={
                "num_turns": num_turns,
                "turn_cost_usd": turn_cost_usd,
                "stop_reason": stop_reason,
            },
        ):
            pass  # generation data is set via context manager params

        if turn_cost_usd is not None:
            _langfuse.create_score(
                name="turn_cost_usd",
                value=turn_cost_usd,
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
# THE SEAM lives in `app.services.agent_loop` now, and this file goes through it.
#
# D1 (.dev/plans/260807-d1-agent-invocation.md, P1). The nightly eval scored
# reference answers against the contexts those answers were written from and
# never invoked the agent at all. The fix is to make the eval invoke it, and the
# only version of that fix worth having is one where the eval and the customer
# are served by the SAME agent. "Same agent" is not the same model id; it is the
# system prompt, the tool server (which is where the capability envelope is
# enforced), the tool list, the two ceilings and the model, together. Assemble
# any of those twice and the eval measures something adjacent to the product,
# which the measurement-layer audit records as this repo's recurring defect.
#
# So they are assembled exactly once, in `build_agent_turn`, and both callers go
# through it. ADR 0008 moved that function out of this module together with the
# loop it feeds, because the loop is service code and this file is a Celery task;
# `build_agent_options` held the same line for the SDK path and this is its
# successor. tests/unit/test_agent_options_seam.py fails if `run_agent_turn`
# builds a turn (or a tool server, or a system prompt) by any other route. That
# test is the mechanism; this comment is only its explanation.
#
# SETTLED (BACKLOG 2.5, owner, 2026-08-07). The turn the seam returns carries a
# LIVE tool server bound to the tenant's real connection string. Every caller
# therefore reaches, by default:
#   * retrieve            -> write_retrieval_metrics(conn_str, …) into the tenant DB
#   * escalate_to_human   -> _mark_conversation_escalated(…) + send_escalation_email
#   * the 6 mutating transactional skills -> a tool_calls_audit row AND the real
#     ProviderAdapter: place_order, cancel_order, issue_refund,
#     update_subscription, book_slot, update_customer_record.
# The answer is `side_effects`: MANDATORY, no default. A default is exactly the
# mechanism by which the eval path silently ends up live, so a caller that does
# not state which it wants raises TypeError at the call site rather than
# discovering the question against a real tenant at 3am.
#
# The alternative, a read-only tool subset for the eval, was rejected, and the
# reason still constrains every future change to the seam. Removing the mutating
# skills would make the eval measure an agent with fewer capabilities than
# production serves, and a scenario testing "the agent should refuse to refund
# here" could no longer FAIL, because the agent could not even try. An agent that
# cannot attempt the wrong thing cannot be measured on refusing it. So the tool
# list is identical in both modes, and the capability envelope, IDV gate and
# Actor seam all still run; what changes is only the outer edge, where a call
# would leave this process.
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------

#: Retry countdown for a tenant-DB connect that failed while the endpoint was
#: waking. The generic countdown in the handler below is ``2 ** retries`` — 1s
#: then 2s — which is shorter than a per-tenant Neon project's cold start, so a
#: wake-triggered retry on that schedule fires while the endpoint is still
#: suspended and burns the whole retry budget without ever reaching a live
#: endpoint.
#:
#: The value is set by arithmetic, not by picking a point in the 8-20s wake
#: window. ``max_retries=2`` buys three attempts and therefore only TWO
#: countdown gaps, and a refusal from a suspended endpoint can come back fast
#: rather than consuming its connect budget, so the attempts themselves may
#: contribute nothing. Cumulative delay before the FINAL attempt is
#: ``2 * countdown``, and that product is what has to clear the top of the wake
#: window — not the countdown itself. At 8s it is 16s, which leaves a 17-20s
#: wake exhausting all three attempts; at 10s it is 20s, which covers the
#: ceiling. Changing max_retries changes this number.
TENANT_WAKE_RETRY_COUNTDOWN_S = 10


def _retry_countdown(exc: Exception, retries: int) -> int:
    """How long to wait before the next attempt at this turn.

    An OperationalError is overwhelmingly the tenant endpoint waking rather than a
    defect, and the wake outlasts the generic exponential countdown, so it retries
    on the wake window instead.
    """
    if isinstance(exc, psycopg2.OperationalError):
        return TENANT_WAKE_RETRY_COUNTDOWN_S
    return 2 ** retries


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
        # for this job_id. Prevents duplicate model calls and duplicate events
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
        conn_str = fernet_decrypt(require_ciphertext(agent.neon_connection_string, "agents.neon_connection_string"))

        # PROD-05: open ONE pooled tenant-DB connection for all per-turn read and
        # write helpers (_create_conversation_row, _validate_conversation_owner,
        # _read_turn_history, _persist_messages).  Reduces connection churn
        # from 4 opens/closes per turn to 1.  Uses the pooled endpoint
        # (agent.neon_connection_string) — PgBouncer transaction-mode
        # compatible: no named prepared statements, no SET session vars.
        #
        # The connect is INSIDE the try. It used to sit outside it, and a
        # suspended endpoint is the NORMAL state of a tenant DB idle for ~5
        # minutes — so the first message of every conversation landed on a
        # psycopg2.OperationalError that escaped this task's own except entirely:
        # no retry, no agent.failed, zero job_events, and a widget waiting on a
        # job that had already died. Observed on three live jobs, 2026-08-16.
        # Both are read in the `finally`, which runs even when the connect failed.
        tenant_conn, turn = None, None
        try:
            tenant_conn = psycopg2.connect(
                conn_str, connect_timeout=settings.TENANT_DB_CONNECT_TIMEOUT_S
            )
            # --------------------------------------------------------------
            # EVENT 1: agent.thinking — confirms task is running for this agent
            # --------------------------------------------------------------
            emit(job_id, "agent.thinking", {"agent_id": agent_id}, db, _redis)

            # --------------------------------------------------------------
            # Conversation branching. A first turn creates the row and carries no
            # history; a subsequent turn validates ownership and reads what the
            # turn resumes from. ADR 0008 keeps session state in `conversations`
            # and `messages`, so a follow-up resumes on any container. The SDK's
            # `resume` went with the harness. It stored session files on the
            # container filesystem, and Railway replaces that on every deploy.
            # --------------------------------------------------------------
            history: list[dict] = []
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
                history = _read_turn_history(tenant_conn, local_conversation_id)
                existing_prompt_version_id = conv_row["metadata"].get("prompt_version_id")

            # ----------------------------------------------------------------
            # OPS-16: canary prompt-version resolution — sticky per conversation,
            # never fails a turn (T-21-09-05). See _resolve_turn_prompt_version's
            # own docstring for the first-turn-vs-subsequent-turn distinction.
            #
            # This RESOLUTION runs BEFORE the seam. The soul fields it returns
            # are an input to the system prompt the seam assembles, so it has to
            # precede the one call that consumes them. That part of P1 stands.
            #
            # The WRITE does not. It happens after build_agent_turn returns
            # (BACKLOG 2.6, settled 2026-08-07, "resolve before, commit after").
            # _resolve_turn_prompt_version used to commit itself, so P1's move
            # carried the write forward with the read, and a turn that then died
            # in the seam left the conversation permanently sticky to a version
            # that never served it, where the Celery retry had re-rolled. Pinned
            # both ways by test_the_canary_choice_is_not_committed_when_the_
            # options_build_fails and ..._is_committed_once_the_options_exist.
            # ----------------------------------------------------------------
            prompt_version_id, soul_override, canary_needs_persist = _resolve_turn_prompt_version(
                db,
                agent_id=agent_id,
                local_conversation_id=str(local_conversation_id),
                existing_prompt_version_id=existing_prompt_version_id,
            )

            # --------------------------------------------------------------
            # THE SEAM (ADR 0008). The route, the system prompt, the tool
            # server, the tool list and the two ceilings are assembled in
            # build_agent_turn, the same callable the eval task goes through, so
            # the agent measured is the agent served. Constructing any of them
            # here instead is what test_agent_options_seam.py fails on.
            #
            # side_effects="live" is the chat path, stated rather than defaulted
            # (BACKLOG 2.5). This is the turn a customer is waiting on: its
            # refunds are real, its escalation mail must arrive, and its
            # retrieval_metrics row is what the ops room reads.
            #
            # The ledger takes the DSN rather than `tenant_conn`, deliberately. A
            # per-row connect commits each `model_calls` row as it is made, so a
            # turn that crashes mid-loop keeps the rows for the calls it already
            # paid for. Rows on this pooled connection would sit uncommitted and
            # vanish with it, which is the failure #46 ended.
            # --------------------------------------------------------------
            turn = build_agent_turn(
                agent=agent,
                conn_str=conn_str,
                conversation_id=str(local_conversation_id),
                job_id=job_id,
                side_effects="live",
                verified_session_token=verified_session_token,
                soul_override=soul_override,
                ledger=ledger_recorder(conn_str),
            )

            # --------------------------------------------------------------
            # BACKLOG 2.6: the canary choice becomes sticky only now that there
            # is an agent for it to be sticky to. A turn that died above
            # re-rolls on retry, as it did before P1.
            #
            # Wrapped, and never fatal (T-21-09-05): a tenant-DB failure here
            # must not fail a turn whose seam has already run. The version still
            # served this turn and turn_metrics still attributes it there, which
            # is the honest record; only the stickiness is lost, so the next turn
            # of this conversation re-rolls.
            # --------------------------------------------------------------
            if canary_needs_persist and prompt_version_id:
                try:
                    _set_prompt_version_id(
                        tenant_conn, str(local_conversation_id), prompt_version_id
                    )
                except Exception as canary_exc:
                    log.warning(
                        "run_agent_turn.prompt_version_persist_failed",
                        job_id=job_id,
                        agent_id=agent_id,
                        conversation_id=str(local_conversation_id),
                        error=str(canary_exc),
                    )

            # --------------------------------------------------------------
            # Bridge the async loop into the sync Celery worker. asyncio.run()
            # is the required pattern for Python 3.12 (see CLAUDE.md), and
            # asyncio.wait_for(timeout=AGENT_TURN_TIMEOUT_S) sits inside it.
            # T-04-03-06 DoS guard: the turn's own max_model_calls and
            # max_budget_usd, both set by the seam.
            #
            # OPS-01: monotonic start captured immediately before the call so
            # latency_ms reflects only the turn itself, not queueing/setup.
            # --------------------------------------------------------------
            _turn_start_monotonic = time.monotonic()
            result = asyncio.run(
                asyncio.wait_for(
                    run_agent_loop(
                        message,
                        history=history,
                        turn=turn,
                        job_id=job_id,
                        db=db,
                        redis=_redis,
                    ),
                    timeout=AGENT_TURN_TIMEOUT_S,
                )
            )
            latency_ms = int((time.monotonic() - _turn_start_monotonic) * 1000)

            response_text: str = result["response_text"]
            tool_calls_log: list[dict] = result["tool_calls_log"]
            escalated: bool = result["escalated"]
            escalation_reason: str | None = result["escalation_reason"]
            escalation_context: str | None = result["escalation_context"]
            num_turns: int | None = result.get("num_turns")
            stop_reason: str | None = result.get("stop_reason")

            # OPS-01: derived from this turn's own ledger rows, never reported by
            # the provider. See _turn_cost_usd.
            turn_cost_usd: float | None = _turn_cost_usd(
                turn.calls, job_id=job_id, agent_id=agent_id
            )

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
            # reads no config, so nothing in the response text and nothing in the agent
            # soul can disable it. Citations are extracted from the deflection
            # when a flag fires, which correctly yields an empty citation list — a
            # deflection cites nothing.
            #
            # BACKLOG 7.29: the firewall is given what the tenant PUBLISHED this
            # turn, so an answer quoting the business's own contact address is no
            # longer deleted. Email only; card and SA ID have no exemption path.
            # The list is read for one set-membership test and never interpreted,
            # so a chunk that instructs the firewall off does nothing.
            published = _published_context(tool_calls_log)
            filtered_text, pii_detector = scan_response(
                response_text, published_context=published
            )
            if pii_detector is not None:
                log.warning(
                    "pii_firewall.response_deflected",
                    job_id=job_id,
                    agent_id=agent_id,
                    conversation_id=str(local_conversation_id),
                    detector=pii_detector,
                    original_length=len(response_text),
                    published_chunks=len(published),
                )
            elif published and detect_pii(response_text) is not None:
                # Passed only BECAUSE the address was published. Logged so the
                # exemption is observable rather than silent: this is the one
                # line that tells a later reader an address left the system on
                # the strength of the corpus, and which turn it was.
                log.info(
                    "pii_firewall.published_contact_allowed",
                    job_id=job_id,
                    agent_id=agent_id,
                    conversation_id=str(local_conversation_id),
                    published_chunks=len(published),
                )
            response_text = filtered_text

            # --------------------------------------------------------------
            # Citation extraction — missing block yields [] + warning (not failure)
            # --------------------------------------------------------------
            citations_list = _extract_citations(response_text)

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
                    cost_usd=turn_cost_usd,
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
                turn_cost_usd=turn_cost_usd,
                latency_ms=latency_ms,
                stop_reason=stop_reason,
            )

            # Dispatch validation chain (M5 — VAL-04). BACKLOG 5.16.
            _dispatch_validation_chain(
                agent_id=str(agent_id),
                job_id=job_id,
                response_text=response_text,
                message=message,
                conversation_id=str(local_conversation_id),
                tool_calls_log=tool_calls_log,
            )

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
                error_type=type(exc).__name__,
                error=str(exc) or repr(exc),
            )

            # On final retry exhaustion: emit the failure event, then mark the
            # job failed. TWO separate sessions and two separate try blocks,
            # deliberately.
            #
            # agent.failed is the only thing the widget ever sees when a turn
            # dies, and the job-status write beside it is bookkeeping the ops
            # room reads later. Sharing one try/except-pass ranked them the
            # wrong way round: a raise from get_sync_db(), from db2.get(), or
            # from db2.commit() reached the same bare `except` and took the
            # customer's signal down with the bookkeeping. So the emission goes
            # FIRST and owns its own failure boundary.
            if self.request.retries >= self.max_retries:
                # error_type rides beside error because str() of several
                # exceptions on this path is EMPTY — TimeoutError and a bare
                # psycopg2.OperationalError both render as "" — and
                # {"error": ""} names nothing (BACKLOG 1.30).
                try:
                    with get_sync_db() as db_event:
                        emit(
                            job_id,
                            "agent.failed",
                            {
                                "error_type": type(exc).__name__,
                                "error": str(exc) or repr(exc),
                            },
                            db_event,
                            _redis,
                        )
                except Exception as emit_exc:
                    # Nothing left to tell the customer with. Logged rather than
                    # passed, because a silent failure here is the defect this
                    # whole branch exists to close.
                    log.error(
                        "run_agent_turn.failed_event_not_emitted",
                        job_id=job_id,
                        error_type=type(emit_exc).__name__,
                        error=str(emit_exc) or repr(emit_exc),
                    )

                try:
                    with get_sync_db() as db2:
                        job2 = db2.get(Job, job_id)
                        if job2:
                            job2.status = "failed"
                            job2.finished_at = datetime.now(timezone.utc)
                            db2.commit()
                except Exception:
                    pass
            else:
                raise self.retry(
                    exc=exc, countdown=_retry_countdown(exc, self.request.retries)
                )
        finally:
            # Two debts this attempt owes, on the served path, the timeout path and
            # the retry path alike. THE LEDGER ROWS GO FIRST: writing one opens,
            # commits and closes a tenant connection, which is why the loop only
            # appended them and why they are not written on the event loop a
            # customer waits on. `record_turn_calls` never raises. Then PROD-05's
            # single pooled connection closes, and it is None only if connect failed.
            if turn is not None:
                record_turn_calls(turn)
            if tenant_conn is not None:
                tenant_conn.close()

    return {}
