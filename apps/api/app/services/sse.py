"""
SSE event generator for W Chats job status streams.

event_generator — async generator yielding ServerSentEvent objects.

Protocol:
    Phase 1 (replay): Query DB for all past events and yield them.
                      Returns immediately if a terminal event is already present.
    Phase 2 (live):   Use Redis pub/sub as a wake-up signal, then re-query the DB
                      for new events on each wake-up.  Falls back to polling the DB
                      every POLL_INTERVAL_S seconds if no pub/sub message arrives.
                      DB is always the source of truth — pub/sub is never trusted
                      to carry event data, only to trigger a re-query.
    Close:            Returns on terminal event or client disconnect.

Why DB-poll + pub/sub wakeup (not pure pub/sub):
    Pure pub/sub has a narrow but real race: if an event is published to Redis
    *before* the SSE generator's listen() loop starts reading (e.g. during the
    Phase 1 DB replay or during asyncio scheduler latency), the message is
    permanently lost because Redis pub/sub is fire-and-forget.  The DB is the
    durable record, so polling it guarantees no events are missed regardless
    of timing.  Pub/sub keeps latency low by triggering immediate re-queries
    rather than waiting for the next poll tick.

Terminal events:
    job.complete — chain completed successfully
    job.failed   — chain failed; agent.status = "failed"

    A stream's terminal set is a property of the STREAM, not of the event log, so
    it is a parameter. See CUSTOMER_TERMINAL_EVENTS below.

Public readers:
    This generator is the single read path for both the authenticated operator
    stream and the unauthenticated widget stream, and the DB is the only source of
    payload bytes (pub/sub carries a wake-up, never data). So one per-event-type
    field allowlist, applied here, covers replay and live at once. See
    PUBLIC_EVENT_FIELDS and the `public` parameter.
"""

import asyncio
import json
from collections.abc import AsyncGenerator
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import ServerSentEvent

from app.domain.pii_firewall import scan_response
from app.models.job_event import JobEvent

TERMINAL_EVENTS = frozenset({"job.complete", "job.failed"})

# The customer-facing stream (GET /widget/jobs/{job_id}/events) is finished the moment
# the answer — or the failure — has been delivered. Before this existed the server kept
# yielding keepalives to the 120s hard cap after `agent.response`, and only the widget
# calling es.close() ended it; a third-party client held a socket for two minutes per
# turn and sat on one of the 50 per-agent SSE slots (BACKLOG 7.3).
#
# WHY A PER-STREAM SET rather than either alternative:
#
#   * Adding "agent.response" to TERMINAL_EVENTS itself would also truncate
#     GET /jobs/{job_id}/events, the AUTHENTICATED admin stream. run_agent_turn
#     dispatches the judge chain AFTER emitting agent.response, so gatekeeper.complete,
#     auditor.complete and strategist.complete all land on the SAME job_id afterwards.
#     The admin stream is the only place those verdicts are ever watched live; a global
#     terminal set would make them unreachable there.
#
#   * A distinct terminal marker emitted after agent.response occupies the same position
#     in created_at order, so it truncates a late-join replay at exactly the same point
#     as this does — it buys nothing — while costing a job_events row per turn, a new
#     event type every client must learn to ignore, and a worker code path that runs
#     after the answer is already served and can therefore fail after success.
#
# What this does NOT change:
#   * The judge chain's writes. emit() is a DB commit plus a fire-and-forget
#     redis.publish(); a channel with no subscriber is a no-op, so closing the customer
#     stream cannot affect gatekeeper/auditor/strategist persistence.
#   * Late-join replay. Phase 1 still replays the full history in created_at order. Only
#     the stop condition differs, and stopping the CUSTOMER stream at the customer's
#     answer is the intent — it is what apps/widget/src/sse.js already does client-side.
#
# agent.failed rides along because it is the same defect on the failure path and the
# widget already treats it as terminal too.
CUSTOMER_TERMINAL_EVENTS = TERMINAL_EVENTS | {"agent.response", "agent.failed"}

