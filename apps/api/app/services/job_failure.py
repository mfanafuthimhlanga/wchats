"""
One rule for every pipeline task: a task that stops leaves the job row and the
event stream saying so (issue #63).

Before this module each task module spelled its own ending, and most of them
spelled nothing.  ``chunk_documents`` rolled back and logged, ``generate_metadata``
wrote a job.failed only on the enriched-nothing path, ``synthesize_retrieval_strategy``
and ``reembed_corpus`` re-raised bare, and ``provision_neon`` guarded its cleanup
with ``except MaxRetriesExceededError`` — an exception Celery does not raise when
``retry()`` was handed one:

    # celery/app/task.py, Task.retry
    if max_retries is not None and retries > max_retries:
        if exc:
            raise_with_context(exc)
        raise self.MaxRetriesExceededError(...)

So the shape a pipeline task must handle is "the last attempt raised", never
"MaxRetriesExceededError arrived".  ``retry_or_fail_the_job`` decides which of the
two attempts this is and raises either way, so a call site is one statement with
no branch of its own.

``job.failed`` is what ``app/services/sse.py`` treats as terminal, alongside
``job.complete``: without it the admin ingest page holds the last progress event
open until the client gives up.

emit is reached through the module rather than imported as a symbol, so a test
that patches ``app.services.events.emit`` sees the terminal event whichever task
called this.
"""

from datetime import datetime, timezone
from typing import NoReturn
from uuid import UUID

from redis import Redis as SyncRedis
from sqlalchemy.orm import Session

from app.models.job import Job
from app.services import events


def failure_reason(exc: BaseException) -> str:
    """The error type and its message, the one string the row and the event share.

    The type leads because it is the half that survives an empty message: a
    psycopg2.OperationalError raised by a dropped socket has ``str(exc) == ""``,
    and ``{"error": str(exc)}`` on that exception told the widget nothing at all.
    """
    message = str(exc)
    return "%s: %s" % (type(exc).__name__, message) if message else type(exc).__name__


def retries_are_spent(task) -> bool:
    """True when this attempt is the last one, so ``task.retry`` would re-raise.

    Same comparison Celery makes internally (``retries + 1 > max_retries``),
    asked one step earlier so the caller can write the job row before the
    exception leaves.
    """
    return task.request.retries >= task.max_retries


def fail_the_job(
    job_id: UUID | str,
    reason: str,
    db: Session,
    redis: SyncRedis,
    agent=None,
) -> None:
    """Mark the job row failed and emit the terminal job.failed event.

    Args:
        job_id: the control-DB jobs row this task is running for.
        reason: what stopped it, as ``failure_reason`` renders it.
        db:     control-DB sync Session, owned by the caller.
        redis:  sync Redis client, owned by the caller.
        agent:  the Agent row, when the failure also ends the agent's build.
                provision_neon and apply_migrations pass one; the four ingestion
                hops do not, because a failed ingest leaves a ready agent ready.
    """
    job_row = db.get(Job, job_id)
    if job_row is not None:
        job_row.status = "failed"
        job_row.error = reason
        job_row.finished_at = datetime.now(timezone.utc)
    if agent is not None:
        agent.status = "failed"
    db.commit()
    events.emit(job_id, "job.failed", {"error": reason}, db, redis)


def retry_or_fail_the_job(
    task,
    exc: BaseException,
    job_id: UUID | str,
    db: Session,
    redis: SyncRedis,
    countdown: int,
    agent=None,
) -> NoReturn:
    """Retry this attempt, or fail the job and re-raise once no retry is left.

    Never returns. The retry path raises Celery's ``Retry``; the exhausted path
    re-raises the original exception, so the worker records FAILURE and the chain
    stops rather than forwarding a success value.
    """
    if retries_are_spent(task):
        fail_the_job(job_id, failure_reason(exc), db, redis, agent)
        raise exc
    raise task.retry(exc=exc, countdown=countdown)
