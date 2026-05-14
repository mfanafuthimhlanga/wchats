"""
Neon API service helpers for Veridian.

Provides:
    create_neon_project  — create a Neon project and return project_id + connection URIs.
                           Does NOT poll operations (unreliable on free tier).
    wait_for_neon_ready  — probe a Neon compute endpoint until it accepts queries.

Threat context:
    T-03-02: Log only project_id from Neon responses; never log connection URIs
             or the raw API response object which may embed credentials.

Design note: Neon operations polling was removed. The operations list reflects
Neon's internal work queue and can stay non-terminal indefinitely on free tier.
Connection URIs are available immediately after project creation; actual compute
readiness is verified via wait_for_neon_ready() (probe loop in apply_migrations).
"""

import time

import structlog
from neon_api import NeonAPI
from sqlalchemy import create_engine, pool, text

from app.core.config import settings

log = structlog.get_logger(__name__)


def create_neon_project(agent_id: str) -> dict:
    """Create a Neon project for the given agent and return project_id + URIs.

    Fetches connection URIs immediately after project creation — does NOT wait
    for Neon operations to settle. Actual compute readiness is probed by
    wait_for_neon_ready() in apply_migrations.

    Args:
        agent_id: The agent's UUID string, used for project naming.

    Returns:
        dict with keys:
            "id"          — Neon project ID (str)
            "pooled_uri"  — PgBouncer-pooled connection URI (str)
            "direct_uri"  — Direct (non-pooled) connection URI (str)

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

    # Fetch connection URIs immediately — available as soon as the project exists.
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


def wait_for_neon_ready(conn_string: str, max_attempts: int = 15) -> None:
    """Probe a Neon compute endpoint until it accepts a simple SELECT query.

    Neon compute endpoints warm up asynchronously after project creation.
    This probe loop retries with exponential backoff so apply_migrations
    does not fail with "connection refused" on a cold compute.

    Args:
        conn_string:  Direct (non-pooled) connection URI for the tenant DB.
                      Pass the decrypted direct URI — do NOT use the pooled URI.
        max_attempts: Number of probe attempts before giving up.
                      Default 15 (2^0 + ... + 2^14 ≈ 9h max, typically 1–4
                      attempts ~30s total on a cold Neon free-tier project).

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
            backoff = min(2**attempt, 60)  # cap individual sleep at 60s
            log.debug("neon.compute_probe_waiting", attempt=attempt, backoff_s=backoff)
            time.sleep(backoff)
        finally:
            # Always dispose — NullPool means each engine holds its own connection;
            # without this, failed attempts leak file descriptors and TCP connections.
            engine.dispose()
