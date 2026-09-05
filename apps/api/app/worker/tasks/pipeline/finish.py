"""finish_ingestion, the one task that ends an ingestion run (issue #168).

WHY A TASK OF ITS OWN
    `embed_and_migrate` used to write `job.status = 'complete'` and emit
    `job.complete`, and it was the last hop when it was written. The chain grew:
    `synthesize_retrieval_strategy` runs after it. So the run's terminal event was
    emitted with a whole task still to go, and an SSE subscriber that closes on
    `job.complete` (which is what `app.services.sse.TERMINAL_EVENTS` tells it to
    do) stopped reading before the strategy step reported anything, its failure
    included.

    The rule that fixes it is ownership, not a moved line. ONE task owns the
    terminal event of a run and every earlier hop emits only its own step events.
    Making the LAST hop the owner puts the same defect back the next time the
    chain grows, because the identity of the last hop is exactly what growth
    changes. A hop appended for the purpose survives that: it stays last by
    construction, and `documents.py` appends it once.

WHY IT NEVER RUNS ON THE FAILURE PATH
    A Celery chain does not run a hop whose predecessor raised, and every hop
    fails through `retry_or_fail_the_job`, which writes the job row and emits
    `job.failed` before the exception leaves. So a failed run's terminal event is
    already on the stream and this task is never reached. It still reads the row
    before emitting, because "never reached" is a claim about the chain and the
    row is the record.

IDEMPOTENCY, AND WHY IT IS THE EVENT ROW AND NOT THE JOB ROW
    `acks_late=True` means a redelivery re-runs this after the broker lost the
    ack, so the emit has to be conditional or a stream carries two terminal
    events. The condition is the presence of a `job.complete` row in job_events,
    not `jobs.status == 'complete'`, because the row write commits first: a crash
    between the two would leave the status saying complete and the stream saying
    nothing, and a redelivery reading the status would skip the emit for good.
"""

from datetime import datetime, timezone

import redis as redis_lib
import structlog
from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.redis_tls import redis_ssl_kwargs
from app.domain.ingestion_job import IngestionJob
from app.models.job import Job
from app.models.job_event import JobEvent
from app.services.events import emit
from app.worker.celery_app import celery_app
from app.worker.tasks.pipeline.chain_edge import job_in_job_out

log = structlog.get_logger(__name__)

#: The event this task owns. Spelled once, read once, emitted once.
JOB_COMPLETE = "job.complete"

# Module-level sync Redis client. Strip the query string, then redis_ssl_kwargs decides TLS.
_url_clean = settings.REDIS_URL.split("?")[0] if "?" in settings.REDIS_URL else settings.REDIS_URL
_ssl_opts: dict = redis_ssl_kwargs(_url_clean)
_redis = redis_lib.from_url(_url_clean, **_ssl_opts)


def _job_complete_is_already_on_the_stream(db, job_id: str) -> bool:
    """True when a `job.complete` row for this job already exists."""
    return (
        db.execute(
            select(JobEvent.id)
            .where(JobEvent.job_id == job_id, JobEvent.event_type == JOB_COMPLETE)
            .limit(1)
        ).first()
        is not None
    )


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=5,
    queue="pipeline",
)
@job_in_job_out
def finish_ingestion(self, job: IngestionJob) -> IngestionJob:
    """Write the terminal job row state and emit `job.complete`, once, last.

    Args:
        job: the IngestionJob synthesize_retrieval_strategy forwarded.

    Returns:
        The same job. Nothing runs after this, so the return exists for the chain
        contract and for a caller that reads the chain's result.
    """
    job_id = job.job_id
    with get_sync_db() as db:
        job_row = db.get(Job, job_id)
        if job_row is None:
            log.error("finish_ingestion.job_row_missing", job_id=job_id)
            return job

        if job_row.status == "failed":
            # A hop failed and already ended the run. Reaching here means the
            # chain ran on past a failure, which is worth a line of its own.
            log.warning("finish_ingestion.already_failed", job_id=job_id)
            return job

        already_emitted = _job_complete_is_already_on_the_stream(db, job_id)

        if job_row.status != "complete":
            job_row.status = "complete"
            job_row.finished_at = datetime.now(timezone.utc)
            db.commit()

        if already_emitted:
            log.info("finish_ingestion.redelivered", job_id=job_id)
            return job

        emit(job_id, JOB_COMPLETE, {"job_id": job_id}, db, _redis)
        log.info("finish_ingestion.complete", job_id=job_id, agent_id=job.agent_id)

    return job
