"""
run_weekly_digest_beat + run_weekly_digest — M10 OPS-02 weekly owner digest.

Beat dispatcher: fans out per agent select_beat_fanout_agents() returns.
Per-agent task: idempotency via digest_runs 7-day window. acks_late=True on both.
conn_str NEVER in task args (CTL-08).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import text

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.log_bounds import log_failure
from app.core.security import fernet_decrypt
from app.models.agent import Agent, select_beat_fanout_agents
from app.services.digest_service import _collect_digest_stats, send_digest_email
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=1,
    default_retry_delay=60,
    queue="runtime",
    name="app.worker.tasks.runtime.digest.run_weekly_digest_beat",
)
def run_weekly_digest_beat(self) -> dict:
    """Beat-triggered: fan out run_weekly_digest per deployed, ready agent."""
    if not settings.DIGEST_ENABLED:
        return {"dispatched": 0}
    with get_sync_db() as db:
        agents = db.execute(
            select_beat_fanout_agents()
        ).scalars().all()
    dispatched = 0
    for agent in agents:
        run_weekly_digest.apply_async(kwargs={"agent_id": str(agent.id)}, queue="runtime")
        dispatched += 1
    log.info("run_weekly_digest_beat.dispatched", count=dispatched)
    return {"dispatched": dispatched}


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=30,
    queue="runtime",
    name="app.worker.tasks.runtime.digest.run_weekly_digest",
)
def run_weekly_digest(self, agent_id: str) -> dict:
    """Per-agent weekly digest: collect stats, email owner, record digest_run."""
    since = datetime.now(timezone.utc) - timedelta(days=7)
    try:
        with get_sync_db() as db:
            existing = db.execute(
                text(
                    "SELECT id FROM digest_runs WHERE agent_id = :agent_id "
                    "AND sent_at >= :since LIMIT 1"
                ),
                {"agent_id": agent_id, "since": since.isoformat()},
            ).fetchone()
            if existing:
                log.info("run_weekly_digest.idempotent_skip", agent_id=agent_id)
                return {"status": "already_sent"}

            agent = db.get(Agent, agent_id)
            if agent is None or not agent.neon_connection_string:
                return {"status": "skipped_no_agent"}
            conn_str = fernet_decrypt(agent.neon_connection_string)
            stats = _collect_digest_stats(agent_id, conn_str, db)

            # WR-02: commit the digest_runs row FIRST as the idempotency anchor.
            # If send_digest_email fails, the committed row prevents duplicate sends
            # on retry (the idempotency guard at the top of this function fires).
            # Email is fire-and-forget; the row ensures at-most-once delivery.
            db.execute(
                text(
                    # CAST(:payload AS jsonb), never :payload::jsonb — SQLAlchemy's
                    # bindparam regex backtracks one character off `:payload::jsonb`
                    # and silently binds `payloa`, so the literal `:` reached
                    # Postgres and this INSERT raised on EVERY run. Because it is
                    # the WR-02 idempotency anchor committed before the send, the
                    # outer except retried 3x and re-raised: no digest_runs row was
                    # ever written and send_digest_email was never reached.
                    # tests/unit/test_sql_paramstyle_collisions.py gates the class.
                    "INSERT INTO digest_runs (agent_id, payload) "
                    "VALUES (:agent_id, CAST(:payload AS jsonb))"
                ),
                {"agent_id": agent_id, "payload": json.dumps(stats)},
            )
            db.commit()
            send_digest_email(agent.name, agent_id, stats)
    except Exception as exc:
        log_failure(log, "run_weekly_digest.failed", exc, level="error", agent_id=agent_id)
        if self.request.retries >= self.max_retries:
            raise
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)
    return {"agent_id": agent_id, "status": "sent"}
