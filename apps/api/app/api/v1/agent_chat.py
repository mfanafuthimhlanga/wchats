"""
Agent chat routes for W Chats M4.

POST /agents/{agent_id}/chat         — dispatch a single agent turn (202 Accepted)
GET  /agents/{agent_id}/conversations — list recent conversations for an agent

Security:
    - Requires X-API-Key on all routes (get_current_tenant dependency).
    - Agent validated by Agent.id AND Agent.tenant_id — T-02-06-01 pattern.
    - Agent must have status == 'ready' before dispatch.
    - conversation_id ownership validated against tenant DB (T-04-04-05).
    - message text is NEVER logged (T-04-03-05).
    - conn_str is decrypted at runtime, NEVER passed in task args (CLAUDE.md rule 4).

Queue: runtime (CLAUDE.md: both Celery queues always present).
"""

import structlog
import psycopg2
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_tenant
from app.core.database import get_async_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.models.job import Job
from app.models.tenant import Tenant
from app.schemas.agent_chat import (
    AgentChatRequest,
    AgentChatResponse,
    ConversationListItem,
    ConversationListResponse,
)
from app.worker.tasks.runtime.agent import run_agent_turn

log = structlog.get_logger(__name__)
router = APIRouter(tags=["agent_chat"])


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
) -> AgentChatResponse:
    """Accept a user message and dispatch an agent turn job.

    Validates agent ownership and readiness, optionally validates
    conversation_id ownership, creates a Job row in the control DB,
    dispatches run_agent_turn to the runtime queue, and returns 202 Accepted.

    Security: body.message is NEVER logged (T-04-03-05).

    Returns:
        AgentChatResponse (202 Accepted).

    Raises:
        404 if agent not found or not owned by the authenticated tenant.
        409 if agent is not in status == 'ready'.
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
    # 2. Validate conversation_id ownership (T-04-04-05)
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
    conn_str = fernet_decrypt(agent.neon_connection_string)
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
