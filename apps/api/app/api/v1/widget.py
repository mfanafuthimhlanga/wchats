"""
Widget routes for Veridian M4.

GET    /widget/{agent_id}/config            — public config + JWT for embedded widget
POST   /widget/{agent_id}/chat              — Bearer JWT authenticated chat dispatch
GET    /widget/jobs/{job_id}/events         — PUBLIC SSE stream (R-03 resolution)
OPTIONS /widget/{agent_id}/config           — CORS preflight
OPTIONS /widget/{agent_id}/chat             — CORS preflight
OPTIONS /widget/jobs/{job_id}/events        — CORS preflight

Security:
    - /config: no auth; agent_id + status guard prevents fishing for agent details.
    - /chat: Bearer JWT (HS256, 15-min expiry); agent_id claim must match URL.
    - /events: no auth; job_id UUID4 entropy (~122 bits) is the access token (R-03).
    - Rate limit: 60 req/min per agent_id via Redis INCR + 60s TTL (T-04-04-06).
    - CORS: only widget routes set Access-Control-Allow-Origin: *; global
            CORSMiddleware remains locked to settings.CORS_ORIGINS (T-04-06).
    - message text NEVER logged (T-04-03-05).
    - conn_str NEVER in task args (CLAUDE.md rule 4).

Queue: runtime (CLAUDE.md: both Celery queues always present).
"""

import time
import structlog
import psycopg2
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import Response as PlainResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_async_db, get_async_redis
from app.core.config import settings
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.models.job import Job
from app.schemas.widget import WidgetChatRequest, WidgetChatResponse, WidgetConfigResponse
from app.services.sse import event_generator
from app.worker.tasks.runtime.agent import run_agent_turn

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# HTTPBearer scheme — auto_error=True raises 403 if Authorization header absent
# ---------------------------------------------------------------------------
bearer_scheme = HTTPBearer(auto_error=True)

router = APIRouter(tags=["widget"])

# ---------------------------------------------------------------------------
# CORS header constants for widget responses
# ---------------------------------------------------------------------------
_CORS_ALLOW_ORIGIN = "*"
_CORS_ALLOW_METHODS = "GET, POST, OPTIONS"
_CORS_ALLOW_HEADERS = "Content-Type, Authorization"
_CORS_MAX_AGE = "3600"


# ---------------------------------------------------------------------------
# JWT helpers (module-level — exported for unit tests)
# ---------------------------------------------------------------------------


def create_widget_jwt(agent_id: str) -> str:
    """Generate a short-lived HS256 JWT for widget authentication.

    Claim shape:
        sub:       "widget"
        agent_id:  str(agent_id)
        exp:       now + 900 seconds (15 minutes)

    Security (T-04-04-01): JWT stored in widget JS module scope only, never
    localStorage. 15-min expiry caps blast radius from XSS on embedding page.

    Args:
        agent_id: UUID string of the agent this token grants access to.

    Returns:
        Compact serialised HS256 JWT string.
    """
    payload = {
        "sub": "widget",
        "agent_id": agent_id,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=900),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def validate_widget_jwt(token: str, expected_agent_id: str) -> dict:
    """Decode and validate a widget JWT.

    Verifies:
        1. HS256 signature against settings.JWT_SECRET (T-04-04-03).
        2. Token has not expired (jose raises JWTError on expiry).
        3. agent_id claim matches expected_agent_id (T-04-04-02).

    Args:
        token:              Raw Bearer token string.
        expected_agent_id:  The agent_id from the URL path — must match claim.

    Returns:
        Decoded claims dict on success.

    Raises:
        HTTPException 401 on bad signature, expiry, or agent_id mismatch.
    """
    try:
        claims = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if claims.get("agent_id") != expected_agent_id:
        raise HTTPException(status_code=401, detail="Token agent_id mismatch")
    return claims


# ---------------------------------------------------------------------------
# Helper — conversation ownership validation (T-04-04-05)
# ---------------------------------------------------------------------------


