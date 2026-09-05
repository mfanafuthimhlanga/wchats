"""
GET /health — liveness and readiness probe.

No authentication required.  Always returns HTTP 200 with a JSON body
reflecting the current status of Redis and the control DB.

Used by docker-compose healthcheck and load balancers.
"""

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_redis
from app.core.database import get_async_db

router = APIRouter(tags=["health"])

log = structlog.get_logger(__name__)


@router.get("/health")
async def health_check(
    db: AsyncSession = Depends(get_async_db),
    redis=Depends(get_async_redis),
) -> dict:
    """Return status of Redis and DB.

    Both probes are attempted; failures are captured as "error" so the
    endpoint always returns 200 (callers can inspect per-service status).

    A failure is split between two audiences (#142). The body is public, so it
    carries the exception type and nothing else: an invalid credential, an
    unreachable host, a suspended endpoint and a TLS mismatch stop rendering as
    the same four characters. The message and the traceback go to the log,
    where a DSN in a connection error is not being handed to a stranger.
    """
    body: dict = {"status": "ok"}

    # Probe DB
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:
        db_status = "error"
        body["db_error"] = type(exc).__name__
        log.warning(
            "health.db_probe_failed",
            error_type=type(exc).__name__,
            error=str(exc),
            exc_info=True,
        )

    # Probe Redis
    try:
        await redis.ping()
        redis_status = "ok"
    except Exception as exc:
        redis_status = "error"
        body["redis_error"] = type(exc).__name__
        log.warning(
            "health.redis_probe_failed",
            error_type=type(exc).__name__,
            error=str(exc),
            exc_info=True,
        )

    return {**body, "redis": redis_status, "db": db_status}
