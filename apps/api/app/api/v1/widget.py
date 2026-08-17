"""
Widget routes for W Chats M4.

GET    /widget/{agent_id}/config            — public config + JWT for embedded widget
POST   /widget/{agent_id}/chat              — Bearer JWT authenticated chat dispatch
GET    /widget/jobs/{job_id}/events         — PUBLIC SSE stream (R-03 resolution)
POST   /widget/agents/{agent_id}/feedback   — Bearer JWT authenticated thumbs/CSAT (OPS-02)
OPTIONS /widget/{agent_id}/config           — CORS preflight
OPTIONS /widget/{agent_id}/chat             — CORS preflight
OPTIONS /widget/jobs/{job_id}/events        — CORS preflight
OPTIONS /widget/agents/{agent_id}/feedback  — CORS preflight

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

import asyncio
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

import psycopg2
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import Response as PlainResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_async_db, get_async_redis
from app.core.config import settings
from app.core.security import fernet_decrypt, require_ciphertext
from app.models.agent import Agent
from app.models.job import Job
from app.schemas.widget import (
    OtpRequestBody,
    OtpVerifyBody,
    OtpVerifyResponse,
    WidgetChatRequest,
    WidgetChatResponse,
    WidgetConfigResponse,
    WidgetFeedbackRequest,
)
from app.services.budget import ESTIMATED_TURN_COST_USD, check_and_increment_budget
from app.services.identity_service import OtpInvalid, OtpRateLimited, OtpStorageError, request_otp, verify_otp
from app.services.rate_limit import check_agent_turn_rate_limit
from app.services.sse import CUSTOMER_TERMINAL_EVENTS, event_generator
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
# SSE slot cap constant (F8 — T-04.1-02-02)
# ---------------------------------------------------------------------------
_MAX_CONCURRENT_SSE_PER_AGENT = 50

# ---------------------------------------------------------------------------
# Redis key namespace for this route's 60/min per-agent turn ceiling.
# POST /agents/{id}/chat passes its own prefix so the two routes never share a
# bucket (BACKLOG 7.4). The literal value is unchanged from T-04-04-06.
# ---------------------------------------------------------------------------
_CHAT_RATE_LIMIT_PREFIX = "rate"

# ---------------------------------------------------------------------------
# F4: theming served when a tenant has never saved a widget_config
# (migration 0009 defaults the JSONB column to {}).
# Values from UI-SPEC.md Surface 1 Token System.
# ---------------------------------------------------------------------------
DEFAULT_THEMING = {
    "primary_color": "#7B1C3A",
    "accent_gold": "#B8860B",
    "font_family": "system-ui",
    "border_radius": "14px",
    "background": "#FDF9F5",
}


# ---------------------------------------------------------------------------
# F2: Per-IP rate limit helper for GET /widget/{agent_id}/config
# ---------------------------------------------------------------------------


async def _check_config_rate_limit(client_ip: str, redis: Redis) -> None:
    """Enforce 10 req/min per client IP on the config (JWT mint) endpoint.

    Key: rate:config:{client_ip}:{bucket}  (bucket = 60-second window)
    TTL: 120 seconds (two windows — belt-and-suspenders against clock drift)
    Ceiling: 10 requests per 60-second window.
    Rate is per-IP only; agent_id is not in scope (WR-07).

    Raises:
        HTTPException 429 with Retry-After: 60 if ceiling exceeded.
    """
    bucket = int(time.time()) // 60
    key = f"rate:config:{client_ip}:{bucket}"
    await redis.set(key, 0, nx=True, ex=120)
    count = await redis.incr(key)
    if count > 10:
        raise HTTPException(
            status_code=429,
            detail="Too many config requests",
            headers={"Retry-After": "60"},
        )


# ---------------------------------------------------------------------------
# F8: SSE concurrent slot management helpers
# ---------------------------------------------------------------------------


async def _acquire_sse_slot(agent_id: str, job_id: str, redis: Redis) -> bool:
    """Acquire a per-agent SSE slot.  Returns True if slot acquired, False if at capacity.

    Uses Redis SETNX to claim a unique slot per job_id, then INCR to track count.
    If count exceeds _MAX_CONCURRENT_SSE_PER_AGENT, the slot is released immediately.
    """
    count_key = f"sse:count:{agent_id}"
    slot_key = f"sse:slot:{agent_id}:{job_id}"
    # Atomic slot claim with 150s TTL (> 120s hard timeout + buffer)
    acquired = await redis.set(slot_key, "1", nx=True, ex=150)
    if not acquired:
        return False  # duplicate job_id connection attempt
    count = await redis.incr(count_key)
    if count == 1:
        await redis.expire(count_key, 3600)
    if count > _MAX_CONCURRENT_SSE_PER_AGENT:
        # Over limit — release slot immediately
        await redis.delete(slot_key)
        await redis.decr(count_key)
        return False
    return True


async def _release_sse_slot(agent_id: str, job_id: str, redis: Redis) -> None:
    """Release a previously acquired SSE slot.  Safe to call even if slot expired."""
    slot_key = f"sse:slot:{agent_id}:{job_id}"
    deleted = await redis.delete(slot_key)
    if deleted:
        await redis.decr(f"sse:count:{agent_id}")


# ---------------------------------------------------------------------------
# Stored widget_config → the flat theming dict the widget consumes
# ---------------------------------------------------------------------------


def theming_from_widget_config(widget_config: dict | None) -> dict:
    """Flatten a stored ``agents.widget_config`` into a flat theming dict.

    The widget turns every theming entry into a CSS custom property
    (``Object.entries(cfg.theming)`` → ``--<key>``), so a nested block would render
    as the string "[object Object]". WidgetConfigUpdate's ``colors`` and
    ``typography`` blocks are therefore spread to the top level; their key spaces
    do not collide with each other or with ``appearance`` / ``launcher_shape``.

    Null values are dropped rather than serialised — ``font_custom_url`` is
    optional, and ``--font-custom-url: None`` is not a CSS value.

    Args:
        widget_config: The JSONB column contents. None or {} means the tenant has
                       never saved a config.

    Returns:
        A flat str→value dict. DEFAULT_THEMING (a copy) when nothing is stored.
    """
    if not widget_config:
        return dict(DEFAULT_THEMING)
    flat: dict = {}
    for key, value in widget_config.items():
        if isinstance(value, dict):
            flat.update({k: v for k, v in value.items() if v is not None})
        elif value is not None:
            flat[key] = value
    return flat


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
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis_client=Depends(get_async_redis),
) -> WidgetConfigResponse:
    """Return widget configuration including a short-lived JWT.

    No authentication required — agent_id + status guard prevents leaking
    configuration for non-ready or deleted agents.

    Rate limited: 10 req/min per client IP (F2 — T-04.1-02-01).
    X-Forwarded-For is logged but NOT trusted in M4.1 (proxy hardening is M8 scope).

    Returns:
        WidgetConfigResponse with Access-Control-Allow-Origin: * header.

    Raises:
        429 if per-IP rate limit exceeded (10/min).
        404 if agent not found or deleted.
        409 if agent is not in status == 'ready'.
    """
    # F2: Per-IP rate limit — enforce before any DB access or JWT mint
    if request.headers.get("X-Forwarded-For"):
        log.debug(
            "config_rate_limit.x_forwarded_for_present",
            note="trusting request.client.host in M4.1; production should configure proxy",
        )
    await _check_config_rate_limit(request.client.host if request.client else "unknown", redis_client)

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

    # The tenant's saved design (POST /agents/{id}/widget-config, migration 0009),
    # falling back to DEFAULT_THEMING when nothing has been saved.
    theming = theming_from_widget_config(agent.widget_config)

    return WidgetConfigResponse(
        agent_id=agent_id,
        name=agent.name,
        agent_name=agent.name,
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
    #    Shared with POST /agents/{id}/chat via app.services.rate_limit (7.4);
    #    separate bucket prefix, identical ceiling.
    # ------------------------------------------------------------------
    if not await check_agent_turn_rate_limit(
        _CHAT_RATE_LIMIT_PREFIX, str(agent_id), redis_client
    ):
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
        owned = await asyncio.to_thread(
            _validate_conv_owner,
            agent.neon_connection_string,  # type: ignore[arg-type]  # provisioning is enforced upstream; None never reaches here
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
    # 5b. Budget guard — F4 (T-04.1-03-01): check tenant daily ceiling
    #     BEFORE dispatching the Celery task. Returns 429 if exhausted.
    # ------------------------------------------------------------------
    budget_ok = await check_and_increment_budget(
        str(agent.tenant_id),
        ESTIMATED_TURN_COST_USD,
        redis_client,
        settings.TENANT_DAILY_BUDGET_USD,
    )
    if not budget_ok:
        raise HTTPException(
            status_code=429,
            detail="Daily usage limit reached. Please try again tomorrow.",
            headers={"Retry-After": "3600"},
        )

    # ------------------------------------------------------------------
    # 6. Dispatch run_agent_turn (message + verified_session_token NEVER logged — T-04-03-05)
    # ------------------------------------------------------------------
    # THREAT MODEL NOTE (Phase 17 accepted trade-off):
    # The raw verified_session_token is stored in Redis as a Celery task arg. Mitigations
    # already in place: Redis is not a public endpoint; the token has a short TTL
    # (VERIFIED_SESSION_TTL_SECONDS=3600) and grants access only to IDV-gated tools — not
    # admin or tenant-level access. Full mitigation (Fernet encryption before task dispatch,
    # consistent with the neon_connection_string pattern) is deferred to Phase 18.
    run_agent_turn.apply_async(
        args=[
            str(job.id),
            str(agent.id),
            body.message,
            str(body.conversation_id) if body.conversation_id else None,
            body.verified_session_token or "",  # IDV-05: 5th positional arg (empty string = no session)
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

    F8 controls (T-04.1-02-02, T-04.1-02-03):
        - Per-agent_id concurrent SSE connection cap (_MAX_CONCURRENT_SSE_PER_AGENT=50)
        - asyncio.timeout(120) hard cap on the SSE generator loop

    Terminal set: CUSTOMER_TERMINAL_EVENTS, so the server closes the stream itself on
    agent.response / agent.failed instead of holding a slot to the 120s cap waiting for
    the client to hang up (BACKLOG 7.3). The judge chain keeps writing to this job_id
    afterwards; those events remain visible on the authenticated /jobs/{id}/events
    stream, which keeps the default job-lifecycle terminal set.

    Headers:
        X-Accel-Buffering: no          — prevents nginx buffering
        Cache-Control: no-store        — explicit belt-and-suspenders
        Access-Control-Allow-Origin: * — required for cross-origin widget
    """
    # ------------------------------------------------------------------
    # 1. Resolve agent_id from Job row (needed for per-agent slot key)
    # ------------------------------------------------------------------
    result = await db.execute(select(Job).where(Job.id == job_id))
    job_row = result.scalar_one_or_none()
    if job_row is None:
        raise HTTPException(status_code=404, detail="Job not found")

    agent_id_for_job = job_row.agent_id

    # ------------------------------------------------------------------
    # 2. Acquire SSE slot — returns 503 if agent at connection capacity
    # ------------------------------------------------------------------
    slot_ok = await _acquire_sse_slot(
        str(agent_id_for_job), str(job_id), redis_client
    )
    if not slot_ok:
        raise HTTPException(
            status_code=503,
            detail="Too many concurrent connections for this agent",
        )

    # ------------------------------------------------------------------
    # 3. Wrapped generator: asyncio.timeout(120) + slot release in finally
    # ------------------------------------------------------------------
    async def _wrapped_generator():
        try:
            async with asyncio.timeout(120):
                async for event in event_generator(
                    request,
                    job_id,
                    db,
                    redis_client,
                    terminal_events=CUSTOMER_TERMINAL_EVENTS,
                ):
                    yield event
        except asyncio.TimeoutError:
            yield "event: timeout\ndata: {}\n\n"
        finally:
            await _release_sse_slot(
                str(agent_id_for_job), str(job_id), redis_client
            )

    sse_response = EventSourceResponse(_wrapped_generator())
    sse_response.headers["X-Accel-Buffering"] = "no"
    sse_response.headers["Cache-Control"] = "no-store"
    sse_response.headers["Access-Control-Allow-Origin"] = _CORS_ALLOW_ORIGIN
    return sse_response


