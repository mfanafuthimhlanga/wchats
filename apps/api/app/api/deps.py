"""
FastAPI dependency functions for Veridian API authentication.

get_current_tenant  — validates X-API-Key header; returns authenticated Tenant
get_admin           — validates X-Admin-Key header against settings.ADMIN_KEY
get_async_redis     — yields an async Redis client for SSE pub/sub and health checks

Threat mitigations:
    T-04-01: API key verification uses argon2 verify() (timing-attack resistant).
    T-04-02: api_key variable is NEVER bound to structlog context or logged.
             Route handlers must not log request.headers.
    T-04-03: HTTPException detail strings contain no DB error messages or key fragments.
"""

import secrets

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_async_db
from app.core.security import verify_api_key
from app.models.tenant import Tenant

# ---------------------------------------------------------------------------
# Header extractors — auto_error=True raises 403 if header is absent
# ---------------------------------------------------------------------------
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)
_admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=True)


# ---------------------------------------------------------------------------
# get_current_tenant
# ---------------------------------------------------------------------------


async def get_current_tenant(
    api_key: str = Security(_api_key_header),
    db: AsyncSession = Depends(get_async_db),
) -> Tenant:
    """Validate X-API-Key and return the matching Tenant.

    Iterates over all non-deleted tenants and runs argon2 verify() against
    each stored hash.  Returns on first match.

    SECURITY: api_key is NEVER logged or bound to structlog context.
              Only raises HTTP 401 on mismatch — no detail that hints at
              which part of validation failed (T-04-03).
    """
    result = await db.execute(select(Tenant).where(Tenant.deleted_at.is_(None)))
    for tenant in result.scalars():
        # verify_api_key always returns bool; never raises on mismatch (01-02 decision)
        if verify_api_key(tenant.api_key_hash, api_key):
            return tenant

    # T-04-03: detail string contains no key fragment or DB error
    raise HTTPException(status_code=401, detail="Invalid API key")


# ---------------------------------------------------------------------------
# get_admin
# ---------------------------------------------------------------------------


async def get_admin(
    admin_key: str = Security(_admin_key_header),
) -> bool:
    """Validate X-Admin-Key against settings.ADMIN_KEY.

    Uses secrets.compare_digest for constant-time comparison (T-04-01).
    Raises HTTP 403 on mismatch.
    """
    if not secrets.compare_digest(
        admin_key.encode(), settings.ADMIN_KEY.encode()
    ):
        raise HTTPException(status_code=403, detail="Invalid admin key")
    return True


# ---------------------------------------------------------------------------
# get_async_redis
# ---------------------------------------------------------------------------


async def get_async_redis():
    """Yield an async Redis client for use in route handlers.

    Creates a connection from REDIS_URL for each request and closes it
    in the finally block to ensure proper cleanup.
    """
    client = aioredis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()
