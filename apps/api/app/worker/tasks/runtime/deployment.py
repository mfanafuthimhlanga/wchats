"""
M8 Deployment Checklist Celery task: run_deployment_checklist.

Lives in the `runtime` queue.

Architecture constraints (CLAUDE.md — non-negotiable):
    - acks_late=True AND idempotency guard on every Celery task (both always required)
    - run_deployment_checklist receives only `agent_id` — NEVER a conn_str in task args (CTL-08)
    - conn_str is fetched from the control DB and decrypted at runtime via fernet_decrypt
    - asyncio.run(asyncio.wait_for(..., timeout=ORCHESTRATOR_TIMEOUT_S)) bridge, never
      loop.run_until_complete

Dual-DB split (PATTERNS.md — non-negotiable):
    - Control DB (checklist_runs, agents): use get_sync_db() SQLAlchemy ORM
    - Tenant DB (eval_runs, red_team_runs, verified_qa, documents, chunks):
      use _fetch_*_sync psycopg2 functions from deployment_service.py
    - Phase 18 BLR-01 extends this: the fifth signal, blast_radius, reads the
      CONTROL DB via get_sync_db() (capability_envelopes/tool_calls_audit/
      tenants all live there) — it is the one collector that breaks the
      tenant-DB-only convention the other four follow, and it needs no
      conn_str at all.

Flow (run_deployment_checklist):
    1. Fetch agent from control DB; decrypt conn_str
    2. Idempotency guard — skip if a running checklist_run for this agent exists within 60 min
    3. Insert checklist_runs row (status='running') in control DB via ORM
    3b. THE CHECKLIST SEQUENCES THE JOBS IT GRADES (#54, decision 19 rule 5).
       Dispatch BOTH the eval chain and a red-team run, then wait for each to
       reach a terminal status before collecting anything, bounded by
       CHECKLIST_WAIT_CEILING_S. Until this existed the task dispatched an eval
       on two signal states, never dispatched a red team, and collected
       immediately, so the first checklist ever executed read eval_signal=no_runs
       seconds after starting the eval it was asking about. A job that does not
       reach terminal inside the ceiling reads as an ABSENT record and blocks;
       the pre-dispatch summary is never read.
    4. Collect all 5 signals plus the BLR-02 envelope hash synchronously (4
       signals via psycopg2 against the tenant DB, the 5th signal —
       blast_radius — and the envelope hash both via get_sync_db() against
       the control DB). Always AFTER the wait, so every summary describes the
       runs this checklist sequenced.
    5. Run the orchestrator's turn on the owned loop, under ORCHESTRATOR_TIMEOUT_S,
       billed to the assessed tenant through a LedgerContext this task builds
    6. Parse result, apply the deterministic evidence gate (P2 — an eval or
       red-team signal that is not 'measured' forces recommendation='block'
       with a stated warning; the gate never softens a verdict), then UPDATE
       checklist_runs row to status='complete'; persist the envelope hash
       alongside status, recommendation, report and warnings; merge the
       deterministic evidence and blast-radius warnings into the persisted
       warnings list, de-duplicated by warning_id
    7. On exception: UPDATE checklist_runs row to status='failed'; retry if retries < max_retries
"""

from __future__ import annotations

import asyncio
import json
import time

import structlog
from sqlalchemy import select, text

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.model_client import LedgerContext, ledger_recorder
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.models.checklist_run import ChecklistRun
from app.services.deployment_service import (
    BLAST_RADIUS_DEFAULT_SIGNAL,
    EVAL_SUMMARY_UNAVAILABLE_SIGNAL,
    RED_TEAM_SUMMARY_UNAVAILABLE_SIGNAL,
    DeploymentReport,
    _compute_envelope_hash_sync,
    _dispatch_moment,
    _fetch_blast_radius_sync,
    _fetch_corpus_stats_sync,
    _fetch_eval_summary_sync,
    _fetch_red_team_summary_sync,
    _fetch_verified_qa_stats_sync,
    apply_signal_evidence_gate,
    derive_blast_radius_warnings,
    latest_eval_run_status_since,
    latest_red_team_run_status_since,
    wait_for_terminal_runs,
)
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)

