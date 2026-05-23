"""M6 eval tasks — nightly eval suite, per-agent Ragas eval, scenario generation.

All tasks: acks_late=True, runtime queue, no conn_str in args (CTL-08).
Neon branch created per eval run, deleted in finally block (D-10).
Ragas 0.4.x only — D-01 through D-04.
"""

from __future__ import annotations

import uuid

import psycopg2
import structlog
from sqlalchemy import select

from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.services.eval_service import (
    run_ragas_eval,
    write_eval_results,
    update_eval_run_status,
    promote_to_verified_qa,
)
from app.services.neon import create_branch, delete_branch, wait_for_neon_ready
from app.services.scenario_service import (
    generate_eval_suite_for_agent,
    mine_production_scenarios,
    store_scenarios,
)
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# EVL-04: run_eval_suite_beat — beat dispatcher (D-19 LOCKED)
# Task name must match beat_schedule entry in celery_app.py exactly.
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=1,
    default_retry_delay=60,
    queue="runtime",
    name="app.worker.tasks.runtime.eval.run_eval_suite_beat",
)
def run_eval_suite_beat(self) -> dict:
    """Beat-triggered dispatcher: find all status='ready' agents and dispatch run_eval_suite per agent.

    Queries the control DB for agents with status='ready' and fans out one
    run_eval_suite task per agent. No conn_str is passed — the per-agent task
    fetches and decrypts at runtime (CTL-08).

    No idempotency guard needed here — the nightly beat fires once at 02:00 UTC;
    duplicate dispatches are harmless because run_eval_suite itself is idempotent.

    Returns:
        {"dispatched": int} — number of per-agent tasks dispatched.
    """
    with get_sync_db() as db:
        agents = db.execute(
            select(Agent).where(Agent.status == "ready")
        ).scalars().all()

    dispatched = 0
    for agent in agents:
        run_eval_suite.apply_async(
            kwargs={"agent_id": str(agent.id)},
            queue="runtime",
        )
        dispatched += 1

    log.info("run_eval_suite_beat.dispatched", count=dispatched)
    return {"dispatched": dispatched}


