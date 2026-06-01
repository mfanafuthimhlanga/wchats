"""
Query routes for W Chats M3 hybrid retrieval.

POST /agents/{agent_id}/query   — dispatch a hybrid retrieval job (202 Accepted)
GET  /agents/{agent_id}/queries — list the 50 most-recent query jobs for an agent

Security:
    - Requires X-API-Key on all routes (get_current_tenant dependency).
    - tenant_id sourced from authenticated Tenant (not request body).
    - Agent validated by both Agent.id AND Agent.tenant_id — T-02-06-01 pattern.
    - Agent must have status == 'ready' before dispatch.
    - query text (body.query) is NEVER logged — security constraint (CLAUDE.md).
    - Only IDs are passed in Celery task args; connection strings NEVER in args.

Queue: runtime (CLAUDE.md non-negotiable: both Celery queues always present).
"""

import structlog
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_tenant
from app.core.database import get_async_db
from app.models.agent import Agent
from app.models.job import Job
from app.models.tenant import Tenant
from app.schemas.query import (
    QueryJobItem,
    QueryJobResponse,
    QueryListResponse,
    QueryRequest,
)
from app.worker.tasks.runtime.retrieve import retrieve_and_rank

log = structlog.get_logger(__name__)
router = APIRouter(tags=["query"])


# ---------------------------------------------------------------------------
# POST /agents/{agent_id}/query
# ---------------------------------------------------------------------------


@router.post(
    "/agents/{agent_id}/query",
    status_code=202,
    response_model=QueryJobResponse,
)
async def post_agent_query(
    agent_id: UUID,
    body: QueryRequest,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> QueryJobResponse:
    """Accept a query string and dispatch a hybrid retrieval job.

    Validates agent ownership and readiness, creates a job row in the
    control DB, dispatches retrieve_and_rank to the runtime queue, and
    returns 202 Accepted with job_id and events_url for SSE polling.

    Security: body.query is NEVER logged — only job_id and agent_id are emitted.

    Returns:
        QueryJobResponse (202 Accepted).

    Raises:
        404 if agent not found or not owned by the authenticated tenant.
        409 if agent exists but is not in status == 'ready'.
    """
    # ------------------------------------------------------------------
    # 1. Validate agent exists, belongs to tenant, and is not deleted
    # ------------------------------------------------------------------
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
    if agent.status != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Agent is not ready (status={agent.status})",
        )

    # ------------------------------------------------------------------
    # 2. Create job row in control DB
    # ------------------------------------------------------------------
    job = Job(
        tenant_id=tenant.id,
        agent_id=agent.id,
        kind="query_agent",
        status="pending",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # ------------------------------------------------------------------
    # 3. Dispatch retrieve_and_rank to runtime queue
    #    Only IDs + query text passed; NO conn_str (CLAUDE.md rule 4).
    #    body.query is NOT logged — security constraint.
    # ------------------------------------------------------------------
    retrieve_and_rank.apply_async(
        args=[str(job.id), str(agent.id), body.query],
        queue="runtime",
    )

    log.info(
        "query_agent.dispatched",
        agent_id=str(agent.id),
        job_id=str(job.id),
    )

    # ------------------------------------------------------------------
    # 4. Return 202 Accepted
    # ------------------------------------------------------------------
    return QueryJobResponse(
        job_id=job.id,
        status="pending",
        events_url=f"/jobs/{job.id}/events",
    )


# ---------------------------------------------------------------------------
# GET /agents/{agent_id}/queries
# ---------------------------------------------------------------------------


@router.get(
    "/agents/{agent_id}/queries",
    response_model=QueryListResponse,
)
async def get_agent_queries(
    agent_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> QueryListResponse:
    """Return the 50 most-recent query_agent jobs for the specified agent.

    Validates agent ownership, then queries the control DB for job rows
    with kind == 'query_agent', ordered by created_at DESC, limited to 50.

    Returns:
        QueryListResponse containing up to 50 QueryJobItem entries.

    Raises:
        404 if agent not found or not owned by the authenticated tenant.
    """
    # ------------------------------------------------------------------
    # 1. Validate agent ownership (T-02-06-01 pattern)
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 2. Fetch query jobs for this agent (LIMIT 50)
    # ------------------------------------------------------------------
    jobs_result = await db.execute(
        select(Job)
        .where(Job.agent_id == agent_id, Job.kind == "query_agent")
        .order_by(Job.created_at.desc())
        .limit(50)
    )
    rows = jobs_result.scalars().all()

    # ------------------------------------------------------------------
    # 3. Map Job rows → QueryJobItem
    #    Job.id → job_id (explicit mapping; Job PK is 'id', schema field is 'job_id')
    # ------------------------------------------------------------------
    items = [
        QueryJobItem(
            job_id=j.id,
            status=j.status,
            kind=j.kind,
            created_at=j.created_at,
            finished_at=j.finished_at,
        )
        for j in rows
    ]

    return QueryListResponse(jobs=items)
