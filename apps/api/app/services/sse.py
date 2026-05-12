"""
SSE event generator for Veridian job status streams.

event_generator — async generator yielding ServerSentEvent objects.

Protocol:
    Phase 1: Replay all existing job_events rows from the DB so late-joining
             clients receive full history.  If a terminal event is already in
             the DB, the generator returns immediately without subscribing to Redis.
    Phase 2: Subscribe to Redis pub/sub channel job_events:{job_id} and yield
             live events as they arrive from Celery tasks.
    Close:   Generator returns when terminal event received in either phase,
             or when the client disconnects (request.is_disconnected()).

Terminal events:
    job.complete — chain completed successfully
    job.failed   — chain failed; agent.status = "failed"

RESEARCH.md Pitfall 4: MUST check for terminal event in DB replay phase and
return immediately.  Otherwise the generator hangs forever on a completed job.
"""

import json
from collections.abc import AsyncGenerator
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import ServerSentEvent

from app.models.job_event import JobEvent

# Terminal event types that close the SSE stream
TERMINAL_EVENTS = frozenset({"job.complete", "job.failed"})


async def event_generator(
    request: Request,
    job_id: UUID,
    db: AsyncSession,
    redis_client,
) -> AsyncGenerator[ServerSentEvent, None]:
    """Async generator for job SSE events.

    Args:
        request:      FastAPI Request — used to detect client disconnection.
        job_id:       UUID of the job to stream events for.
        db:           Async SQLAlchemy session for DB replay phase.
        redis_client: Async Redis client for pub/sub phase.

    Yields:
        ServerSentEvent with event= (event_type) and data= (JSON payload).
        The id= field is set in the DB replay phase for cache recovery.
    """
    # ------------------------------------------------------------------
    # Phase 1: DB replay — backfill for late-joining clients
    # ------------------------------------------------------------------
    past = await db.execute(
        select(JobEvent)
        .where(JobEvent.job_id == job_id)
        .order_by(JobEvent.created_at)
    )
    for evt in past.scalars():
        if await request.is_disconnected():
            return
        yield ServerSentEvent(
            data=json.dumps(evt.payload),
            event=evt.event_type,
            id=str(evt.id),
        )
        # RESEARCH.md Pitfall 4: return immediately if terminal event is in DB.
        # Do NOT enter the Redis subscribe phase for an already-completed job.
        if evt.event_type in TERMINAL_EVENTS:
            return

    # ------------------------------------------------------------------
    # Phase 2: Redis pub/sub — live stream
    # ------------------------------------------------------------------
    async with redis_client.pubsub() as pubsub:
        await pubsub.subscribe(f"job_events:{job_id}")
        async for message in pubsub.listen():
            if await request.is_disconnected():
                break
            if message["type"] != "message":
                continue
            data = json.loads(message["data"])
            yield ServerSentEvent(
                data=json.dumps(data["payload"]),
                event=data["event_type"],
            )
            if data["event_type"] in TERMINAL_EVENTS:
                break
