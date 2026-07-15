"""Metrics routes for W Chats Phase 21 (OPS-03).

Queries tenant DB (turn_metrics, message_feedback) for the "Live" region's
headline KPIs: containment, deflection, escalation rate, CSAT, thumbs-down
rate, p95 latency, and cost-per-session over a window. All computation lives
in app/services/metrics_service.py; this route only owns IDOR + conn_str
resolution + the asyncio.to_thread boundary.

Routes:
    GET /agents/{agent_id}/metrics — aggregate KPIs over a window (OPS-03)

Architecture:
    - turn_metrics/message_feedback live in the TENANT DB (per-Neon-project),
      not the control DB — same tier as eval_runs/eval_results (evals.py).
    - Route fetches agent from control DB (get_async_db) for IDOR check only.
    - Tenant DB aggregation goes through psycopg2 with asyncio.to_thread() to
      avoid blocking the FastAPI event loop (D-30 pattern — same as evals.py).

NOTE: this router is extended in a later plan (21-04) with
GET /agents/{id}/retrieval-health — left structured (single router, single
IDOR-guarded GET-per-agent-resource shape) for that addition.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_tenant
from app.core.database import get_async_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.services.metrics_service import compute_agent_metrics

router = APIRouter(tags=["metrics"])
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Route: GET /agents/{agent_id}/metrics — aggregate KPIs over a window
# ---------------------------------------------------------------------------


@router.get("/agents/{agent_id}/metrics")
async def get_agent_metrics(
    agent_id: UUID,
    window_days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Return containment/deflection/escalation/CSAT/thumbs/p95/cost KPIs for an agent.

    Security:
        Fetches agent from control DB and checks agent.tenant_id == tenant.id
        (IDOR prevention, T-21-02-01). Returns 404 for unknown agents or agents
        belonging to a different tenant — never 403 (no existence leak).

    Response shape:
        {containment, deflection, escalation_rate, csat_avg, thumbs_down_rate,
         p95_latency_ms, cost_per_session, sample_size, window_days}
        Every ratio/average/percentile field is either a float or the literal
        string "not_tracked" when zero underlying rows exist in the window
        (honest-empty-state discipline — never a fabricated 0.0).
    """
    # 1. Fetch agent from control DB (only metadata — not tenant DB)
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 2. IDOR check — agent must belong to the authenticated tenant
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 3. Guard: agent must have a tenant DB configured
    if not agent.neon_connection_string:
        raise HTTPException(status_code=404, detail="Agent database not provisioned")

    # 4. Decrypt connection string at runtime — never stored, never logged (T-02-01)
    conn_str = fernet_decrypt(agent.neon_connection_string)

    # 5. Compute aggregates in a thread pool to avoid blocking the event loop
    metrics = await asyncio.to_thread(compute_agent_metrics, conn_str, window_days)

    log.info(
        "agent_metrics.ok",
        agent_id=str(agent_id),
        tenant_id=str(tenant.id),
        window_days=window_days,
        sample_size=metrics.get("sample_size"),
    )
    return metrics
