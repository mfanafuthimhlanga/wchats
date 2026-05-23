"""
Neon API service helpers for Veridian.

Provides:
    NeonHTTPError        — raised by create_neon_project when the Neon API
                           returns a non-2xx response; carries .status_code.
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

SDK limitation (bug fix — 2026-05-15):
    The neon_api SDK raises NeonAPIError(r.text) without passing response=r,
    so exc.response is always None and exc.status_code does not exist.
    To preserve the HTTP status code for correct 4xx/5xx triage in the Celery
    task, create_neon_project calls the Neon API via requests directly and
    raises NeonHTTPError which carries .status_code explicitly.
"""

import json
import time

import requests
import structlog
from sqlalchemy import create_engine, pool, text

from app.core.config import settings

log = structlog.get_logger(__name__)


class NeonHTTPError(Exception):
    """Raised when the Neon API returns a non-2xx response.

    Attributes:
        status_code (int): The HTTP status code from the Neon API response.
        message (str): Sanitised error message (body text, credentials scrubbed).
    """

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"Neon API {status_code}: {message}")


_NEON_API_BASE = "https://console.neon.tech/api/v2"


def _neon_headers() -> dict:
    """Return Neon API request headers. Key is never logged."""
    return {
        "Authorization": f"Bearer {settings.NEON_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


import re as _re


def _project_slug(agent_name: str, account_tag: str) -> str:
    """Build a human-readable Neon project name.

    Format: {agent-slug}-{account-tag}-{8-digit-random}
    Example: portfolio-assistant-bant1234-82741059

    Rules:
    - Lowercase, spaces/special chars → hyphens, collapse runs, strip edges.
    - agent_name truncated to 30 chars; account_tag to 8 chars.
    - 8-digit random suffix ensures uniqueness across retries.
    - Total stays under Neon's 64-char project name limit.
    """
    import random

    def _slug(s: str, maxlen: int) -> str:
        s = s.lower()
        s = _re.sub(r"[^a-z0-9]+", "-", s)
        s = s.strip("-")
        return s[:maxlen]

    agent_part = _slug(agent_name, 30)
    account_part = _slug(account_tag, 8)
    suffix = str(random.randint(10_000_000, 99_999_999))
    parts = [p for p in [agent_part, account_part, suffix] if p]
    return "-".join(parts)


def create_neon_project(agent_id: str, project_name: str | None = None) -> dict:
    """Create a Neon project for the given agent and return project_id + URIs.

    Fetches connection URIs immediately after project creation — does NOT wait
    for Neon operations to settle. Actual compute readiness is probed by
    wait_for_neon_ready() in apply_migrations.

    Uses requests directly (not the neon_api SDK) to preserve the HTTP status
    code on error responses — the SDK drops it when raising NeonAPIError.

    Args:
        agent_id: The agent's UUID string (fallback name if project_name omitted).
        project_name: Human-readable Neon project name. Pass the result of
                      _project_slug(agent.name, account_tag) from the task.

    Returns:
        dict with keys:
            "id"          — Neon project ID (str)
            "pooled_uri"  — PgBouncer-pooled connection URI (str)
            "direct_uri"  — Direct (non-pooled) connection URI (str)

    Raises:
        NeonHTTPError: On any non-2xx response from the Neon API. Caller uses
                       .status_code to distinguish fatal 4xx from retryable 5xx.
    """
    name = project_name or f"vrd-{agent_id}"
    # --- Create project ---------------------------------------------------
    r = requests.post(
        f"{_NEON_API_BASE}/projects",
        headers=_neon_headers(),
        json={
            "project": {
                "name": name,
                "region_id": settings.NEON_REGION,
                "pg_version": 17,
            }
        },
        timeout=30,
    )
    if not r.ok:
        # T-03-06: never embed full response body in logs — it may contain
        # credentials or quota detail that should not appear in Celery logs.
        # Truncate to 200 chars for structured log field.
        body_snippet = r.text[:200] if r.text else ""
        raise NeonHTTPError(r.status_code, body_snippet)

    data = r.json()
    project_id = data["project"]["id"]
    log.debug("neon.project_created", project_id=project_id, agent_id=agent_id)

    # --- Fetch pooled connection URI --------------------------------------
    r_pooled = requests.get(
        f"{_NEON_API_BASE}/projects/{project_id}/connection_uri",
        headers=_neon_headers(),
        params={"database_name": "neondb", "role_name": "neondb_owner", "pooled": "true"},
        timeout=15,
    )
    if not r_pooled.ok:
        raise NeonHTTPError(r_pooled.status_code, r_pooled.text[:200])

    # --- Fetch direct (non-pooled) connection URI -------------------------
    r_direct = requests.get(
        f"{_NEON_API_BASE}/projects/{project_id}/connection_uri",
        headers=_neon_headers(),
        params={"database_name": "neondb", "role_name": "neondb_owner", "pooled": "false"},
        timeout=15,
    )
    if not r_direct.ok:
        raise NeonHTTPError(r_direct.status_code, r_direct.text[:200])

    # T-03-02: return URIs to caller but do not log them here
    return {
        "id": project_id,
        "pooled_uri": r_pooled.json()["uri"],
        "direct_uri": r_direct.json()["uri"],
    }


def create_branch(project_id: str, branch_name: str) -> tuple[str, str]:
    """Create a Neon branch and return (branch_id, connection_string).

    Branch name should be 'eval-{run_id}'. conn_str is NEVER stored — pass as
    a local variable to the eval task only (D-18).

    Args:
        project_id:  The Neon project ID (str) for the tenant's project.
        branch_name: A human-readable branch name, e.g. 'eval-{run_id}'.

    Returns:
        tuple[str, str]: (branch_id, conn_str)
            branch_id — Neon branch ID, used later by delete_branch().
            conn_str  — Direct (non-pooled) connection URI for the branch.
                        NEVER log or store this value (T-03-02, D-18).

    Raises:
        NeonHTTPError: On any non-2xx response from the Neon API.
    """
    # Step 1 — POST to create branch with a read_write endpoint
    r = requests.post(
        f"{_NEON_API_BASE}/projects/{project_id}/branches",
        headers=_neon_headers(),
        json={"branch": {"name": branch_name}, "endpoints": [{"type": "read_write"}]},
        timeout=30,
    )
    if not r.ok:
        raise NeonHTTPError(r.status_code, r.text[:200])
    data = r.json()
    branch_id = data["branch"]["id"]
    # T-03-02: log project_id and branch_id only — do NOT log conn_str
    log.debug("neon.branch_created", project_id=project_id, branch_id=branch_id)

    # Step 2 — GET connection URI for the branch (direct/non-pooled for psycopg2)
    r_uri = requests.get(
        f"{_NEON_API_BASE}/projects/{project_id}/connection_uri",
        headers=_neon_headers(),
        params={
            "database_name": "neondb",
            "role_name": "neondb_owner",
            "pooled": "false",
            "branch_id": branch_id,
        },
        timeout=15,
    )
    if not r_uri.ok:
        raise NeonHTTPError(r_uri.status_code, r_uri.text[:200])
    conn_str = r_uri.json()["uri"]

    # T-03-02: DO NOT log conn_str
    return (branch_id, conn_str)


def delete_branch(project_id: str, branch_id: str) -> None:
    """Delete a Neon branch.

    Called in the eval task finally block (D-10) to ensure cleanup even on
    exception. Raises NeonHTTPError on failure — the caller is responsible for
    catching and logging.

    Args:
        project_id: The Neon project ID for the tenant's project.
        branch_id:  The Neon branch ID returned by create_branch().

    Raises:
        NeonHTTPError: On any non-2xx response from the Neon API.
    """
    r = requests.delete(
        f"{_NEON_API_BASE}/projects/{project_id}/branches/{branch_id}",
        headers=_neon_headers(),
        timeout=30,
    )
    if not r.ok:
        raise NeonHTTPError(r.status_code, r.text[:200])
    log.debug("neon.branch_deleted", project_id=project_id, branch_id=branch_id)


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
