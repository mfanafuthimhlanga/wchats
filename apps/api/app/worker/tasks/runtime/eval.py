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

    scenario read / eval_runs / results  -> conn_str         (PRODUCTION)
    scoring (run_ragas_eval)             -> no database at all
    branch deletion in finally           -> unchanged, every path

The middle line is the correction to an earlier version of this docstring,
which said scoring ran against `branch_conn_str`. It does not, and never did:
run_ragas_eval scores rows that are already in memory against the judge API and
never referenced the connection string it was handed. The scenario rows
themselves are read from PRODUCTION below. So no statement in this task is ever
issued against the eval branch.

The branch is still created and still deleted in `finally`, because D-10 has to
be in place the day this eval starts invoking retrieval or the agent against
tenant data. What changed is that its ABSENCE is no longer fatal while
eval_service.EVAL_SCORING_REQUIRES_BRANCH is False: a degraded Neon endpoint
used to abandon a whole night's measurement over a resource nothing reads.

verified_qa promotion is not performed by this task at all. It is disabled
behind eval_service's label trust hierarchy, and the decision — with its reason
— is recorded on the run in `eval_runs.config` so the disablement is a statement
in the record rather than an absence a later reader has to infer.

Which rows a run covers
-----------------------
The selector was `ORDER BY RANDOM() LIMIT 30` — a different sample every night,
so run-to-run variance was dominated by the draw and a regression could not be
distinguished from a redraw. It is now two queries: every `dataset='golden'` row
UNSAMPLED, plus a rotating exploratory sample. The same golden items scored on
consecutive runs give a paired per-item delta; the rotating half is what stops
the fixed half being overfit. The two are reported separately all the way out
(`datasets` in the return, `config["dataset"]` on the run) because a golden
score and an exploratory score are different measurements.

