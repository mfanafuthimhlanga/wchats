"""Red team routes for Veridian M7.

Queries tenant DB (red_team_runs) for run history and per-run findings.
All routes require X-API-Key auth via get_current_tenant.
IDOR prevented by verifying agent.tenant_id == tenant.id.

Routes:
    GET  /agents/{agent_id}/red-team-runs             — list runs (up to 20)
    GET  /agents/{agent_id}/red-team-runs/{run_id}    — single run detail with findings
    POST /agents/{agent_id}/red-team-runs             — dispatch run_red_team manually (202)
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import psycopg2
import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_tenant
from app.core.database import get_async_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.schemas.red_team import RedTeamRunListResponse, RedTeamRunResponse, RedTeamTriggerResponse
from app.worker.tasks.runtime.red_team import run_red_team

router = APIRouter(tags=["red_team"])
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal helper — wraps blocking psycopg2 calls for asyncio.to_thread
# ---------------------------------------------------------------------------


def _query_tenant_db_sync(conn_str: str, sql: str, params: dict) -> list[tuple]:
    """Execute a SELECT against the tenant DB synchronously.

    Wraps psycopg2 in a try/finally to ensure the connection is always closed.
    Called inside asyncio.to_thread() to avoid blocking the FastAPI event loop.

    Args:
        conn_str: Decrypted tenant DB connection string (never logged — T-02-01).
        sql: SQL query with %(name)s placeholders.
        params: Dict of query parameters.

    Returns:
        List of row tuples from fetchall().
    """
    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Route 1: GET /agents/{agent_id}/red-team-runs — list runs
# ---------------------------------------------------------------------------

_LIST_RED_TEAM_RUNS_SQL = """
    SELECT id, kind, status, started_at, finished_at, findings, max_severity, deployment_blocked
    FROM red_team_runs
    WHERE kind = %(kind)s
    ORDER BY started_at DESC
    LIMIT 20
"""


@router.get("/agents/{agent_id}/red-team-runs")
async def list_red_team_runs(
    agent_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Return up to 20 red team runs for an agent.

    Security:
        Fetches agent from control DB and checks agent.tenant_id == tenant.id (IDOR prevention).
        Returns 404 for unknown agents or agents belonging to a different tenant.

    Response shape:
        {"runs": [{id, kind, status, started_at, finished_at, findings, max_severity, deployment_blocked}]}
    """
    # 1. Fetch agent from control DB
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 2. IDOR check — agent must belong to the authenticated tenant
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 3. Guard: agent must have a tenant DB configured
    if not agent.neon_connection_string:
        raise HTTPException(status_code=404, detail="Agent database not provisioned")

    # 4. Decrypt connection string at runtime — never stored, never logged (T-02-01)
    conn_str = fernet_decrypt(agent.neon_connection_string)

    # 5. Query tenant DB in a thread pool to avoid blocking the event loop
    rows = await asyncio.to_thread(
        _query_tenant_db_sync, conn_str, _LIST_RED_TEAM_RUNS_SQL, {"kind": f"m7:{agent_id}"}
    )

    # 6. Build response
    runs = []
    for row in rows:
        (run_id, kind, status, started_at, finished_at, findings, max_severity, deployment_blocked) = row
        runs.append(
            {
                "id": str(run_id),
                "kind": kind or "",
                "status": status or "",
                "started_at": started_at,
                "finished_at": finished_at,
                "findings": findings if findings is not None else [],
                "max_severity": max_severity,
                "deployment_blocked": deployment_blocked if deployment_blocked is not None else False,
            }
        )

    log.info(
        "list_red_team_runs.ok",
        agent_id=str(agent_id),
        tenant_id=str(tenant.id),
        run_count=len(runs),
    )
    return {"runs": runs}


# ---------------------------------------------------------------------------
# Route 2: GET /agents/{agent_id}/red-team-runs/{run_id} — single run detail
# ---------------------------------------------------------------------------

_GET_RED_TEAM_RUN_SQL = """
    SELECT id, kind, status, started_at, finished_at, findings, max_severity, deployment_blocked
    FROM red_team_runs
    WHERE id = %(run_id)s
      AND kind = %(kind)s
"""


@router.get("/agents/{agent_id}/red-team-runs/{run_id}")
async def get_red_team_run(
    agent_id: UUID,
    run_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Return a single red team run with all findings.

    Security:
        Same IDOR prevention as list_red_team_runs — agent ownership verified.

    Response shape:
        {"run": {id, kind, status, started_at, finished_at, findings, max_severity, deployment_blocked}}
    """
    # 1. Fetch agent from control DB
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 2. IDOR check — agent must belong to the authenticated tenant
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 3. Guard: agent must have a tenant DB configured
    if not agent.neon_connection_string:
        raise HTTPException(status_code=404, detail="Agent database not provisioned")

    # 4. Decrypt connection string at runtime — never logged (T-02-01)
    conn_str = fernet_decrypt(agent.neon_connection_string)

    # 5. Query tenant DB in a thread pool
    rows = await asyncio.to_thread(
        _query_tenant_db_sync,
        conn_str,
        _GET_RED_TEAM_RUN_SQL,
        {"run_id": str(run_id), "kind": f"m7:{agent_id}"},
    )

    # 6. 404 if not found
    if not rows:
        raise HTTPException(status_code=404, detail="Red team run not found")

    # 7. Build and return single run dict
    (rid, kind, status, started_at, finished_at, findings, max_severity, deployment_blocked) = rows[0]
    run_dict = {
        "id": str(rid),
        "kind": kind or "",
        "status": status or "",
        "started_at": started_at,
        "finished_at": finished_at,
        "findings": findings if findings is not None else [],
        "max_severity": max_severity,
        "deployment_blocked": deployment_blocked if deployment_blocked is not None else False,
    }

    log.info(
        "get_red_team_run.ok",
        agent_id=str(agent_id),
        run_id=str(run_id),
        tenant_id=str(tenant.id),
    )
    return {"run": run_dict}


# ---------------------------------------------------------------------------
# Route 3: POST /agents/{agent_id}/red-team-runs — manual trigger (202)
# ---------------------------------------------------------------------------


@router.post("/agents/{agent_id}/red-team-runs", status_code=202)
async def trigger_red_team_run(
    agent_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Manually dispatch run_red_team for an agent and return 202 immediately.

    Security:
        Agent ownership verified (IDOR prevention).
        Agent must be in 'ready' state — 400 otherwise.

    Celery:
        Dispatches run_red_team.apply_async(kwargs={"agent_id": str(agent_id)},
        queue="runtime"). Only agent_id is passed — no connection string in task
        args (CTL-08 / CLAUDE.md non-negotiable).

    Returns HTTP 202 immediately. Poll GET /red-team-runs to detect completion.

    Response: {"job_id": str, "run_id": str, "message": str}
    """
    # 1. Fetch agent from control DB
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 2. IDOR check — 404 on ownership mismatch
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 3. Guard: agent must be ready to run red team
    if agent.status != "ready":
        raise HTTPException(
            status_code=400,
            detail="Agent must be in ready state to run red team",
        )

    # 4. Dispatch Celery task — only agent_id, never conn_str (CTL-08)
    task = run_red_team.apply_async(
        kwargs={"agent_id": str(agent_id)},
        queue="runtime",
    )

    log.info(
        "red_team_trigger.dispatched",
        agent_id=str(agent_id),
        task_id=task.id,
        tenant_id=str(tenant.id),
    )

    return {"job_id": task.id, "run_id": task.id, "message": "Red team run queued — poll GET /red-team-runs for results"}