# BACKLOG 1.33: the ceiling now lives in deployment_service so the task and
# the service's own run_orchestrator bridge cannot drift apart. They had.
from app.services.deployment_service import ORCHESTRATOR_TIMEOUT_S  # noqa: E402


def _dispatch_eval_run(agent_id: str) -> bool:
    """Start an eval suite for this agent. Returns True iff it was dispatched.

    THE DAY-1 PATH HAD NO WAY TO PRODUCE AN EVAL RUN (P2 review). Making
    EVAL_SIGNAL_NO_RUNS hard-block is right — an agent with no measurement has
    no evidence of quality — but nothing in signup → ingest → deploy dispatched
    `run_eval_suite`. It runs from the nightly beat or from the "Run Now" button
    on the eval dashboard, and the onboarding flow routes to neither, so a new
    tenant's readiness check blocked and POST /approve-deployment answered 422
    with the only remedy on a page they had not been shown. CLAUDE.md's stated
    core value ends in "deploy"; a gate that cannot be satisfied by the primary
    journey is not a gate, it is a wall.

    So the checklist starts the measurement it is asking for, and the warning
    apply_signal_evidence_gate emits says so. The recommendation is still
    `block` — this run has no evidence and must not ship on the promise of some
    — but the block now converges instead of being terminal.

    NO LONGER ONLY THE FIRST (P3 review), hence the rename from
    `_dispatch_first_eval_run`: existing tenants report AGENT_NOT_INVOKED rather
    than `no_runs`, so the wall had moved to the larger population.

    AND NO LONGER CONDITIONAL AT ALL (#54). Step 4b chose between those states by
    reading a signal collected BEFORE the dispatch, the ordering the sequencer
    removes. The spend bound is the checklist's own 60-minute idempotency guard,
    one eval per agent per hour, tighter than the conditional ever was.

    `generate_eval_suite` runs first because a tenant whose scenario generation
    has never run has nothing to evaluate against; both tasks carry their own
    idempotency guards (>= 10 scenarios, and a 'running' run inside 10 minutes),
    and run_eval_suite records even an empty run terminally, so this fires at
    most once per agent rather than on every readiness check.

    Only agent_id crosses the task boundary — never a connection string
    (CLAUDE.md rule 4). Imported inside the function: the eval task module pulls
    in scenario_service and the Neon client, and this task has no other reason
    to load them.

    Best-effort. A broker failure here must not fail the checklist, which has
    already collected every other signal and still owes the owner a report.
    """
    try:
        from celery import chain  # noqa: PLC0415

        from app.worker.tasks.runtime.eval import (  # noqa: PLC0415
            generate_eval_suite,
            run_eval_suite,
        )

        chain(
            generate_eval_suite.si(agent_id),
            run_eval_suite.si(agent_id),
        ).apply_async(queue="runtime")
        log.info("run_deployment_checklist.eval_dispatched", agent_id=agent_id)
        return True
    except Exception as exc:
        log.warning(
            "run_deployment_checklist.eval_dispatch_failed",
            agent_id=agent_id,
            error=str(exc),
        )
        return False


def _dispatch_red_team_run(agent_id: str) -> bool:
    """Start a red-team run for this agent. Returns True iff it was dispatched.

    THE CHECKLIST NEVER STARTED ONE (#54). It dispatched an eval on two of the
    seven eval signal states and left the security half entirely to the weekly
    beat, so an agent the beat had not reached was graded against a
    red_team_runs table the checklist itself could have filled. That is the
    same wall _dispatch_eval_run's docstring describes, on the other signal.

    run_red_team's own idempotency guard (a 'running' row inside 90 minutes)
    absorbs a run already in flight, so this costs at most one live run per
    agent. Only agent_id crosses the task boundary (CTL-08). Imported inside the
    function: red_team.py pulls in seven attacker runners and the transactional
    probe builder, and this task has no other reason to load them.

    Best-effort, on _dispatch_eval_run's terms. A broker failure must not fail a
    checklist that still owes the owner a report; the run then never reaches
    terminal, the wait reports it absent, and the gate blocks.
    """
    try:
        from app.worker.tasks.runtime.red_team import run_red_team  # noqa: PLC0415

        run_red_team.apply_async(kwargs={"agent_id": agent_id}, queue="runtime")
        log.info("run_deployment_checklist.red_team_dispatched", agent_id=agent_id)
        return True
    except Exception as exc:
        log.warning(
            "run_deployment_checklist.red_team_dispatch_failed",
            agent_id=agent_id,
            error=str(exc),
        )
        return False


