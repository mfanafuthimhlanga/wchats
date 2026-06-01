"""Deployment checklist routes for W Chats M8.

Manages the pre-deployment checklist lifecycle for agents.
All routes require X-API-Key or Bearer auth via get_current_tenant.
IDOR prevented by verifying agent.tenant_id == tenant.id on every route.

Routes:
    POST /agents/{agent_id}/checklist-runs                            — trigger checklist (202)
    GET  /agents/{agent_id}/checklist-runs                           — list runs (most-recent-first, limit 10)
    GET  /agents/{agent_id}/checklist-runs/{run_id}                  — single run detail with report
    POST /agents/{agent_id}/checklist-runs/{run_id}/acknowledge      — acknowledge warnings
    POST /agents/{agent_id}/approve-deployment                       — approve and flip is_deployed
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_tenant
from app.core.database import get_async_db
from app.models.agent import Agent
from app.models.checklist_run import ChecklistRun
from app.models.tenant import Tenant
from app.schemas.deployment import (
    AcknowledgeRequest,
    AcknowledgeResponse,
    ApproveDeploymentRequest,
    ApproveDeploymentResponse,
    ChecklistRunListResponse,
    ChecklistRunResponse,
    ChecklistRunTriggerResponse,
)
from app.services.deployment_service import _make_iframe_snippet
from app.worker.tasks.runtime.deployment import run_deployment_checklist

router = APIRouter(tags=["deployment"])
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal helper — convert ORM object to response dict
# ---------------------------------------------------------------------------


def _run_to_dict(run: ChecklistRun) -> dict:
    """Convert a ChecklistRun ORM object to a dict matching ChecklistRunResponse."""
    return {
        "id": str(run.id),
        "agent_id": str(run.agent_id),
        "status": run.status,
        "recommendation": run.recommendation,
        "report": run.report,
        "warnings": run.warnings if run.warnings is not None else [],
        "warning_acknowledgments": run.warning_acknowledgments if run.warning_acknowledgments is not None else {},
        "all_warnings_acknowledged": run.all_warnings_acknowledged,
        "approved_at": run.approved_at,
        "approved_by": run.approved_by,
        "created_at": run.created_at,
    }


# ---------------------------------------------------------------------------
# Route 1: POST /agents/{agent_id}/checklist-runs — trigger checklist (202)
# ---------------------------------------------------------------------------


@router.post("/agents/{agent_id}/checklist-runs", status_code=202)
async def trigger_checklist_run(
    agent_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Dispatch run_deployment_checklist for an agent and return 202 immediately.

    Security:
        Agent ownership verified (IDOR prevention).
        Agent must be in 'ready' state — 400 otherwise.

    Celery:
        Dispatches run_deployment_checklist.apply_async(kwargs={"agent_id": str(agent_id)},
        queue="runtime"). Only agent_id is passed — no connection string in task
        args (CTL-08 / CLAUDE.md non-negotiable).

    Returns HTTP 202 immediately. Poll GET /checklist-runs to detect completion.

    Response: {"checklist_run_id": str, "status": "queued"}
    """
    # 1. Fetch agent from control DB
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 2. IDOR check — agent must belong to the authenticated tenant
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 3. Guard: agent must be ready before running the checklist
    if agent.status != "ready":
        raise HTTPException(status_code=400, detail="Agent must be in ready state")

    # 4. Dispatch Celery task — only agent_id passed; no connection string in args (CTL-08)
    task = run_deployment_checklist.apply_async(
        kwargs={"agent_id": str(agent_id)},
        queue="runtime",
    )

    log.info(
        "checklist_trigger.dispatched",
        agent_id=str(agent_id),
        task_id=task.id,
        tenant_id=str(tenant.id),
    )

    return {"checklist_run_id": task.id, "status": "queued"}


# ---------------------------------------------------------------------------
# Route 2: GET /agents/{agent_id}/checklist-runs — list runs
# ---------------------------------------------------------------------------


@router.get("/agents/{agent_id}/checklist-runs")
async def list_checklist_runs(
    agent_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Return up to 10 checklist runs for an agent, most-recent-first.

    Security:
        Fetches agent from control DB and checks agent.tenant_id == tenant.id (IDOR prevention).

    Response shape:
        {"runs": [{id, agent_id, status, recommendation, report, warnings, ...}]}
    """
    # 1. Fetch agent from control DB
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 2. IDOR check — agent must belong to the authenticated tenant
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 3. Query control DB via SQLAlchemy async ORM (checklist_runs is in control DB)
    result = await db.execute(
        select(ChecklistRun)
        .where(ChecklistRun.agent_id == agent_id)
        .order_by(ChecklistRun.created_at.desc())
        .limit(10)
    )
    runs = result.scalars().all()

    log.info(
        "list_checklist_runs.ok",
        agent_id=str(agent_id),
        tenant_id=str(tenant.id),
        run_count=len(runs),
    )
    return {"runs": [_run_to_dict(r) for r in runs]}


# ---------------------------------------------------------------------------
# Route 3: GET /agents/{agent_id}/checklist-runs/{run_id} — single run detail
# ---------------------------------------------------------------------------


@router.get("/agents/{agent_id}/checklist-runs/{run_id}")
async def get_checklist_run(
    agent_id: UUID,
    run_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Return a single checklist run with full report JSONB.

    Security:
        Same IDOR prevention as list — agent ownership verified.
        Also verifies run.agent_id == agent_id (T-08-04-04).

    Response shape:
        {"run": {id, agent_id, status, recommendation, report, warnings, ...}}
    """
    # 1. Fetch agent from control DB
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 2. IDOR check — agent must belong to the authenticated tenant
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 3. Fetch checklist run — also verifies run belongs to this agent
    run = await db.get(ChecklistRun, run_id)
    if run is None or run.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="Checklist run not found")

    log.info(
        "get_checklist_run.ok",
        agent_id=str(agent_id),
        run_id=str(run_id),
        tenant_id=str(tenant.id),
    )
    return {"run": _run_to_dict(run)}


