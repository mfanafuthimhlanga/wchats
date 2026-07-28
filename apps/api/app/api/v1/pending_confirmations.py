"""Pending-confirmation routes for W Chats Phase 22 (ACT-07).

Routes:
    GET  /agents/{agent_id}/pending-confirmations                              — the approver's triage queue
    POST /agents/{agent_id}/pending-confirmations/{confirmation_id}/resolve    — the atomic resolve claim

Purpose: `pending_confirmations` rows are written by two code paths (the Actor
seam's require_human verdict, and confirm_action) and read by none. These two
routes give them a resolver — one that claims the row in the database rather
than in application logic, enforces expiry inside that same statement, and
hands the actual provider call to a `runtime`-queue Celery task, because a
FastAPI request handler must never make it (the provider-adapter resolution
function's own docstring forbids a route-handler call site).

Security:
    Both routes require X-API-Key or Bearer auth via get_current_tenant.
    IDOR is prevented by verifying agent.tenant_id == tenant.id on every
    route, 404 (not 403) on both the missing-agent and the foreign-agent
    branch — the house convention copied verbatim from
    capability_envelopes.py's own copy of _get_owned_agent, so a foreign
    agent is indistinguishable from a missing one.

    The atomic claim (`UPDATE ... WHERE resolved_at IS NULL ... RETURNING`)
    is the ENTIRE concurrency control for the resolve route — no read-then-
    write check backs it up, and none is needed: the database decides the
    single winner. Expiry is forced inside that same statement (OD-2, lazy,
    no sweep task) with a strict `expires_at < now()` comparison, so the
    boundary instant belongs to the approver and a null deadline never
    silently becomes a refusal.

    The claim is committed BEFORE the execution task is dispatched (OD-6,
    overturning `22-PATTERNS.md`'s shown ordering): a task dispatched before
    the claim is durable could be picked up by a worker reading the row in
    its pre-claim state. The one remaining window — the dispatch call itself
    failing after a durable claim — is the accepted, named residual
    T-22-ACT-09.

    Neither route imports the provider-adapter resolution function or its
    module. The Celery task this module dispatches by id (never a route)
    does the one thing this module structurally must not.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_tenant
from app.core.database import get_async_db
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.schemas.pending_confirmation import (
    PendingConfirmationListResponse,
    PendingConfirmationResolve,
    PendingConfirmationResponse,
)
from app.services.transactional.schemas import SKILL_INPUT_MODELS

router = APIRouter(tags=["pending-confirmations"])
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Shared IDOR guard (copied byte-identically from capability_envelopes.py's
# own copy of _get_owned_agent, itself copied from prompt_versions.py — the
# single correct form, confirmed to have not drifted between its two existing
# copies by 22-PATTERNS.md)
# ---------------------------------------------------------------------------


async def _get_owned_agent(agent_id: UUID, db: AsyncSession, tenant: Tenant) -> Agent:
    """Fetch agent and enforce IDOR (404, not 403, on mismatch — no existence leak)."""
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


# ---------------------------------------------------------------------------
# Execution-outcome lookup (OD-3) — module-private, one named testable unit
# ---------------------------------------------------------------------------


async def _execution_outcome_for(
    db: AsyncSession,
    agent_id: UUID,
    skill: str,
    arguments: dict | None,
) -> tuple[str | None, str | None, object | None]:
    """Read-time execution-outcome lookup against tool_calls_audit (OD-3).

    No new column, no 0020 migration — the resolver already writes exactly
    one tool_calls_audit row on every terminal outcome (22-02, T-22-ACT-06),
    so this reads that row instead of maintaining a second, independently
    driftable source of the same fact.

    Matched on FOUR predicates: agent_id, skill, the arguments' idempotency
    key (extracted from the row's stored JSONB with the ->> text operator),
    and actor_decision = 'approved_by_human'. That fourth predicate is the
    discriminator and is NOT optional: the original approval-required audit
    row written when this confirmation was first created shares agent_id,
    skill AND arguments with the resolver's own row — dropping the
    actor-decision predicate would let this lookup match that original row
    and report an execution that never happened.

    Returns:
        (execution_outcome, execution_error, executed_at) — all None when no
        matching audit row exists yet (the honest "awaiting execution" state:
        the task has not run, or the dispatch itself failed, T-22-ACT-09).
        ("executed", None, created_at) when the matched row's error is NULL.
        ("not_executed", raw_error_string, created_at) otherwise.
    """
    if not isinstance(arguments, dict):
        return None, None, None
    idempotency_key = arguments.get("idempotency_key")
    if not idempotency_key:
        return None, None, None

    result = await db.execute(
        sa_text(
            "SELECT error, created_at FROM tool_calls_audit "
            "WHERE agent_id = :agent_id "
            "AND skill = :skill "
            "AND arguments->>'idempotency_key' = :idempotency_key "
            "AND actor_decision = 'approved_by_human' "
            "ORDER BY created_at DESC "
            "LIMIT 1"
        ),
        {"agent_id": str(agent_id), "skill": skill, "idempotency_key": idempotency_key},
    )
    row = result.mappings().first()
    if row is None:
        return None, None, None
    if row["error"] is None:
        return "executed", None, row["created_at"]
    return "not_executed", row["error"], row["created_at"]


def _row_to_response(row: dict, execution_outcome: str | None, execution_error: str | None, executed_at: object | None) -> PendingConfirmationResponse:
    """Build one PendingConfirmationResponse from a scripted/claimed row dict."""
    return PendingConfirmationResponse(
        id=row["id"],
        skill=row["skill"],
        arguments=row["arguments"],
        requested_at=row["requested_at"],
        expires_at=row["expires_at"],
        resolved_at=row["resolved_at"],
        resolution=row["resolution"],
        execution_outcome=execution_outcome,
        execution_error=execution_error,
        executed_at=executed_at,
    )


# ---------------------------------------------------------------------------
# Route 1: GET /agents/{agent_id}/pending-confirmations — the approver's queue
# ---------------------------------------------------------------------------


@router.get("/agents/{agent_id}/pending-confirmations")
async def list_pending_confirmations(
    agent_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> PendingConfirmationListResponse:
    """Return the triage queue: every unresolved row, then every row resolved
    within the last 24 hours. Rows resolved more than 24 hours ago are
    excluded — this is a triage queue, not an audit trail.

    Ordering is total and stable:
      - unresolved rows first, by deadline ascending with nulls last, then id
        ascending;
      - then recently-resolved rows, by resolution time descending, then id
        ascending.
    The id tiebreak in both groups is load-bearing, not decoration: without
    it two rows sharing a timestamp can swap position between requests, and a
    queue that reorders under the approver's cursor is a mis-click waiting to
    happen.
    """
    await _get_owned_agent(agent_id, db, tenant)

    result = await db.execute(
        sa_text(
            "SELECT id, skill, arguments, requested_at, expires_at, resolved_at, resolution "
            "FROM pending_confirmations "
            "WHERE agent_id = :agent_id "
            "AND (resolved_at IS NULL OR resolved_at >= now() - interval '24 hours') "
            "ORDER BY "
            "    (resolved_at IS NULL) DESC, "
            "    CASE WHEN resolved_at IS NULL THEN expires_at END ASC NULLS LAST, "
            "    resolved_at DESC, "
            "    id ASC"
        ),
        {"agent_id": str(agent_id)},
    )
    rows = [dict(r) for r in result.mappings().all()]

    confirmations: list[PendingConfirmationResponse] = []
    for row in rows:
        execution_outcome = execution_error = executed_at = None
        # Skip the lookup entirely for a row whose resolution is not the
        # approved value — a rejection has no execution and no denial code,
        # and asking for one would be the conflation 22-UI-SPEC.md §
        # Contradictions corrects.
        if row["resolution"] == "approved":
            execution_outcome, execution_error, executed_at = await _execution_outcome_for(
                db, agent_id, row["skill"], row["arguments"]
            )
        confirmations.append(_row_to_response(row, execution_outcome, execution_error, executed_at))

    log.info(
        "list_pending_confirmations.ok",
        agent_id=str(agent_id),
        tenant_id=str(tenant.id),
        row_count=len(confirmations),
    )
    return PendingConfirmationListResponse(confirmations=confirmations)


# ---------------------------------------------------------------------------
# Route 2: POST .../resolve — the atomic claim
# ---------------------------------------------------------------------------


@router.post("/agents/{agent_id}/pending-confirmations/{confirmation_id}/resolve")
async def resolve_pending_confirmation(
    agent_id: UUID,
    confirmation_id: UUID,
    body: PendingConfirmationResolve,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> PendingConfirmationResponse:
    """Claim a pending_confirmations row and, only on a durable approved
    claim of a mutating skill, dispatch its execution.

    Order of operations matters and is the entire security contract of this
    route:

    1. IDOR first — before the confirmation id or the body is touched.
    2. The atomic claim: a single UPDATE ... WHERE resolved_at IS NULL ...
       RETURNING. The database decides the single winner. A second caller
       gets nothing back and a 409 that does not distinguish "already
       resolved" from "does not exist" from "not yours" — safe because it
       only fires after the ownership-guarded check has already passed, so
       it leaks nothing across tenants.
    3. The expiry check is inside that same statement: a non-null deadline
       strictly less than the database clock forces the resolution to
       'expired' regardless of what the caller requested. The boundary
       instant belongs to the approver (strict <, never <=), and a null
       deadline is never treated as expired.
    4. await db.commit() — the claim must be durable before anything else
       happens.
    5. ONLY THEN, and only when the claimed resolution is 'approved' AND the
       claimed skill is a key of SKILL_INPUT_MODELS, dispatch the execution
       task by confirmation id (OD-6: commit before enqueue, overturning
       22-PATTERNS.md's shown ordering — a task dispatched before the claim
       commits could be picked up by a worker reading the row in its
       pre-claim state). A non-mutating confirmation-skill row (no adapter
       method, no idempotency key) is still resolvable; it simply executes
       nothing, because there was never anything to execute.

    The Celery task import is deliberately local to this function body, not
    module-level: it is the exact patch point Task 3's tests target ("the
    name the route resolves it under"), and it keeps this route free of any
    import-time coupling to the worker task package.
    """
    await _get_owned_agent(agent_id, db, tenant)

    result = await db.execute(
        sa_text(
            "UPDATE pending_confirmations "
            "SET resolved_at = now(), "
            "    resolution = CASE "
            "        WHEN expires_at IS NOT NULL AND expires_at < now() THEN 'expired' "
            "        ELSE :resolution "
            "    END "
            "WHERE id = :confirmation_id AND agent_id = :agent_id AND resolved_at IS NULL "
            "RETURNING id, skill, arguments, requested_at, expires_at, resolved_at, resolution"
        ),
        {
            "confirmation_id": str(confirmation_id),
            "agent_id": str(agent_id),
            "resolution": body.resolution,
        },
    )
    claimed_row = result.mappings().first()
    if claimed_row is None:
        raise HTTPException(
            status_code=409,
            detail="This confirmation was already resolved, does not exist, or does not belong to this agent.",
        )
    claimed = dict(claimed_row)

    await db.commit()

    enqueued = False
    if claimed["resolution"] == "approved" and claimed["skill"] in SKILL_INPUT_MODELS:
        from app.worker.tasks.runtime.confirmations import resolve_approved_confirmation

        resolve_approved_confirmation.delay(str(claimed["id"]))
        enqueued = True

    log.info(
        "resolve_pending_confirmation.ok",
        agent_id=str(agent_id),
        tenant_id=str(tenant.id),
        confirmation_id=str(confirmation_id),
        resolution=claimed["resolution"],
        enqueued=enqueued,
    )
    # execution_outcome is always None here: even a dispatched task has not
    # run yet at response time. The GET queue route is where an approver
    # later reads the real outcome.
    return _row_to_response(claimed, None, None, None)
