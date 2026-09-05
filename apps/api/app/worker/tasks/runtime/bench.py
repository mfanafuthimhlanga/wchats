"""Bench flywheel Celery task — promote_trace_to_scenario (OPS-11).

Lives in the `runtime` queue.

Closes the flywheel's write side. 21-05 built the read/grade side (bench_service
+ traces.py: list failing traces, grade filed | held | dismissed, stored as
append-only control-DB job_events rows). This task takes a 'filed' trace and
inserts it into the tenant DB eval_scenarios table as source='production',
provenance=trace_id, origin_trace_id=trace_id — so it appears in the next
eval run tagged born-in-production (SC3).

A filed trace carries no ground truth (the label inversion, audit D5)
------------------------------------------------------------------------
traces.py lists FAILING traces. The operator grades one 'filed', which means
"this answer was wrong and I want it in the eval set". This task used to write
that answer into `reference_answer` — the column whose entire meaning is "the
correct answer to this question". A known-bad answer became the ground truth
for its own question, and the eval then scored the bad answer against itself.

Worse, the promotion path reads `reference_answer` to decide what may enter
`verified_qa`, which retrieval_service.verified_qa_lookup serves to real
customers ahead of hybrid search at 0.93 cosine similarity. Only the fact that
eval results were being written to a throwaway Neon branch kept the operator's
own flagged failure from being served back to customers as a verified answer —
protection by accident, and this branch removes that accident.

What a filed trace actually contains is (question, known-bad answer, NO ground
truth). So it is stored as exactly that:

    reference_answer = ''   — the same honest convention
                              scenario_service.mine_production_scenarios uses.
                              It is also inert by construction: run_eval_suite
                              selects WHERE reference_answer != '', so no eval
                              can score a row that has no label.

    the failing answer      — NOT copied into eval_scenarios, which has no
                              non-label column for it. It already lives durably
                              in the append-only control-DB job_events
                              'agent.response' row this task reads it from, and
                              origin_trace_id on the scenario row is the pointer
                              back to it. A second copy in a column that means
                              something else is how D5 happened.

Filing therefore produces a question with no answer, which the eval selector
skips — the honest consequence of having no correction UI yet, and it is said
out loud rather than papered over: each promotion appends a
'trace.promoted_to_scenario' event to the trace recording that no reference
answer was stored, why, and the agent answer it declined to treat as a label.
Wiring a correction UI (owner writes the RIGHT answer) is what makes filing
produce a scorable row; inventing a label here would only produce a wrong one.

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
from app.core.log_bounds import log_failure
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.models.job_event import JobEvent
from app.services.bench_service import _fetch_customer_turn
from app.services.scenario_service import insert_provenance_scenario
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

# Written into eval_scenarios.reference_answer for every filed trace. Named
# rather than inlined so the label test can assert against the same constant
# the task writes, and so grep for it lands on the reasoning above.
NO_GROUND_TRUTH = ""

# Recorded verbatim on the trace event below. The operator asked for this
# question to be evaluated; they are owed a straight answer about why it will
# not be scored yet, rather than a silently unscorable row.
NO_REFERENCE_ANSWER_REASON = (
    "A filed trace is a question plus a KNOWN-BAD answer, not a ground truth. "
    "The agent's answer is not stored as reference_answer because that column "
    "means 'the correct answer' and is read by the verified_qa promotion path "
    "that serves answers to customers. The scenario is therefore stored "
    "without a label and is skipped by the eval selector "
    "(WHERE reference_answer != '') until an owner supplies the correct answer."
)

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
           origin_trace_id=trace_id, reference_answer=NO_GROUND_TRUTH).
        6. Append a 'trace.promoted_to_scenario' event to the trace saying no
           reference answer was stored and why (audit D5 — see module docstring).

    The agent's answer recovered in step 2 is used ONLY to correlate the
    customer turn in step 3. It is never written to reference_answer: a filed
    trace is a failure the operator flagged, so its answer is a known-bad one,
    and reference_answer is the column the customer-serving verified_qa
    promotion path reads.

    Args:
        agent_id: UUID string of the agent that owns the trace.
        trace_id: The job_id of the filed trace being promoted.

    Returns:
        {"status": "promoted", "trace_id", "scenario_id",
         "reference_answer_stored": False}                    on success.
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
            log_failure(log, "promote_trace_to_scenario.fetch_customer_turn_failed", exc, trace_id=trace_id)

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

            # D5: reference_answer is NO_GROUND_TRUTH, never agent_turn. See
            # the module docstring — agent_turn is the answer the operator
            # flagged as FAILING, and this column is read by the verified_qa
            # promotion path that serves answers to customers.
            scenario_id = insert_provenance_scenario(
                conn,
                source="production",
                question=question,
                reference_answer=NO_GROUND_TRUTH,
                retrieved_contexts=[],
                provenance=trace_id,
                origin_trace_id=trace_id,
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        log_failure(
            log, "promote_trace_to_scenario.insert_failed", exc, level="error",
            agent_id=agent_id,
            trace_id=trace_id,
        )
        if self.request.retries >= self.max_retries:
            return {}
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)

    # ------------------------------------------------------------------
    # Say it in the trace. The scenario row above carries no label, and an
    # operator who filed a trace expecting it to be evaluated deserves to
    # read why it will not be, on the trace they filed. Append-only
    # job_events row, the same shape bench_service.grade_trace writes.
    #
    # Best-effort by design: the scenario insert is already committed and
    # idempotency-guarded, and the agent's answer is NOT lost if this write
    # fails — it lives in this trace's own 'agent.response' event, which is
    # where step 2 read it from. Raising here would retry a committed insert
    # for the sake of a note.
    # ------------------------------------------------------------------
    try:
        with get_sync_db() as db:
            db.add(
                JobEvent(
                    job_id=trace_id,
                    event_type="trace.promoted_to_scenario",
                    payload={
                        "agent_id": agent_id,
                        "scenario_id": scenario_id,
                        "source": "production",
                        "reference_answer_stored": False,
                        "reason": NO_REFERENCE_ANSWER_REASON,
                        # The flagged answer, in a field that claims nothing
                        # about correctness. Kept here so a reader of the
                        # trace sees what was declined as a label.
                        "flagged_agent_answer": agent_turn,
                    },
                )
            )
            db.commit()
    except Exception as note_exc:
        log_failure(
            log, "promote_trace_to_scenario.trace_note_failed", note_exc,
            agent_id=agent_id,
            trace_id=trace_id,
        )

    log.info(
        "promote_trace_to_scenario.complete",
        agent_id=agent_id,
        trace_id=trace_id,
        scenario_id=scenario_id,
        reference_answer_stored=False,
    )
    return {
        "status": "promoted",
        "trace_id": trace_id,
        "scenario_id": scenario_id,
        # Explicit, not implied: this scenario has no ground truth and the
        # eval selector will skip it until an owner supplies one.
        "reference_answer_stored": False,
    }
