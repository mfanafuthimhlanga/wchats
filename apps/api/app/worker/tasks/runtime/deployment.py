"""
M8 Deployment Checklist Celery task: run_deployment_checklist.

Lives in the `runtime` queue.

Architecture constraints (CLAUDE.md — non-negotiable):
    - acks_late=True AND idempotency guard on every Celery task (both always required)
    - run_deployment_checklist receives only `agent_id` — NEVER a conn_str in task args (CTL-08)
    - conn_str is fetched from the control DB and decrypted at runtime via fernet_decrypt
    - asyncio.run(asyncio.wait_for(..., timeout=120.0)) bridge — never loop.run_until_complete

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
    4. Collect all 5 signals plus the BLR-02 envelope hash synchronously (4
       signals via psycopg2 against the tenant DB, the 5th signal —
       blast_radius — and the envelope hash both via get_sync_db() against
       the control DB)
    4b. If the agent has never been evaluated, start its first eval suite
       (_dispatch_first_eval_run) and record that on the eval signal. The
       verdict is unaffected — an eval that is running is not evidence — but the
       block becomes one the owner can wait out rather than a dead end no route
       in the primary journey can clear.
    5. Call run_orchestrator(signals_json, result_container) via asyncio.run bridge
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

import structlog
from sqlalchemy import select, text

from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.models.checklist_run import ChecklistRun
from app.services.deployment_service import (
    BLAST_RADIUS_DEFAULT_SIGNAL,
    EVAL_SIGNAL_NO_RUNS,
    EVAL_SUMMARY_UNAVAILABLE_SIGNAL,
    RED_TEAM_SUMMARY_UNAVAILABLE_SIGNAL,
    DeploymentReport,
    _compute_envelope_hash_sync,
    _fetch_blast_radius_sync,
    _fetch_corpus_stats_sync,
    _fetch_eval_summary_sync,
    _fetch_red_team_summary_sync,
    _fetch_verified_qa_stats_sync,
    apply_signal_evidence_gate,
    derive_blast_radius_warnings,
)
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)


def _dispatch_first_eval_run(agent_id: str) -> bool:
    """Start this agent's first eval suite. Returns True iff it was dispatched.

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
        log.info("run_deployment_checklist.first_eval_dispatched", agent_id=agent_id)
        return True
    except Exception as exc:
        log.warning(
            "run_deployment_checklist.first_eval_dispatch_failed",
            agent_id=agent_id,
            error=str(exc),
        )
        return False


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

    Collects quality signals from the tenant DB, calls the Agent SDK orchestrator,
    and records the recommendation in the control DB checklist_runs table.

    Receives agent_id str — no conn_str in args (CTL-08 / CLAUDE.md non-negotiable).

    Sequence:
        1. Fetch agent from control DB; decrypt conn_str at runtime.
        2. Idempotency guard — skip if a 'running' checklist_run for this agent
           was created within the last 60 minutes.
        3. Insert checklist_runs row (status='running') in control DB via ORM.
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

    # ------------------------------------------------------------------
    # Step 2 — Idempotency guard: check checklist_runs for a recent running row
    # Uses control DB (ORM) — NOT psycopg2 against tenant DB.
    # 60-minute window (longer than red_team.py's 30-minute window) because
    # deployment checklist involves a Sonnet call with 120s timeout.
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

    # Step 4b — the day-1 remedy. An agent that has never been evaluated cannot
    # ship, and until now nothing in the product's primary journey would ever
    # produce the eval that unblocks it. The dispatch is recorded ON the signal
    # so the owner-facing warning can say "we started it" instead of naming a
    # page the onboarding flow does not route to. It does not soften the
    # verdict: apply_signal_evidence_gate still blocks on the signal, because a
    # measurement that has been STARTED is not a measurement.
    if eval_summary.get("eval_signal") == EVAL_SIGNAL_NO_RUNS:
        eval_summary["first_eval_dispatched"] = _dispatch_first_eval_run(agent_id)

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
    # Step 5 — Call Agent SDK orchestrator via asyncio.run bridge
    # asyncio.run(asyncio.wait_for(..., timeout=120.0)) — CTL-08 rule.
    # run_orchestrator is a sync bridge that internally calls asyncio.run,
    # so we call it directly here (no nested asyncio.run needed).
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

    try:
        asyncio.run(
            asyncio.wait_for(
                _call_orchestrator_async(signals_json, result_container),
                timeout=120.0,
            )
        )
    except Exception as exc:
        log.error(
            "run_deployment_checklist.orchestrator_failed",
            agent_id=agent_id,
            run_id=run_id,
            error=str(exc),
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
                recommendation=gated_recommendation,
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


async def _call_orchestrator_async(signals_json: str, result_container: dict) -> None:
    """Thin async shim that calls run_orchestrator's internal loop.

    run_orchestrator is a sync function that calls asyncio.run internally.
    We need an awaitable to pass to asyncio.wait_for, so this shim calls
    the service's _run_orchestrator_loop directly via an import.

    This avoids a nested asyncio.run() which would raise RuntimeError in Python 3.12.
    """
    from app.services.deployment_service import _run_orchestrator_loop
    await _run_orchestrator_loop(signals_json, result_container)
