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

Architecture:
    - POST /agents does NOT emit events inline — the "started" event is emitted
      by the provision_neon task as its first action (CLAUDE.md: FastAPI never
      does work inline; PLAN.md must_haves).
    - Celery chain dispatched with request_id propagated via task headers
      (RESEARCH.md §Pattern 10).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
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
    AgentResponse,
    AgentSoulUpdate,
)
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

    # 5. Persist
    await db.commit()
    await db.refresh(agent)

    # 6. Return full agent representation
    return AgentDetailResponse.model_validate(agent)
