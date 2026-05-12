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


def create_neon_project(agent_id: str) -> dict:
    """Create a Neon project for the given agent and wait until it is ready.

    Steps:
        1. Create the project via Neon API.
        2. Write the project_id to the log for verification (no URI logged).
        3. Poll project operations until all are in a terminal status or 90s pass.
        4. Fetch both pooled (application traffic) and direct (Alembic) URIs.

    Args:
        agent_id: The agent's UUID string, used for project naming.

    Returns:
        dict with keys:
            "id"          — Neon project ID (str)
            "pooled_uri"  — PgBouncer-pooled connection URI (str)
            "direct_uri"  — Direct (non-pooled) connection URI (str)

    Raises:
        TimeoutError: If operations do not finish within 90 seconds.
        Any NeonAPI exception on non-2xx responses (caller handles 4xx/5xx split).
    """
    client = NeonAPI(api_key=settings.NEON_API_KEY)

    # Step 1 — Create the project
    response = client.project_create(
        project={
            "name": f"vrd-{agent_id}",
            "region_id": settings.NEON_REGION,
            "pg_version": 17,
        }
    )
    project_id = response.project.id

    # Step 2 — Log project_id only (T-03-02: never log URIs or raw response)
    log.debug(
        "neon.project_created",
        project_id=project_id,
        agent_id=agent_id,
    )

    # Step 3 — Poll operations until all are terminal or deadline passes
    deadline = time.time() + 90
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
            f"Neon project {project_id} did not become ready in 90s"
        )

    log.info("neon.operations_finished", project_id=project_id)

    # Step 4 — Fetch both URIs
    # pooled=True  — PgBouncer endpoint, used for application-layer queries
    # pooled=False — Direct endpoint, used by Alembic (DDL requires session mode)
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

    # T-03-02: return URIs to caller but do not log them here
    return {
        "id": project_id,
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
        try:
            engine = create_engine(conn_string, poolclass=pool.NullPool)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            engine.dispose()
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
