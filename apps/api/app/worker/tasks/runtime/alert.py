"""run_alert_check_beat + run_alert_check — daily metric threshold check (M10 OPS-04)."""
from __future__ import annotations

import structlog
from sqlalchemy import select

from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.services.alert_service import check_and_write_alerts
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=1,
    default_retry_delay=60,
    queue="runtime",
    name="app.worker.tasks.runtime.alert.run_alert_check_beat",
)
def run_alert_check_beat(self) -> dict:
    """Beat-triggered: fan out run_alert_check per deployed agent."""
    with get_sync_db() as db:
        agents = db.execute(
            select(Agent).where(Agent.is_deployed == True)  # noqa: E712
        ).scalars().all()
    dispatched = 0
    for agent in agents:
        run_alert_check.apply_async(kwargs={"agent_id": str(agent.id)}, queue="runtime")
        dispatched += 1
    return {"dispatched": dispatched}


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=30,
    queue="runtime",
    name="app.worker.tasks.runtime.alert.run_alert_check",
)
def run_alert_check(self, agent_id: str) -> dict:
    """Per-agent daily alert check.

    conn_str is decrypted here from the control DB and passed to check_and_write_alerts
    for tenant-DB queries (eval_results, red_team_runs). NEVER passed as task arg (CTL-08).
    """
    try:
        with get_sync_db() as db:
            agent = db.get(Agent, agent_id)
            if agent is None:
                return {"skipped": True}
            if not agent.neon_connection_string:
                log.info("run_alert_check.no_conn_str", agent_id=agent_id)
                return {"skipped": True, "reason": "no_conn_str"}
            conn_str = fernet_decrypt(agent.neon_connection_string)
            new_alerts = check_and_write_alerts(
                agent_id=agent_id,
                conn_str=conn_str,
                agent_name=agent.name,
                tenant_id=str(agent.tenant_id),
                db=db,
            )
        log.info("run_alert_check.complete", agent_id=agent_id, new_alerts=len(new_alerts))
        return {"agent_id": agent_id, "new_alerts": len(new_alerts)}
    except Exception as exc:
        log.error("run_alert_check.failed", agent_id=agent_id, error=str(exc))
        if self.request.retries >= self.max_retries:
            raise
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)