def _log_wait_outcome(agent_id: str, statuses: dict, waited_s: float) -> None:
    """Name which half ran out of ceiling, with the wait actually observed.

    The observed number rather than the configured one: a run that expired at
    1500.4s and a run that expired because the poll returned None instantly are
    different incidents, and only the measured wait tells them apart.
    """
    timed_out = sorted(name for name, status in statuses.items() if status is None)
    if timed_out:
        log.warning(
            "run_deployment_checklist.wait_ceiling_expired",
            agent_id=agent_id,
            timed_out=timed_out,
            waited_s=round(waited_s, 1),
            ceiling_s=settings.CHECKLIST_WAIT_CEILING_S,
            detail="each named job reads as an absent measurement and blocks",
        )
        return
    log.info(
        "run_deployment_checklist.both_runs_terminal",
        agent_id=agent_id,
        waited_s=round(waited_s, 1),
        eval_status=statuses.get("eval"),
        red_team_status=statuses.get("red_team"),
    )


def _sequence_measurements(agent_id: str, conn_str: str) -> dict:
    """Start both measurements, then wait for both to end. The checklist's step 3b.

    Returns eval_dispatched, red_team_dispatched, statuses and waited_s.
    `statuses` maps 'eval' and 'red_team' to a terminal status or to None, and
    None is what the rest of the task reads as an absent record.

    NO SECOND IDEMPOTENCY GUARD LIVES HERE, and that is deliberate (#85 family).
    The task is acks_late=True, so a broker redelivery mid-wait re-runs the whole
    body; what absorbs it is the 60-minute 'running' checklist_runs guard in step
    2, which is still holding this run's own row while the wait sleeps. A guard
    added here would be a second answer to a question step 2 has already
    answered, and the two would drift the way the timeout in BACKLOG 1.33 did.

    The two dispatch calls are made before the first poll, so both jobs are in
    flight for the whole wait rather than one after the other.

    A JOB ALREADY IN FLIGHT AT DISPATCH TIME IS THE ONE RESIDUAL. Its guard
    absorbs the dispatch, so no row exists at or after `since`, the wait expires
    and the record reads absent even though a run will finish shortly after. The
    owner re-runs the check and gets it. That is the fail-closed direction and it
    is the price of the boundary that keeps last night's run out.
    """
    since = _dispatch_moment(conn_str)
    eval_dispatched = _dispatch_eval_run(agent_id)
    red_team_dispatched = _dispatch_red_team_run(agent_id)
    statuses, waited_s = wait_for_terminal_runs(
        {
            "eval": lambda: latest_eval_run_status_since(agent_id, conn_str, since),
            "red_team": lambda: latest_red_team_run_status_since(
                agent_id, conn_str, since
            ),
        },
        poll_s=settings.CHECKLIST_WAIT_POLL_S,
        ceiling_s=settings.CHECKLIST_WAIT_CEILING_S,
        sleep=time.sleep,
        clock=time.monotonic,
    )
    _log_wait_outcome(agent_id, statuses, waited_s)
    return {
        "eval_dispatched": eval_dispatched,
        "red_team_dispatched": red_team_dispatched,
        "statuses": statuses,
        "waited_s": waited_s,
    }