def _validate_conv_owner(
    encrypted_conn_str: bytes,
    conversation_id: UUID,
    agent_id: UUID,
) -> bool:
    """Return True if conversation_id belongs to agent_id in the tenant DB."""
    conn_str = fernet_decrypt(encrypted_conn_str)
    sql = "SELECT 1 FROM conversations WHERE id = %s AND agent_id = %s LIMIT 1"
    conn = psycopg2.connect(conn_str, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (str(conversation_id), str(agent_id)))
            row = cur.fetchone()
    finally:
        conn.close()
    return row is not None


# ---------------------------------------------------------------------------
# GET /widget/{agent_id}/config  — PUBLIC
# ---------------------------------------------------------------------------


@router.get(
    "/widget/{agent_id}/config",
    response_model=WidgetConfigResponse,
)
async def get_widget_config(
    agent_id: UUID,
    response: Response,
    db: AsyncSession = Depends(get_async_db),
) -> WidgetConfigResponse:
    """Return widget configuration including a short-lived JWT.

    No authentication required — agent_id + status guard prevents leaking
    configuration for non-ready or deleted agents.

    Returns:
        WidgetConfigResponse with Access-Control-Allow-Origin: * header.

    Raises:
        404 if agent not found or deleted.
        409 if agent is not in status == 'ready'.
    """
    result = await db.execute(
        select(Agent).where(
            Agent.id == agent_id,
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

    jwt_token = create_widget_jwt(str(agent_id))

    # CORS — only widget routes use wildcard (T-04-04-04)
    response.headers["Access-Control-Allow-Origin"] = _CORS_ALLOW_ORIGIN
    response.headers["Cache-Control"] = "no-store"

    # Theming config from UI-SPEC.md Surface 1 Token System
    theming = {
        "primary_color": "#7B1C3A",
        "accent_gold": "#B8860B",
        "font_family": "system-ui",
        "border_radius": "14px",
        "background": "#FDF9F5",
    }

    return WidgetConfigResponse(
        agent_id=agent_id,
        name=agent.name,
        theming=theming,
        jwt=jwt_token,
    )


# ---------------------------------------------------------------------------
# POST /widget/{agent_id}/chat  — Bearer JWT auth
# ---------------------------------------------------------------------------


@router.post(
    "/widget/{agent_id}/chat",
    status_code=202,
    response_model=WidgetChatResponse,
)
async def post_widget_chat(
    agent_id: UUID,
    body: WidgetChatRequest,
    response: Response,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_async_db),
    redis_client=Depends(get_async_redis),
) -> WidgetChatResponse:
    """Accept a widget chat message and dispatch an agent turn job.

    Authentication: Bearer JWT generated by GET /widget/{id}/config.
    Rate limit: 60 req/min per agent_id via Redis INCR + 60s TTL (T-04-04-06).

    Returns:
        WidgetChatResponse (202 Accepted) with Access-Control-Allow-Origin: *.

    Raises:
        401 if JWT is invalid, expired, or agent_id claim mismatches URL.
        429 if rate limit exceeded (60 req/min per agent_id).
        404 if agent not found or deleted.
        409 if agent is not in status == 'ready'.
        403 if conversation_id provided but does not belong to this agent.
    """
    # ------------------------------------------------------------------
    # 1. Validate JWT (T-04-04-02, T-04-04-03)
    # ------------------------------------------------------------------
    validate_widget_jwt(credentials.credentials, str(agent_id))

    # ------------------------------------------------------------------
    # 2. Rate limit: 60 req/min per agent_id (T-04-04-06)
    #    Bucket key rotates each 60-second window.
    # ------------------------------------------------------------------
    bucket = str(int(time.time()) // 60)
    key = f"rate:{agent_id}:{bucket}"
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, 60)
    if count > 60:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": "60"},
        )

    # ------------------------------------------------------------------
    # 3. Load and validate agent
    # ------------------------------------------------------------------
    result = await db.execute(
        select(Agent).where(
            Agent.id == agent_id,
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
    # 4. Validate conversation_id ownership (T-04-04-05)
    # ------------------------------------------------------------------
    if body.conversation_id is not None:
        owned = _validate_conv_owner(
            agent.neon_connection_string,
            body.conversation_id,
            agent.id,
        )
        if not owned:
            raise HTTPException(
                status_code=403,
                detail="Conversation not found or access denied",
            )

    # ------------------------------------------------------------------
    # 5. Create job row in control DB
    # ------------------------------------------------------------------
    job = Job(
        tenant_id=agent.tenant_id,
        agent_id=agent.id,
        kind="agent_turn",
        status="pending",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # ------------------------------------------------------------------
    # 6. Dispatch run_agent_turn (message NEVER logged — T-04-03-05)
    # ------------------------------------------------------------------
    run_agent_turn.apply_async(
        args=[
            str(job.id),
            str(agent.id),
            body.message,
            str(body.conversation_id) if body.conversation_id else None,
        ],
        queue="runtime",
    )

    log.info(
        "widget_chat.dispatched",
        agent_id=str(agent.id),
        job_id=str(job.id),
    )

    # ------------------------------------------------------------------
    # 7. Set CORS header and return 202
    # ------------------------------------------------------------------
    response.headers["Access-Control-Allow-Origin"] = _CORS_ALLOW_ORIGIN

    return WidgetChatResponse(
        job_id=job.id,
        status="pending",
        events_url=f"/widget/jobs/{job.id}/events",
        conversation_id=body.conversation_id,
    )


# ---------------------------------------------------------------------------
# GET /widget/jobs/{job_id}/events  — PUBLIC (R-03 resolution)
# ---------------------------------------------------------------------------


@router.get("/widget/jobs/{job_id}/events")
async def widget_job_events(
    request: Request,
    job_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    redis_client=Depends(get_async_redis),
) -> EventSourceResponse:
    """Public SSE endpoint for widget clients.

    R-03 resolution: EventSource cannot send Authorization headers, so this
    public sibling reuses the existing event_generator from sse.py.
    Security: job_id is a server-generated UUID4 (~122 bits entropy) —
    cannot be guessed; short-lived (terminal event closes stream within seconds).

    Headers:
        X-Accel-Buffering: no          — prevents nginx buffering
        Cache-Control: no-store        — explicit belt-and-suspenders
        Access-Control-Allow-Origin: * — required for cross-origin widget
    """
    sse_response = EventSourceResponse(
        event_generator(request, job_id, db, redis_client)
    )
    sse_response.headers["X-Accel-Buffering"] = "no"
    sse_response.headers["Cache-Control"] = "no-store"
    sse_response.headers["Access-Control-Allow-Origin"] = _CORS_ALLOW_ORIGIN
    return sse_response


# ---------------------------------------------------------------------------
# OPTIONS preflight handlers — CORS for embedded widget (T-04-04-04)
# ---------------------------------------------------------------------------


def _cors_preflight_response() -> PlainResponse:
    """Build a 204 No Content response with permissive CORS headers."""
    return PlainResponse(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": _CORS_ALLOW_ORIGIN,
            "Access-Control-Allow-Methods": _CORS_ALLOW_METHODS,
            "Access-Control-Allow-Headers": _CORS_ALLOW_HEADERS,
            "Access-Control-Max-Age": _CORS_MAX_AGE,
        },
    )


@router.options("/widget/{agent_id}/config")
async def options_widget_config(agent_id: UUID) -> PlainResponse:
    """Handle OPTIONS preflight for GET /widget/{agent_id}/config."""
    return _cors_preflight_response()


@router.options("/widget/{agent_id}/chat")
async def options_widget_chat(agent_id: UUID) -> PlainResponse:
    """Handle OPTIONS preflight for POST /widget/{agent_id}/chat."""
    return _cors_preflight_response()


@router.options("/widget/jobs/{job_id}/events")
async def options_widget_events(job_id: UUID) -> PlainResponse:
    """Handle OPTIONS preflight for GET /widget/jobs/{job_id}/events."""
    return _cors_preflight_response()
