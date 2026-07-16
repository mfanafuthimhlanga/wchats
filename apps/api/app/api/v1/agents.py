"""
Agent routes.

POST   /agents                 — create agent and dispatch Celery chain; returns 202 Accepted
GET    /agents/{agent_id}      — return current agent state
PATCH  /agents/{agent_id}      — partial update of soul fields (AGT-11)

Security:
    - Requires X-API-Key on all routes (get_current_tenant dependency).
    - tenant_id sourced from authenticated Tenant (not request body) — T-04-04.
    - GET /agents/{agent_id} filters by both agent.id AND tenant_id — T-04-05.
    - PATCH /agents/{agent_id} enforces tenant ownership (T-04-06-03).
    - DELETE /agents/{agent_id} soft-deletes; tenant_id filter prevents IDOR
      (cross-tenant delete).

Architecture:
    - POST /agents does NOT emit events inline — the "started" event is emitted
      by the provision_neon task as its first action (CLAUDE.md: FastAPI never
      does work inline; PLAN.md must_haves).
    - Celery chain dispatched with request_id propagated via task headers
      (RESEARCH.md §Pattern 10).
"""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.contextvars import get_contextvars

from app.api.deps import get_current_tenant
from app.core.database import get_async_db
from app.models.agent import Agent
from app.models.job import Job
from app.models.tenant import Tenant
from app.schemas.agent import (
    AgentCreate,
    AgentCreateResponse,
    AgentDetailResponse,
    AgentListResponse,
    AgentResponse,
    AgentSoulUpdate,
    WidgetConfigUpdate,
)
from app.services.prompt_version_service import SOUL_FIELDS, create_version_from_agent
from app.worker.tasks.pipeline.provision import provision_neon

router = APIRouter(tags=["agents"])


