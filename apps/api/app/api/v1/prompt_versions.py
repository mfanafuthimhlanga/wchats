"""Prompt-version routes for W Chats Phase 21 (OPS-16).

Non-destructive, canary-able soul editing. prompt_versions is a CONTROL-DB
table (agents.soul* are control-DB columns) — no cross-DB join is required.

Routes:
    GET  /agents/{agent_id}/prompt-versions              — list versions, newest first
    GET  /agents/{agent_id}/prompt-versions/diff          — per-field diff of two versions
    POST /agents/{agent_id}/prompt-versions/canary        — set a canary % for a version
    POST /agents/{agent_id}/prompt-versions/rollback      — restore a prior version

Security:
    IDOR pattern copied from evals.py/traces.py: agent.tenant_id == tenant.id,
    404 (not 403) on mismatch — never leaks agent existence to a foreign tenant.
    prompt_version_service's IDOR guard additionally verifies each version_id
    belongs to the path agent_id before any read/write.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_tenant
from app.core.database import get_async_db
from app.models.agent import Agent
from app.models.prompt_version import PromptVersion
from app.models.tenant import Tenant
from app.services import prompt_version_service

router = APIRouter(tags=["prompt-versions"])
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class SetCanaryRequest(BaseModel):
    """POST /prompt-versions/canary request body.

    percent is bounded 0-100 by Pydantic — out-of-range values return 422
    before the route body ever runs (T-21-09 acceptance criteria).
    """

    version_id: UUID
    percent: int = Field(ge=0, le=100)


class RollbackRequest(BaseModel):
    """POST /prompt-versions/rollback request body."""

    version_id: UUID


# ---------------------------------------------------------------------------
# Shared IDOR guard (copied from evals.py/traces.py's inline pattern)
# ---------------------------------------------------------------------------


async def _get_owned_agent(agent_id: UUID, db: AsyncSession, tenant: Tenant) -> Agent:
    """Fetch agent and enforce IDOR (404, not 403, on mismatch — no existence leak)."""
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


def _serialize_version(v: PromptVersion) -> dict:
    return {
        "id": str(v.id),
        "agent_id": str(v.agent_id),
        "version_number": v.version_number,
        "soul_role": v.soul_role,
        "soul_voice": v.soul_voice,
        "soul_do_list": v.soul_do_list,
        "soul_donot_list": v.soul_donot_list,
        "label": v.label,
        "canary_percent": v.canary_percent,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


# ---------------------------------------------------------------------------
# Route 1: GET /agents/{agent_id}/prompt-versions — list versions
# ---------------------------------------------------------------------------


@router.get("/agents/{agent_id}/prompt-versions")
async def list_prompt_versions(
    agent_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Return all prompt_versions rows for an agent, newest first."""
    await _get_owned_agent(agent_id, db, tenant)

    versions = await prompt_version_service.list_versions(db, agent_id)

    log.info(
        "list_prompt_versions.ok",
        agent_id=str(agent_id),
        tenant_id=str(tenant.id),
        version_count=len(versions),
    )
    return {"versions": [_serialize_version(v) for v in versions]}


# ---------------------------------------------------------------------------
# Route 2: GET /agents/{agent_id}/prompt-versions/diff — compare two versions
# ---------------------------------------------------------------------------


@router.get("/agents/{agent_id}/prompt-versions/diff")
async def diff_prompt_versions(
    agent_id: UUID,
    a: UUID = Query(...),
    b: UUID = Query(...),
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Per-field diff of the 4 soul fields between version `a` and version `b`."""
    await _get_owned_agent(agent_id, db, tenant)

    try:
        result = await prompt_version_service.diff_versions(db, agent_id, a, b)
    except prompt_version_service.PromptVersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    log.info("diff_prompt_versions.ok", agent_id=str(agent_id), tenant_id=str(tenant.id))
    return result


# ---------------------------------------------------------------------------
# Route 3: POST /agents/{agent_id}/prompt-versions/canary — set canary %
# ---------------------------------------------------------------------------


@router.post("/agents/{agent_id}/prompt-versions/canary")
async def set_prompt_version_canary(
    agent_id: UUID,
    body: SetCanaryRequest,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Route `body.percent`% of turns to `body.version_id` (T-21-09-01: never a draft)."""
    await _get_owned_agent(agent_id, db, tenant)

    try:
        version = await prompt_version_service.set_canary(
            db, agent_id, body.version_id, body.percent
        )
    except prompt_version_service.PromptVersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    await db.commit()

    log.info(
        "set_prompt_version_canary.ok",
        agent_id=str(agent_id),
        version_id=str(body.version_id),
        percent=body.percent,
        tenant_id=str(tenant.id),
    )
    return _serialize_version(version)


# ---------------------------------------------------------------------------
# Route 4: POST /agents/{agent_id}/prompt-versions/rollback — restore a version
# ---------------------------------------------------------------------------


@router.post("/agents/{agent_id}/prompt-versions/rollback")
async def rollback_prompt_version(
    agent_id: UUID,
    body: RollbackRequest,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Restore agent to `body.version_id`'s soul WITHOUT deleting any history."""
    await _get_owned_agent(agent_id, db, tenant)

    try:
        new_version = await prompt_version_service.rollback(db, agent_id, body.version_id)
    except prompt_version_service.PromptVersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    await db.commit()

    log.info(
        "rollback_prompt_version.ok",
        agent_id=str(agent_id),
        restored_from_version_id=str(body.version_id),
        new_version_id=str(new_version.id),
        tenant_id=str(tenant.id),
    )
    return _serialize_version(new_version)