# ---------------------------------------------------------------------------
# What a PUBLIC reader of this stream may see, field by field (#104, #83).
#
# GET /widget/jobs/{job_id}/events authenticates on nothing but the job id, and
# four model-authored strings used to ride out on it: the tool call's `input`,
# the tool result's `summary`, the escalation's `reason` and `context`, and
# `str(exc)` on agent.failed, which renders a provider error body verbatim
# ("Error code: 429 - {...}") and echoes a DSN fragment straight out of libpq.
#
# The split is PERSIST versus PUBLISH, not one payload. Every field keeps being
# written to job_events, because server-side readers need them:
# retrieval_eval._fetch_turn_context reads `summary` off the persisted
# agent.tool_result rows as its retrieved-context proxy, and the tenant's
# escalation email carries the real reason and context, because the tenant is the
# data controller and the context is the point of the escalation. This map governs
# only what leaves the process to an unauthenticated caller.
#
# FAIL CLOSED. An event type with no entry here publishes an EMPTY payload rather
# than its contents, so a new event type says nothing on the public stream until
# someone decides what a customer may see. A public boundary that defaults to
# passing everything is how #104 happened.
#
# tests/unit/test_sse.py derives the turn path's emitted event types from the
# compiled modules and fails when one of them has no entry here.
PUBLIC_EVENT_FIELDS: dict[str, frozenset[str]] = {
    # No consumer reads this payload. The widget sets a typing indicator on the
    # event's arrival and ignores what it carries.
    "agent.thinking": frozenset(),
    # `input` is whatever the model put in the tool call. Widget.jsx reads
    # p.tool_name and passes input={} to the label, so the browser loses nothing.
    # tests/evals/capture_responses.py reads `input` off this stream, but only on
    # its fallback path: BACKLOG 7.34 made the tenant DB copy authoritative, and a
    # capture without that DB is already recorded BLIND.
    "agent.tool_call": frozenset({"tool_name"}),
    # `summary` is the first 200 characters of the tool result. Persisted for
    # retrieval_eval, never published.
    "agent.tool_result": frozenset({"tool_name"}),
    # EscalationPanel renders `reason` as visible text, so allowlisting it is not
    # enough on its own. PUBLIC_SCANNED_FIELDS puts it through the PII firewall.
    # `context` has no consumer on the wire at all.
    "agent.escalated": frozenset({"reason"}),
    # `text` already went through scan_response inside the turn seam (#50), with
    # that turn's published_context. It is deliberately NOT re-scanned here: this
    # layer holds no published_context, so a second scan would deflect a correct
    # answer that quotes the tenant's own published address (BACKLOG 7.29).
    # `citations` are parsed out of that same already-scanned text.
    "agent.response": frozenset({"text", "citations", "conversation_id", "message_id"}),
    # #83. error_type stays, because str() of TimeoutError and of a bare
    # psycopg2.OperationalError are both empty and {"error": ""} names nothing
    # (BACKLOG 1.30). Raw str(exc) never reaches a public reader.
    "agent.failed": frozenset({"error_type"}),
    # The judge chain writes to the same job_id after agent.response. Phase 1 stops
    # at the terminal event, but a phase 2 re-query yields the whole batch it read,
    # so a verdict committed in the same poll window as agent.response does reach a
    # customer. Verdict reasoning is written about the customer's own turn and has
    # no browser consumer.
    "gatekeeper.complete": frozenset(),
    "auditor.complete": frozenset(),
    "strategist.complete": frozenset(),
}

# Allowlisted AND scanned. scan_response replaces the WHOLE string with
# PII_DEFLECTION, which is the correct shape for a string a customer reads.
PUBLIC_SCANNED_FIELDS: dict[str, frozenset[str]] = {
    "agent.escalated": frozenset({"reason"}),
}

# emit() assigns `at` itself, on its own copy, after the caller's payload is in
# hand, so no call site can put anything else under that key. It carries no caller
# data, and listing it in every entry above would say the same thing ten times.
_SERVER_STAMPED = frozenset({"at"})


def public_payload(event_type: str, payload: dict | None) -> dict:
    """The subset of `payload` an unauthenticated reader may see.

    Returns {} for any event type PUBLIC_EVENT_FIELDS does not name.

    Example:
        >>> public_payload("agent.failed", {"error_type": "APIStatusError",
        ...                                 "error": "Error code: 429 - {...}"})
        {'error_type': 'APIStatusError'}
        >>> public_payload("nothing.known", {"secret": "x"})
        {}
    """
    allowed = PUBLIC_EVENT_FIELDS.get(event_type)
    if allowed is None:
        return {}
    scanned = PUBLIC_SCANNED_FIELDS.get(event_type, frozenset())
    visible: dict = {}
    for key, value in (payload or {}).items():
        if key not in allowed and key not in _SERVER_STAMPED:
            continue
        if key in scanned and isinstance(value, str):
            value = scan_response(value)[0]
        visible[key] = value
    return visible


