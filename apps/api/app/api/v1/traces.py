"""Trace/bench routes for W Chats Phase 21 (OPS-09/10).

Failure-triage bench: surfaces failing production turns (Gatekeeper 'fail' /
Auditor 'ungrounded'/'partial' verdicts sourced from job_events) with the
customer turn, agent turn, and judge rationale, and lets an operator grade
each trace filed | held | dismissed. A 'filed' grade is irrevocable
(TERRARIUM law) — POST .../grade returns 409 on any attempt to re-grade a
filed trace.

Routes:
    GET  /agents/{agent_id}/traces?status=failing         — list failing traces (OPS-09)
    POST /agents/{agent_id}/traces/{trace_id}/grade        — grade a trace (OPS-10)

Architecture:
    - job_events (judge verdicts + operator grades) lives in the CONTROL DB.
    - messages (customer/agent turn text) lives in the TENANT DB.
    - No cross-DB SQL join is possible — bench_service does the correlation
      in Python (RESEARCH.md Pattern 2). See bench_service.py module docstring.
    - IDOR pattern copied verbatim from evals.py: agent.tenant_id == tenant.id,
      404 (not 403) on mismatch — never leaks agent existence to a foreign tenant.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_tenant
from app.core.database import get_async_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.services import bench_service

router = APIRouter(tags=["traces"])
log = structlog.get_logger(__name__)


class GradeTraceRequest(BaseModel):
    """POST /traces/{trace_id}/grade request body.

    grade is a closed enum — Pydantic Literal validation returns 422 on any
    other value (T-21-05-03: grade enum injection prevention).
    """

    grade: Literal["filed", "held", "dismissed"]


# ---------------------------------------------------------------------------
# Shared IDOR + provisioning guard (copied from evals.py's inline pattern)
# ---------------------------------------------------------------------------


async def _get_owned_agent(agent_id: UUID, db: AsyncSession, tenant: Tenant) -> Agent:
    """Fetch agent and enforce IDOR + tenant-DB-provisioned guards.

    Security:
        1. Agent must exist.
        2. agent.tenant_id must equal tenant.id — 404 (not 403) on mismatch,
           so a foreign-tenant caller cannot distinguish "doesn't exist" from
           "belongs to someone else" (no existence leak).
        3. Agent must have a tenant DB provisioned.
    """
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent.neon_connection_string:
        raise HTTPException(status_code=404, detail="Agent database not provisioned")
    return agent


# ---------------------------------------------------------------------------
# Route 1: GET /agents/{agent_id}/traces — list failing traces (OPS-09)
# ---------------------------------------------------------------------------


@router.get("/agents/{agent_id}/traces")
async def list_traces(
    agent_id: UUID,
    status: str = "failing",
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Return failing production traces for an agent (OPS-09).

    Security:
        Fetches agent from control DB and checks agent.tenant_id == tenant.id
        (IDOR prevention). Returns 404 for unknown agents or agents belonging
        to a different tenant.

    Response shape:
        {"traces": [{trace_id, verdict, judge_rationale, customer_turn,
                     agent_turn, conversation_id, graded_status}],
         "tally": {"filed": int, "held": int, "dismissed": int}}
    """
    agent = await _get_owned_agent(agent_id, db, tenant)

    if status != "failing":
        raise HTTPException(status_code=400, detail="Only status=failing is supported")

    conn_str = fernet_decrypt(agent.neon_connection_string)

    result = await bench_service.list_failing_traces(db, conn_str, str(agent_id))

    log.info(
        "list_traces.ok",
        agent_id=str(agent_id),
        tenant_id=str(tenant.id),
        trace_count=len(result["traces"]),
    )
    return result


# ---------------------------------------------------------------------------
# Route 2: POST /agents/{agent_id}/traces/{trace_id}/grade — grade a trace (OPS-10)
# ---------------------------------------------------------------------------


@router.post("/agents/{agent_id}/traces/{trace_id}/grade")
async def grade_trace(
    agent_id: UUID,
    trace_id: UUID,
    body: GradeTraceRequest,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Grade a trace filed | held | dismissed (OPS-10).

    Security:
        Same IDOR prevention as list_traces. bench_service.grade_trace also
        confirms trace_id's flagged judge event payload has agent_id ==
        the path agent_id (T-21-05-01) — mismatch raises TraceNotFoundError,
        mapped to 404 here, before any write is attempted.

    TERRARIUM law (T-21-05-02):
        A trace already graded 'filed' is irrevocable. Re-grading a filed
        trace raises bench_service.TraceAlreadyFiledError, mapped to 409.

    Response shape:
        {"trace_id": str, "grade": str, "tally": {"filed": int, "held": int, "dismissed": int}}
    """
    await _get_owned_agent(agent_id, db, tenant)

    try:
        result = await bench_service.grade_trace(db, str(agent_id), str(trace_id), body.grade)
    except bench_service.TraceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except bench_service.TraceAlreadyFiledError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if body.grade == "filed":
        # OPS-11: filing a trace is the flywheel's promotion ceremony — it must
        # become a permanent regression scenario. Lazy import keeps the worker
        # task graph off this route module's import path (same convention as the
        # transactional dispatcher). Only IDs cross the task boundary; conn_str
        # is decrypted inside the task at runtime (CLAUDE.md rule 4).
        from app.worker.tasks.runtime.bench import promote_trace_to_scenario

        try:
            promote_trace_to_scenario.apply_async(args=[str(agent_id), str(trace_id)])
        except Exception as exc:  # broker unreachable — the grade is already committed
            # Do NOT fail the request: the filing itself succeeded and the task is
            # idempotent, so it can be safely re-dispatched. Log loudly so a silently
            # un-promoted trace is visible rather than lost.
            log.error(
                "grade_trace.promote_dispatch_failed",
                agent_id=str(agent_id),
                trace_id=str(trace_id),
                error=str(exc),
            )

    log.info(
        "grade_trace.ok",
        agent_id=str(agent_id),
        trace_id=str(trace_id),
        tenant_id=str(tenant.id),
        grade=body.grade,
    )
    return result
