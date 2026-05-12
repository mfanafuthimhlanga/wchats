"""
GET /health — liveness and readiness probe.

No authentication required.  Always returns HTTP 200 with a JSON body
reflecting the current status of Redis and the control DB.

Used by docker-compose healthcheck and load balancers.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_redis
from app.core.database import get_async_db

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(
    db: AsyncSession = Depends(get_async_db),
    redis=Depends(get_async_redis),
) -> dict:
    """Return status of Redis and DB.

    Both probes are attempted; failures are captured as "error" so the
    endpoint always returns 200 (callers can inspect per-service status).
    """
    # Probe DB
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"

    # Probe Redis
    try:
        await redis.ping()
        redis_status = "ok"
    except Exception:
        redis_status = "error"

    return {"status": "ok", "redis": redis_status, "db": db_status}
