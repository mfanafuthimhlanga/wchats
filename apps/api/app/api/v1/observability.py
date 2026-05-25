"""FastAPI observability routes — GET/resolve alerts (M10 OPS-04)."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_tenant
from app.models.agent import Agent
from app.models.alert import Alert

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/agents", tags=["observability"])


@router.get("/{agent_id}/alerts")
async def list_alerts(
    agent_id: UUID,
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_async_db),
):
    """List unresolved alerts for an agent (OPS-04)."""
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.tenant_id != tenant.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    result = await db.execute(
        select(Alert)
        .where(Alert.agent_id == agent_id, Alert.resolved_at == None)  # noqa: E711
        .order_by(Alert.triggered_at.desc())
    )
    alerts = result.scalars().all()
    return [
        {
            "id": str(a.id),
            "alert_type": a.alert_type,
            "severity": a.severity,
            "message": a.message,
            "triggered_at": a.triggered_at.isoformat(),
        }
        for a in alerts
    ]


@router.post("/{agent_id}/alerts/{alert_id}/resolve")
async def resolve_alert(
    agent_id: UUID,
    alert_id: UUID,
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_async_db),
):
    """Mark an alert as resolved."""
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.tenant_id != tenant.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    alert = await db.get(Alert, alert_id)
    if alert is None or alert.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="Alert not found")
    # WR-01: defense-in-depth direct tenant ownership check.
    # The agent.tenant_id check above (lines 57-58) is the primary guard;
    # this direct check eliminates the TOCTOU window if agent ownership
    # were ever transferred or the prior check refactored away.
    if alert.tenant_id is not None and alert.tenant_id != tenant.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    alert.resolved_at = datetime.now(timezone.utc)
    await db.commit()
    return {"resolved": True}
