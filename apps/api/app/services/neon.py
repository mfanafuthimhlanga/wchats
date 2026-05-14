"""
Neon API service helpers for Veridian.

Provides:
    create_neon_project  — create a Neon project, poll until operations finish,
                           return both pooled and direct connection URIs.
    wait_for_neon_ready  — probe a Neon compute endpoint until it accepts queries.

Threat context:
    T-03-02: Log only project_id from Neon responses; never log connection URIs
             or the raw API response object which may embed credentials.
    T-03-04: 90s operation-polling deadline is bounded; worker thread is blocked
             for at most 90s (accepted: each pipeline task runs on its own thread).

Neon operation status lifecycle:
    scheduling → running → finished (terminal, success)
                         → failed / cancelled / skipped (terminal, non-success)

The polling loop exits when no pending (non-terminal) operations remain.
A TimeoutError is raised if the deadline passes before all operations settle.
"""

import logging
import time

import structlog
from neon_api import NeonAPI
from sqlalchemy import create_engine, pool, text

from app.core.config import settings

log = structlog.get_logger(__name__)

# Operation statuses that are considered "done" — no further work expected
_TERMINAL_STATUSES = frozenset({"finished", "skipped", "cancelled", "failed"})


def create_neon_project_request(agent_id: str) -> str:
    """Call the Neon API to create a project and return its project_id immediately.

    Does NOT poll for readiness — the caller must save this project_id to the
    DB before polling so the idempotency guard can prevent duplicate projects
    if the worker is killed during the polling phase.

    Args:
        agent_id: The agent's UUID string, used for project naming.

    Returns:
        project_id (str) — Neon project ID, ready to be persisted.

    Raises:
        Any NeonAPI exception on non-2xx responses (caller handles 4xx/5xx split).
    """
    client = NeonAPI(api_key=settings.NEON_API_KEY)
    response = client.project_create(
        project={
            "name": f"vrd-{agent_id}",
            "region_id": settings.NEON_REGION,
            "pg_version": 17,
        }
    )
    project_id = response.project.id
    log.debug("neon.project_created", project_id=project_id, agent_id=agent_id)
    return project_id


def poll_neon_project_ready(project_id: str, timeout: int = 300) -> dict:
    """Poll Neon operations until all settle, then fetch and return connection URIs.

    Args:
        project_id: The Neon project ID returned by create_neon_project_request.
        timeout:    Max seconds to wait for all operations to reach a terminal
                    status. Default 300s — Neon cold starts can exceed 90s on
                    free tier when provisioning a fresh compute endpoint.

    Returns:
        dict with keys:
            "pooled_uri"  — PgBouncer-pooled connection URI (str)
            "direct_uri"  — Direct (non-pooled) connection URI (str)

    Raises:
        TimeoutError: If operations do not finish within `timeout` seconds.
    """
    client = NeonAPI(api_key=settings.NEON_API_KEY)

    deadline = time.time() + timeout
    while time.time() < deadline:
        ops = client.operations(project_id)
        pending = [
            op
            for op in ops.operations
            if op.status not in _TERMINAL_STATUSES
        ]
        if not pending:
            break
        time.sleep(2)
    else:
        raise TimeoutError(
            f"Neon project {project_id} did not become ready in {timeout}s"
        )

    log.info("neon.operations_finished", project_id=project_id)

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

    return {
        "pooled_uri": pooled_response.uri,
        "direct_uri": direct_response.uri,
    }


def wait_for_neon_ready(conn_string: str, max_attempts: int = 10) -> None:
    """Probe a Neon compute endpoint until it accepts a simple SELECT query.

    Neon marks operations as "finished" before the compute endpoint is
    fully warm and accepting connections (RESEARCH.md Pitfall 3). This
    probe loop adds a safety buffer so that apply_migrations does not fail
    with "connection refused" immediately after provision_neon completes.

    Args:
        conn_string:  Direct (non-pooled) connection URI for the tenant DB.
                      Pass the decrypted direct URI — do NOT use the pooled URI.
        max_attempts: Number of probe attempts before giving up.
                      Default 10 (2^0 + 2^1 + ... + 2^9 ≈ 17 min absolute max,
                      but practically ready in 1–3 attempts ~10s total).

    Raises:
        RuntimeError: After max_attempts consecutive failures.
    """
    for attempt in range(max_attempts):
        engine = create_engine(conn_string, poolclass=pool.NullPool)
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            log.info("neon.compute_ready", attempt=attempt)
            return
        except Exception:  # noqa: BLE001 — broad catch is intentional for probe loop
            if attempt == max_attempts - 1:
                raise RuntimeError(
                    f"Neon project not query-ready after {max_attempts} probe attempts"
                )
            backoff = 2**attempt
            log.debug("neon.compute_probe_waiting", attempt=attempt, backoff_s=backoff)
            time.sleep(backoff)
        finally:
            # Always dispose — NullPool means each engine holds its own connection;
            # without this, failed attempts leak file descriptors and TCP connections.
            engine.dispose()
