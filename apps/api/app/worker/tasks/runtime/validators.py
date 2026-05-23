"""
run_gatekeeper / run_auditor / run_strategist — Celery tasks: Post-response validation chain.

Position in M5 runtime flow:
    run_agent_turn completes and streams response to user
      → Plan 04 dispatches chain: run_gatekeeper.si() | run_auditor.si() | run_strategist.si()
      → Each task runs on the runtime queue after the response is delivered

Idempotency mechanism:
    Each task guards on its own '<judge>.complete' event type in job_events.
    Safe to retry without duplicate judge calls or duplicate SSE events.

Security constraints (CLAUDE.md non-negotiable rules):
    - Task args: (agent_id, job_id, response_text, question, ...) ONLY.
      NO conn_str in task args (CTL-08 / T-05-03-01).
    - Auditor: conn_str fetched via fernet_decrypt(agent.neon_connection_string) at runtime.
    - conn_str is intentionally not logged.

Validator rules (per plan 05-03):
    - All judge calls are synchronous (D-02). No coroutines.
    - On retry exhaustion: log.error + return {}. Validators never disrupt the user-facing turn.
    - acks_late=True on all three tasks (CLAUDE.md non-negotiable).

Queue: runtime (CLAUDE.md non-negotiable: both Celery queues always present)
"""

import json
import ssl
import uuid

import psycopg2
import redis as redis_lib
import structlog
from sqlalchemy import text as sa_text

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.services.events import emit
from app.services.validation_service import (
    call_gatekeeper,
    call_auditor,
    call_strategist,
    _log_verdict,
)
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level sync Redis client (copied verbatim from agent.py lines 74-76)
# Strip query params; pass ssl_cert_reqs as Python constant.
# ---------------------------------------------------------------------------
_url_clean = settings.REDIS_URL.split("?")[0] if "?" in settings.REDIS_URL else settings.REDIS_URL
_ssl_opts: dict = {"ssl_cert_reqs": ssl.CERT_NONE} if _url_clean.startswith("rediss://") else {}
_redis = redis_lib.from_url(_url_clean, **_ssl_opts)


# ---------------------------------------------------------------------------
# Tenant DB helper — verified_qa_candidates insert (D-19 / D-20)
# ---------------------------------------------------------------------------

