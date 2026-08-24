"""
Agent chat routes for W Chats M4.

POST /agents/{agent_id}/chat         — dispatch a single agent turn (202 Accepted)
GET  /agents/{agent_id}/conversations — list recent conversations for an agent

Security:
    - Requires X-API-Key on all routes (get_current_tenant dependency).
    - Agent validated by Agent.id AND Agent.tenant_id — T-02-06-01 pattern.
    - Agent must have status == 'ready' before dispatch.
    - Rate limit: 60 turns/min per agent_id, shared implementation with the widget
      route (app.services.rate_limit), own bucket prefix.
    - Budget: tenant daily ceiling enforced before dispatch (app.services.budget).
    - conversation_id ownership validated against tenant DB (T-04-04-05).
    - message text is NEVER logged (T-04-03-05).
    - conn_str is decrypted at runtime, NEVER passed in task args (CLAUDE.md rule 4).

Queue: runtime (CLAUDE.md: both Celery queues always present).
"""

from uuid import UUID

import psycopg2
import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_redis, get_current_tenant
from app.core.config import settings
from app.core.database import get_async_db
from app.core.security import fernet_decrypt, require_ciphertext
from app.models.agent import Agent
from app.models.job import Job
from app.models.tenant import Tenant
from app.schemas.agent_chat import (
    AgentChatRequest,
    AgentChatResponse,
    ConversationListItem,
    ConversationListResponse,
)
from app.services.budget import ESTIMATED_TURN_COST_USD, check_and_increment_budget
from app.services.rate_limit import check_agent_turn_rate_limit
from app.worker.tasks.runtime.agent import run_agent_turn

log = structlog.get_logger(__name__)
router = APIRouter(tags=["agent_chat"])

# Redis key namespace for this route's turn ceiling. Deliberately NOT the widget
# route's "rate" prefix: an integration driving the API must not be able to starve
# the tenant's live widget customers out of their 60/min, or be starved by them
# (BACKLOG 7.4 — same reasoning the feedback route already keys separately).
_CHAT_RATE_LIMIT_PREFIX = "rate:apichat"


# ---------------------------------------------------------------------------
# Helper — conversation ownership validation (T-04-04-05)
# ---------------------------------------------------------------------------


def _validate_conv_owner(
    encrypted_conn_str: bytes,
    conversation_id: UUID,
    agent_id: UUID,
) -> bool:
    """Return True if conversation_id belongs to agent_id in the tenant DB.

    Uses psycopg2 directly because the tenant DB is a separate Neon project.
    Security (T-04-04-05): WHERE id=%s AND agent_id=%s prevents cross-agent
    conversation hijacking via a guessed UUID.

    Returns:
        True if row exists, False otherwise.
    """
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
# POST /agents/{agent_id}/chat
# ---------------------------------------------------------------------------


