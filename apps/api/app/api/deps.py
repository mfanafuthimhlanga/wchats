"""
FastAPI dependency functions for W Chats API authentication.

get_current_tenant  — validates Clerk JWT (Bearer) first, falls back to X-API-Key; returns authenticated Tenant
get_credential_kind — WHICH of those two paths authenticated this request
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
from fastapi import Depends, HTTPException, Request, Security
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
# Which credential authenticated the request
# ---------------------------------------------------------------------------
# WHY THIS EXISTS. `get_current_tenant` resolves BOTH a Clerk JWT — behind which
# there is one specific signed-in human — and an `X-API-Key`, which is a machine
# credential a script, a scheduler or a model-driven pipeline can hold. It
# returns the same `Tenant` either way and used to report nothing about which
# path ran, so a route could not tell a person from an automation.
#
# For almost every route that is fine: they authorise an ACCOUNT to act on its
# own data. It is not fine for exactly one route. `POST .../label` stamps
# `eval_scenarios.label_trust_tier = 'human_authored'`, a claim about WHO WROTE a
# string, and `VERIFIED_QA_MIN_TRUST_TIER` is defined over that hierarchy. If a
# machine credential can produce that tier, then `human_authored` means "whoever
# holds an API key said so", and label_service's four restrictions — which bind
# in-process Celery and ContextVar state — cannot see an out-of-process caller at
# all. The credential is the only evidence about the caller that survives the
# process boundary, so it is the only place that check can live.
CREDENTIAL_CLERK_JWT = "clerk_jwt"
CREDENTIAL_API_KEY = "api_key"
# Nothing recorded a credential. Reached only when `get_current_tenant` is
# overridden (a test) or replaced; a route that cares must treat it as "cannot
# tell" and fail CLOSED, never as "probably a human".
CREDENTIAL_UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# get_current_tenant
# ---------------------------------------------------------------------------


async def get_current_tenant(
    request: Request,
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

    Records WHICH path succeeded on `request.state.credential_kind` before every
    successful return, for `get_credential_kind` below. It is set on the way out
    rather than returned, so no existing caller's type changes; the kind itself
    is never logged and never leaves the process — it is a fact about the
    credential's SHAPE, not the credential.
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
                request.state.credential_kind = CREDENTIAL_CLERK_JWT
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
            request.state.credential_kind = CREDENTIAL_API_KEY
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
                request.state.credential_kind = CREDENTIAL_API_KEY
                return tenant

    # T-04-03: detail string contains no key fragment or DB error
    raise HTTPException(status_code=401, detail="Authentication required")


async def get_credential_kind(
    request: Request,
    tenant: Tenant = Depends(get_current_tenant),
) -> str:
    """CREDENTIAL_CLERK_JWT, CREDENTIAL_API_KEY or CREDENTIAL_UNKNOWN.

    Depends on `get_current_tenant` rather than merely running beside it, so the
    ordering is a property of the dependency graph and not of the parameter order
    in whichever handler declares both. FastAPI caches the sub-dependency, so the
    tenant is still resolved exactly once per request.

    Returns CREDENTIAL_UNKNOWN rather than raising when nothing was recorded: the
    honest answer to "which credential was this?" when no credential resolver ran
    is "cannot tell", and the decision about what to do with that belongs to the
    route that cares. The only route that cares — the human-label write — treats
    it as a refusal.
    """
    return getattr(request.state, "credential_kind", CREDENTIAL_UNKNOWN)


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
