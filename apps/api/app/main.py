"""
W Chats FastAPI application factory.

Provides:
    app — FastAPI instance with lifespan, middleware, CORS, and all v1 routers.

Architecture:
    - lifespan context manager (not deprecated @app.on_event)
    - RequestIdMiddleware — pure ASGI middleware for structlog request_id binding
    - CORSMiddleware — locked to settings.CORS_ORIGINS (no wildcard; T-04-06)
    - Cache-Control: no-store middleware — on ALL responses (T-04-07)
    - Sentry SDK — initialized only if settings.SENTRY_DSN is set
    - JWKS startup probe — validates CLERK_JWKS_URL returns at least one RS256 key (T-04-10-11)

Threat mitigations:
    T-04-06: CORS allow_origins uses settings.CORS_ORIGINS, never "*".
    T-04-07: Cache-Control: no-store injected by response middleware on every
             response including SSE.
    T-04-10-11: JWKS endpoint probed at startup; server refuses to start if the
                endpoint returns 0 keys, preventing silent 401s caused by a
                misconfigured or stale-cached PyJWKClient.
"""

import structlog
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import RequestIdMiddleware, configure_logging

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup tasks run before yield, teardown after."""
    # Startup —— configure structured logging before the first request arrives
    configure_logging(settings.LOG_LEVEL)

    # Sentry — only active if SENTRY_DSN env var is set
    if settings.SENTRY_DSN:
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.fastapi import FastApiIntegration

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            integrations=[FastApiIntegration(), CeleryIntegration()],
        )

    # T-04-10-11: Probe JWKS endpoint at startup to catch misconfigured CLERK_JWKS_URL.
    # This forces the PyJWKClient cache to populate with the correct signing keys
    # and fails fast if the endpoint returns 0 keys (e.g. if the default
    # https://api.clerk.com/v1/jwks is used instead of the instance-specific URL).
    _validate_jwks_on_startup()

    yield

    # Teardown — nothing to clean up in M1


def _validate_jwks_on_startup() -> None:
    """Probe the JWKS endpoint and warm the PyJWKClient cache.

    Raises RuntimeError if the endpoint returns 0 signing keys, which would
    cause all JWT verifications to fail with a silent 401.
    """
    from app.core.clerk_jwt import _get_jwks_client
    try:
        client = _get_jwks_client()
        signing_keys = client.get_signing_keys()
        log.info(
            "jwks.startup_probe_ok",
            url=settings.CLERK_JWKS_URL,
            key_count=len(signing_keys),
            kids=[k.key_id for k in signing_keys],
        )
    except Exception as exc:
        # Fail fast — misconfigured JWKS URL means every JWT will return 401.
        raise RuntimeError(
            f"JWKS startup probe failed for CLERK_JWKS_URL={settings.CLERK_JWKS_URL!r}. "
            f"Verify the URL in your .env file. Error: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

_is_production = settings.ENVIRONMENT == "production"

app = FastAPI(
    title="W Chats Control Plane",
    version="1.0.0",
    # Disable interactive API docs in production to reduce attack surface (WR-04).
    # Set ENVIRONMENT=production in the deployment environment to suppress /docs and /redoc.
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware (order matters — added last runs first)
# ---------------------------------------------------------------------------

# Pure ASGI request-ID middleware — must be innermost ASGI layer
app.add_middleware(RequestIdMiddleware)

# CORS — locked to known origins; never wildcard (T-04-06)
# Authorization added for browser Clerk JWT calls from admin UI (M4.1 — T-04-10-08)
# PATCH added for soul editor PATCH /agents/{id} calls
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["X-API-Key", "X-Admin-Key", "Content-Type", "Authorization"],
)


# ---------------------------------------------------------------------------
# Cache-Control: no-store on every response (T-04-07)
# ---------------------------------------------------------------------------


@app.middleware("http")
async def add_cache_control(request: Request, call_next):
    """Inject Cache-Control: no-store on every HTTP response.

    This covers all routes including SSE.  The SSE endpoint also sets the
    header explicitly on the EventSourceResponse for belt-and-suspenders.
    """
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

from app.api.v1 import agents, documents, health, jobs, tenants, query, agent_chat, widget, webhooks, evals, red_team, deployment, observability  # noqa: E402

# webhooks router registered FIRST — ensures /webhooks/clerk and /me/provision are matched
# before any wildcard route patterns
app.include_router(webhooks.router)
app.include_router(health.router)
app.include_router(widget.router)
app.include_router(tenants.router, prefix="/api/v1")
app.include_router(agents.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(query.router, prefix="/api/v1")
app.include_router(agent_chat.router, prefix="/api/v1")
app.include_router(evals.router, prefix="/api/v1")
app.include_router(red_team.router, prefix="/api/v1")
app.include_router(deployment.router, prefix="/api/v1")
app.include_router(observability.router, prefix="/api/v1")