# ---------------------------------------------------------------------------
# POST /widget/{agent_id}/identity/request  — Bearer JWT auth (IDV-02/IDV-03)
# ---------------------------------------------------------------------------


@router.post(
    "/widget/{agent_id}/identity/request",
    status_code=204,
    response_class=PlainResponse,
)
async def post_widget_identity_request(
    agent_id: UUID,
    body: OtpRequestBody,
    response: Response,
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    redis_client=Depends(get_async_redis),
) -> PlainResponse:
    """Request an OTP code. Always returns 204 — code is never echoed (T-17-19).

    Authentication: Bearer JWT generated by GET /widget/{id}/config.
    Rate limit: 10 req/min per client IP (T-17-18 SMS cost control).
    The request route ALWAYS returns 204 regardless of whether external_id is known
    — no enumeration oracle.

    Returns:
        204 No Content. The OTP code is NEVER included in the response body.

    Raises:
        401 if JWT is invalid, expired, or agent_id claim mismatches URL.
        429 if per-IP send limit or per-external_id send cap exceeded.
    """
    # ------------------------------------------------------------------
    # 1. Validate JWT (T-17-20) — runs FIRST on all identity routes
    # ------------------------------------------------------------------
    validate_widget_jwt(credentials.credentials, str(agent_id))

    # ------------------------------------------------------------------
    # 2. Per-IP send rate limit: 10/min (T-17-18 cost guard)
    # NOTE: CORS header is set on the returned PlainResponse object directly
    # (FastAPI does not merge injected response headers into returned Response objs).
    # ------------------------------------------------------------------
    bucket_60s = str(int(time.time()) // 60)
    ip_key = f"otp_sendip:{request.client.host if request.client else 'unknown'}:{bucket_60s}"
    await redis_client.set(ip_key, 0, nx=True, ex=120)
    ip_count = await redis_client.incr(ip_key)
    if ip_count > 10:
        raise HTTPException(
            status_code=429,
            detail="Too many OTP requests from this IP — try again later",
            headers={"Retry-After": "60"},
        )

    # ------------------------------------------------------------------
    # 4. Delegate to identity service
    #    OtpRateLimited (per-external_id cap) → 429
    # ------------------------------------------------------------------
    try:
        await request_otp(redis_client, str(agent_id), body.external_id, body.method)
    except OtpRateLimited:
        raise HTTPException(
            status_code=429,
            detail="OTP send limit exceeded — try again later",
            headers={"Retry-After": "60"},
        )

    # 204 No Content — code is NEVER returned to client (T-17-19)
    # CORS header set directly on PlainResponse (FastAPI does not merge injected
    # response headers when the handler returns a Response object directly).
    return PlainResponse(
        status_code=204,
        headers={"Access-Control-Allow-Origin": _CORS_ALLOW_ORIGIN},
    )


# ---------------------------------------------------------------------------
# POST /widget/{agent_id}/identity/verify  — Bearer JWT auth (IDV-05)
# ---------------------------------------------------------------------------


@router.post(
    "/widget/{agent_id}/identity/verify",
    response_model=OtpVerifyResponse,
)
async def post_widget_identity_verify(
    agent_id: UUID,
    body: OtpVerifyBody,
    response: Response,
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_async_db),
    redis_client=Depends(get_async_redis),
) -> OtpVerifyResponse:
    """Verify an OTP code and issue a verified session token (IDV-05).

    Authentication: Bearer JWT generated by GET /widget/{id}/config.

    Security:
        T-17-19: Returns SAME 400 detail for expired and wrong code — no oracle.
        T-17-11: otp_code and the returned token are NEVER passed to any log call.
        T-17-07: Token is minted server-side by verify_otp; client cannot supply its own.

    Returns:
        200 with OtpVerifyResponse containing verified_session_token on correct code.

    Raises:
        401 if JWT is invalid, expired, or agent_id claim mismatches URL.
        404 if agent not found or deleted.
        400 on wrong or expired code (identical detail for both — T-17-19).
        429 if attempt limit exceeded.
    """
    # ------------------------------------------------------------------
    # 1. Validate JWT (T-17-20) — runs FIRST on all identity routes
    # ------------------------------------------------------------------
    validate_widget_jwt(credentials.credentials, str(agent_id))

    # ------------------------------------------------------------------
    # 2. CORS header (widget endpoint convention)
    # ------------------------------------------------------------------
    response.headers["Access-Control-Allow-Origin"] = _CORS_ALLOW_ORIGIN

    # ------------------------------------------------------------------
    # 3. Load agent to obtain decrypted tenant conn_str
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

    conn_str = fernet_decrypt(require_ciphertext(agent.neon_connection_string, "agents.neon_connection_string"))

    # ------------------------------------------------------------------
    # 4. Verify OTP — delegate to identity service
    #    OtpInvalid → 400 (same detail for wrong AND expired — T-17-19)
    #    OtpRateLimited → 429
    #    otp_code and token are NEVER logged (T-17-11)
    # ------------------------------------------------------------------
    try:
        token = await verify_otp(
            redis_client,
            str(agent_id),
            body.external_id,
            body.otp_code,
            body.method,
            conn_str,
        )
    except OtpInvalid:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired code",  # identical detail for wrong AND expired (T-17-19)
        )
    except OtpRateLimited:
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts — try again later",
            headers={"Retry-After": "60"},
        )
    except OtpStorageError:
        # OTP was correct and consumed (single-use invariant enforced), but the
        # session UPSERT failed (e.g. DB timeout). Client must request a new OTP.
        raise HTTPException(
            status_code=503,
            detail="Verification succeeded but session could not be saved — please request a new code",
            headers={"Retry-After": "30"},
        )

    return OtpVerifyResponse(verified_session_token=token)


