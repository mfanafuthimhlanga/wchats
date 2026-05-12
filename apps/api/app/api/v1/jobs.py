"""
Job routes.

GET /jobs/{job_id}         — job details with paginated events (requires X-API-Key)
GET /jobs/{job_id}/events  — SSE stream: replay DB history then subscribe Redis

Security:
    - Both routes require X-API-Key (get_current_tenant dependency).
    - T-04-03: no DB errors surfaced in HTTP response detail.

SSE:
    - X-Accel-Buffering: no — prevents nginx buffering the stream.
    - Cache-Control: no-store — set explicitly on EventSourceResponse
      (belt-and-suspenders; also set by the global middleware).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_async_redis, get_current_tenant
from app.core.database import get_async_db
from app.models.job import Job
from app.models.job_event import JobEvent
from app.models.tenant import Tenant
from app.schemas.job import JobEventResponse, JobResponse
from app.services.sse import event_generator

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> JobResponse:
    """Return job details with the last 100 events."""
    # Verify the job belongs to the authenticated tenant
    result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.tenant_id == tenant.id,
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Fetch last 100 events ordered by created_at
    events_result = await db.execute(
        select(JobEvent)
        .where(JobEvent.job_id == job_id)
        .order_by(JobEvent.created_at)
        .limit(100)
    )
    events = events_result.scalars().all()

    return JobResponse(
        id=job.id,
        agent_id=job.agent_id,
        kind=job.kind,
        status=job.status,
        error=job.error,
        created_at=job.created_at,
        events=[JobEventResponse.model_validate(e) for e in events],
    )


@router.get("/jobs/{job_id}/events")
async def stream_job_events(
    request: Request,
    job_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    redis_client=Depends(get_async_redis),
    _: Tenant = Depends(get_current_tenant),
) -> EventSourceResponse:
    """SSE endpoint: replay job history then stream live events.

    Phase 1: Replay all job_events rows from DB (late-join backfill).
    Phase 2: Subscribe to Redis pub/sub channel job_events:{job_id}.
    Closes automatically on terminal event (job.complete or job.failed).

    Headers:
        X-Accel-Buffering: no  — prevents nginx buffering
        Cache-Control: no-store — already set by global middleware; set explicitly here
                                   for belt-and-suspenders compliance with CONTEXT.md
    """
    response = EventSourceResponse(event_generator(request, job_id, db, redis_client))
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-store"
    return response