@celery_app.task(
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=30,
    queue="runtime",
    name="app.worker.tasks.runtime.deployment.run_deployment_checklist",
)
def run_deployment_checklist(self, agent_id: str) -> dict:
    """Per-agent deployment checklist run.

    Collects quality signals from the tenant DB, runs the orchestrator's turn on
    the owned loop, and records the recommendation in control DB checklist_runs.

    Receives agent_id str — no conn_str in args (CTL-08 / CLAUDE.md non-negotiable).

    Sequence:
        1. Fetch agent from control DB; decrypt conn_str at runtime.
        2. Idempotency guard — skip if a 'running' checklist_run for this agent
           was created within the last 60 minutes.
        3. Insert checklist_runs row (status='running') in control DB via ORM.
        3b. Dispatch the eval chain and the red-team run, then wait for both to
           reach a terminal status (_sequence_measurements), bounded by
           CHECKLIST_WAIT_CEILING_S.
        4. Collect all 5 signals synchronously (psycopg2 against tenant DB) plus
           the BLR-02 envelope hash (control DB, own guarded block).
        5. Call run_orchestrator via asyncio.run(asyncio.wait_for(..., timeout=120.0)) bridge.
        6. Parse result and UPDATE checklist_runs to status='complete', persisting
           the envelope hash alongside status/recommendation/report/warnings.
        7. On exception: UPDATE checklist_runs to status='failed'; retry if possible.

    Args:
        agent_id: UUID string of the agent to check.

    Returns:
        {"status": "complete", "run_id": str, "recommendation": str}  on success.
        {"status": "already_running"}                                  on idempotent skip.
        {}                                                             on retry exhaustion.
    """
    # ------------------------------------------------------------------
    # Step 1 — Fetch agent from control DB; decrypt conn_str at runtime
    # conn_str is intentionally not logged — CTL-08 constraint.
    # ------------------------------------------------------------------
    with get_sync_db() as db:
        agent = db.get(Agent, agent_id)
        if agent is None or not agent.neon_connection_string:
            log.error(
                "run_deployment_checklist.agent_not_found",
                agent_id=agent_id,
            )
            return {}

        conn_str = fernet_decrypt(agent.neon_connection_string)
        tenant_id = str(agent.tenant_id)

    # ------------------------------------------------------------------
    # Step 2 — Idempotency guard: check checklist_runs for a recent running row
    # Uses control DB (ORM) — NOT psycopg2 against tenant DB.
    # 60-minute window because this checklist makes a model call with a 120s
    # timeout. Independent of red_team.py's window, which is sized to ITS bound.
    # ------------------------------------------------------------------
    with get_sync_db() as db:
        existing = db.execute(
            select(ChecklistRun).where(
                ChecklistRun.agent_id == agent_id,
                ChecklistRun.status == "running",
                ChecklistRun.created_at > text("now() - interval '60 minutes'"),
            )
        ).scalar_one_or_none()
        if existing:
            log.info("run_deployment_checklist.idempotency_skip", agent_id=agent_id)
            return {"status": "already_running"}

    # ------------------------------------------------------------------
    # Step 3 — Insert checklist_runs row in control DB via ORM
    # (NOT in tenant DB — checklist_runs is control DB only, T-08-03-04)
    # ------------------------------------------------------------------
    with get_sync_db() as db:
        run = ChecklistRun(agent_id=agent_id, status="running")
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = str(run.id)

    log.info("run_deployment_checklist.started", agent_id=agent_id, run_id=run_id)

    # ------------------------------------------------------------------
    # Step 3b. THE CHECKLIST SEQUENCES THE JOBS IT GRADES (#54, decision 19
    # rule 5). Both are dispatched here, unconditionally, and this call does not
    # return until each has a terminal row of its own or the ceiling expires.
    # Step 4 used to run first, which is why the first checklist ever executed
    # reported eval_signal=no_runs about the eval it had started seconds
    # earlier. A job that never reaches terminal reads as an ABSENT record, so
    # the gate blocks on it; the pre-dispatch summary is never read.
    # ------------------------------------------------------------------
    sequenced = _sequence_measurements(agent_id, conn_str)

    # ------------------------------------------------------------------
    # Step 4 — Collect signals from tenant DB (psycopg2 sync — fine in Celery)
    # Each _fetch_*_sync function opens its own psycopg2 connection and closes it.
    # Wrapped in try/except to handle missing tables or connection errors gracefully.
    # ------------------------------------------------------------------
    try:
        eval_summary = _fetch_eval_summary_sync(agent_id, conn_str)
    except Exception as exc:
        log.warning(
            "run_deployment_checklist.eval_summary_fetch_failed",
            agent_id=agent_id,
            error=str(exc),
        )
        # Copy so a later mutation cannot poison the module constant. The
        # substitution used to be `{"pass_rates": {}, "failing_scenarios": 0}`,
        # which asserted "no eval metric is failing" about a query that never
        # ran — audit D3's fail-open, and the reason a column-name typo could
        # disable half the deploy gate for the whole of M8's life. The
        # replacement says 'unavailable' and apply_signal_evidence_gate below
        # refuses to ship on it.
        eval_summary = dict(EVAL_SUMMARY_UNAVAILABLE_SIGNAL)

    # The dispatch is recorded ON the signal so the owner-facing warning can say
    # the platform started the measurement, rather than naming an Evaluation page
    # the onboarding flow never routes to. It does not soften the verdict:
    # apply_signal_evidence_gate still blocks on a signal that is not 'measured',
    # because a measurement that was STARTED is not a measurement.
    #
    # IT IS UNCONDITIONAL NOW. Step 4b used to fire on EVAL_SIGNAL_NO_RUNS and on
    # the absent half of EVAL_SIGNAL_AGENT_NOT_INVOKED, reading a signal that
    # described the state before the dispatch to decide whether to dispatch. The
    # sequencer above dispatches first and waits, so by the time this signal is
    # collected it already describes the run the checklist started, and there is
    # no earlier state left to branch on.
    eval_summary["eval_dispatched"] = sequenced["eval_dispatched"]

    try:
        red_team_summary = _fetch_red_team_summary_sync(agent_id, conn_str)
    except Exception as exc:
        log.warning(
            "run_deployment_checklist.red_team_summary_fetch_failed",
            agent_id=agent_id,
            error=str(exc),
        )
        # Same correction on the security half: zeros nobody read are not zeros.
        red_team_summary = dict(RED_TEAM_SUMMARY_UNAVAILABLE_SIGNAL)

    try:
        verified_qa_stats = _fetch_verified_qa_stats_sync(agent_id, conn_str)
    except Exception as exc:
        log.warning(
            "run_deployment_checklist.verified_qa_stats_fetch_failed",
            agent_id=agent_id,
            error=str(exc),
        )
        verified_qa_stats = {"row_count": 0, "avg_faithfulness": 0.0, "avg_relevance": 0.0}

    try:
        corpus_stats = _fetch_corpus_stats_sync(agent_id, conn_str)
    except Exception as exc:
        log.warning(
            "run_deployment_checklist.corpus_stats_fetch_failed",
            agent_id=agent_id,
            error=str(exc),
        )
        corpus_stats = {"document_count": 0, "chunk_count": 0, "last_ingested_at": None}

    try:
        # Fifth collector — control DB, no conn_str (BLR-01). This is the one
        # collector that reads capability_envelopes/tool_calls_audit/tenants
        # directly rather than the tenant DB, so it takes agent_id only.
        blast_radius = _fetch_blast_radius_sync(agent_id)
    except Exception as exc:
        log.warning(
            "run_deployment_checklist.blast_radius_fetch_failed",
            agent_id=agent_id,
            error=str(exc),
        )
        # Copy so a later mutation cannot poison the module constant.
        blast_radius = dict(BLAST_RADIUS_DEFAULT_SIGNAL)

    try:
        # Sixth collector — control DB, no conn_str (BLR-02). Computed
        # separately from the `signals` dict below because it is not a
        # narrative quality signal for the orchestrator; it is persisted
        # directly onto the checklist run in Step 6. A None result here
        # (collector failure) is not a neutral outcome: envelope_drift
        # treats an absent recorded hash as drift, so a run whose hash
        # collector failed can never be approved — the deliberate
        # fail-closed direction, matching a NULL pre-0019 historical hash.
        envelope_hash = _compute_envelope_hash_sync(agent_id)
    except Exception as exc:
        log.warning(
            "run_deployment_checklist.envelope_hash_failed",
            agent_id=agent_id,
            error=str(exc),
        )
        envelope_hash = None

    # ------------------------------------------------------------------
    # Step 5 - the orchestrator's turn, under ORCHESTRATOR_TIMEOUT_S. The shim
    # awaits the service's loop; run_orchestrator would nest a second asyncio.run.
    # ------------------------------------------------------------------
    signals = {
        "eval_summary": eval_summary,
        "red_team_summary": red_team_summary,
        "verified_qa_stats": verified_qa_stats,
        "corpus_stats": corpus_stats,
        "blast_radius": blast_radius,
    }
    signals_json = json.dumps(signals)
    result_container: dict = {}
    ledger = _orchestrator_ledger(tenant_id, agent_id, run_id, conn_str)

    try:
        asyncio.run(
            asyncio.wait_for(
                _call_orchestrator_async(signals_json, result_container, ledger=ledger),
                timeout=ORCHESTRATOR_TIMEOUT_S,
            )
        )
    except Exception as exc:
        # BACKLOG 1.30. `error=str(exc)` alone logged an EMPTY STRING for the
        # failure that actually happens: `str(asyncio.TimeoutError())` is `""`,
        # and the timeout is the orchestrator's most likely failure by far. The
        # first real checklist run ever executed (E2E-4, 2026-08-13) therefore
        # reported `orchestrator_failed error=` followed by "Orchestrator did
        # not produce a report" — two lines that name no cause between them.
        # Same shape as the `getattr(x, "name", "unknown")` family: a default
        # that is silently plausible converts a diagnosis into a blank.
        log.error(
            "run_deployment_checklist.orchestrator_failed",
            agent_id=agent_id,
            run_id=run_id,
            error_type=type(exc).__name__,
            error=str(exc) or repr(exc),
            timeout_s=ORCHESTRATOR_TIMEOUT_S,
        )
        # Fall through to Step 7 (status='failed')

    # ------------------------------------------------------------------
    # Step 6 — Parse result and UPDATE control DB on success
    # ------------------------------------------------------------------
    run_obj = None
    try:
        report_data = result_container.get("report")
        if report_data:
            # P2 — the evidence gate runs BEFORE the report is constructed, so
            # the recommendation the owner sees, the one persisted on the
            # checklist run and the one the approve route reads are all the
            # gated value. It can only make the verdict more conservative:
            # `ship` over an eval or red-team signal that is not 'measured'
            # becomes `block` with a stated reason. Deterministic Python, never
            # the orchestrator's reading of a state field (CLAUDE.md:
            # programmatic core, agentic edges) — the same division of labour as
            # derive_blast_radius_warnings below.
            gated_recommendation, evidence_warnings = apply_signal_evidence_gate(
                report_data.get("recommendation", "block"),
                eval_summary,
                red_team_summary,
            )
            if gated_recommendation != report_data.get("recommendation", "block"):
                log.warning(
                    "run_deployment_checklist.evidence_gate_downgraded",
                    agent_id=agent_id,
                    run_id=run_id,
                    orchestrator_recommendation=report_data.get("recommendation"),
                    gated_recommendation=gated_recommendation,
                    eval_signal=eval_summary.get("eval_signal"),
                    red_team_signal=red_team_summary.get("signal"),
                )

            # Validate via Pydantic — ensures recommendation is a known value
            report = DeploymentReport(
                recommendation=gated_recommendation,  # type: ignore[arg-type]  # DeploymentReport validates the literal at construction
                summary=report_data.get("summary", ""),
                warnings=report_data.get("warnings", []),
                eval_summary=eval_summary,
                red_team_summary=red_team_summary,
                verified_qa_stats=verified_qa_stats,
                corpus_stats=corpus_stats,
                blast_radius=blast_radius,
            )
            with get_sync_db() as db:
                run_obj = db.get(ChecklistRun, run_id)
                if run_obj:
                    run_obj.status = "complete"
                    run_obj.recommendation = report.recommendation
                    run_obj.report = {
                        **signals,
                        "summary": report.summary,
                        "recommendation": report.recommendation,
                    }
                    # BLR-01: the deterministic blast-radius warnings are derived
                    # in Python (never by the orchestrator) and APPENDED to the
                    # orchestrator's own warnings — never replacing them. The
                    # merge de-duplicates by warning_id so a future prompt change
                    # that starts emitting a blast-radius warning cannot produce
                    # two rows the owner has to acknowledge twice. This keeps the
                    # acknowledge flow (POST /checklist-runs/{run_id}/acknowledge,
                    # which validates submitted ids against run.warnings) working
                    # unchanged for the new ids.
                    # The evidence-gate warnings merge on the same terms and
                    # through the same de-duplication: whatever forced a
                    # downgrade must be visible to the owner as a warning with a
                    # stated reason, or 'block' arrives with no explanation.
                    derived = evidence_warnings + derive_blast_radius_warnings(
                        blast_radius
                    )
                    existing_ids = {w.warning_id for w in report.warnings}
                    merged_warnings = list(report.warnings)
                    for w in derived:
                        if w.warning_id not in existing_ids:
                            merged_warnings.append(w)
                            existing_ids.add(w.warning_id)
                    run_obj.warnings = [w.model_dump() for w in merged_warnings]
                    # BLR-02: the hash lands in the same transaction as
                    # status/report/warnings. Acknowledgement (the sibling
                    # timestamp column) is never stamped here — that is the
                    # owner's act at approve time, not the platform's at
                    # checklist time.
                    run_obj.envelope_hash = envelope_hash
                    db.commit()
                    db.refresh(run_obj)

            log.info(
                "run_deployment_checklist.complete",
                agent_id=agent_id,
                run_id=run_id,
                recommendation=report.recommendation,
            )
            return {
                "status": "complete",
                "run_id": run_id,
                "recommendation": report.recommendation,
            }
        else:
            # Orchestrator did not call submit_report — treat as failure
            log.error(
                "run_deployment_checklist.no_report",
                agent_id=agent_id,
                run_id=run_id,
            )
            raise RuntimeError("Orchestrator did not produce a report")

    except Exception as exc:
        # ------------------------------------------------------------------
        # Step 7 — UPDATE status=failed on exception; retry if retries remain
        # ------------------------------------------------------------------
        log.error(
            "run_deployment_checklist.failed",
            agent_id=agent_id,
            run_id=run_id,
            error=str(exc),
        )
        try:
            with get_sync_db() as db:
                run_obj = db.get(ChecklistRun, run_id)
                if run_obj:
                    run_obj.status = "failed"
                    db.commit()
        except Exception as update_exc:
            log.warning(
                "run_deployment_checklist.update_failed_status_error",
                agent_id=agent_id,
                run_id=run_id,
                error=str(update_exc),
            )

        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=2 ** self.request.retries)
        return {}


def _orchestrator_ledger(
    tenant_id: str, agent_id: str, run_id: str, conn_str: str
) -> LedgerContext:
    """The orchestrator's turn is billed to the tenant whose agent it assesses.

    The row lands in that tenant's own ledger and the checklist run is the job,
    mirroring red_team.py's _run_ledger. conn_str reaches the recorder and
    nothing else: no carrier and no ledger row has a field for it (rule 1).
    """
    return LedgerContext(
        tenant_id=tenant_id,
        agent_id=agent_id,
        job_id=run_id,
        recorder=ledger_recorder(conn_str),
    )


async def _call_orchestrator_async(
    signals_json: str, result_container: dict, *, ledger: LedgerContext
) -> None:
    """Thin async shim that awaits the service's orchestrator loop.

    The service's run_orchestrator bridge is synchronous and calls asyncio.run
    itself. wait_for needs an awaitable, so this shim awaits the loop directly
    and avoids a nested asyncio.run(), which raises RuntimeError in Python 3.12.
    """
    from app.services.deployment_service import _run_orchestrator_loop
    await _run_orchestrator_loop(signals_json, result_container, ledger=ledger)
