"""Celery task: an owner re-tests one red-team finding (runtime queue).

Receives `agent_id` and `finding_id`; the connection string is fetched from the
control DB and decrypted at runtime, never passed in (CLAUDE.md rule 1).

acks_late AND idempotency (rule 2). The guard is the finding row itself: the task
claims it by writing `retest.status = 'running'` in one UPDATE that matches only an
open finding of this agent with no re-test running inside
RETEST_IDEMPOTENCY_WINDOW_MINUTES. A redelivered message or a second click finds
the claim and returns without spending a model call. A claim older than the
window belongs to a dead worker and is taken over.
"""

from __future__ import annotations

import json
import uuid

import psycopg2
import structlog

from app.core.database import get_sync_db
from app.core.log_bounds import log_failure
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.services.red_team_retest import run_retest
from app.worker.celery_app import celery_app
from app.worker.tasks.runtime.red_team import _build_probe_fn, _run_ledger

log = structlog.get_logger(__name__)

#: How long a running re-test holds its claim. One attacker sequence spends at most
#: RED_TEAM_ATTEMPT_BUDGET_S (240 s) plus the victim turns inside it.
RETEST_IDEMPOTENCY_WINDOW_MINUTES = 15

#: Claims one open finding of this agent for a re-test, or matches nothing.
_CLAIM_SQL = """
    UPDATE red_team_findings f
    SET retest = jsonb_build_object('id', %s::text, 'status', 'running', 'started_at', now())
    FROM red_team_runs r
    WHERE f.id = %s AND r.id = f.run_id AND r.kind = %s AND f.status = 'open'
      AND (f.retest IS NULL OR f.retest->>'status' <> 'running'
           OR (f.retest->>'started_at')::timestamptz < now() - make_interval(mins => %s))
    RETURNING f.severity, f.probe_message, f.agent_response, f.attack_vector, f.claims
"""

#: Writes the outcome. `resolved` closes the finding; `still_lands` re-grades it to
#: what the re-test found and keeps the grade it had; `inconclusive` changes neither.
_FINISH_SQL = """
    UPDATE red_team_findings
    SET status = CASE WHEN %(outcome)s = 'resolved' THEN 'resolved' ELSE status END,
        severity = COALESCE(%(grade)s, severity),
        retest = retest || %(payload)s::jsonb
            || jsonb_build_object('status', 'complete', 'finished_at', now(),
                                  'previous_severity', severity)
    WHERE id = %(finding_id)s AND retest->>'id' = %(retest_id)s AND status = 'open'
"""

_FAIL_SQL = """
    UPDATE red_team_findings
    SET retest = retest || jsonb_build_object('status', 'failed', 'error_type', %s::text)
    WHERE id = %s AND retest->>'id' = %s
"""


def _claim(conn, retest_id: str, finding_id: str, agent_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(_CLAIM_SQL, (retest_id, finding_id, f"m7:{agent_id}", RETEST_IDEMPOTENCY_WINDOW_MINUTES))
        row = cur.fetchone()
    conn.commit()
    if row is None:
        return None
    return dict(zip(("severity", "probe_message", "agent_response", "attack_vector", "claims"), row))


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=0,
    queue="runtime",
    name="app.worker.tasks.runtime.red_team_retest.retest_red_team_finding",
)
def retest_red_team_finding(self, agent_id: str, finding_id: str) -> dict:
    """Replay one finding's recorded attack against the agent and record the outcome.

    Returns:
        {"finding_id", "retest_id", "outcome"}   when the re-test ran.
        {"status": "not_claimed"}                 when the finding is not open for this
                                                  agent or a re-test already holds it.
        {}                                        when the agent is gone or unconfigured.
    """
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        if agent is None or not agent.neon_connection_string:
            log.error("retest_red_team_finding.agent_not_found_or_unconfigured", agent_id=agent_id)
            return {}
        conn_str = fernet_decrypt(agent.neon_connection_string)
        tenant_id = str(agent.tenant_id)

    retest_id = str(uuid.uuid4())
    conn = psycopg2.connect(conn_str, connect_timeout=10)
    try:
        finding = _claim(conn, retest_id, finding_id, agent_id)
        if finding is None:
            log.info("retest_red_team_finding.not_claimed", agent_id=agent_id, finding_id=finding_id)
            return {"status": "not_claimed"}
        try:
            outcome = run_retest(
                finding,
                _build_probe_fn(agent, conn_str, tenant_id, retest_id),
                ledger=_run_ledger(tenant_id, agent_id, retest_id, conn_str),
            )
        except Exception as exc:
            log_failure(log, "retest_red_team_finding.failed", exc, agent_id=agent_id, finding_id=finding_id)
            with conn.cursor() as cur:
                cur.execute(_FAIL_SQL, (type(exc).__name__, finding_id, retest_id))
            conn.commit()
            raise
        with conn.cursor() as cur:
            cur.execute(_FINISH_SQL, {
                "outcome": outcome.outcome,
                "grade": outcome.grade if outcome.outcome == "still_lands" else None,
                "payload": json.dumps(outcome.payload()),
                "finding_id": finding_id,
                "retest_id": retest_id,
            })
        conn.commit()
    finally:
        conn.close()
    log.info(
        "retest_red_team_finding.complete", agent_id=agent_id, finding_id=finding_id,
        outcome=outcome.outcome, grade=outcome.grade,
    )
    return {"finding_id": finding_id, "retest_id": retest_id, "outcome": outcome.outcome}