@router.post(
    "/agents/{agent_id}/chat",
    status_code=202,
    response_model=AgentChatResponse,
)
async def post_agent_chat(
    agent_id: UUID,
    body: AgentChatRequest,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
    redis_client=Depends(get_async_redis),
) -> AgentChatResponse:
    """Accept a user message and dispatch an agent turn job.

    Validates agent ownership and readiness, optionally validates
    conversation_id ownership, creates a Job row in the control DB,
    dispatches run_agent_turn to the runtime queue, and returns 202 Accepted.

    Rate limit: 60 turns/min per agent_id. Budget: tenant daily ceiling
    (settings.TENANT_DAILY_BUDGET_USD). Both ceilings match POST /widget/{id}/chat
    in value — an authenticated caller spends the tenant's Anthropic key exactly as
    a widget visitor does (BACKLOG 7.4).

    The ORDER differs deliberately. The widget route rate-limits first because a
    visitor has no tenant identity to check. Here the caller does, and agent_id is
    a public identifier, so ownership is established before anything is charged to
    the agent's bucket: otherwise any valid API key could drain a foreign tenant's
    window.

    Security: body.message is NEVER logged (T-04-03-05).

    Returns:
        AgentChatResponse (202 Accepted).

    Raises:
        404 if agent not found or not owned by the authenticated tenant.
        409 if agent is not in status == 'ready'.
        429 if the agent's 60/min turn ceiling is exceeded, or the tenant's daily
            budget is exhausted.
        403 if conversation_id provided but does not belong to this agent.
    """
    # ------------------------------------------------------------------
    # 1. Validate agent ownership (T-02-06-01 pattern)
    # ------------------------------------------------------------------
    result = await db.execute(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.tenant_id == tenant.id,
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
    # 1b. Rate limit: 60 turns/min per agent_id — AFTER the ownership check.
    #
    #     The bucket is keyed by agent_id, and agent_id is public: it is in the
    #     embed snippet every visitor downloads and in the unauthenticated
    #     GET /widget/{agent_id}/config. Counting a request before establishing
    #     that the caller owns the agent therefore lets ANY valid API key spend
    #     a victim tenant's 60/min — 61 requests, 61 404s, and the victim's own
    #     integration is 429'd for the rest of the window. That is the exact
    #     starvation the separate bucket exists to prevent, aimed cross-tenant.
    #
    #     Guarding first would not have saved a database round trip in any case:
    #     Depends(get_current_tenant) has already run a SELECT against the
    #     control DB plus an argon2 verify before this function body starts
    #     (app/api/deps.py). "Before any DB access" was never true here.
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
    # 2. Validate conversation_id ownership (T-04-04-05)
    # ------------------------------------------------------------------
    if body.conversation_id is not None:
        owned = _validate_conv_owner(
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
    # 3. Create job row in control DB
    # ------------------------------------------------------------------
    job = Job(
        tenant_id=tenant.id,
        agent_id=agent.id,
        kind="agent_turn",
        status="pending",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # ------------------------------------------------------------------
    # 3b. Budget guard — tenant daily ceiling, checked BEFORE dispatching the
    #     Celery task. Placed after the job row for step-for-step parity with
    #     POST /widget/{id}/chat: one shape for both routes is what stops the two
    #     ceilings drifting apart again.
    # ------------------------------------------------------------------
    budget_ok = await check_and_increment_budget(
        str(tenant.id),
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
    # 4. Dispatch run_agent_turn to runtime queue.
    #    Only IDs + message passed; NO conn_str in task args (CLAUDE.md rule 4).
    #    body.message is NOT logged — security constraint (T-04-03-05).
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
        "agent_chat.dispatched",
        agent_id=str(agent.id),
        job_id=str(job.id),
    )

    # ------------------------------------------------------------------
    # 5. Return 202 Accepted
    #    events_url uses the public widget SSE path (R-03 resolution) —
    #    admin callers can also use /jobs/{id}/events with X-API-Key.
    # ------------------------------------------------------------------
    return AgentChatResponse(
        job_id=job.id,
        status="pending",
        events_url=f"/widget/jobs/{job.id}/events",
        conversation_id=body.conversation_id,
    )


# ---------------------------------------------------------------------------
# GET /agents/{agent_id}/conversations
# ---------------------------------------------------------------------------


@router.get(
    "/agents/{agent_id}/conversations",
    response_model=ConversationListResponse,
)
async def get_agent_conversations(
    agent_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> ConversationListResponse:
    """Return the 50 most-recent conversations for the specified agent.

    Validates agent ownership, then queries the tenant DB via psycopg2.
    Each conversation includes escalated flag and message_count via subquery.

    Returns:
        ConversationListResponse with up to 50 ConversationListItem entries.

    Raises:
        404 if agent not found or not owned by the authenticated tenant.
    """
    # ------------------------------------------------------------------
    # 1. Validate agent ownership (T-02-06-01 pattern)
    # ------------------------------------------------------------------
    result = await db.execute(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.tenant_id == tenant.id,
            Agent.deleted_at.is_(None),
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # ------------------------------------------------------------------
    # 2. Query tenant DB for conversations (psycopg2 — separate Neon project)
    # ------------------------------------------------------------------
    conn_str = fernet_decrypt(require_ciphertext(agent.neon_connection_string, "agents.neon_connection_string"))
    conn = psycopg2.connect(conn_str, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    c.id,
                    c.created_at,
                    COALESCE((c.metadata->>'escalated')::bool, false),
                    (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id)
                FROM conversations c
                WHERE c.agent_id = %s
                ORDER BY c.created_at DESC
                LIMIT 50
                """,
                (str(agent.id),),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    # ------------------------------------------------------------------
    # 3. Map rows → ConversationListItem
    # ------------------------------------------------------------------
    items = [
        ConversationListItem(
            id=row[0],
            created_at=row[1],
            escalated=bool(row[2]),
            message_count=int(row[3]),
        )
        for row in rows
    ]

    return ConversationListResponse(conversations=items)
