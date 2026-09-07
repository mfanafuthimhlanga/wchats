"""draft_golden_scenarios: draft golden pairs for the owner to label, writing no rows (#203).

The route creates a Job of kind `golden_draft` and dispatches this task on the
runtime queue with ids only. The task reads the corpus, picks chunks so every
document is covered before any repeats, drafts one pair per chunk, keeps the ones
whose citation is a passage of their chunk, and hands them back as job events:

    golden_draft.started    {agent_id, n}
    golden_draft.pair       one per kept draft, the DraftPair as its payload
    golden_draft.complete   {kept, dropped, documents}

`get_job` returns the last 100 events, so a caller reads the drafts from the job.
Nothing is written to eval_scenarios; a keep goes through the golden registration
route like any hand-written pair.

Idempotent by the same guard `retrieve_and_rank` uses: a `golden_draft.complete`
event already on the job means a redelivery returns at once, so a retry never
re-bills the drafts or duplicates the pair events.
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
    fetch_chunks_by_document,
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


def _already_complete(db, job_id: str) -> bool:
    row = db.execute(
        sa_text(
            "SELECT 1 FROM job_events WHERE job_id = :jid AND event_type = :et LIMIT 1"
        ),
        {"jid": job_id, "et": EVENT_COMPLETE},
    ).fetchone()
    return row is not None


def _finish(db, job: Job, status: str) -> None:
    job.status = status
    job.finished_at = datetime.now(timezone.utc)
    db.commit()


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=1,
    default_retry_delay=30,
    queue="runtime",
    name="app.worker.tasks.runtime.golden_draft.draft_golden_scenarios",
)
def draft_golden_scenarios(self, job_id: str, agent_id: str, n: int) -> dict:
    """Draft `n` golden pairs for the agent and emit them on the job.

    Args:
        job_id:   the control-DB job the route created.
        agent_id: the agent whose corpus is drafted from.
        n:        how many chunks to draft from; the kept count is at most this.

    Returns:
        {"kept": int, "dropped": int} for the worker log, never the drafts.
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
            job.status = "running"
            job.started_at = datetime.now(timezone.utc)
            db.commit()
            emit(job_id, EVENT_STARTED, {"agent_id": agent_id, "n": n}, db, _redis)

            chunks = pick_chunks_by_document(fetch_chunks_by_document(conn_str), n)
            ledger = LedgerContext(
                tenant_id=str(agent.tenant_id), agent_id=agent_id, job_id=job_id,
                recorder=ledger_recorder(conn_str),
            )
            kept, dropped = draft_golden_pairs(chunks, ledger)

            for draft in kept:
                emit(job_id, EVENT_PAIR, draft, db, _redis)
            summary = {
                "kept": len(kept),
                "dropped": dropped,
                "documents": len({c["document_id"] for c in chunks}),
            }
            emit(job_id, EVENT_COMPLETE, summary, db, _redis)
            _finish(db, job, "complete")
            log.info("golden_draft.complete", job_id=job_id, agent_id=agent_id, **summary)
            return summary
        except Exception as exc:
            log_failure(log, "golden_draft.failed", exc, level="error", job_id=job_id, agent_id=agent_id)
            if self.request.retries >= self.max_retries:
                emit(job_id, EVENT_FAILED, {"error_type": type(exc).__name__}, db, _redis)
                _finish(db, job, "failed")
                return {}
            raise self.retry(exc=exc)
