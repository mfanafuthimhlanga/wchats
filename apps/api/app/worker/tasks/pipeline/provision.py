"""
provision_neon — Celery task: create and provision a Neon project for an agent.

This is the first task in the M1 Celery chain:
    chain(provision_neon.s(tenant_id, agent_id), apply_migrations.s())

Idempotency contract (RESEARCH.md Pitfall 2 — CRITICAL):
    1. Check agent.neon_project_id BEFORE calling the Neon API.
       If it is already set, return early — the project was created on a prior attempt.
    2. Write agent.neon_project_id to the DB and commit IMMEDIATELY after the Neon
       API returns the project ID — before polling, before encryption, before any other
       work. This is the safety window: if the worker is kill-9'd after this commit,
       the next attempt will hit the idempotency guard and not create a duplicate project.
    3. Encryption/storage of the connection strings can be re-run safely on retry
       (Fernet produces a new ciphertext each call, but the stored value is always
       correct once written).

Event emission order (CONTEXT.md §Event Emission Pattern):
    job.started         ← emitted FIRST, after idempotency guard passes
    neon.project.creating ← emitted before Neon API call
    neon.project.ready  ← emitted after connection strings stored

Return value:
    {"agent_id": str, "project_id": str}
    Connection strings are NEVER returned — they stay encrypted in the control DB.
    (CLAUDE.md rule: connection strings never in Celery task args or return values)

Failure modes (prd-M1.md §7.1):
    4xx  → fatal: sets agent.status="failed", job.status="failed", emits job.failed; no retry
    5xx / timeout → exponential backoff via self.retry(countdown=2**retries), max 3x

Threat mitigations:
    T-03-01: Return value contains only agent_id and project_id — no connection URI.
    T-03-02: structlog calls log only project_id; connection strings and Neon API key
             are never passed to any log call in this module.
    T-03-05: tenant_id and agent_id originate from FastAPI route which validates them
             against the control DB before dispatching the chain. Accepted for M1.
    T-03-06: Neon API exceptions are caught; only status_code and a sanitised message
             are logged — exc.__str__() might embed the API key in some SDK versions.
"""

import structlog
from datetime import datetime, timezone

import redis as redis_lib

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.security import fernet_encrypt
from app.models.agent import Agent
from app.models.job import Job
from app.services.events import emit
from app.services.neon import create_neon_project
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

# Module-level sync Redis client — shared across task invocations in the same
# worker process (RESEARCH.md §Open Questions (RESOLVED) Q3).
# Each Celery worker process creates exactly one client; no cross-process sharing.
_redis = redis_lib.from_url(settings.REDIS_URL)


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=5,
    queue="pipeline",
)
def provision_neon(self, tenant_id: str, agent_id: str) -> dict:
    """Provision a Neon project for the given agent.

    Args:
        tenant_id: UUID string of the owning tenant (from FastAPI route).
        agent_id:  UUID string of the agent to provision.

    Returns:
        {"agent_id": str, "project_id": str} — passed as the ``result`` argument
        to the next task in the chain (apply_migrations).
        Connection strings are NEVER included in the return value.
    """
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        if not agent:
            raise ValueError(f"Agent {agent_id} not found")

        # ------------------------------------------------------------------
        # Idempotency guard — skip if project was already provisioned
        # (handles retry after a previous successful Neon API call)
        # ------------------------------------------------------------------
        if agent.neon_project_id:
            log.info(
                "provision_neon.already_provisioned",
                agent_id=agent_id,
                project_id=agent.neon_project_id,
            )
            return {"agent_id": agent_id, "project_id": agent.neon_project_id}

        # ------------------------------------------------------------------
        # Mark agent as provisioning; find the pending job for this agent
        # ------------------------------------------------------------------
        agent.status = "provisioning"

        job = (
            db.query(Job)
            .filter(Job.agent_id == agent.id, Job.status != "complete")
            .first()
        )
        if not job:
            # No active job found but agent is not yet provisioned — contradictory
            # state (idempotency guard passed, meaning neon_project_id is still None).
            # apply_migrations cannot proceed without a connection string; raise to
            # abort the chain rather than passing project_id: None downstream.
            log.error(
                "provision_neon.no_job_found_for_unprovisioned_agent",
                agent_id=agent_id,
            )
            raise ValueError(
                f"No active job found for unprovisioned agent {agent_id}"
            )

        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        db.commit()

        # ------------------------------------------------------------------
        # Emit job.started — FIRST event in the chain
        # (must come before neon.project.creating per CONTEXT.md)
        # ------------------------------------------------------------------
        emit(job.id, "job.started", {"agent_id": agent_id}, db, _redis)

        # ------------------------------------------------------------------
        # Emit neon.project.creating — before the Neon API call
        # ------------------------------------------------------------------
        emit(job.id, "neon.project.creating", {"agent_id": agent_id}, db, _redis)

        # ------------------------------------------------------------------
        # Call Neon API — create the project
        # ------------------------------------------------------------------
        try:
            result = create_neon_project(agent_id)
        except Exception as exc:
            # Determine if this is a fatal 4xx or a retriable 5xx/timeout.
            # T-03-06: log only status_code, not the raw exception string.
            status_code = getattr(exc, "status_code", None) or getattr(
                getattr(exc, "response", None), "status_code", None
            )
            log.warning(
                "provision_neon.neon_api_error",
                agent_id=agent_id,
                status_code=status_code,
            )

            if status_code and 400 <= status_code < 500:
                # Fatal: 4xx — no retry; mark both agent and job as failed
                agent.status = "failed"
                job.status = "failed"
                job.error = f"Neon API {status_code} error"
                job.finished_at = datetime.now(timezone.utc)
                db.commit()
                emit(
                    job.id,
                    "job.failed",
                    {"error": f"Neon API {status_code} error"},
                    db,
                    _redis,
                )
                # Raise a non-retriable exception to abort the chain and
                # prevent apply_migrations from receiving None and crashing.
                raise Exception(f"Neon API fatal {status_code} — chain aborted")

            # Retriable: 5xx or timeout — exponential backoff
            raise self.retry(exc=exc, countdown=2**self.request.retries)

        project_id = result["id"]

        # ------------------------------------------------------------------
        # CRITICAL: Write project_id IMMEDIATELY after API returns
        # (before polling, before encryption — this is the idempotency save point)
        # If the worker is killed here, the next retry hits the guard above.
        # ------------------------------------------------------------------
        agent.neon_project_id = project_id
        db.commit()

        log.info(
            "provision_neon.project_id_saved",
            agent_id=agent_id,
            project_id=project_id,
        )

        # ------------------------------------------------------------------
        # Encrypt and store both connection strings as BYTEA
        # pooled URI  → neon_connection_string (application traffic)
        # direct URI  → neon_direct_connection_string (Alembic migrations only)
        # ------------------------------------------------------------------
        pooled_encrypted = fernet_encrypt(result["pooled_uri"])
        direct_encrypted = fernet_encrypt(result["direct_uri"])
        agent.neon_connection_string = pooled_encrypted
        agent.neon_direct_connection_string = direct_encrypted
        db.commit()

        # ------------------------------------------------------------------
        # Emit neon.project.ready — after connection strings are stored
        # ------------------------------------------------------------------
        emit(
            job.id,
            "neon.project.ready",
            {"project_id": project_id},
            db,
            _redis,
        )

        log.info(
            "provision_neon.complete",
            agent_id=agent_id,
            project_id=project_id,
        )

        # T-03-01: Return only agent_id and project_id — no connection string
        return {"agent_id": agent_id, "project_id": project_id}