def _insert_verified_qa_candidate(
    conn_str: str,
    conversation_id: str,
    question: str,
    answer: str,
    citations: list[dict],
    auditor_confidence: float,
) -> None:
    """Insert a verified QA candidate row into the tenant DB.

    Uses psycopg2 directly (not SQLAlchemy) because the tenant DB is a
    separate Neon project — not the control DB that get_sync_db() connects to.

    Security (T-05-03-03): All values bound as psycopg2 %s parameters.
    citations serialized via json.dumps then cast to ::jsonb — no f-string SQL.
    Uses ON CONFLICT DO NOTHING for idempotency on duplicate (job_id, question) retries.

    Args:
        conn_str:            Decrypted Neon connection string for tenant DB.
        conversation_id:     UUID string of the conversation where QA was generated.
        question:            User question text.
        answer:              Agent response text.
        citations:           List of citation span dicts (from AuditorVerdict.citation_spans).
        auditor_confidence:  Float [0,1] confidence score from the Auditor judge.
    """
    conn = psycopg2.connect(conn_str, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO verified_qa_candidates
                  (id, conversation_id, question, answer, citations, auditor_confidence, queued_at, status)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, NOW(), 'pending')
                ON CONFLICT DO NOTHING
                """,
                (
                    str(uuid.uuid4()),
                    conversation_id,
                    question,
                    answer,
                    json.dumps(citations),
                    auditor_confidence,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    log.debug(
        "_insert_verified_qa_candidate.done",
        conversation_id=conversation_id,
        auditor_confidence=auditor_confidence,
    )


# ---------------------------------------------------------------------------
# VAL-01 / VAL-02: run_gatekeeper — "Does this response address the question?"
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=5,
    queue="runtime",
    name="run_gatekeeper",
)
def run_gatekeeper(
    self,
    agent_id: str,
    job_id: str,
    response_text: str,
    question: str,
) -> dict:
    """Run the Gatekeeper judge synchronously and emit a 'gatekeeper.complete' event.

    Idempotent: returns {"status": "already_complete"} immediately if a
    'gatekeeper.complete' event row already exists for this job_id.

    Validators never disrupt the user-facing turn: on retry exhaustion log.error + return {}.

    Args:
        agent_id:      UUID string of the agent whose response is being evaluated.
        job_id:        UUID string of the runtime chat job.
        response_text: The agent's response to evaluate.
        question:      The user's original question.

    Returns:
        {"status": "already_complete"}  — idempotent path
        {}                              — all other paths (success or exhaustion)
    """
    with get_sync_db() as db:
        # ------------------------------------------------------------------
        # Idempotency guard — exit immediately if gatekeeper.complete already
        # exists for this job_id. Prevents duplicate Haiku calls on retry.
        # ------------------------------------------------------------------
        existing = db.execute(
            sa_text(
                "SELECT 1 FROM job_events"
                " WHERE job_id = :jid AND event_type = 'gatekeeper.complete' LIMIT 1"
            ),
            {"jid": job_id},
        ).fetchone()
        if existing:
            log.info("run_gatekeeper.idempotent_skip", job_id=job_id)
            return {"status": "already_complete"}

        # ------------------------------------------------------------------
        # Fetch agent from control DB — required for logging metadata.
        # ------------------------------------------------------------------
        agent = db.get(Agent, agent_id)
        if agent is None:
            log.error(
                "run_gatekeeper.agent_not_found",
                job_id=job_id,
                agent_id=agent_id,
            )
            return {}

        try:
            # --------------------------------------------------------------
            # Call Gatekeeper judge (synchronous Haiku API call — D-02)
            # --------------------------------------------------------------
            verdict = call_gatekeeper(question, response_text)

            # --------------------------------------------------------------
            # Log to Langfuse via _log_verdict (Pitfall 2: flush inside)
            # --------------------------------------------------------------
            _log_verdict(
                judge_name="gatekeeper",
                agent_id=agent_id,
                job_id=job_id,
                input_payload={
                    "question_length": len(question),
                    "response_length": len(response_text),
                },
                verdict_dict=verdict.model_dump(),
            )

            # --------------------------------------------------------------
            # Emit gatekeeper.complete with verdict payload + agent_id
            # agent_id included for VAL-06 counting queries
            # --------------------------------------------------------------
            emit(
                job_id,
                "gatekeeper.complete",
                {**verdict.model_dump(), "agent_id": agent_id},
                db,
                _redis,
            )

            log.info(
                "run_gatekeeper.complete",
                job_id=job_id,
                agent_id=agent_id,
                verdict=verdict.verdict,
                confidence=verdict.confidence,
            )

        except Exception as exc:
            log.error(
                "run_gatekeeper.failed",
                job_id=job_id,
                agent_id=agent_id,
                error=str(exc),
            )
            # Validators never disrupt the user-facing turn: log and return {} on exhaustion.
            if self.request.retries >= self.max_retries:
                return {}
            else:
                countdown = 2 ** self.request.retries
                raise self.retry(exc=exc, countdown=countdown)

    return {}


# ---------------------------------------------------------------------------
# VAL-03: run_auditor — "Is every claim grounded in retrieved context?"
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=5,
    queue="runtime",
    name="run_auditor",
)
def run_auditor(
    self,
    agent_id: str,
    job_id: str,
    response_text: str,
    question: str,
    retrieved_context_json: str,
    conversation_id: str,
) -> dict:
    """Run the Auditor judge and handle verified_qa_candidates insert + resynthesis flag.

    Idempotent: returns {"status": "already_complete"} if 'auditor.complete' already
    exists for this job_id.

    Post-verdict logic (D-19):
        grounded + confidence >= threshold → insert verified_qa_candidates row on tenant DB.
        conn_str decrypted at runtime (CTL-08 — never in task args).

    Post-verdict logic (D-10 / VAL-06):
        ungrounded → count recent ungrounded auditor.complete events for this agent
        in the last 24h. If count >= 3 → set strategy_resynthesis_flagged = TRUE.

    Note: auditor.complete is emitted BEFORE the count query so the current verdict
    is already in the 24h window when we check.

    Validators never disrupt the user-facing turn: on retry exhaustion log.error + return {}.

    Args:
        agent_id:               UUID string of the agent.
        job_id:                 UUID string of the runtime chat job.
        response_text:          The agent's response to audit.
        question:               The user's original question.
        retrieved_context_json: JSON string of retrieved context passages.
        conversation_id:        UUID string of the conversation (for QA insert).

    Returns:
        {"status": "already_complete"}  — idempotent path
        {}                              — all other paths (success or exhaustion)
    """
    with get_sync_db() as db:
        # ------------------------------------------------------------------
        # Idempotency guard
        # ------------------------------------------------------------------
        existing = db.execute(
            sa_text(
                "SELECT 1 FROM job_events"
                " WHERE job_id = :jid AND event_type = 'auditor.complete' LIMIT 1"
            ),
            {"jid": job_id},
        ).fetchone()
        if existing:
            log.info("run_auditor.idempotent_skip", job_id=job_id)
            return {"status": "already_complete"}

        # ------------------------------------------------------------------
        # Fetch agent from control DB
        # ------------------------------------------------------------------
        agent = db.get(Agent, agent_id)
        if agent is None:
            log.error(
                "run_auditor.agent_not_found",
                job_id=job_id,
                agent_id=agent_id,
            )
            return {}

        try:
            # --------------------------------------------------------------
            # Parse retrieved context (Pitfall 3: arrives as JSON string)
            # --------------------------------------------------------------
            retrieved_context = json.loads(retrieved_context_json or "[]")

            # --------------------------------------------------------------
            # Call Auditor judge (synchronous Haiku API call — D-02)
            # --------------------------------------------------------------
            verdict = call_auditor(question, response_text, retrieved_context)

            # --------------------------------------------------------------
            # Log to Langfuse
            # --------------------------------------------------------------
            _log_verdict(
                judge_name="auditor",
                agent_id=agent_id,
                job_id=job_id,
                input_payload={
                    "question_length": len(question),
                    "response_length": len(response_text),
                    "context_chunks": len(retrieved_context) if isinstance(retrieved_context, list) else 0,
                },
                verdict_dict=verdict.model_dump(),
            )

            # --------------------------------------------------------------
            # Emit auditor.complete BEFORE count query so this verdict is
            # included in the 24h window (per plan spec — emit before count)
            # --------------------------------------------------------------
            emit(
                job_id,
                "auditor.complete",
                {**verdict.model_dump(), "agent_id": agent_id},
                db,
                _redis,
            )

            # --------------------------------------------------------------
            # D-19: Insert verified_qa_candidate when grounded + above threshold
            # Threshold: per-agent override with global settings default
            # conn_str decrypted at runtime (CTL-08 — never in task args)
            # --------------------------------------------------------------
            threshold = (agent.retrieval_strategy or {}).get(
                "verified_qa_threshold",
                settings.VERIFIED_QA_CONFIDENCE_THRESHOLD,
            )

            if verdict.verdict == "grounded" and verdict.confidence >= threshold:
                conn_str = fernet_decrypt(agent.neon_connection_string)
                _insert_verified_qa_candidate(
                    conn_str=conn_str,
                    conversation_id=conversation_id,
                    question=question,
                    answer=response_text,
                    citations=[s.model_dump() for s in verdict.citation_spans],
                    auditor_confidence=verdict.confidence,
                )
                log.info(
                    "run_auditor.verified_qa_inserted",
                    job_id=job_id,
                    agent_id=agent_id,
                    confidence=verdict.confidence,
                )

            # --------------------------------------------------------------
            # D-10 / VAL-06: Count recent ungrounded verdicts for this agent
            # in the 24h window. If >= 3, set strategy_resynthesis_flagged.
            # Note: auditor.complete was already emitted above, so THIS verdict
            # is already in the window.
            # --------------------------------------------------------------
            if verdict.verdict == "ungrounded":
                recent_ungrounded = db.execute(
                    sa_text("""
                        SELECT COUNT(*) FROM job_events
                        WHERE event_type = 'auditor.complete'
                          AND payload->>'agent_id' = :agent_id
                          AND payload->>'verdict' = 'ungrounded'
                          AND created_at > NOW() - INTERVAL '24 hours'
                    """),
                    {"agent_id": agent_id},
                ).scalar()

                if recent_ungrounded >= 3:
                    db.execute(
                        sa_text(
                            "UPDATE agents SET strategy_resynthesis_flagged = TRUE WHERE id = :id"
                        ),
                        {"id": agent_id},
                    )
                    db.commit()
                    log.warning(
                        "run_auditor.resynthesis_flagged",
                        job_id=job_id,
                        agent_id=agent_id,
                        recent_ungrounded=recent_ungrounded,
                    )

            log.info(
                "run_auditor.complete",
                job_id=job_id,
                agent_id=agent_id,
                verdict=verdict.verdict,
                confidence=verdict.confidence,
                citation_spans=len(verdict.citation_spans),
            )

        except Exception as exc:
            log.error(
                "run_auditor.failed",
                job_id=job_id,
                agent_id=agent_id,
                error=str(exc),
            )
            # Validators never disrupt the user-facing turn: log and return {} on exhaustion.
            if self.request.retries >= self.max_retries:
                return {}
            else:
                countdown = 2 ** self.request.retries
                raise self.retry(exc=exc, countdown=countdown)

    return {}


# ---------------------------------------------------------------------------
# VAL-05: run_strategist — "Is the response on-brand and coherent?"
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=5,
    queue="runtime",
    name="run_strategist",
)
def run_strategist(
    self,
    agent_id: str,
    job_id: str,
    response_text: str,
    question: str,
) -> dict:
    """Run the Strategist judge synchronously and emit a 'strategist.complete' event.

    Evaluates response coherence, on-brand alignment, and role alignment using
    the agent's soul fields (D-12/D-13).

    Idempotent: returns {"status": "already_complete"} if 'strategist.complete' already
    exists for this job_id.

    Validators never disrupt the user-facing turn: on retry exhaustion log.error + return {}.

    Args:
        agent_id:      UUID string of the agent.
        job_id:        UUID string of the runtime chat job.
        response_text: The agent's response to evaluate.
        question:      The user's original question.

    Returns:
        {"status": "already_complete"}  — idempotent path
        {}                              — all other paths (success or exhaustion)
    """
    with get_sync_db() as db:
        # ------------------------------------------------------------------
        # Idempotency guard
        # ------------------------------------------------------------------
        existing = db.execute(
            sa_text(
                "SELECT 1 FROM job_events"
                " WHERE job_id = :jid AND event_type = 'strategist.complete' LIMIT 1"
            ),
            {"jid": job_id},
        ).fetchone()
        if existing:
            log.info("run_strategist.idempotent_skip", job_id=job_id)
            return {"status": "already_complete"}

        # ------------------------------------------------------------------
        # Fetch agent from control DB — required for soul fields (D-13).
        # ------------------------------------------------------------------
        agent = db.get(Agent, agent_id)
        if agent is None:
            log.error(
                "run_strategist.agent_not_found",
                job_id=job_id,
                agent_id=agent_id,
            )
            return {}

        try:
            # --------------------------------------------------------------
            # Call Strategist judge with soul fields (D-13 — soul fields from
            # Agent row: soul_role, soul_voice, soul_do_list, soul_donot_list)
            # --------------------------------------------------------------
            verdict = call_strategist(
                question=question,
                response_text=response_text,
                soul_role=agent.soul_role or "",
                soul_voice=agent.soul_voice or "",
                soul_do_list=agent.soul_do_list or [],
                soul_donot_list=agent.soul_donot_list or [],
            )

            # --------------------------------------------------------------
            # Log to Langfuse
            # --------------------------------------------------------------
            _log_verdict(
                judge_name="strategist",
                agent_id=agent_id,
                job_id=job_id,
                input_payload={
                    "question_length": len(question),
                    "response_length": len(response_text),
                },
                verdict_dict=verdict.model_dump(),
            )

            # --------------------------------------------------------------
            # Emit strategist.complete with verdict payload + agent_id
            # --------------------------------------------------------------
            emit(
                job_id,
                "strategist.complete",
                {**verdict.model_dump(), "agent_id": agent_id},
                db,
                _redis,
            )

            log.info(
                "run_strategist.complete",
                job_id=job_id,
                agent_id=agent_id,
                verdict=verdict.verdict,
                confidence=verdict.confidence,
            )

        except Exception as exc:
            log.error(
                "run_strategist.failed",
                job_id=job_id,
                agent_id=agent_id,
                error=str(exc),
            )
            # Validators never disrupt the user-facing turn: log and return {} on exhaustion.
            if self.request.retries >= self.max_retries:
                return {}
            else:
                countdown = 2 ** self.request.retries
                raise self.retry(exc=exc, countdown=countdown)

    return {}