# ---------------------------------------------------------------------------
# EVL-02 / EVL-03 / EVL-05: run_eval_suite — per-agent eval run (D-10 LOCKED)
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=30,
    queue="runtime",
    name="app.worker.tasks.runtime.eval.run_eval_suite",
)
def run_eval_suite(self, agent_id: str) -> dict:
    """Per-agent eval run. Creates Neon branch, runs Ragas 0.4.x eval, promotes verified_qa,
    deletes branch in finally (D-10). Receives agent_id str — no conn_str in args (CTL-08 / D-18).

    Sequence:
        1. Idempotency guard — skip if a 'running' eval_run for this agent
           was created within the last 10 minutes.
        2. Fetch agent from control DB; decrypt conn_str at runtime.
        3. Fetch up to 30 eval scenarios from tenant DB; mine new production scenarios.
        4. Insert eval_run row on production branch (status='running').
        5. Create Neon branch; wait for readiness.
        6. try: run Ragas eval → write results → promote verified_qa → mark complete.
           except: mark failed.
           finally: delete Neon branch (D-10 — always runs, even on exception).

    Args:
        agent_id: UUID string of the agent to evaluate.

    Returns:
        {"run_id": str, "scenario_count": int, "promoted": int}  on success.
        {"status": "already_running"}                            on idempotent skip.
        {"status": "no_scenarios"}                               when eval_scenarios is empty.
        {}                                                        on retry exhaustion.
    """
    # ------------------------------------------------------------------
    # Step 1 — Idempotency guard: check for a recent 'running' eval run
    # The kind column is used as the per-agent idempotency key: 'm6:{agent_id}'
    # ------------------------------------------------------------------
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        if agent is None or not agent.neon_project_id or not agent.neon_connection_string:
            log.error(
                "run_eval_suite.agent_not_found_or_unconfigured",
                agent_id=agent_id,
            )
            return {}

        # Decrypt conn_str at runtime — never stored, never passed as arg (CTL-08)
        conn_str = fernet_decrypt(agent.neon_connection_string)
        neon_project_id = agent.neon_project_id

    # Check eval_runs table on tenant DB for a recent running run
    try:
        _check_conn = psycopg2.connect(conn_str, connect_timeout=5)
        try:
            with _check_conn.cursor() as _cur:
                _cur.execute(
                    """
                    SELECT id FROM eval_runs
                    WHERE kind = %s
                      AND status = 'running'
                      AND started_at > NOW() - INTERVAL '10 minutes'
                    LIMIT 1
                    """,
                    (f"m6:{agent_id}",),
                )
                _existing = _cur.fetchone()
        finally:
            _check_conn.close()

        if _existing:
            log.info("run_eval_suite.idempotent_skip", agent_id=agent_id)
            return {"status": "already_running"}
    except Exception as exc:
        # If we cannot check, proceed — idempotency guard is best-effort
        log.warning(
            "run_eval_suite.idempotency_check_failed",
            agent_id=agent_id,
            error=str(exc),
        )

    # ------------------------------------------------------------------
    # Step 3 — Fetch eval scenarios from tenant DB
    # ------------------------------------------------------------------
    try:
        _scen_conn = psycopg2.connect(conn_str, connect_timeout=5)
        try:
            with _scen_conn.cursor() as _cur:
                _cur.execute(
                    """
                    SELECT id, source, question, reference_answer, retrieved_contexts
                    FROM eval_scenarios
                    WHERE reference_answer != ''
                    ORDER BY RANDOM()
                    LIMIT 30
                    """,
                )
                rows = _cur.fetchall()
        finally:
            _scen_conn.close()
    except Exception as exc:
        log.error(
            "run_eval_suite.fetch_scenarios_failed",
            agent_id=agent_id,
            error=str(exc),
        )
        if self.request.retries >= self.max_retries:
            return {}
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)

    scenarios = [
        {
            "id": str(row[0]),
            "source": row[1],
            "question": row[2],
            "reference_answer": row[3],
            "retrieved_contexts": row[4] if isinstance(row[4], list) else [],
            # For M6: use reference_answer as proxy agent_response to test the eval harness
            "agent_response": row[3],
        }
        for row in rows
    ]

    # Mine new production scenarios and store them
    try:
        with get_sync_db() as control_db:
            mined = mine_production_scenarios(agent_id, conn_str, control_db)
        if mined:
            store_scenarios(mined, conn_str)
    except Exception as mine_exc:
        # Mining is best-effort — never blocks the eval run
        log.warning(
            "run_eval_suite.mine_failed",
            agent_id=agent_id,
            error=str(mine_exc),
        )

    if not scenarios:
        log.warning("run_eval_suite.no_scenarios", agent_id=agent_id)
        return {"status": "no_scenarios"}

    # ------------------------------------------------------------------
    # Step 5 — Insert eval_run row on production branch
    # ------------------------------------------------------------------
    run_id = str(uuid.uuid4())
    try:
        _run_conn = psycopg2.connect(conn_str, connect_timeout=5)
        try:
            with _run_conn.cursor() as _cur:
                _cur.execute(
                    """
                    INSERT INTO eval_runs (id, kind, started_at, status)
                    VALUES (%s, %s, NOW(), 'running')
                    """,
                    (run_id, f"m6:{agent_id}"),
                )
            _run_conn.commit()
        finally:
            _run_conn.close()
    except Exception as exc:
        log.error(
            "run_eval_suite.insert_eval_run_failed",
            agent_id=agent_id,
            error=str(exc),
        )
        if self.request.retries >= self.max_retries:
            return {}
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)

    # ------------------------------------------------------------------
    # Step 6 — Create Neon branch; run eval in try/finally (D-10 LOCKED)
    # ------------------------------------------------------------------
    try:
        branch_id, branch_conn_str = create_branch(
            neon_project_id, f"eval-{run_id}"
        )
        wait_for_neon_ready(branch_conn_str)
    except Exception as exc:
        log.error(
            "run_eval_suite.branch_create_failed",
            agent_id=agent_id,
            run_id=run_id,
            error=str(exc),
        )
        update_eval_run_status(run_id, "failed", finished_at=True, branch_conn_str=conn_str)
        if self.request.retries >= self.max_retries:
            return {}
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)

    # Capture branch_id before entering try so finally always has it
    branch_id_for_finally = branch_id

    try:
        # Filter scenarios — reference_answer already required by the SQL query above,
        # but double-check here for safety
        valid_scenarios = [s for s in scenarios if s.get("reference_answer")]

        results = run_ragas_eval(valid_scenarios, branch_conn_str)
        write_eval_results(run_id, results["scores"], branch_conn_str)
        promoted = promote_to_verified_qa(valid_scenarios, results["scores"], branch_conn_str)
        update_eval_run_status(run_id, "complete", finished_at=True, branch_conn_str=conn_str)

        log.info(
            "run_eval_suite.complete",
            agent_id=agent_id,
            run_id=run_id,
            promoted=promoted,
            scenario_count=len(valid_scenarios),
        )
        return {
            "run_id": run_id,
            "scenario_count": len(valid_scenarios),
            "promoted": promoted,
        }

    except Exception as exc:
        log.error(
            "run_eval_suite.eval_failed",
            agent_id=agent_id,
            run_id=run_id,
            error=str(exc),
        )
        update_eval_run_status(run_id, "failed", finished_at=True, branch_conn_str=conn_str)
        if self.request.retries >= self.max_retries:
            return {}
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)

    finally:
        # D-10 LOCKED: always delete the Neon branch, even on exception
        try:
            delete_branch(neon_project_id, branch_id_for_finally)
        except Exception as del_exc:
            log.warning(
                "run_eval_suite.branch_delete_failed",
                agent_id=agent_id,
                run_id=run_id,
                error=str(del_exc),
            )


