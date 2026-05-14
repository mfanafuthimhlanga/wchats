"""
provision_neon — Celery task: create and provision a Neon project for an agent.

This is the first task in the M1 Celery chain:
    chain(provision_neon.s(tenant_id, agent_id), apply_migrations.s())

Idempotency contract (RESEARCH.md Pitfall 2 — CRITICAL):
    1. Check agent.neon_project_id AND agent.neon_connection_string BEFORE calling Neon.
       A) Both set → fully done, return early.
       B) project_id set but no URIs → project created but URI fetch failed on prior
          attempt; skip project creation, retry URI fetch only.
       C) Neither set → fresh start.
    2. Write agent.neon_project_id to the DB IMMEDIATELY after the Neon
       API returns the project ID — before URI fetch, before encryption.
       This is the safety window: if the worker is kill-9'd after this commit,
       the next attempt hits the idempotency guard and skips project creation.
    3. Encryption/storage of the connection strings can be re-run safely on retry
       (Fernet produces a new ciphertext each call, but the stored value is always
       correct once written).

Event emission order (CONTEXT.md §Event Emission Pattern):
    job.started              ← emitted FIRST, after idempotency guard passes
    neon.project.creating    ← emitted before Neon API call
    neon.project.ready       ← emitted after connection strings stored

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
        # Idempotency guard — three states:
        #   A) neon_project_id + neon_connection_string both set → fully done
        #   B) neon_project_id set but no URIs → project created but URI fetch
        #      failed; skip project creation, retry URI fetch + encrypt + store
        #   C) neither set → fresh start
        # ------------------------------------------------------------------
        if agent.neon_project_id and agent.neon_connection_string:
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

        emit(job.id, "job.started", {"agent_id": agent_id}, db, _redis)
        emit(job.id, "neon.project.creating", {"agent_id": agent_id}, db, _redis)

        # ------------------------------------------------------------------
        # State B: project_id saved but URIs not stored → skip API call,
        # jump directly to URI fetch (create_neon_project handles both).
        # State C: call Neon API to create the project.
        # ------------------------------------------------------------------
        if agent.neon_project_id:
            # Resuming from a prior attempt that saved project_id but failed
            # before storing URIs. Re-fetch URIs using the existing project_id.
            project_id = agent.neon_project_id
            log.info("provision_neon.resuming_uri_fetch", agent_id=agent_id, project_id=project_id)
            try:
                from neon_api import NeonAPI
                client = NeonAPI(api_key=settings.NEON_API_KEY)
                pooled_response = client.connection_uri(
                    project_id=project_id,
                    database_name="neondb",
                    role_name="neondb_owner",
                    pooled=True,
                )
                direct_response = client.connection_uri(
                    project_id=project_id,
                    database_name="neondb",
                    role_name="neondb_owner",
                    pooled=False,
                )
                result = {
                    "id": project_id,
                    "pooled_uri": pooled_response.uri,
                    "direct_uri": direct_response.uri,
                }
            except Exception as exc:
                log.warning("provision_neon.uri_fetch_error", agent_id=agent_id, project_id=project_id)
                raise self.retry(exc=exc, countdown=2**self.request.retries)
        else:
            # ------------------------------------------------------------------
            # Fresh start: Call Neon API to create the project + fetch URIs
            # T-03-06: log only status_code, not the raw exception string.
            # ------------------------------------------------------------------
            try:
                result = create_neon_project(agent_id)
            except Exception as exc:
                status_code = getattr(exc, "status_code", None) or getattr(
                    getattr(exc, "response", None), "status_code", None
                )
                log.warning(
                    "provision_neon.neon_api_error",
                    agent_id=agent_id,
                    status_code=status_code,
                )
                if status_code and 400 <= status_code < 500:
                    agent.status = "failed"
                    job.status = "failed"
                    job.error = f"Neon API {status_code} error"
                    job.finished_at = datetime.now(timezone.utc)
                    db.commit()
                    emit(job.id, "job.failed", {"error": f"Neon API {status_code} error"}, db, _redis)
                    raise Exception(f"Neon API fatal {status_code} — chain aborted")
                raise self.retry(exc=exc, countdown=2**self.request.retries)

            project_id = result["id"]

            # ------------------------------------------------------------------
            # CRITICAL: Write project_id IMMEDIATELY after API returns
            # (before URI storage — this is the idempotency save point)
            # If the worker is killed here, the next retry hits state B above.
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
