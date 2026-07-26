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
    4. Collect all 5 signals synchronously (4 via psycopg2 against the tenant DB,
       the 5th — blast_radius — via get_sync_db() against the control DB)
    5. Call run_orchestrator(signals_json, result_container) via asyncio.run bridge
    6. Parse result and UPDATE checklist_runs row to status='complete'; merge the
       deterministic blast-radius warnings into the persisted warnings list,
       de-duplicated by warning_id
    7. On exception: UPDATE checklist_runs row to status='failed'; retry if retries < max_retries
"""

from __future__ import annotations

import asyncio
import json
import uuid

import structlog
from sqlalchemy import select, text

from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.models.checklist_run import ChecklistRun
from app.services.deployment_service import (
    BLAST_RADIUS_DEFAULT_SIGNAL,
    DeploymentReport,
    _fetch_blast_radius_sync,
    _fetch_corpus_stats_sync,
    _fetch_eval_summary_sync,
    _fetch_red_team_summary_sync,
    _fetch_verified_qa_stats_sync,
    derive_blast_radius_warnings,
    run_orchestrator,
)
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)


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
        4. Collect all 4 signals synchronously (psycopg2 against tenant DB).
        5. Call run_orchestrator via asyncio.run(asyncio.wait_for(..., timeout=120.0)) bridge.
        6. Parse result and UPDATE checklist_runs to status='complete'.
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
        eval_summary = {
            "last_run_at": None,
            "scenario_count": 0,
            "pass_rates": {},
            "failing_scenarios": 0,
        }

    try:
        red_team_summary = _fetch_red_team_summary_sync(agent_id, conn_str)
    except Exception as exc:
        log.warning(
            "run_deployment_checklist.red_team_summary_fetch_failed",
            agent_id=agent_id,
            error=str(exc),
        )
        red_team_summary = {
            "last_run_at": None,
            "deployment_blocked": False,
            "critical_count": 0,
            "high_count": 0,
            "medium_count": 0,
            "low_count": 0,
        }

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
            # Validate via Pydantic — ensures recommendation is a known value
            report = DeploymentReport(
                recommendation=report_data.get("recommendation", "block"),
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
                    derived = derive_blast_radius_warnings(blast_radius)
                    existing_ids = {w.warning_id for w in report.warnings}
                    merged_warnings = list(report.warnings) + [
                        w for w in derived if w.warning_id not in existing_ids
                    ]
                    run_obj.warnings = [w.model_dump() for w in merged_warnings]
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
