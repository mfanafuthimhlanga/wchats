"""M6 eval tasks — nightly eval suite, per-agent Ragas eval, scenario generation.

All tasks: acks_late=True, runtime queue, no conn_str in args (CTL-08).
Neon branch created per eval run, deleted in finally block (D-10).
Ragas 0.4.x only — D-01 through D-04.

Where each write lands
----------------------
D-10 ("never evaluate against production") is correct for tenant data and was
over-applied to the run's own results: `eval_results`, the terminal `eval_runs`
status and the verified_qa promotion were all written to the Neon branch that
this task then deletes in `finally`. Production consequently never learned that
any run finished — a successful run left status='running' forever, and
`eval_results` never existed on production at all.

An eval result is an OBSERVATION ABOUT a run, not tenant data. So:

    scoring (run_ragas_eval)             -> branch_conn_str  (isolation, D-10)
    eval_runs insert / status / results  -> conn_str         (PRODUCTION)
    branch deletion in finally           -> unchanged, every path

verified_qa promotion is not performed by this task at all. It is disabled
behind eval_service's label trust hierarchy, and the decision — with its reason
— is recorded on the run in `eval_runs.config` so the disablement is a statement
in the record rather than an absence a later reader has to infer.
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
    VERIFIED_QA_PROMOTION_DECISION,
    build_eval_run_config,
    insert_eval_run,
    run_ragas_eval,
    write_eval_results,
    update_eval_run_status,
)
from app.services.neon import create_branch, delete_branch, wait_for_neon_ready
from app.services.scenario_service import (
    generate_eval_suite_for_agent,
    mine_production_scenarios,
    store_scenarios,
)
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)


def _mark_failed_on_production(run_id: str, conn_str: str, agent_id: str) -> None:
    """Best-effort terminal 'failed' status write on PRODUCTION.

    A run must end in a terminal state on production or it never happened —
    but the write itself must never derail the two things that matter more on
    an already-failing path: deleting the Neon branch (the `finally` below)
    and the caller's `self.retry`. An unguarded raise here would skip
    `raise self.retry(...)` entirely, so the task would die instead of
    retrying, and the failure would be attributed to the status write rather
    than to whatever actually broke.

    A failure here is logged at error level rather than swallowed quietly: it
    means production still reads 'running' for a run that is over, which is
    exactly the indistinguishable-from-hung state this phase exists to remove.
    """
    try:
        update_eval_run_status(run_id, "failed", finished_at=True, conn_str=conn_str)
    except Exception as status_exc:
        log.error(
            "run_eval_suite.mark_failed_failed",
            agent_id=agent_id,
            run_id=run_id,
            error=str(status_exc),
            detail="production eval_runs row still reads 'running' for a finished run",
        )


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
    """Per-agent eval run. Scores on a Neon branch, records the run on production,
    deletes the branch in finally (D-10). Receives agent_id str — no conn_str in args
    (CTL-08 / D-18).

    Sequence:
        1. Idempotency guard — skip if a 'running' eval_run for this agent
           was created within the last 10 minutes.
        2. Fetch agent from control DB; decrypt conn_str at runtime.
        3. Fetch up to 30 eval scenarios from tenant DB; mine new production scenarios.
        4. Collect the configuration tuple, then insert the eval_run row on
           PRODUCTION with it (status='running').
        5. Create Neon branch; wait for readiness.
        6. try: run Ragas eval on the BRANCH → write results to PRODUCTION →
                mark complete on PRODUCTION.
           except: mark failed on PRODUCTION.
           finally: delete Neon branch (D-10 — always runs, even on exception).

    No verified_qa promotion happens here. See the module docstring and
    eval_service.VERIFIED_QA_PROMOTION_DECISION: promotion is gated on the label
    trust hierarchy and unreachable for every scenario source the schema allows,
    and the decision is recorded on the run in eval_runs.config.

    Args:
        agent_id: UUID string of the agent to evaluate.

    Returns:
        {"run_id", "scenario_count", "promoted", "config_recorded",
         "promotion_disabled_reason"}                            on success.
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
    # Step 5 — Insert the eval_run row on PRODUCTION, stamped with the
    # configuration tuple this run is an assertion about (migration 0013).
    #
    # build_eval_run_config never raises: an unattributable run is worth less
    # than an attributed one but far more than no run at all, so a collector
    # failure degrades attribution and names itself in config["unavailable"].
    # ------------------------------------------------------------------
    run_id = str(uuid.uuid4())
    attribution = build_eval_run_config(agent_id, conn_str)
    try:
        config_recorded = insert_eval_run(
            run_id,
            f"m6:{agent_id}",
            attribution["prompt_version_id"],
            attribution["config"],
            conn_str,
        )
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
        _mark_failed_on_production(run_id, conn_str, agent_id)
        if self.request.retries >= self.max_retries:
            return {}
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)

    # Capture branch_id before entering try so finally always has it
    branch_id_for_finally = branch_id

    try:
        # Filter scenarios — reference_answer already required by the SQL query above,
        # but double-check here for safety
        valid_scenarios = [s for s in scenarios if s.get("reference_answer")]

        # Scoring is the ONLY half that runs against the branch (D-10).
        results = run_ragas_eval(valid_scenarios, branch_conn_str)

        # Observations about the run land on PRODUCTION, which is the whole
        # point of the split: the branch below is about to be destroyed.
        write_eval_results(run_id, results["scores"], conn_str)
        update_eval_run_status(run_id, "complete", finished_at=True, conn_str=conn_str)

        log.info(
            "run_eval_suite.complete",
            agent_id=agent_id,
            run_id=run_id,
            scenario_count=len(valid_scenarios),
            config_recorded=config_recorded,
            promoted=0,
            promotion_enabled=VERIFIED_QA_PROMOTION_DECISION["enabled"],
        )
        return {
            "run_id": run_id,
            "scenario_count": len(valid_scenarios),
            # Always 0 — promotion is disabled behind the trust gate, and the
            # key is kept so a caller reading it sees the zero rather than a
            # missing key it might treat as "not measured".
            "promoted": 0,
            "config_recorded": config_recorded,
            "promotion_disabled_reason": VERIFIED_QA_PROMOTION_DECISION["reason"],
        }

    except Exception as exc:
        log.error(
            "run_eval_suite.eval_failed",
            agent_id=agent_id,
            run_id=run_id,
            error=str(exc),
        )
        _mark_failed_on_production(run_id, conn_str, agent_id)
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
