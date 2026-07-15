"""Bench flywheel Celery task — promote_trace_to_scenario (OPS-11).

Lives in the `runtime` queue.

Closes the flywheel's write side. 21-05 built the read/grade side (bench_service
+ traces.py: list failing traces, grade filed | held | dismissed, stored as
append-only control-DB job_events rows). This task takes a 'filed' trace and
inserts it into the tenant DB eval_scenarios table as source='production',
provenance=trace_id, origin_trace_id=trace_id — so it appears in the next
eval run tagged born-in-production (SC3).

Architecture constraints (CLAUDE.md — non-negotiable):
    - acks_late=True AND an idempotency guard on every Celery task (both always)
    - promote_trace_to_scenario receives only agent_id/trace_id — conn_str is
      decrypted from the control DB at runtime, NEVER passed in task args
      (CLAUDE.md rule 4 / CTL-08)

Cross-DB correlation (21-RESEARCH.md Pattern 2 / Pitfall 5):
    conversation_id and the agent's response text are sourced from the trace's
    OWN 'agent.response' event payload in the control DB job_events table —
    never from a `jobs` table column (Job has no conversation_id column at
    all; scenario_service.mine_production_scenarios' fallback query against
    it is a known-broken pattern this task must not repeat). The customer's
    question is then recovered from tenant DB `messages` via
    bench_service._fetch_customer_turn — the same last-user-before-matching-
    assistant walk 21-05's trace listing already uses.

Idempotency:
    A pre-check `SELECT 1 FROM eval_scenarios WHERE origin_trace_id = trace_id`
    runs (on the same connection, before any INSERT) — a second promote call
    for the same trace_id inserts zero rows and returns
    {"status": "already_promoted", ...}.
"""

from __future__ import annotations

import psycopg2
import structlog
from sqlalchemy import text as sa_text

from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.services.bench_service import _fetch_customer_turn
from app.services.scenario_service import insert_provenance_scenario
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

# Same query shape as bench_service._AGENT_RESPONSE_SQL (control DB, sync
# session here rather than async — get_sync_db()'s Session.execute is the
# synchronous twin of AsyncSession.execute used by bench_service).
_AGENT_RESPONSE_SQL = sa_text(
    """
    SELECT payload FROM job_events
    WHERE job_id = :job_id AND event_type = 'agent.response'
    LIMIT 1
    """
)


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=30,
    queue="runtime",
    name="app.worker.tasks.runtime.bench.promote_trace_to_scenario",
)
def promote_trace_to_scenario(self, agent_id: str, trace_id: str) -> dict:
    """Promote a 'filed' production trace into eval_scenarios (source='production').

    Receives only agent_id/trace_id — conn_str is decrypted at runtime, never
    passed as a task arg (CLAUDE.md rule 4 / CTL-08).

    Sequence:
        1. Fetch agent from control DB; decrypt conn_str.
        2. Recover conversation_id + agent answer from the trace's own
           'agent.response' job_events payload (control DB) — NOT from a
           jobs table column (Pitfall 5).
        3. Recover the customer question from tenant DB messages via the
           same correlation bench_service._fetch_customer_turn uses.
        4. Idempotency guard: SELECT 1 FROM eval_scenarios WHERE
           origin_trace_id = trace_id — skip the insert if already promoted.
        5. insert_provenance_scenario(source='production', provenance=trace_id,
           origin_trace_id=trace_id).

    Args:
        agent_id: UUID string of the agent that owns the trace.
        trace_id: The job_id of the filed trace being promoted.

    Returns:
        {"status": "promoted", "trace_id": trace_id}          on success.
        {"status": "already_promoted", "trace_id": trace_id}  idempotent skip.
        {"status": "no_response_event", "trace_id": trace_id} cannot recover the turn.
        {}                                                      on retry exhaustion.
    """
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        if agent is None or not agent.neon_connection_string:
            log.error("promote_trace_to_scenario.agent_not_found", agent_id=agent_id)
            return {}

        conn_str = fernet_decrypt(agent.neon_connection_string)

        response_row = db.execute(_AGENT_RESPONSE_SQL, {"job_id": trace_id}).fetchone()

    if response_row is None or not response_row.payload:
        log.info("promote_trace_to_scenario.no_response_event", trace_id=trace_id)
        return {"status": "no_response_event", "trace_id": trace_id}

    payload = response_row.payload
    conversation_id = payload.get("conversation_id")
    agent_turn = payload.get("text", "")

    question = ""
    if conversation_id:
        try:
            question = _fetch_customer_turn(conn_str, conversation_id, agent_turn)
        except Exception as exc:
            log.warning(
                "promote_trace_to_scenario.fetch_customer_turn_failed",
                trace_id=trace_id,
                error=str(exc),
            )

    try:
        conn = psycopg2.connect(conn_str, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM eval_scenarios WHERE origin_trace_id = %s LIMIT 1",
                    (trace_id,),
                )
                if cur.fetchone() is not None:
                    log.info(
                        "promote_trace_to_scenario.idempotent_skip",
                        agent_id=agent_id,
                        trace_id=trace_id,
                    )
                    return {"status": "already_promoted", "trace_id": trace_id}

            insert_provenance_scenario(
                conn,
                source="production",
                question=question,
                reference_answer=agent_turn,
                retrieved_contexts=[],
                provenance=trace_id,
                origin_trace_id=trace_id,
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        log.error(
            "promote_trace_to_scenario.insert_failed",
            agent_id=agent_id,
            trace_id=trace_id,
            error=str(exc),
        )
        if self.request.retries >= self.max_retries:
            return {}
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)

    log.info(
        "promote_trace_to_scenario.complete",
        agent_id=agent_id,
        trace_id=trace_id,
    )
    return {"status": "promoted", "trace_id": trace_id}