@router.post("/agents", status_code=202, response_model=AgentCreateResponse)
async def create_agent(
    body: AgentCreate,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> AgentCreateResponse:
    """Create an agent row and job row, dispatch Celery chain, return 202.

    IMPORTANT: This route does NOT emit events.  The provision_neon task
    emits the "started" event as its first action after the idempotency guard.
    The route only creates DB rows, dispatches the chain, and returns immediately.
    """
    # Create agent with status=pending; tenant_id from authenticated context (T-04-04)
    agent = Agent(
        tenant_id=tenant.id,
        name=body.name,
        soul=body.soul.model_dump(),
        role=body.role,
        status="pending",
    )
    db.add(agent)
    await db.flush()  # get agent.id without committing yet

    # Create job row with kind=create_agent
    job = Job(
        tenant_id=tenant.id,
        agent_id=agent.id,
        kind="create_agent",
        status="pending",
    )
    db.add(job)
    await db.commit()
    await db.refresh(agent)
    await db.refresh(job)

    # Propagate request_id from FastAPI context into Celery task headers
    # (RESEARCH.md §Pattern 10 — structlog contextvars → Celery headers)
    # Note: provision_neon dispatches apply_migrations directly inside the task
    # body (see provision.py) — no Celery chain needed here. The chain callback
    # mechanism triggers a broker reconnect on Windows that raises ValueError
    # (billiard issue #299); direct dispatch inside the task avoids that path.
    ctx = get_contextvars()
    provision_neon.apply_async(
        args=[str(tenant.id), str(agent.id)],
        queue="pipeline",
        headers={"request_id": ctx.get("request_id", "")},
    )

    return AgentCreateResponse(
        agent_id=agent.id,
        job_id=job.id,
        status="pending",
        events_url=f"/jobs/{job.id}/events",
    )


@router.get("/agents", response_model=AgentListResponse)
async def list_agents(
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> AgentListResponse:
    """Return all non-deleted agents for the authenticated tenant, newest first.

    Security:
        T-04.2-02-01: tenant_id filter prevents cross-tenant enumeration (T-04-05 pattern).
        Only agents where Agent.tenant_id == tenant.id are returned.
    """
    result = await db.execute(
        select(Agent)
        .where(Agent.tenant_id == tenant.id, Agent.deleted_at.is_(None))
        .order_by(Agent.created_at.desc())
    )
    agents = result.scalars().all()
    return AgentListResponse(agents=[AgentResponse.model_validate(a) for a in agents])


@router.get("/agents/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> AgentResponse:
    """Return the current state of an agent.

    Filters by both agent.id AND tenant_id to prevent cross-tenant access (T-04-05).
    Returns 404 if agent not found or belongs to a different tenant.
    """
    result = await db.execute(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.tenant_id == tenant.id,
            Agent.deleted_at.is_(None),
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    return AgentResponse.model_validate(agent)


@router.patch("/agents/{agent_id}", response_model=AgentDetailResponse)
async def patch_agent(
    agent_id: UUID,
    body: AgentSoulUpdate,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> AgentDetailResponse:
    """Partially update soul fields on an agent (AGT-11).

    Only fields present in the JSON body are updated; missing fields are
    unchanged (model_dump(exclude_unset=True) semantics).

    Security:
        T-04-06-03: Agent ownership enforced via tenant_id filter.
        T-04-06-01: Empty-string list items stripped server-side (defense in depth).

    Returns 404 if agent does not exist or does not belong to authenticated tenant.
    Returns 422 on Pydantic validation failures (e.g. name="").
    """
    # 1. Validate agent exists and belongs to this tenant
    result = await db.execute(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.tenant_id == tenant.id,
            Agent.deleted_at.is_(None),
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 2. Extract only the fields the caller provided
    updates = body.model_dump(exclude_unset=True)

    # 3. Strip blank / whitespace-only list items (T-04-06-01 defense in depth)
    if "soul_do_list" in updates:
        updates["soul_do_list"] = [
            s.strip() for s in updates["soul_do_list"] if s and s.strip()
        ]
    if "soul_donot_list" in updates:
        updates["soul_donot_list"] = [
            s.strip() for s in updates["soul_donot_list"] if s and s.strip()
        ]

    # 4. Apply updates to the ORM row
    for field, value in updates.items():
        setattr(agent, field, value)

    # 4b. OPS-16: every soul edit appends an immutable prompt_versions row
    # (history is never overwritten — create_version_from_agent only ever
    # INSERTs a new row; a prior 'production' row is relabeled 'archived',
    # never mutated). A pure `name`-only PATCH is not a soul edit and does
    # not churn the version ledger.
    if any(field in updates for field in SOUL_FIELDS):
        await create_version_from_agent(db, agent)

    # 5. Persist
    await db.commit()
    await db.refresh(agent)

    # 6. Return full agent representation
    return AgentDetailResponse.model_validate(agent)


@router.delete("/agents/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> Response:
    """Soft-delete an agent by setting deleted_at.

    Soft-delete (not hard DELETE) preserves the row for audit trails and for any
    foreign-key references (jobs, documents). All read routes already filter on
    deleted_at IS NULL, so a soft-deleted agent disappears from the API surface.

    Security:
        Filters by both agent.id AND tenant_id so a tenant cannot delete another
        tenant's agent (IDOR). Returns 404 if not found or owned by another tenant.

    Idempotency:
        Re-deleting an already-deleted agent returns 404 (the deleted_at IS NULL
        filter excludes it), which is the same response as a non-existent agent.

    Returns 204 No Content on success.
    """
    result = await db.execute(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.tenant_id == tenant.id,
            Agent.deleted_at.is_(None),
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent.deleted_at = datetime.now(timezone.utc)
    await db.commit()

    return Response(status_code=204)


@router.get("/agents/{agent_id}/widget-config")
async def get_widget_config(
    agent_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Return the stored widget_config JSONB for an agent.

    Security:
        T-04.2-02-02: IDOR prevention — filters by both agent.id AND tenant_id.
        Returns 404 if agent not found or belongs to a different tenant.
    """
    result = await db.execute(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.tenant_id == tenant.id,
            Agent.deleted_at.is_(None),
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent.widget_config or {}


@router.post("/agents/{agent_id}/widget-config", status_code=200)
async def save_widget_config(
    agent_id: UUID,
    body: WidgetConfigUpdate,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Validate and persist a widget_config payload for an agent.

    Security:
        T-04.2-02-02: IDOR prevention — filters by both agent.id AND tenant_id.
        T-04.2-02-03: Color hex validated by WidgetColorsSchema before this route runs.
        T-04.2-02-04: Appearance/launcher_shape/font/radius constrained by Literal.
    """
    result = await db.execute(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.tenant_id == tenant.id,
            Agent.deleted_at.is_(None),
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent.widget_config = body.model_dump()
    await db.commit()
    await db.refresh(agent)
    return agent.widget_config
