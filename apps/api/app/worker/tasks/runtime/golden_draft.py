"""draft_golden_scenarios: draft golden pairs for the owner to label (#203).

The route creates a Job of kind `golden_draft` and dispatches this task on the
runtime queue with ids only. The task reads the corpus index, picks chunks so
every document is covered before any repeats, fetches those chunks' content,
drafts one pair per chunk, keeps the ones the chunk vouches for, and hands them
back as job events:

    golden_draft.started    {agent_id, n}
    golden_draft.pair       one per kept draft, the DraftPair as its payload
    golden_draft.complete   {kept, dropped, documents}
    golden_draft.failed     {error_type}

`get_job` returns the last 100 events, so a caller reads the drafts from the job.
No scenario row is written; a keep goes through the golden registration route
like any hand-written pair. What is written: the job row, the events, and one
tenant ledger row per model call.

Idempotency, in two parts. A `golden_draft.complete` event already on the job
means a redelivery returns at once. A redelivery of a job that was mid-run (the
worker was stopped between two pair events) reads the pair events already on the
job and drafts only the chunks that have none, so the drafts already billed are
neither re-billed nor duplicated. There is no Celery retry: a failure fails the
job on its first attempt, because the owner asking again is cheaper than every
draft being billed twice.
"""

from __future__ import annotations

from datetime import datetime, timezone

import redis as redis_lib
import structlog
from sqlalchemy import text as sa_text

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.log_bounds import log_failure
from app.core.model_client import LedgerContext, ledger_recorder
from app.core.redis_tls import redis_ssl_kwargs
from app.core.security import fernet_decrypt, require_ciphertext
from app.models.agent import Agent
from app.models.job import Job
from app.services.events import emit
from app.services.golden_draft_service import (
    draft_golden_pairs,
    fetch_chunk_content,
    fetch_chunk_index,
    pick_chunks_by_document,
)
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

_url_clean = settings.REDIS_URL.split("?")[0] if "?" in settings.REDIS_URL else settings.REDIS_URL
_redis = redis_lib.from_url(_url_clean, **redis_ssl_kwargs(_url_clean))

EVENT_STARTED = "golden_draft.started"
EVENT_PAIR = "golden_draft.pair"
EVENT_COMPLETE = "golden_draft.complete"
EVENT_FAILED = "golden_draft.failed"

_COMPLETE_SQL = sa_text(
    "SELECT 1 FROM job_events WHERE job_id = :jid AND event_type = :et LIMIT 1"
)
_DRAFTED_CHUNKS_SQL = sa_text(
    "SELECT payload->>'source_chunk_id' FROM job_events "
    "WHERE job_id = :jid AND event_type = :et"
)


def _already_complete(db, job_id: str) -> bool:
    return db.execute(_COMPLETE_SQL, {"jid": job_id, "et": EVENT_COMPLETE}).fetchone() is not None


def _chunks_already_drafted(db, job_id: str) -> set[str]:
    """The chunks whose pair events a previous delivery already emitted."""
    rows = db.execute(_DRAFTED_CHUNKS_SQL, {"jid": job_id, "et": EVENT_PAIR}).fetchall()
    return {row[0] for row in rows if row[0]}


def _finish(db, job: Job, status: str) -> None:
    job.status = status
    job.finished_at = datetime.now(timezone.utc)
    db.commit()


def _fail(job_id: str, agent_id: str, exc: Exception) -> None:
    """Mark the job failed on a fresh session, so a session the failure poisoned
    cannot take the terminal write down with it and leave the job running."""
    try:
        with get_sync_db() as db:
            job = db.get(Job, job_id)
            emit(job_id, EVENT_FAILED, {"error_type": type(exc).__name__}, db, _redis)
            if job is not None:
                _finish(db, job, "failed")
    except Exception as terminal_exc:  # noqa: BLE001 — the record of the failure, best effort
        log_failure(log, "golden_draft.fail_write_failed", terminal_exc, job_id=job_id, agent_id=agent_id)


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=0,
    queue="runtime",
    name="app.worker.tasks.runtime.golden_draft.draft_golden_scenarios",
)
def draft_golden_scenarios(self, job_id: str, agent_id: str, n: int) -> dict:
    """Draft `n` golden pairs for the agent and emit them on the job.

    Returns {"kept", "dropped", "documents"} for the worker log, never the drafts.
    """
    with get_sync_db() as db:
        if _already_complete(db, job_id):
            log.info("golden_draft.idempotent_skip", job_id=job_id)
            return {}
        job = db.get(Job, job_id)
        agent = db.get(Agent, agent_id)
        if job is None or agent is None:
            log.error("golden_draft.job_or_agent_not_found", job_id=job_id, agent_id=agent_id)
            return {}
        conn_str = fernet_decrypt(
            require_ciphertext(agent.neon_connection_string, "agents.neon_connection_string")
        )
        try:
            drafted = _chunks_already_drafted(db, job_id)
            if not drafted:
                job.status = "running"
                job.started_at = datetime.now(timezone.utc)
                db.commit()
                emit(job_id, EVENT_STARTED, {"agent_id": agent_id, "n": n}, db, _redis)

            picked = pick_chunks_by_document(fetch_chunk_index(conn_str), n)
            to_draft = fetch_chunk_content(conn_str, [c for c in picked if c["chunk_id"] not in drafted])
            ledger = LedgerContext(
                tenant_id=str(agent.tenant_id), agent_id=agent_id, job_id=job_id,
                recorder=ledger_recorder(conn_str),
            )
            # A lead-in is asked for only when the corpus has something to be
            # ambiguous BETWEEN. One document means one subject (#227).
            kept, dropped = draft_golden_pairs(
                to_draft, ledger, len({c["document_id"] for c in picked}) > 1
            )
            for draft in kept:
                emit(job_id, EVENT_PAIR, draft, db, _redis)
            summary = {
                "kept": len(kept) + len(drafted),
                "dropped": dropped,
                "documents": len({c["document_id"] for c in picked}),
            }
            emit(job_id, EVENT_COMPLETE, summary, db, _redis)
            _finish(db, job, "complete")
            log.info("golden_draft.complete", job_id=job_id, agent_id=agent_id, resumed=len(drafted), **summary)
            return summary
        except Exception as exc:  # noqa: BLE001 — the task's own terminal handler
            log_failure(log, "golden_draft.failed", exc, level="error", job_id=job_id, agent_id=agent_id)
            failure: Exception = exc
    _fail(job_id, agent_id, failure)
    return {}
