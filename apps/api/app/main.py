"""
Veridian FastAPI application factory.

Provides:
    app — FastAPI instance with lifespan, middleware, CORS, and all v1 routers.

Architecture:
    - lifespan context manager (not deprecated @app.on_event)
    - RequestIdMiddleware — pure ASGI middleware for structlog request_id binding
    - CORSMiddleware — locked to settings.CORS_ORIGINS (no wildcard; T-04-06)
    - Cache-Control: no-store middleware — on ALL responses (T-04-07)
    - Sentry SDK — initialized only if settings.SENTRY_DSN is set

Threat mitigations:
    T-04-06: CORS allow_origins uses settings.CORS_ORIGINS, never "*".
    T-04-07: Cache-Control: no-store injected by response middleware on every
             response including SSE.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import RequestIdMiddleware, configure_logging


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

    yield

    # Teardown — nothing to clean up in M1


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Veridian Control Plane",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware (order matters — added last runs first)
# ---------------------------------------------------------------------------

# Pure ASGI request-ID middleware — must be innermost ASGI layer
app.add_middleware(RequestIdMiddleware)

# CORS — locked to known origins; never wildcard (T-04-06)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["X-API-Key", "X-Admin-Key", "Content-Type"],
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

from app.api.v1 import agents, health, jobs, tenants  # noqa: E402

app.include_router(tenants.router)
app.include_router(agents.router)
app.include_router(jobs.router)
app.include_router(health.router)
