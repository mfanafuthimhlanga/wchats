"""Bench service — failure-triage flywheel (OPS-09/10).

Makes the ops-room "bench" region real: lists production turns the Gatekeeper
or Auditor judges flagged (fail / ungrounded / partial) and lets an operator
grade each one filed | held | dismissed. A 'filed' grade is irrevocable
(TERRARIUM law) — grade_trace() refuses any write that would transition a
trace away from 'filed'.

Architecture (RESEARCH.md Pattern 2 — cross-DB correlation in application code):
    - Judge verdicts (gatekeeper.complete / auditor.complete) live in the
      CONTROL DB job_events table.
    - The customer question / agent answer TEXT lives in the TENANT DB
      messages table.
    - There is no cross-DB SQL join available (control DB and tenant DB are
      separate Neon projects) — the correlation happens as two sequential
      queries merged in Python, exactly like scenario_service.mine_production_scenarios.
    - conversation_id is ALWAYS sourced from the flagged job's own
      'agent.response' event payload (control DB job_events) — NEVER from the
      Job ORM model, which has no conversation_id column at all (Pitfall 5).
      Copying scenario_service's broken fallback query (a SELECT of that
      nonexistent column against the jobs table) is explicitly prohibited by
      this plan's must_haves.

Storage (Assumption A-BENCH, documented in 21-05-PLAN.md):
    Operator grades are stored as append-only control-DB job_events rows
    (event_type='trace.graded') — there is no new bench table. Irrevocability
    is enforced as a refuse-to-write invariant at this service layer, not via
    an UPDATE/DELETE guard (grades are never mutated or deleted).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import psycopg2
import structlog
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job_event import JobEvent

log = structlog.get_logger(__name__)

_VALID_GRADES = {"filed", "held", "dismissed"}


class TraceAlreadyFiledError(Exception):
    """Raised when grade_trace() is called on a trace already graded 'filed'.

    TERRARIUM law: a filed grade is irrevocable. This exception is raised
    BEFORE any write is attempted — grade_trace() never inserts a job_events
    row when this fires. The route layer maps this to HTTP 409.
    """


class InvalidGradeError(Exception):
    """Raised when grade_trace() is called with a grade outside filed|held|dismissed."""


class TraceNotFoundError(Exception):
    """Raised when trace_id has no flagged judge event owned by agent_id.

    Mitigates T-21-05-01: without this check, an operator could grade an
    arbitrary job_id UUID that belongs to a DIFFERENT agent/tenant, since
    job_events has no foreign-key-enforced agent ownership. The route layer
    maps this to HTTP 404 (IDOR-consistent — no existence leak).
    """


# ---------------------------------------------------------------------------
# Internal helper — wraps blocking psycopg2 calls for asyncio.to_thread
# ---------------------------------------------------------------------------


def _query_tenant_db_sync(conn_str: str, sql: str, params: dict) -> list[tuple]:
    """Execute a SELECT against the tenant DB synchronously.

    Wraps psycopg2 in a try/finally to ensure the connection is always closed.
    Called inside asyncio.to_thread() to avoid blocking the FastAPI event loop.
    Same idiom as evals.py / red_team.py's _query_tenant_db_sync.

    Args:
        conn_str: Decrypted tenant DB connection string (never logged — T-02-01).
        sql: SQL query with %(name)s placeholders.
        params: Dict of query parameters.

    Returns:
        List of row tuples from fetchall().
    """
    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def _fetch_customer_turn(conn_str: str, conversation_id: str, agent_turn_text: str) -> str:
    """Find the customer (user) message immediately preceding the matching agent turn.

    messages has no job_id/turn linkage (schema confirmed: id, conversation_id,
    role, content, created_at only), so the correlation walks the conversation's
    messages in order and returns the last 'user' message content seen before
    the 'assistant' message whose content matches agent_turn_text exactly
    (user + assistant rows are inserted back-to-back in the same transaction
    by _persist_messages, so this pairing is reliable).

    Falls back to the LAST user message in the conversation if no exact
    content match is found (e.g. response text was altered after the
    agent.response event was emitted) — never raises, worst case returns
    a slightly-stale customer turn rather than an empty one.

    Args:
        conn_str: Decrypted tenant DB connection string (never logged).
        conversation_id: UUID string of the conversation.
        agent_turn_text: The agent's response text from the agent.response payload.

    Returns:
        The customer's message content, or "" if the conversation has no user messages.
    """
    rows = _query_tenant_db_sync(
        conn_str,
        """
        SELECT role, content FROM messages
        WHERE conversation_id = %(conv_id)s::uuid
        ORDER BY seq ASC
        """,
        {"conv_id": conversation_id},
    )

    last_user = ""
    for role, content in rows:
        if role == "user":
            last_user = content
        elif role == "assistant" and content == agent_turn_text:
            return last_user
    return last_user


# ---------------------------------------------------------------------------
# SQL — control DB (job_events)
# ---------------------------------------------------------------------------

_FLAGGED_EVENTS_SQL = sa_text(
    """
    SELECT je.job_id, je.payload->>'verdict' AS verdict,
           je.payload->>'reason' AS reason, je.created_at
    FROM job_events je
    WHERE je.event_type IN ('gatekeeper.complete', 'auditor.complete')
      AND je.payload->>'agent_id' = :agent_id
      AND je.payload->>'verdict' IN ('fail', 'ungrounded', 'partial')
    ORDER BY je.created_at DESC
    LIMIT :row_limit
    """
)

_AGENT_RESPONSE_SQL = sa_text(
    """
    SELECT payload FROM job_events
    WHERE job_id = :job_id AND event_type = 'agent.response'
    LIMIT 1
    """
)

_TRACE_OWNER_CHECK_SQL = sa_text(
    """
    SELECT 1 FROM job_events
    WHERE job_id = :job_id
      AND event_type IN ('gatekeeper.complete', 'auditor.complete')
      AND payload->>'agent_id' = :agent_id
    LIMIT 1
    """
)

_EXISTING_GRADES_FOR_TRACE_SQL = sa_text(
    """
    SELECT payload->>'grade' AS grade
    FROM job_events
    WHERE job_id = :job_id AND event_type = 'trace.graded'
    ORDER BY created_at ASC
    """
)

_ALL_GRADED_EVENTS_FOR_AGENT_SQL = sa_text(
    """
    SELECT job_id, payload->>'grade' AS grade
    FROM job_events
    WHERE event_type = 'trace.graded' AND payload->>'agent_id' = :agent_id
    ORDER BY created_at ASC
    """
)


# ---------------------------------------------------------------------------
# list_failing_traces (OPS-09)
# ---------------------------------------------------------------------------


async def list_failing_traces(
    control_db: AsyncSession,
    conn_str: str,
    agent_id: str,
    limit: int = 50,
) -> dict:
    """Return failing production traces with customer turn, agent turn, and judge rationale.

    Cross-DB correlation (RESEARCH.md Pattern 2): control-DB job_events judge
    verdicts are merged in Python with tenant-DB messages text. conversation_id
    is read from the SAME job's 'agent.response' event payload — never from a
    jobs table (Pitfall 5).

    Args:
        control_db: AsyncSession bound to the control DB.
        conn_str: Decrypted tenant DB connection string (never logged).
        agent_id: UUID string of the agent.
        limit: Max distinct failing traces to return (default 50).

    Returns:
        {"traces": [{trace_id, verdict, judge_rationale, customer_turn,
                     agent_turn, conversation_id, graded_status}],
         "tally": {"filed": int, "held": int, "dismissed": int}}
    """
    # Over-fetch flagged rows since multiple judge events can share a job_id
    # (gatekeeper + auditor both fired on the same turn) — dedupe below keeps
    # only the most recent verdict per job_id.
    flagged_result = await control_db.execute(
        _FLAGGED_EVENTS_SQL, {"agent_id": agent_id, "row_limit": limit * 4}
    )
    flagged_rows = flagged_result.fetchall()

    tally = await bench_tally(control_db, agent_id)
    graded_by_job = tally["graded_by_job"]

    traces: list[dict] = []
    seen_job_ids: set[str] = set()

    for row in flagged_rows:
        if len(traces) >= limit:
            break

        job_id = str(row.job_id)
        if job_id in seen_job_ids:
            continue
        seen_job_ids.add(job_id)

        # Fetch the SAME job's agent.response event payload for conversation_id
        # + agent turn text — NEVER jobs.conversation_id (Pitfall 5).
        response_result = await control_db.execute(_AGENT_RESPONSE_SQL, {"job_id": job_id})
        response_row = response_result.fetchone()

        if response_row is None or not response_row.payload:
            # No agent.response event recorded for this job — cannot recover
            # the turn text; skip rather than surface a hollow trace.
            log.info("bench.list_failing_traces.no_response_event", job_id=job_id)
            continue

        response_payload = response_row.payload
        conversation_id = response_payload.get("conversation_id")
        agent_turn = response_payload.get("text", "")

        customer_turn = ""
        if conversation_id:
            customer_turn = await asyncio.to_thread(
                _fetch_customer_turn, conn_str, conversation_id, agent_turn
            )

        traces.append(
            {
                "trace_id": job_id,
                "verdict": row.verdict,
                "judge_rationale": row.reason or "",
                "customer_turn": customer_turn,
                "agent_turn": agent_turn,
                "conversation_id": conversation_id,
                "graded_status": graded_by_job.get(job_id, "ungraded"),
            }
        )

    log.info(
        "bench.list_failing_traces.ok",
        agent_id=agent_id,
        flagged_count=len(flagged_rows),
        trace_count=len(traces),
    )
    return {"traces": traces, "tally": tally["counts"]}


# ---------------------------------------------------------------------------
# bench_tally
# ---------------------------------------------------------------------------


async def bench_tally(control_db: AsyncSession, agent_id: str) -> dict:
    """Aggregate the latest grade per trace (job_id) for this agent.

    'filed' is terminal: once a job_id has a 'filed' row, later rows for the
    same job_id (which grade_trace() should never produce, but this is a
    belt-and-suspenders read-path guard) are ignored in the tally so a bug
    upstream can never surface an un-filed count for an already-filed trace.

    Args:
        control_db: AsyncSession bound to the control DB.
        agent_id: UUID string of the agent.

    Returns:
        {"counts": {"filed": int, "held": int, "dismissed": int},
         "graded_by_job": {job_id: latest_grade}}
    """
    result = await control_db.execute(_ALL_GRADED_EVENTS_FOR_AGENT_SQL, {"agent_id": agent_id})
    rows = result.fetchall()

    graded_by_job: dict[str, str] = {}
    for row in rows:
        job_id = str(row.job_id)
        if graded_by_job.get(job_id) == "filed":
            continue  # filed is terminal — never overwritten
        graded_by_job[job_id] = row.grade

    counts = {"filed": 0, "held": 0, "dismissed": 0}
    for grade in graded_by_job.values():
        if grade in counts:
            counts[grade] += 1

    return {"counts": counts, "graded_by_job": graded_by_job}


# ---------------------------------------------------------------------------
# grade_trace (OPS-10)
# ---------------------------------------------------------------------------


async def grade_trace(
    control_db: AsyncSession,
    agent_id: str,
    trace_id: str,
    grade: str,
) -> dict:
    """Persist an operator grade for a trace as an append-only job_events row.

    TERRARIUM law: a trace already graded 'filed' is irrevocable —
    raises TraceAlreadyFiledError (route maps to 409) BEFORE any write is
    attempted. Grades are never updated or deleted; each grade call inserts a
    new job_events row (event_type='trace.graded').

    Mitigates T-21-05-01: verifies trace_id belongs to agent_id (via the
    flagged judge event's payload) before writing — prevents grading a trace
    that belongs to a different agent/tenant.

    Args:
        control_db: AsyncSession bound to the control DB.
        agent_id: UUID string of the agent (stored in the payload; used for tally queries).
        trace_id: The job_id of the trace being graded.
        grade: One of 'filed' | 'held' | 'dismissed'.

    Raises:
        InvalidGradeError: grade is not one of the three valid values.
        TraceNotFoundError: trace_id has no flagged judge event owned by agent_id.
        TraceAlreadyFiledError: trace_id already has a 'filed' grade.

    Returns:
        {"trace_id": trace_id, "grade": grade, "tally": {"filed", "held", "dismissed"}}
    """
    if grade not in _VALID_GRADES:
        raise InvalidGradeError(
            f"Invalid grade {grade!r} — must be one of {sorted(_VALID_GRADES)}."
        )

    owner_result = await control_db.execute(
        _TRACE_OWNER_CHECK_SQL, {"job_id": trace_id, "agent_id": agent_id}
    )
    if owner_result.fetchone() is None:
        raise TraceNotFoundError(f"Trace {trace_id} not found for agent {agent_id}.")

    existing_result = await control_db.execute(_EXISTING_GRADES_FOR_TRACE_SQL, {"job_id": trace_id})
    existing_grades = [row.grade for row in existing_result.fetchall()]
    if "filed" in existing_grades:
        raise TraceAlreadyFiledError(
            f"Trace {trace_id} was already graded 'filed' — filed grades are irrevocable."
        )

    payload = {
        "grade": grade,
        "agent_id": agent_id,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    control_db.add(JobEvent(job_id=trace_id, event_type="trace.graded", payload=payload))
    await control_db.commit()

    tally = await bench_tally(control_db, agent_id)

    log.info(
        "bench.grade_trace.ok",
        agent_id=agent_id,
        trace_id=trace_id,
        grade=grade,
    )
    return {"trace_id": trace_id, "grade": grade, "tally": tally["counts"]}
