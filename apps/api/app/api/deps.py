"""
FastAPI dependency functions for Veridian API authentication.

get_current_tenant  — validates Clerk JWT (Bearer) first, falls back to X-API-Key; returns authenticated Tenant
get_admin           — validates X-Admin-Key header against settings.ADMIN_KEY
get_async_redis     — yields an async Redis client for SSE pub/sub and health checks

Threat mitigations:
    T-04-01: API key verification uses argon2 verify() (timing-attack resistant).
    T-04-02: api_key variable is NEVER bound to structlog context or logged.
             Route handlers must not log request.headers.
    T-04-03: HTTPException detail strings contain no DB error messages or key fragments.
    T-04-10-01: Clerk JWT path uses RS256 via PyJWKClient — unsigned tokens rejected.
    T-04-10-08: Bearer token from Clerk's in-memory store (via getToken()), never localStorage.
"""

import secrets
import ssl

import redis.asyncio as aioredis
import structlog
from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError, PyJWKClientConnectionError, PyJWKClientError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clerk_jwt import verify_clerk_jwt
from app.core.config import settings
from app.core.database import get_async_db
from app.core.security import hmac_key_prefix, verify_api_key
from app.models.tenant import Tenant

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Header extractors — auto_error=False for dual-auth (neither header is mandatory alone)
# ---------------------------------------------------------------------------
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=True)
_bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# get_current_tenant
# ---------------------------------------------------------------------------


async def get_current_tenant(
    bearer: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
    api_key: str | None = Security(_api_key_header),
    db: AsyncSession = Depends(get_async_db),
) -> Tenant:
    """Dual-auth dependency: tries Clerk JWT first, falls back to X-API-Key.

    JWT path:  Authorization: Bearer <clerk_session_token>
               → decode JWT → extract sub (clerk_user_id)
               → SELECT tenant WHERE clerk_user_id = sub
               → 404 if JWT valid but no tenant row (webhook missed — call /me/provision)

    API key path: X-API-Key: vrd_live_xxx
               → existing argon2 HMAC-prefix lookup (O(1) + fallback scan for legacy rows)

    Raises HTTP 401 if neither credential is present or valid.
    Raises HTTP 503 if the JWKS endpoint is unreachable (network error, not an auth failure).
    Never logs credentials (T-04-02).
    """
    # --- Path 1: Clerk JWT ---
    if bearer is not None:
        try:
            payload = verify_clerk_jwt(bearer.credentials)
            clerk_user_id: str = payload["sub"]  # "user_xxx" format
            result = await db.execute(
                select(Tenant).where(
                    Tenant.deleted_at.is_(None),
                    Tenant.clerk_user_id == clerk_user_id,
                )
            )
            tenant = result.scalars().first()
            if tenant:
                return tenant
            # JWT valid but no tenant provisioned yet (webhook may not have fired)
            raise HTTPException(
                status_code=404,
                detail="Tenant not provisioned. Call POST /me/provision.",
            )
        except HTTPException:
            raise
        except (PyJWKClientConnectionError, PyJWKClientError) as exc:
            # JWKS endpoint unreachable or key set error — infrastructure failure,
            # NOT an auth failure. Return 503 so the client knows to retry later.
            # T-04-03: detail string contains no key fragment or raw exception message.
            token_prefix = (bearer.credentials[:8] + "...") if bearer and len(bearer.credentials) > 8 else "(short)"
            log.error(
                "jwt.jwks_unavailable",
                exc_type=type(exc).__name__,
                token_prefix=token_prefix,
            )
            raise HTTPException(
                status_code=503,
                detail="Authentication service temporarily unavailable. Please retry.",
            )
        except InvalidTokenError as exc:
            # JWT signature/expiry/format failure — genuine auth failure → 401.
            token_prefix = (bearer.credentials[:8] + "...") if bearer and len(bearer.credentials) > 8 else "(short)"
            exc_msg = str(exc)[:200]
            log.warning(
                "jwt.verify_failed",
                exc_type=type(exc).__name__,
                exc_msg=exc_msg,
                token_prefix=token_prefix,
            )
            detail = f"Invalid session token: {exc_msg}" if settings.ENVIRONMENT != "production" else "Invalid session token"
            raise HTTPException(status_code=401, detail=detail)
        except Exception as exc:
            # Unexpected error (DB connection failure, ORM error, etc.) — log as
            # error (not warning) and return 503 to avoid masking infra failures as 401.
            # T-04-03: detail string contains no DB error message or key fragment.
            token_prefix = (bearer.credentials[:8] + "...") if bearer and len(bearer.credentials) > 8 else "(short)"
            log.error(
                "jwt.unexpected_error",
                exc_type=type(exc).__name__,
                exc_msg=str(exc)[:200],
                token_prefix=token_prefix,
            )
            raise HTTPException(
                status_code=503,
                detail="Authentication service temporarily unavailable. Please retry.",
            )

    # --- Path 2: X-API-Key (legacy + service-account tokens) ---
    if api_key is not None:
        # Primary path: O(1) indexed lookup by HMAC prefix (WR-01)
        prefix = hmac_key_prefix(api_key)
        result = await db.execute(
            select(Tenant).where(
                Tenant.deleted_at.is_(None),
                Tenant.api_key_prefix == prefix,
            )
        )
        tenant = result.scalars().first()
        if tenant and verify_api_key(tenant.api_key_hash, api_key):
            return tenant

        # Fallback path: scan rows where prefix is NULL (legacy rows without prefix)
        result = await db.execute(
            select(Tenant).where(
                Tenant.deleted_at.is_(None),
                Tenant.api_key_prefix.is_(None),
            )
        )
        for tenant in result.scalars():
            # verify_api_key always returns bool; never raises on mismatch (01-02 decision)
            if verify_api_key(tenant.api_key_hash, api_key):
                return tenant

    # T-04-03: detail string contains no key fragment or DB error
    raise HTTPException(status_code=401, detail="Authentication required")


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
    # Strip query params — ssl_cert_reqs=CERT_NONE is not parsed by redis-py from URL;
    # must be passed as a Python ssl constant kwarg (same pattern as celery_app.py).
    _url = settings.REDIS_URL.split("?")[0] if "?" in settings.REDIS_URL else settings.REDIS_URL
    _kwargs: dict = {"decode_responses": True}
    if _url.startswith("rediss://"):
        _kwargs["ssl_cert_reqs"] = ssl.CERT_NONE
    client = aioredis.Redis.from_url(_url, **_kwargs)
    try:
        yield client
    finally:
        await client.aclose()