def _sse_event(evt: JobEvent, public: bool) -> ServerSentEvent:
    """One job_events row as a ServerSentEvent, filtered when the reader is public."""
    payload = public_payload(evt.event_type, evt.payload) if public else evt.payload
    return ServerSentEvent(data=json.dumps(payload), event=evt.event_type, id=str(evt.id))


# Maximum seconds to wait for a pub/sub wake-up before doing a DB poll anyway.
# Keeps the stream alive during low-traffic jobs and catches any missed pub/sub.
POLL_INTERVAL_S = 3.0


async def _next_pubsub_message(pubsub) -> dict | None:
    """Return the next pub/sub 'message'-type message, or None if the channel
    has no more subscribers.  Skips subscribe/unsubscribe confirmations."""
    async for msg in pubsub.listen():
        if msg["type"] == "message":
            return msg
    return None


async def event_generator(
    request: Request,
    job_id: UUID,
    db: AsyncSession,
    redis_client,
    terminal_events: frozenset[str] = TERMINAL_EVENTS,
    public: bool = False,
) -> AsyncGenerator[ServerSentEvent, None]:
    """Async generator for job SSE events.

    Args:
        request:         FastAPI Request — used to detect client disconnection.
        job_id:          UUID of the job to stream events for.
        db:              Async SQLAlchemy session for DB queries.
        redis_client:    Async Redis client for pub/sub wake-up signal.
        terminal_events: Event types that close THIS stream. Defaults to the job
                         lifecycle set; the customer widget stream passes
                         CUSTOMER_TERMINAL_EVENTS.
        public:          True filters every payload through PUBLIC_EVENT_FIELDS.
                         Opt-in, and the PUBLIC widget endpoint is the only caller
                         that opts in: the authenticated operator stream reads the
                         same rows and keeps the full detail (#104).

    Yields:
        ServerSentEvent with event= (event_type) and data= (JSON payload).
        The id= field is set from the DB row UUID for SSE cache recovery.
    """
    seen_ids: set[UUID] = set()

    async with redis_client.pubsub() as pubsub:
        await pubsub.subscribe(f"job_events:{job_id}")

        # ------------------------------------------------------------------
        # Phase 1: Initial DB replay — backfill for late-joining clients.
        # ------------------------------------------------------------------
        db.expire_all()
        past = await db.execute(
            select(JobEvent)
            .where(JobEvent.job_id == job_id)
            .order_by(JobEvent.created_at)
        )
        for evt in past.scalars():
            seen_ids.add(evt.id)  # type: ignore[arg-type]
            if await request.is_disconnected():
                return
            yield _sse_event(evt, public)
            if evt.event_type in terminal_events:
                return

        # ------------------------------------------------------------------
        # Phase 2: Live stream — pub/sub triggers immediate DB re-query;
        # periodic polling acts as fallback if pub/sub message is lost.
        # ------------------------------------------------------------------
        while True:
            if await request.is_disconnected():
                return

            # Wait for either a pub/sub wake-up or the poll timeout.
            try:
                await asyncio.wait_for(
                    _next_pubsub_message(pubsub),
                    timeout=POLL_INTERVAL_S,
                )
            except asyncio.TimeoutError:
                pass  # poll interval elapsed — re-query regardless

            if await request.is_disconnected():
                return

            # Re-query DB for any events committed since last check.
            # expire_all() clears the SQLAlchemy identity-map cache so
            # the SELECT sees rows committed after this session was opened.
            db.expire_all()
            fresh = await db.execute(
                select(JobEvent)
                .where(JobEvent.job_id == job_id)
                .order_by(JobEvent.created_at)
            )
            terminal_seen = False
            for evt in fresh.scalars():
                if evt.id in seen_ids:
                    continue
                seen_ids.add(evt.id)  # type: ignore[arg-type]
                if await request.is_disconnected():
                    return
                yield _sse_event(evt, public)
                if evt.event_type in terminal_events:
                    terminal_seen = True

            if terminal_seen:
                return

            # Keepalive comment — prevents HTTP intermediaries from closing the
            # connection during long jobs where no new events are emitted for
            # several poll cycles (e.g. during a multi-batch Haiku run).
            yield ServerSentEvent(comment="keepalive")