# ---------------------------------------------------------------------------
# Helper — message_feedback INSERT (OPS-02)
# ---------------------------------------------------------------------------


def _insert_message_feedback_sync(
    conn_str: str,
    message_id: UUID,
    conversation_id: UUID | None,
    rating: str,
    csat_score: int | None,
) -> None:
    """INSERT one message_feedback row (OPS-02).

    Called inside asyncio.to_thread() to avoid blocking the event loop.
    rating/csat_score are already Pydantic-validated (Literal + 1-5 bound) by
    the time this runs; the table's CHECK constraints (migration 0009) are
    defense in depth (T-21-02-04).
    """
    conn = psycopg2.connect(conn_str, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO message_feedback (message_id, conversation_id, rating, csat_score)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    str(message_id),
                    str(conversation_id) if conversation_id else None,
                    rating,
                    csat_score,
                ),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# POST /widget/agents/{agent_id}/feedback  — Bearer JWT auth (OPS-02)
# ---------------------------------------------------------------------------


@router.post(
    "/widget/agents/{agent_id}/feedback",
    status_code=204,
    response_class=PlainResponse,
)
async def post_widget_feedback(
    agent_id: UUID,
    body: WidgetFeedbackRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_async_db),
    redis_client=Depends(get_async_redis),
) -> PlainResponse:
    """Record widget thumbs +/- and optional 1-5 CSAT for one assistant message.

    Authentication: Bearer JWT generated by GET /widget/{id}/config — same
    requirement as POST /widget/{id}/chat (T-21-02-02: this route MUST NOT be
    unauthenticated the way /widget/{id}/config is).
    Rate limit: 60 req/min per agent_id via Redis INCR (T-21-02-03), own bucket
    key (rate:feedback:...) distinct from /chat's rate limiter so feedback
    traffic cannot starve — or be starved by — chat traffic on the same agent.

    Returns:
        204 No Content with Access-Control-Allow-Origin: *.

    Raises:
        401 if JWT is invalid, expired, or agent_id claim mismatches URL.
        403 if the Authorization header is missing entirely (HTTPBearer auto_error).
        422 if rating is not 'up'/'down' or csat_score is outside 1-5 (Pydantic).
        429 if rate limit exceeded (60 req/min per agent_id).
        404 if agent not found, deleted, or has no tenant DB provisioned.
    """
    # ------------------------------------------------------------------
    # 1. Validate JWT (T-21-02-02) — runs FIRST, same as chat/identity routes
    # ------------------------------------------------------------------
    validate_widget_jwt(credentials.credentials, str(agent_id))

    # ------------------------------------------------------------------
    # 2. Rate limit: 60 req/min per agent_id (T-21-02-03)
    #    Own bucket key, mirrors /chat's shape but never shares its key.
    # ------------------------------------------------------------------
    bucket = str(int(time.time()) // 60)
    key = f"rate:feedback:{agent_id}:{bucket}"
    await redis_client.set(key, 0, nx=True, ex=60)
    count = await redis_client.incr(key)
    if count > 60:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": "60"},
        )

    # ------------------------------------------------------------------
    # 3. Load agent and resolve conn_str (mirrors post_widget_identity_verify)
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

    if not agent.neon_connection_string:
        raise HTTPException(status_code=404, detail="Agent database not provisioned")

    conn_str = fernet_decrypt(agent.neon_connection_string)

    # ------------------------------------------------------------------
    # 4. INSERT message_feedback row (message text is never involved here —
    #    only message_id/rating/csat_score, T-04-03-05 pattern)
    # ------------------------------------------------------------------
    await asyncio.to_thread(
        _insert_message_feedback_sync,
        conn_str,
        body.message_id,
        body.conversation_id,
        body.rating,
        body.csat_score,
    )

    log.info(
        "widget_feedback.recorded",
        agent_id=str(agent_id),
        rating=body.rating,
        has_csat=body.csat_score is not None,
    )

    return PlainResponse(
        status_code=204,
        headers={"Access-Control-Allow-Origin": _CORS_ALLOW_ORIGIN},
    )


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


@router.options("/widget/{agent_id}/identity/request")
async def options_widget_identity_request(agent_id: UUID) -> PlainResponse:
    """Handle OPTIONS preflight for POST /widget/{agent_id}/identity/request."""
    return _cors_preflight_response()


@router.options("/widget/{agent_id}/identity/verify")
async def options_widget_identity_verify(agent_id: UUID) -> PlainResponse:
    """Handle OPTIONS preflight for POST /widget/{agent_id}/identity/verify."""
    return _cors_preflight_response()


@router.options("/widget/agents/{agent_id}/feedback")
async def options_widget_feedback(agent_id: UUID) -> PlainResponse:
    """Handle OPTIONS preflight for POST /widget/agents/{agent_id}/feedback."""
    return _cors_preflight_response()