# ---------------------------------------------------------------------------
# Route 4: POST /agents/{agent_id}/checklist-runs/{run_id}/acknowledge
# ---------------------------------------------------------------------------


@router.post("/agents/{agent_id}/checklist-runs/{run_id}/acknowledge")
async def acknowledge_warnings(
    agent_id: UUID,
    run_id: UUID,
    body: AcknowledgeRequest,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Acknowledge one or more warnings on a completed checklist run.

    Security:
        IDOR check on agent. Run ownership verified against agent_id.
        T-08-04-03: Only warning_ids present in run.warnings are accepted —
        arbitrary injection of unknown warning_ids is rejected (422).

    Updates warning_acknowledgments JSONB and recalculates all_warnings_acknowledged.

    Response shape:
        {"all_warnings_acknowledged": bool}
    """
    # 1. Fetch agent from control DB
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 2. IDOR check — agent must belong to the authenticated tenant
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 3. Fetch run — verify ownership
    run = await db.get(ChecklistRun, run_id)
    if run is None or run.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="Checklist run not found")

    # 4. Guard: can only acknowledge warnings on a completed run
    if run.status != "complete":
        raise HTTPException(status_code=422, detail="Checklist not yet complete")

    # 5. T-08-04-03: validate submitted warning_ids against actual warnings in the run
    #    Only IDs present in run.warnings are accepted — prevents arbitrary JSONB injection.
    valid_warning_ids = {w["warning_id"] for w in (run.warnings or []) if isinstance(w, dict)}
    invalid_ids = [wid for wid in body.warning_ids if wid not in valid_warning_ids]
    if invalid_ids:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown warning_ids: {invalid_ids}",
        )

    # 6. Update warning_acknowledgments JSONB
    acks = dict(run.warning_acknowledgments or {})
    now_iso = datetime.now(timezone.utc).isoformat()
    for wid in body.warning_ids:
        acks[wid] = now_iso

    # 7. Recalculate all_warnings_acknowledged
    all_ids = {w["warning_id"] for w in (run.warnings or []) if isinstance(w, dict)}
    run.all_warnings_acknowledged = all_ids.issubset(set(acks.keys()))
    run.warning_acknowledgments = acks
    await db.commit()

    log.info(
        "acknowledge_warnings.ok",
        agent_id=str(agent_id),
        run_id=str(run_id),
        all_acknowledged=run.all_warnings_acknowledged,
    )
    return {"all_warnings_acknowledged": run.all_warnings_acknowledged}


# ---------------------------------------------------------------------------
# Route 5: POST /agents/{agent_id}/approve-deployment
# ---------------------------------------------------------------------------


@router.post("/agents/{agent_id}/approve-deployment")
async def approve_deployment(
    agent_id: UUID,
    body: ApproveDeploymentRequest,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Approve deployment after checklist passes all gates.

    Security:
        IDOR check on agent. Checklist run ownership verified.
        T-08-04-02: Server-side validation before any mutation —
        blocked or incomplete runs are rejected (422).

    Validation sequence (CONTEXT.md §Approval Validation):
        1. run.status != "complete"     → 422 "Checklist is still running"
        2. recommendation == "block"    → 422 "Cannot approve a blocked deployment..."
        3. ship_with_warnings + not all acknowledged → 422 "Acknowledge all warnings..."

    On success:
        - agent.is_deployed = True
        - run.approved_at = now()
        - run.approved_by = str(tenant.id)

    Response shape:
        {"deployed": True, "agent_id": str, "iframe_snippet": str}
    """
    # 1. Fetch agent from control DB
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 2. IDOR check — agent must belong to the authenticated tenant
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 3. Fetch checklist run by body.checklist_run_id — verify it belongs to this agent
    run = await db.get(ChecklistRun, body.checklist_run_id)
    if run is None or run.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="Checklist run not found")

    # 4. Approval validation sequence (CONTEXT.md §Approval Validation — T-08-04-02)
    if run.status != "complete":
        raise HTTPException(status_code=422, detail="Checklist is still running")
    if run.recommendation == "block":
        raise HTTPException(
            status_code=422,
            detail="Cannot approve a blocked deployment — resolve critical issues first",
        )
    if run.recommendation == "ship_with_warnings" and not run.all_warnings_acknowledged:
        raise HTTPException(
            status_code=422,
            detail="Acknowledge all warnings before approving",
        )

    # 5. Flip is_deployed and stamp approval metadata
    agent.is_deployed = True
    run.approved_at = datetime.now(timezone.utc)
    run.approved_by = str(tenant.id)
    await db.commit()

    log.info(
        "deployment.approved",
        agent_id=str(agent_id),
        run_id=str(run.id),
        tenant_id=str(tenant.id),
    )

    return {
        "deployed": True,
        "agent_id": str(agent_id),
        "iframe_snippet": _make_iframe_snippet(str(agent_id)),
    }
