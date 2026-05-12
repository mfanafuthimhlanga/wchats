"""
apply_migrations — Celery task: run Alembic upgrade head against a tenant DB.

This is the second task in the M1 Celery chain:
    chain(provision_neon.s(tenant_id, agent_id), apply_migrations.s())

The ``result`` argument received from provision_neon contains:
    {"agent_id": str, "project_id": str}

Connection string security (CLAUDE.md rule — non-negotiable):
    The connection string is NEVER in the task argument (result dict).
    This task fetches it from the control DB by agent_id and decrypts it at runtime.
    Fetching from DB means the string never appears in Redis, Flower UI,
    or the result backend.

DIRECT connection string (RESEARCH.md Pitfall 1):
    Alembic MUST use the direct (non-pooled) URI from agent.neon_direct_connection_string.
    PgBouncer in transaction mode does not support DDL advisory locks; migrations
    silently fail or hang when run through the pooler.

Idempotency:
    If agent.status == "ready", the task has already completed — return immediately.
    Alembic's upgrade head is naturally idempotent (no-op if revision is current),
    but the early return avoids redundant DB round-trips.

None guard on job query:
    If the job query returns None, it was marked complete on a prior attempt.
    Return immediately — do not try to emit events on a missing job.

Connection probe (RESEARCH.md Pitfall 3):
    Neon reports operations "finished" before the compute endpoint is query-ready.
    wait_for_neon_ready() probes the direct URI with SELECT 1 before Alembic runs.
    Probe exhaustion → retriable (self.retry with exponential backoff).

Failure modes (prd-M1.md §7.2):
    Connection failure → retry 3x exponential backoff (via wait_for_neon_ready → RuntimeError)
    Migration error   → fatal: sets agent.status="failed", job.status="failed", emits job.failed

Event emission order:
    migrations.running   ← emitted before Alembic runs
    migrations.complete  ← emitted after Alembic succeeds
    job.complete         ← emitted after agent.status="ready" is written

Threat mitigations:
    T-03-01/02: Connection string never appears in log calls, task args, or return values.
"""

import structlog
from datetime import datetime, timezone

import redis as redis_lib

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.models.job import Job
from app.services.events import emit
from app.services.migrations import get_current_alembic_revision, run_tenant_migrations
from app.services.neon import wait_for_neon_ready
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

# Module-level sync Redis client — shared across task invocations in the same
# worker process (RESEARCH.md §Open Questions (RESOLVED) Q3).
_redis = redis_lib.from_url(settings.REDIS_URL)


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=10,
    queue="pipeline",
)
def apply_migrations(self, result: dict) -> None:
    """Run Alembic upgrade head against the tenant DB for the provisioned agent.

    Args:
        result: The return value from provision_neon — {"agent_id": str, "project_id": str}.
                The connection string is NOT in this dict; it is fetched from the control DB.

    Returns:
        None
    """
    agent_id = result["agent_id"]

    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)

        # ------------------------------------------------------------------
        # Idempotency guard — if agent is already ready, skip entirely
        # (handles retry after a prior successful apply_migrations run)
        # ------------------------------------------------------------------
        if agent.status == "ready":
            log.info("apply_migrations.already_complete", agent_id=agent_id)
            return

        # ------------------------------------------------------------------
        # None guard — if no running job found, skip (already completed or cancelled)
        # ------------------------------------------------------------------
        job = (
            db.query(Job)
            .filter(Job.agent_id == agent.id, Job.status == "running")
            .first()
        )
        if not job:
            log.info("apply_migrations.no_running_job", agent_id=agent_id)
            return

        # ------------------------------------------------------------------
        # Fetch and decrypt DIRECT connection string from control DB
        # NEVER from the result dict — CLAUDE.md rule.
        # direct_conn_string is intentionally not logged (T-03-02).
        # ------------------------------------------------------------------
        direct_conn_string = fernet_decrypt(agent.neon_direct_connection_string)

        # ------------------------------------------------------------------
        # Emit migrations.running — before Alembic runs
        # ------------------------------------------------------------------
        emit(job.id, "migrations.running", {"agent_id": agent_id}, db, _redis)

        # ------------------------------------------------------------------
        # Connection probe — wait for Neon compute to be query-ready
        # (RESEARCH.md Pitfall 3: operations "finished" ≠ compute ready)
        # ------------------------------------------------------------------
        try:
            wait_for_neon_ready(direct_conn_string)
        except RuntimeError as exc:
            # Probe exhausted — retry the whole task with exponential backoff
            log.warning(
                "apply_migrations.probe_exhausted",
                agent_id=agent_id,
                attempt=self.request.retries,
            )
            raise self.retry(exc=exc, countdown=2**self.request.retries)

        # ------------------------------------------------------------------
        # Run Alembic migrations
        # Migration errors are FATAL — do not retry (prd-M1.md §7.2).
        # Log only the exception type and message; never log conn string.
        # ------------------------------------------------------------------
        try:
            run_tenant_migrations(direct_conn_string)
        except Exception as exc:
            log.error(
                "apply_migrations.migration_failed",
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            agent.status = "failed"
            job.status = "failed"
            job.error = str(exc)
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            emit(job.id, "job.failed", {"error": str(exc)}, db, _redis)
            return  # Fatal — do not raise; chain ends here

        # ------------------------------------------------------------------
        # Record schema version and mark agent ready
        # ------------------------------------------------------------------
        revision = get_current_alembic_revision(direct_conn_string)
        agent.schema_version = revision
        agent.status = "ready"
        job.status = "complete"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()

        log.info(
            "apply_migrations.complete",
            agent_id=agent_id,
            schema_version=revision,
        )

        # ------------------------------------------------------------------
        # Emit migrations.complete then job.complete
        # ------------------------------------------------------------------
        emit(
            job.id,
            "migrations.complete",
            {"schema_version": revision},
            db,
            _redis,
        )
        emit(
            job.id,
            "job.complete",
            {"agent_id": agent_id, "schema_version": revision},
            db,
            _redis,
        )
