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
    One handler covers everything after the job row is found, because the job row is
    the thing that has to end. A Neon 4xx is fatal: fail_the_job writes the row and
    emits job.failed, then ProvisioningAborted leaves, because no retry turns a 400
    into a 201. Everything else reaches retry_or_fail_the_job with countdown
    2**retries, which retries while attempts remain and, on the last one, writes
    agent.status="failed", job.status="failed", emits job.failed and re-raises.
    Celery re-raises the exception it was handed rather than MaxRetriesExceededError,
    so exhaustion is a state check, never an exception handler (#63).

    That handler used to cover the two Neon calls and nothing else. On staging,
    2026-09-04 15:25 UTC, the project was created, its id was committed, and the
    fernet_encrypt on the next line raised ValueError on a key that had lost its
    base64 padding. The pipeline log carried the traceback, the job row stayed
    'running' with 'neon.project.creating' as its last event, and the MCP caller
    polling get_job waited for ever. Everything between the job row and the return
    is inside the handler now, and a retry from there re-enters at state B.

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

from datetime import datetime, timezone

import redis as redis_lib
import requests as req_lib
import structlog
from celery.exceptions import Retry

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.log_bounds import log_failure
from app.core.redis_tls import redis_ssl_kwargs
from app.core.security import fernet_encrypt
from app.models.agent import Agent
from app.models.job import Job
from app.models.tenant import Tenant
from app.services.events import emit
from app.services.job_failure import fail_the_job, failure_reason, retry_or_fail_the_job
from app.services.neon import _NEON_API_BASE, NeonHTTPError, _neon_headers, _project_slug, create_neon_project
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

# Module-level sync Redis client. Strip the query string, then redis_ssl_kwargs decides TLS.
_url_clean = settings.REDIS_URL.split("?")[0] if "?" in settings.REDIS_URL else settings.REDIS_URL
_ssl_opts: dict = redis_ssl_kwargs(_url_clean)
_redis = redis_lib.from_url(_url_clean, **_ssl_opts)


class ProvisioningAborted(Exception):
    """A Neon 4xx. The job is already failed and no attempt can change the answer.

    It is raised from inside the failure handler rather than from the body, so
    nothing catches it on the way out. A bare Exception in its place read exactly
    like the failures that SHOULD be retried, and once the handler covered the
    whole body it would have bought three more attempts against an API that
    answered 400.
    """


def _connection_uris(project_id: str) -> dict:
    """Both connection URIs for a Neon project that already exists.

    State B's half of provisioning. create_neon_project fetches these itself when
    it makes the project; this re-fetches them for a project whose id an earlier
    attempt committed before it reached the line that stores them.

    Raises:
        NeonHTTPError: carrying the status code, which is what decides fatal
            (4xx) from retriable (everything else) in the caller.
    """
    uris = {}
    for field, pooled in (("pooled_uri", "true"), ("direct_uri", "false")):
        response = req_lib.get(
            f"{_NEON_API_BASE}/projects/{project_id}/connection_uri",
            headers=_neon_headers(),
            params={"database_name": "neondb", "role_name": "neondb_owner", "pooled": pooled},
            timeout=15,
        )
        if not response.ok:
            raise NeonHTTPError(response.status_code, response.text[:200])
        uris[field] = response.json()["uri"]
    return {"id": project_id, **uris}


def _create_project(agent: Agent, agent_id: str, db) -> dict:
    """A fresh Neon project, named for the agent and the account that owns it.

    account_tag is the first 8 characters of clerk_user_id after its "user_"
    prefix, or the tenant name when the tenant carries no Clerk id.
    """
    tenant = db.get(Tenant, agent.tenant_id)
    if tenant and tenant.clerk_user_id:
        account_tag = tenant.clerk_user_id.removeprefix("user_")[:8]
    else:
        account_tag = tenant.name if tenant else ""
    return create_neon_project(agent_id, project_name=_project_slug(agent.name, account_tag))


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
            _result = {"agent_id": agent_id, "project_id": agent.neon_project_id}
            # Run apply_migrations synchronously — see end-of-function comment.
            from app.worker.tasks.pipeline.migrations import apply_migrations as _am
            _am.apply(args=[_result])
            return _result

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

        # Read the id once, here, while the session is known good. commit()
        # expires every attribute, so `job.id` inside a failure handler is a
        # SELECT on a session whose last statement may have raised, and it
        # answers PendingRollbackError instead of the id the handler needs to
        # end the job with.
        job_id = job.id

        try:
            emit(job_id, "job.started", {"agent_id": agent_id}, db, _redis)
            emit(job_id, "neon.project.creating", {"agent_id": agent_id}, db, _redis)

            # ----------------------------------------------------------------
            # State B: project_id saved but URIs not stored → skip the create
            # call and re-fetch the URIs for the project that already exists.
            # State C: call Neon to create the project, then commit its id
            # IMMEDIATELY, before URI storage and before encryption. That commit
            # is the idempotency save point: a worker killed after it re-enters
            # at state B rather than creating a second project.
            # ----------------------------------------------------------------
            if agent.neon_project_id:
                project_id = agent.neon_project_id
                log.info("provision_neon.resuming_uri_fetch", agent_id=agent_id, project_id=project_id)
                result = _connection_uris(project_id)
            else:
                result = _create_project(agent, agent_id, db)
                project_id = result["id"]
                agent.neon_project_id = project_id
                db.commit()
                log.info("provision_neon.project_id_saved", agent_id=agent_id, project_id=project_id)

            # ----------------------------------------------------------------
            # Encrypt and store both connection strings as BYTEA
            # pooled URI  → neon_connection_string (application traffic)
            # direct URI  → neon_direct_connection_string (Alembic migrations only)
            # ----------------------------------------------------------------
            agent.neon_connection_string = fernet_encrypt(result["pooled_uri"])
            agent.neon_direct_connection_string = fernet_encrypt(result["direct_uri"])
            db.commit()

            # neon.project.ready, after the connection strings are stored
            emit(job_id, "neon.project.ready", {"project_id": project_id}, db, _redis)
            log.info("provision_neon.complete", agent_id=agent_id, project_id=project_id)

            _result = {"agent_id": agent_id, "project_id": project_id}

            # Run apply_migrations synchronously (Task.apply is an eager call, no broker
            # round-trip).
            #
            # Root cause of why async dispatch fails on Windows:
            #   provision_neon blocks the consumer loop for ~21s while creating the Neon
            #   project. During that time the Upstash Redis TCP connection goes idle;
            #   Upstash drops it server-side without a FIN, leaving the consumer socket
            #   half-open. When the consumer tries to receive apply_migrations after
            #   provision_neon returns, select.select() on the stale socket returns []
            #   instead of (readable, …, …), raising ValueError: not enough values to
            #   unpack (expected 3, got 0). worker_pool="solo" fixed task execution but
            #   not the consumer receive path.
            #
            # Task.apply() runs the function directly in the current process:
            #   - No broker publish, no consumer receive, no select() call.
            #   - apply_migrations.retry() in eager mode retries immediately (no countdown).
            #   - apply_migrations handles its own failure state; we ignore EagerResult.
            #   - T-03-01: _result contains only agent_id and project_id, no connection string.
            from app.worker.tasks.pipeline.migrations import apply_migrations as _am
            _am.apply(args=[_result])

            return _result
        except NeonHTTPError as exc:
            # T-03-06: exc.message is already truncated to 200 chars in neon.py.
            log.warning(
                "provision_neon.neon_api_error",
                agent_id=agent_id,
                project_id=agent.neon_project_id,
                status_code=exc.status_code,
                detail=exc.message,
            )
            if 400 <= exc.status_code < 500:
                fail_the_job(job_id, failure_reason(exc), db, _redis, agent)
                raise ProvisioningAborted(f"Neon API {exc.status_code} aborted the chain") from exc
            retry_or_fail_the_job(self, exc, job_id, db, _redis, 2**self.request.retries, agent)
        except Retry:
            # Celery's own control flow for a scheduled attempt. Catching it below
            # would turn a retry into a failure.
            raise
        except Exception as exc:
            log_failure(log, "provision_neon.failed", exc, level="error", agent_id=agent_id)
            retry_or_fail_the_job(self, exc, job_id, db, _redis, 2**self.request.retries, agent)