Every report carries (attempted, valid, scored): rows fetched, rows carrying a
label, rows Ragas returned a real number for. A rate without its denominator
must not be constructible from what this task returns.
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
    DATASET_GOLDEN,
    EVAL_SCORING_REQUIRES_BRANCH,
    EXPLORATORY_SAMPLE_SIZE,
    VERIFIED_QA_PROMOTION_DECISION,
    build_eval_run_config,
    dataset_composition,
    dataset_of,
    insert_eval_run,
    run_ragas_eval,
    summarise_run_validity,
    update_eval_run_status,
    write_eval_results,
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
    """Per-agent eval run. Records the run on production, creates and deletes a
    Neon branch (D-10), and scores without touching either. Receives agent_id
    str — no conn_str in args (CTL-08 / D-18).

    Sequence:
        1. Idempotency guard — skip if a 'running' eval_run for this agent
           was created within the last 10 minutes.
        2. Fetch agent from control DB; decrypt conn_str at runtime.
        3. Fetch scenarios from PRODUCTION: EVERY golden row, unsampled, plus a
           rotating exploratory sample of EXPLORATORY_SAMPLE_SIZE. Mine new
           production scenarios.
        4. Collect the configuration tuple, then insert the eval_run row on
           PRODUCTION with it (status='running').
        5. Create the Neon branch. Readiness is probed only if scoring is going
           to connect to it, and a branch that cannot be created is fatal only
           then — see EVAL_SCORING_REQUIRES_BRANCH and the block comment below.
        6. try: run Ragas eval (no database) → write results to PRODUCTION →
                mark complete on PRODUCTION.
           except: mark failed on PRODUCTION.
           finally: delete the Neon branch if one was created (D-10 — always
                runs, even on exception).

    No verified_qa promotion happens here. See the module docstring and
    eval_service.VERIFIED_QA_PROMOTION_DECISION: promotion is gated on the label
    trust hierarchy and unreachable for every scenario source the schema allows,
    and the decision is recorded on the run in eval_runs.config.

    Args:
        agent_id: UUID string of the agent to evaluate.

    Returns:
        {"run_id", "scenario_count", "attempted", "valid", "scored", "datasets",
         "dataset_column_available", "golden_set_present", "promoted",
         "config_recorded", "promotion_disabled_reason",
         "branch_isolation"}                                     on success.
        {"status": "already_running"}                            on idempotent skip.
        {"status": "no_scenarios", "run_id", "run_recorded", "attempted",
         "valid", "scored", "dataset_column_available"}          when nothing was
            selected. The empty run is still recorded terminally on production —
            "this tenant has no scenarios" and "nobody has ever evaluated this
            agent" are different claims and the deploy gate can only tell them
            apart if the empty run left a row.
        {}                                                        on retry exhaustion.

    (attempted, valid, scored) are three different claims and all three are
    reported: rows fetched, rows carrying a label (the DENOMINATOR), and rows
    Ragas returned a real number for. `scored < valid` means the run measured
    less than it attempted, and that is invisible from any one of them alone.
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
    # Step 3 — Fetch eval scenarios from tenant DB (PRODUCTION).
    #
    # TWO QUERIES, NOT ONE SAMPLE. This used to be a single
    # `ORDER BY RANDOM() LIMIT 30`, which drew a different 30 rows every night:
    # run-to-run variance was dominated by the draw rather than by anything the
    # agent did, so a regression could not be seen. The golden rows now run in
    # FULL on every eval — the same items scored twice give a paired per-item
    # delta — and the exploratory rows keep rotating, which is what stops the
    # fixed set from being overfit. The two are kept apart all the way to the
    # report; averaging them would throw away exactly the property the split
    # exists to create.
    #
    # `reference_answer != ''` survives in BOTH queries. It is the empty-label
    # exclusion that makes an unlabelled row (a mined production failure, an
    # owner-filed failing trace — see bench.NO_GROUND_TRUTH) inert to this
    # selector by construction, and it is pinned across module boundaries by
    # test_the_scenario_is_inert_to_the_eval_selector_by_construction.
    #
    # A tenant DB that predates migration 0014 has no `dataset` column at all.
    # That is a degradation, not an outage: the fallback below is the pre-0014
    # single query, every row is then exploratory because the column that could
    # say otherwise does not exist, and `dataset_column_available` records which
    # of the two happened so "no golden rows" and "no way to tell" stay
    # different claims. Same tolerance shape as insert_eval_run's pre-0013
    # fallback, for the same reason: tenants are migrated at provision time.
    # ------------------------------------------------------------------
    _GOLDEN_SQL = """
        SELECT id, source, question, reference_answer, retrieved_contexts, dataset
        FROM eval_scenarios
        WHERE reference_answer != ''
          AND dataset = %(golden)s
        ORDER BY created_at
    """
    _EXPLORATORY_SQL = """
        SELECT id, source, question, reference_answer, retrieved_contexts, dataset
        FROM eval_scenarios
        WHERE reference_answer != ''
          AND (dataset IS NULL OR dataset <> %(golden)s)
        ORDER BY RANDOM()
        LIMIT %(limit)s
    """
    _PRE_0014_SQL = """
        SELECT id, source, question, reference_answer, retrieved_contexts, NULL
        FROM eval_scenarios
        WHERE reference_answer != ''
        ORDER BY RANDOM()
        LIMIT %(limit)s
    """

    dataset_column_available = True
    try:
        _scen_conn = psycopg2.connect(conn_str, connect_timeout=5)
        try:
            try:
                with _scen_conn.cursor() as _cur:
                    _cur.execute(_GOLDEN_SQL, {"golden": DATASET_GOLDEN})
                    rows = list(_cur.fetchall())
                    _cur.execute(
                        _EXPLORATORY_SQL,
                        {"golden": DATASET_GOLDEN, "limit": EXPLORATORY_SAMPLE_SIZE},
                    )
                    rows.extend(_cur.fetchall())
            except psycopg2.errors.UndefinedColumn:
                # The aborted transaction must be rolled back before the
                # connection will accept another statement.
                _scen_conn.rollback()
                dataset_column_available = False
                log.warning(
                    "run_eval_suite.dataset_column_absent",
                    agent_id=agent_id,
                    detail=(
                        "tenant DB predates alembic_tenant 0014 — no golden set "
                        "is held fixed for this run"
                    ),
                )
                with _scen_conn.cursor() as _cur:
                    _cur.execute(_PRE_0014_SQL, {"limit": EXPLORATORY_SAMPLE_SIZE})
                    rows = list(_cur.fetchall())
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
            # NULL (never designated) resolves to exploratory — membership of
            # the golden set is asserted, never inherited.
            "dataset": dataset_of(row[5] if len(row) > 5 else None),
            # For M6: use reference_answer as proxy agent_response to test the eval harness
            "agent_response": row[3],
        }
        for row in rows
    ]
    composition = dataset_composition(
        scenarios, dataset_column_available=dataset_column_available
    )
    if composition["golden_over_soft_ceiling"]:
        # Reported, never silently truncated: cutting the golden set down would
        # break the paired comparison it exists for.
        log.warning(
            "run_eval_suite.golden_set_over_soft_ceiling",
            agent_id=agent_id,
            golden_attempted=composition[DATASET_GOLDEN]["attempted"],
        )

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
        log.warning(
            "run_eval_suite.no_scenarios",
            agent_id=agent_id,
            dataset_column_available=dataset_column_available,
        )
        # A RUN THAT COVERED NOTHING STILL HAPPENED (P2 review). This path used
        # to return without writing anything, so production held no eval_runs
        # row and the deploy gate reported EVAL_SIGNAL_NO_RUNS — the same signal
        # as an agent nobody has ever tried to evaluate, and one that blocks the
        # deploy with nothing on the record to explain why. Two consequences,
        # both bad: the owner is told "quality has never been measured" when the
        # truth is "this tenant has no scenarios to measure against", and
        # run_deployment_checklist's day-1 remedy (dispatching the first eval,
        # step 4b there) would re-fire on every readiness check forever because
        # the state it keys off never changes.
        #
        # So the empty run is recorded terminally, with its composition
        # (attempted=0, valid=0) stamped on it. The gate then reads a completed
        # run that produced no valid score — EVAL_SIGNAL_NO_VALID_SCORES, which
        # still blocks, honestly, and converges.
        empty_run_id = str(uuid.uuid4())
        run_recorded = False
        try:
            empty_attribution = build_eval_run_config(
                agent_id, conn_str, dataset=composition
            )
            insert_eval_run(
                empty_run_id,
                f"m6:{agent_id}",
                empty_attribution["prompt_version_id"],
                empty_attribution["config"],
                conn_str,
            )
            update_eval_run_status(
                empty_run_id, "complete", finished_at=True, conn_str=conn_str
            )
            run_recorded = True
        except Exception as record_exc:
            # Best-effort: failing to record an empty run must not turn a
            # nothing-to-do into a retry storm. It is logged at error level
            # because the consequence — an unexplained permanent block — is the
            # thing this write exists to prevent.
            log.error(
                "run_eval_suite.empty_run_record_failed",
                agent_id=agent_id,
                run_id=empty_run_id,
                error=str(record_exc),
                detail=(
                    "no eval_runs row explains why this agent's deploy is "
                    "blocked"
                ),
            )
        # The denominators travel even on the empty path. A run that scored
        # nothing must be readable as such rather than as an absent key a caller
        # might treat as "not applicable".
        return {
            "status": "no_scenarios",
            "run_id": empty_run_id if run_recorded else None,
            "run_recorded": run_recorded,
            "attempted": 0,
            "valid": 0,
            "scored": 0,
            "dataset_column_available": dataset_column_available,
        }

    # ------------------------------------------------------------------
    # Step 5 — Insert the eval_run row on PRODUCTION, stamped with the
    # configuration tuple this run is an assertion about (migration 0013).
    #
    # build_eval_run_config never raises: an unattributable run is worth less
    # than an attributed one but far more than no run at all, so a collector
    # failure degrades attribution and names itself in config["unavailable"].
    # ------------------------------------------------------------------
    run_id = str(uuid.uuid4())
    attribution = build_eval_run_config(agent_id, conn_str, dataset=composition)
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
    # Step 6 — Neon branch (D-10 LOCKED), then scoring in try/finally.
    #
    # The branch exists so an eval can never mutate tenant data. Nothing in
    # this build connects to it: run_ragas_eval scores rows already in memory
    # against the judge API, and the scenario rows were read from production
    # above. So the branch is isolation held IN RESERVE, and this block says
    # which of the two it is instead of asserting the one that is false.
    #
    #   * It is still created and still deleted in `finally`, so the guarantee
    #     is already in place the day scoring starts issuing statements.
    #   * A branch that cannot be created or readied no longer abandons the
    #     run. Abandoning it threw away a night's measurement — on production,
    #     which is reachable — over a resource nothing reads.
    #   * The readiness probe only runs when something is going to connect.
    #     Waiting for an endpoint nobody opens is cost and failure surface
    #     with no signal in it.
    #
    # eval_service.EVAL_SCORING_REQUIRES_BRANCH is the single switch: flip it
    # in the same edit that gives scoring a database, and both the probe and
    # the fatal failure come back, because then the branch really is what
    # stands between the eval and production tenant data.
    #
    # Acquisition sits INSIDE the try/finally rather than before it. Previously
    # it had its own try/except that returned, so a branch that was created and
    # then failed its readiness probe leaked: the `finally` belonged to the
    # block that was never entered. Every path after create_branch returns an
    # id now reaches the deletion below.
    # ------------------------------------------------------------------
    branch_id_for_finally: str | None = None
    branch_isolation = "provisioned_unused"
    try:
        try:
            branch_id_for_finally, branch_conn_str = create_branch(
                neon_project_id, f"eval-{run_id}"
            )
            if EVAL_SCORING_REQUIRES_BRANCH:
                wait_for_neon_ready(branch_conn_str)
        except Exception as branch_exc:
            log.error(
                "run_eval_suite.branch_create_failed",
                agent_id=agent_id,
                run_id=run_id,
                error=str(branch_exc),
                scoring_requires_branch=EVAL_SCORING_REQUIRES_BRANCH,
            )
            if EVAL_SCORING_REQUIRES_BRANCH:
                raise
            # Scoring needs no branch, so the run continues and says on the way
            # out that it ran without one — a reader must never have to guess
            # whether isolation was in force.
            branch_isolation = "unavailable"

        # Filter scenarios — reference_answer already required by the SQL query above,
        # but double-check here for safety. This is the VALID set: rows that
        # were fetched and carry a label, i.e. the rows that can be scored at
        # all. It is the denominator, and it is not the same number as the rows
        # fetched (attempted) or the rows Ragas came back with (scored).
        valid_scenarios = [s for s in scenarios if s.get("reference_answer")]

        # No connection string is passed: scoring opens nothing (audit D1 —
        # each sample's "response" is its own reference answer).
        results = run_ragas_eval(valid_scenarios)

        # Observations about the run land on PRODUCTION, which is the whole
        # point of the split: the branch below is about to be destroyed.
        write_eval_results(run_id, results["scores"], conn_str)
        update_eval_run_status(run_id, "complete", finished_at=True, conn_str=conn_str)

        # (attempted, valid, scored) for the run and for each dataset. Computed
        # over the FETCHED set, not the valid one, so the two counts stay
        # distinguishable — a run that fetched 40 rows and could score 12 has
        # measured far less than a run that fetched 12, and a report that shows
        # only one of the two numbers cannot say which happened.
        validity = summarise_run_validity(scenarios, results["scores"])

        log.info(
            "run_eval_suite.complete",
            agent_id=agent_id,
            run_id=run_id,
            scenario_count=len(valid_scenarios),
            attempted=validity["attempted"],
            valid=validity["valid"],
            scored=validity["scored"],
            golden_valid=validity["datasets"][DATASET_GOLDEN]["valid"],
            golden_set_present=composition["golden_set_present"],
            dataset_column_available=dataset_column_available,
            config_recorded=config_recorded,
            promoted=0,
            promotion_enabled=VERIFIED_QA_PROMOTION_DECISION["enabled"],
            branch_isolation=branch_isolation,
        )
        return {
            "run_id": run_id,
            # Legacy alias for `valid`, kept so an existing reader does not
            # silently change meaning. New readers take the triple below.
            "scenario_count": len(valid_scenarios),
            # The triple. `valid` is the denominator; a rate computed against
            # `attempted` understates and one computed without a denominator at
            # all is not a measurement.
            "attempted": validity["attempted"],
            "valid": validity["valid"],
            "scored": validity["scored"],
            # Per dataset, never averaged together — a golden score and an
            # exploratory score answer different questions. Each metric carries
            # {value, measured, observations}; value is null exactly when
            # measured is false, which is 'unknown', not zero.
            "datasets": validity["datasets"],
            # WHICH rows this run covered, and whether the tenant DB could even
            # tell us. False for dataset_column_available means the tenant
            # predates migration 0014 — not that it has no golden rows.
            "dataset_column_available": dataset_column_available,
            "golden_set_present": composition["golden_set_present"],
            # Always 0 — promotion is disabled behind the trust gate, and the
            # key is kept so a caller reading it sees the zero rather than a
            # missing key it might treat as "not measured".
            "promoted": 0,
            "config_recorded": config_recorded,
            "promotion_disabled_reason": VERIFIED_QA_PROMOTION_DECISION["reason"],
            # 'provisioned_unused' — a branch exists and no statement ran
            # against it; 'unavailable' — Neon could not give us one and the
            # run scored anyway. Never absent, so the state is always readable.
            "branch_isolation": branch_isolation,
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
        # D-10 LOCKED: always delete the Neon branch, even on exception.
        # None means create_branch itself failed, so there is nothing to
        # delete — not a path that may skip deletion for any other reason.
        if branch_id_for_finally is not None:
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