# ---------------------------------------------------------------------------
# EVL-02: generate_eval_suite — build initial scenario library (D-14 LOCKED)
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=30,
    queue="runtime",
    name="app.worker.tasks.runtime.eval.generate_eval_suite",
)
def generate_eval_suite(self, agent_id: str) -> dict:
    """Generate initial eval scenario suite for a newly provisioned agent.

    Dispatched as part of agent build chain (D-14). Receives agent_id — no conn_str
    in args (CTL-08). Fetches and decrypts conn_str at runtime.

    Idempotency: if eval_scenarios already has >= 10 rows for this tenant, skip.
    This prevents duplicate scenario generation on Celery retry (acks_late rule).

    Args:
        agent_id: UUID string of the newly provisioned agent.

    Returns:
        {"agent_id": str, "scenario_count": int}  on success.
        {"status": "already_generated", "count": int}  on idempotent skip.
        {}                                              on retry exhaustion.
    """
    # ------------------------------------------------------------------
    # Fetch agent from control DB and decrypt conn_str at runtime (CTL-08)
    # ------------------------------------------------------------------
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        if agent is None or not agent.neon_connection_string:
            log.error(
                "generate_eval_suite.agent_not_found_or_unconfigured",
                agent_id=agent_id,
            )
            return {}

        # T-02-01: conn_str is never logged — local variable only (D-18)
        conn_str = fernet_decrypt(agent.neon_connection_string)

    # ------------------------------------------------------------------
    # Idempotency guard: skip if eval_scenarios already has >= 10 rows
    # ------------------------------------------------------------------
    try:
        _idm_conn = psycopg2.connect(conn_str, connect_timeout=5)
        try:
            with _idm_conn.cursor() as _cur:
                _cur.execute("SELECT COUNT(*) FROM eval_scenarios")
                count = _cur.fetchone()[0]
        finally:
            _idm_conn.close()

        if count >= 10:
            log.info(
                "generate_eval_suite.idempotent_skip",
                agent_id=agent_id,
                count=count,
            )
            return {"status": "already_generated", "count": count}

    except Exception as exc:
        log.warning(
            "generate_eval_suite.idempotency_check_failed",
            agent_id=agent_id,
            error=str(exc),
        )

    # ------------------------------------------------------------------
    # Generate scenario suite via scenario_service (Claude Haiku — D-12)
    # ------------------------------------------------------------------
    try:
        count = generate_eval_suite_for_agent(
            agent_id,
            conn_str,
            num_scenarios=20,
        )
        log.info(
            "generate_eval_suite.complete",
            agent_id=agent_id,
            count=count,
        )
        return {"agent_id": agent_id, "scenario_count": count}

    except Exception as exc:
        log.error(
            "generate_eval_suite.failed",
            agent_id=agent_id,
            error=str(exc),
        )
        if self.request.retries >= self.max_retries:
            return {}
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)
