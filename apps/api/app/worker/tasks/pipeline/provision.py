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
    MaxRetriesExceededError → caught explicitly; sets agent.status="failed", job.status="failed"

Threat mitigations:
    T-03-01: Return value contains only agent_id and project_id — no connection URI.
    T-03-02: structlog calls log only project_id; connection strings and Neon API key
             are never passed to any log call in this module.
    T-03-05: tenant_id and agent_id originate from FastAPI route which validates them
             against the control DB before dispatching the chain. Accepted for M1.
    T-03-06: Neon API exceptions are caught; only status_code and a sanitised message
             are logged — NeonHTTPError.message is already truncated to 200 chars in
             neon.py before it reaches this handler.

SDK note (2026-05-15):
    The neon_api SDK raises NeonAPIError(r.text) without preserving the HTTP response
    object, so exc.status_code / exc.response are always None. create_neon_project now
    uses requests directly and raises NeonHTTPError which carries .status_code.
    The SDK is still imported for the State-B URI re-fetch path (using an existing
    project_id) — that path's exception is also handled via the NeonHTTPError approach
    by wrapping with requests directly.
"""

import ssl
import structlog
from datetime import datetime, timezone

import redis as redis_lib
import requests as req_lib

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.security import fernet_encrypt
from app.models.agent import Agent
from app.models.job import Job
from app.services.events import emit
from app.services.neon import NeonHTTPError, create_neon_project, _NEON_API_BASE, _neon_headers
from app.worker.celery_app import celery_app
from celery.exceptions import MaxRetriesExceededError

log = structlog.get_logger(__name__)

# Module-level sync Redis client — strip query params and pass ssl_cert_reqs as
# a Python constant; redis-py does not parse ssl_cert_reqs=CERT_NONE from URLs.
_url_clean = settings.REDIS_URL.split("?")[0] if "?" in settings.REDIS_URL else settings.REDIS_URL
_ssl_opts: dict = {"ssl_cert_reqs": ssl.CERT_NONE} if _url_clean.startswith("rediss://") else {}
_redis = redis_lib.from_url(_url_clean, **_ssl_opts)


def _mark_failed(agent: Agent, job: Job, db, reason: str) -> None:
    """Set agent and job to failed and emit job.failed event.

    Extracted to a helper so the same cleanup runs from both the 4xx fatal
    path and the MaxRetriesExceededError path.
    """
    agent.status = "failed"
    job.status = "failed"
    job.error = reason
    job.finished_at = datetime.now(timezone.utc)
    db.commit()
    emit(job.id, "job.failed", {"error": reason}, db, _redis)


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
        # jump directly to URI fetch.
        # State C: call Neon API to create the project.
        # ------------------------------------------------------------------
        if agent.neon_project_id:
            # Resuming from a prior attempt that saved project_id but failed
            # before storing URIs. Re-fetch URIs using the existing project_id.
            project_id = agent.neon_project_id
            log.info("provision_neon.resuming_uri_fetch", agent_id=agent_id, project_id=project_id)
            try:
                r_pooled = req_lib.get(
                    f"{_NEON_API_BASE}/projects/{project_id}/connection_uri",
                    headers=_neon_headers(),
                    params={"database_name": "neondb", "role_name": "neondb_owner", "pooled": "true"},
                    timeout=15,
                )
                if not r_pooled.ok:
                    raise NeonHTTPError(r_pooled.status_code, r_pooled.text[:200])

                r_direct = req_lib.get(
                    f"{_NEON_API_BASE}/projects/{project_id}/connection_uri",
                    headers=_neon_headers(),
                    params={"database_name": "neondb", "role_name": "neondb_owner", "pooled": "false"},
                    timeout=15,
                )
                if not r_direct.ok:
                    raise NeonHTTPError(r_direct.status_code, r_direct.text[:200])

                result = {
                    "id": project_id,
                    "pooled_uri": r_pooled.json()["uri"],
                    "direct_uri": r_direct.json()["uri"],
                }
            except NeonHTTPError as exc:
                log.warning(
                    "provision_neon.uri_fetch_error",
                    agent_id=agent_id,
                    project_id=project_id,
                    status_code=exc.status_code,
                )
                if 400 <= exc.status_code < 500:
                    _mark_failed(agent, job, db, f"Neon API {exc.status_code} on URI fetch")
                    raise Exception(f"Neon API fatal {exc.status_code} on URI fetch — chain aborted") from exc
                try:
                    raise self.retry(exc=exc, countdown=2 ** self.request.retries)
                except MaxRetriesExceededError:
                    _mark_failed(agent, job, db, f"Neon URI fetch failed after {self.max_retries} retries")
                    raise
            except Exception as exc:
                log.warning("provision_neon.uri_fetch_unexpected_error", agent_id=agent_id, project_id=project_id)
                try:
                    raise self.retry(exc=exc, countdown=2 ** self.request.retries)
                except MaxRetriesExceededError:
                    _mark_failed(agent, job, db, f"Neon URI fetch failed after {self.max_retries} retries (unexpected error)")
                    raise
        else:
            # ------------------------------------------------------------------
            # Fresh start: Call Neon API to create the project + fetch URIs
            # T-03-06: log only status_code, not the raw exception string.
            # ------------------------------------------------------------------
            try:
                result = create_neon_project(agent_id)
            except NeonHTTPError as exc:
                log.warning(
                    "provision_neon.neon_api_error",
                    agent_id=agent_id,
                    status_code=exc.status_code,
                    # T-03-06: exc.message is already truncated to 200 chars in neon.py
                    detail=exc.message,
                )
                if 400 <= exc.status_code < 500:
                    _mark_failed(agent, job, db, f"Neon API {exc.status_code} error")
                    raise Exception(f"Neon API fatal {exc.status_code} — chain aborted") from exc
                try:
                    raise self.retry(exc=exc, countdown=2 ** self.request.retries)
                except MaxRetriesExceededError:
                    _mark_failed(agent, job, db, f"Neon project creation failed after {self.max_retries} retries")
                    raise
            except Exception as exc:
                # Network error, timeout, etc. — not a Neon HTTP error.
                log.warning("provision_neon.unexpected_error", agent_id=agent_id)
                try:
                    raise self.retry(exc=exc, countdown=2 ** self.request.retries)
                except MaxRetriesExceededError:
                    _mark_failed(agent, job, db, f"Neon project creation failed after {self.max_retries} retries (network error)")
                    raise

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
