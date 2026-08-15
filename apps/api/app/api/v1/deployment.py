"""Deployment checklist routes for W Chats M8.

Manages the pre-deployment checklist lifecycle for agents.
All routes require X-API-Key or Bearer auth via get_current_tenant.
IDOR prevented by verifying agent.tenant_id == tenant.id on every route.

Routes:
    POST /agents/{agent_id}/checklist-runs                            — trigger checklist (202)
    GET  /agents/{agent_id}/checklist-runs                           — list runs (most-recent-first, limit 10)
    GET  /agents/{agent_id}/checklist-runs/{run_id}                  — single run detail with report
    POST /agents/{agent_id}/checklist-runs/{run_id}/acknowledge      — acknowledge warnings
    POST /agents/{agent_id}/approve-deployment                       — approve and flip is_deployed

Phase 18 BLR-02: both checklist reads (list and detail) now carry
envelope_drift, computed from one live capability-envelope hash query per
request, and approve-deployment gates on a fourth server-side validation —
drift between the live envelope and the hash the checklist run recorded.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_tenant
from app.core.database import get_async_db
from app.models.agent import Agent
from app.models.capability_envelope import CapabilityEnvelope
from app.models.checklist_run import ChecklistRun
from app.models.tenant import Tenant
from app.schemas.deployment import (
    AcknowledgeRequest,
    ApproveDeploymentRequest,
)
from app.services.capability_service import canonical_envelope_hash, envelope_drift
from app.services.deployment_service import (
    STORED_RUN_NOT_INVOKED_DETAIL,
    _make_iframe_snippet,
    stored_run_records_agent_invocation,
)
from app.worker.tasks.runtime.deployment import run_deployment_checklist

router = APIRouter(tags=["deployment"])
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal helper — convert ORM object to response dict
# ---------------------------------------------------------------------------


async def _fetch_envelope_rows(agent_id: UUID, db: AsyncSession) -> list[dict]:
    """Async twin of deployment_service._fetch_envelope_rows_sync (BLR-02).

    This projection must stay byte-for-byte equivalent in field set to the
    sync reader — any divergence between the two makes the approve gate fire
    on every single deploy, since the checklist task and this route would
    then be hashing different data for what is supposed to be the identical
    live configuration. id and updated_at are deliberately not selected.
    """
    result = await db.execute(
        select(
            CapabilityEnvelope.skill,
            CapabilityEnvelope.enabled,
            CapabilityEnvelope.rate_limit,
            CapabilityEnvelope.constraints,
            CapabilityEnvelope.requires_confirmation,
            CapabilityEnvelope.requires_identity_verification,
            CapabilityEnvelope.actor_mode,
        )
        .where(CapabilityEnvelope.agent_id == agent_id)
        .order_by(CapabilityEnvelope.skill)
    )
    return [dict(r) for r in result.mappings().all()]


async def _current_envelope_hash(agent_id: UUID, db: AsyncSession) -> str:
    """Compute the live canonical envelope hash for this agent (BLR-02)."""
    rows = await _fetch_envelope_rows(agent_id, db)
    return canonical_envelope_hash(rows)


def _run_to_dict(run: ChecklistRun, live_envelope_hash: str | None = None) -> dict:
    """Convert a ChecklistRun ORM object to a dict matching ChecklistRunResponse.

    live_envelope_hash is optional because this is a plain sync function with
    no DB access of its own — callers compute the live hash once per request
    (not once per run) and pass it in. When omitted, envelope_drift is
    reported as True: an unknown live hash is never evidence of a match, the
    same fail-closed direction envelope_drift itself takes for a missing
    recorded hash.
    """
    return {
        "id": str(run.id),
        "agent_id": str(run.agent_id),
        "status": run.status,
        "recommendation": run.recommendation,
        "report": run.report,
        "warnings": run.warnings if run.warnings is not None else [],
        "warning_acknowledgments": run.warning_acknowledgments if run.warning_acknowledgments is not None else {},
        "all_warnings_acknowledged": run.all_warnings_acknowledged,
        "approved_at": run.approved_at,
        "approved_by": run.approved_by,
        "created_at": run.created_at,
        "envelope_hash": run.envelope_hash,
        "envelope_acknowledged_at": run.envelope_acknowledged_at,
        "envelope_drift": (
            envelope_drift(live_envelope_hash, run.envelope_hash)
            if live_envelope_hash is not None
            else True
        ),
    }


# ---------------------------------------------------------------------------
# Route 1: POST /agents/{agent_id}/checklist-runs — trigger checklist (202)
# ---------------------------------------------------------------------------


@router.post("/agents/{agent_id}/checklist-runs", status_code=202)
async def trigger_checklist_run(
    agent_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Dispatch run_deployment_checklist for an agent and return 202 immediately.

    Security:
        Agent ownership verified (IDOR prevention).
        Agent must be in 'ready' state — 400 otherwise.

    Celery:
        Dispatches run_deployment_checklist.apply_async(kwargs={"agent_id": str(agent_id)},
        queue="runtime"). Only agent_id is passed — no connection string in task
        args (CTL-08 / CLAUDE.md non-negotiable).

    Returns HTTP 202 immediately. Poll GET /checklist-runs to detect completion.

    Response: {"checklist_run_id": str, "status": "queued"}
    """
    # 1. Fetch agent from control DB
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 2. IDOR check — agent must belong to the authenticated tenant
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 3. Guard: agent must be ready before running the checklist
    if agent.status != "ready":
        raise HTTPException(status_code=400, detail="Agent must be in ready state")

    # 4. Dispatch Celery task — only agent_id passed; no connection string in args (CTL-08)
    task = run_deployment_checklist.apply_async(
        kwargs={"agent_id": str(agent_id)},
        queue="runtime",
    )

    log.info(
        "checklist_trigger.dispatched",
        agent_id=str(agent_id),
        task_id=task.id,
        tenant_id=str(tenant.id),
    )

    return {"checklist_run_id": task.id, "status": "queued"}


# ---------------------------------------------------------------------------
# Route 2: GET /agents/{agent_id}/checklist-runs — list runs
# ---------------------------------------------------------------------------


@router.get("/agents/{agent_id}/checklist-runs")
async def list_checklist_runs(
    agent_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Return up to 10 checklist runs for an agent, most-recent-first.

    Security:
        Fetches agent from control DB and checks agent.tenant_id == tenant.id (IDOR prevention).

    Response shape:
        {"runs": [{id, agent_id, status, recommendation, report, warnings, ...}]}
    """
    # 1. Fetch agent from control DB
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 2. IDOR check — agent must belong to the authenticated tenant
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 3. Compute the live envelope hash once for the whole page — a single
    #    query per request, not one per run (T-18-CAP-03 DoS disposition).
    live_hash = await _current_envelope_hash(agent_id, db)

    # 4. Query control DB via SQLAlchemy async ORM (checklist_runs is in control DB)
    result = await db.execute(
        select(ChecklistRun)
        .where(ChecklistRun.agent_id == agent_id)
        .order_by(ChecklistRun.created_at.desc())
        .limit(10)
    )
    runs = result.scalars().all()

    log.info(
        "list_checklist_runs.ok",
        agent_id=str(agent_id),
        tenant_id=str(tenant.id),
        run_count=len(runs),
    )
    return {"runs": [_run_to_dict(r, live_envelope_hash=live_hash) for r in runs]}


# ---------------------------------------------------------------------------
# Route 3: GET /agents/{agent_id}/checklist-runs/{run_id} — single run detail
# ---------------------------------------------------------------------------


@router.get("/agents/{agent_id}/checklist-runs/{run_id}")
async def get_checklist_run(
    agent_id: UUID,
    run_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Return a single checklist run with full report JSONB.

    Security:
        Same IDOR prevention as list — agent ownership verified.
        Also verifies run.agent_id == agent_id (T-08-04-04).

    Response shape:
        {"run": {id, agent_id, status, recommendation, report, warnings, ...}}
    """
    # 1. Fetch agent from control DB
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 2. IDOR check — agent must belong to the authenticated tenant
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 3. Fetch checklist run — also verifies run belongs to this agent
    run = await db.get(ChecklistRun, run_id)
    if run is None or run.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="Checklist run not found")

    # 4. Compute the live envelope hash once for this read.
    live_hash = await _current_envelope_hash(agent_id, db)

    log.info(
        "get_checklist_run.ok",
        agent_id=str(agent_id),
        run_id=str(run_id),
        tenant_id=str(tenant.id),
    )
    return {"run": _run_to_dict(run, live_envelope_hash=live_hash)}


# ---------------------------------------------------------------------------
# Route 4: POST /agents/{agent_id}/checklist-runs/{run_id}/acknowledge
# ---------------------------------------------------------------------------


@router.post("/agents/{agent_id}/checklist-runs/{run_id}/acknowledge")
async def acknowledge_warnings(
    agent_id: UUID,
    run_id: UUID,
    body: AcknowledgeRequest,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Acknowledge one or more warnings on a completed checklist run.

    Security:
        IDOR check on agent. Run ownership verified against agent_id.
        T-08-04-03: Only warning_ids present in run.warnings are accepted —
        arbitrary injection of unknown warning_ids is rejected (422).

    Updates warning_acknowledgments JSONB and recalculates all_warnings_acknowledged.

    Response shape:
        {"all_warnings_acknowledged": bool}
    """
    # 1. Fetch agent from control DB
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 2. IDOR check — agent must belong to the authenticated tenant
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 3. Fetch run — verify ownership
    run = await db.get(ChecklistRun, run_id)
    if run is None or run.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="Checklist run not found")

    # 4. Guard: can only acknowledge warnings on a completed run
    if run.status != "complete":
        raise HTTPException(status_code=422, detail="Checklist not yet complete")

    # 5. T-08-04-03: validate submitted warning_ids against actual warnings in the run
    #    Only IDs present in run.warnings are accepted — prevents arbitrary JSONB injection.
    valid_warning_ids = {w["warning_id"] for w in (run.warnings or []) if isinstance(w, dict)}
    invalid_ids = [wid for wid in body.warning_ids if wid not in valid_warning_ids]
    if invalid_ids:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown warning_ids: {invalid_ids}",
        )

    # 6. Update warning_acknowledgments JSONB
    acks = dict(run.warning_acknowledgments or {})
    now_iso = datetime.now(timezone.utc).isoformat()
    for wid in body.warning_ids:
        acks[wid] = now_iso

    # 7. Recalculate all_warnings_acknowledged
    all_ids = {w["warning_id"] for w in (run.warnings or []) if isinstance(w, dict)}
    run.all_warnings_acknowledged = all_ids.issubset(set(acks.keys()))
    run.warning_acknowledgments = acks
    await db.commit()

    log.info(
        "acknowledge_warnings.ok",
        agent_id=str(agent_id),
        run_id=str(run_id),
        all_acknowledged=run.all_warnings_acknowledged,
    )
    return {"all_warnings_acknowledged": run.all_warnings_acknowledged}


# ---------------------------------------------------------------------------
# Route 5: POST /agents/{agent_id}/approve-deployment
# ---------------------------------------------------------------------------


async def _refuse_if_a_critical_finding_is_open(agent: Agent) -> None:
    """422 when the agent has an open critical red-team finding, right now.

    BACKLOG `5.1`. `red_team_findings` lives in the TENANT database and this
    route only ever touched the control database, which is the mechanical
    reason it could not consult live findings — it had nothing to read them
    with. The agent's own encrypted connection string is that "something".

    Fail-closed on purpose, in both directions:
      - an unreadable connection string refuses rather than waving the deploy
        through, because "I could not check" is not "there is nothing to find";
      - the check runs on every approval, not only when the frozen
        recommendation is already suspicious.

    Runs in a worker thread: `_fetch_red_team_summary_sync` is blocking psycopg2
    and this is an async route, so calling it inline would stall the event loop
    for the duration of a cross-region round trip.
    """
    import asyncio

    from app.core.security import fernet_decrypt, require_ciphertext
    from app.services.deployment_service import _fetch_red_team_summary_sync

    try:
        conn_str = fernet_decrypt(
            require_ciphertext(
                agent.neon_connection_string, "agents.neon_connection_string"
            )
        )
        summary = await asyncio.to_thread(
            _fetch_red_team_summary_sync, str(agent.id), conn_str
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — fail closed, see docstring
        log.error(
            "approve_deployment.live_finding_check_failed",
            agent_id=str(agent.id),
            error_type=type(exc).__name__,
            error=str(exc) or repr(exc),
        )
        raise HTTPException(
            status_code=422,
            detail=(
                "Could not verify the agent's live red-team findings, so the "
                "deployment cannot be approved. Retry once the tenant database "
                "is reachable."
            ),
        ) from exc

    if summary.get("deployment_blocked"):
        critical = summary.get("critical_count")
        log.warning(
            "approve_deployment.refused_open_critical_finding",
            agent_id=str(agent.id),
            critical_count=critical,
        )
        raise HTTPException(
            status_code=422,
            detail=(
                f"Cannot approve: {critical} critical red-team finding(s) are open "
                "against this agent right now. Contain them and re-run the checklist."
            ),
        )


@router.post("/agents/{agent_id}/approve-deployment")
async def approve_deployment(
    agent_id: UUID,
    body: ApproveDeploymentRequest,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict:
    """Approve deployment after checklist passes all gates.

    Security:
        IDOR check on agent. Checklist run ownership verified.
        T-08-04-02: Server-side validation before any mutation —
        blocked or incomplete runs are rejected (422).

    Validation sequence (CONTEXT.md §Approval Validation; Phase 18 BLR-02 adds
    #4; audit D1 / P3 review adds #3b):
        1. run.status != "complete"     → 422 "Checklist is still running"
        2. recommendation == "block"    → 422 "Cannot approve a blocked deployment..."
        3. ship_with_warnings + not all acknowledged → 422 "Acknowledge all warnings..."
        3b. the run's own report does not record eval_summary.agent_invoked is
           True → 422. `recommendation` is frozen at checklist time, so a run
           completed before the D1 gate landed still says 'ship' over an eval
           that scored the dataset's own reference answers. Absence, falsehood
           and an unreadable report shape all fail identically.
        4. live envelope hash drifted from the run's recorded hash (or the
           recorded hash is NULL — an absent acknowledgement is drift, never
           a match) → 422 "Capability envelope changed..."

    On success:
        - agent.is_deployed = True
        - run.approved_at = now()
        - run.approved_by = str(tenant.id)
        - run.envelope_acknowledged_at is stamped to now() — the approve
          call IS the BLR-02 acknowledgement gesture; no separate endpoint exists

    Response shape:
        {"deployed": True, "agent_id": str, "iframe_snippet": str}
    """
    # 1. Fetch agent from control DB
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 2. IDOR check — agent must belong to the authenticated tenant
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 3. Fetch checklist run by body.checklist_run_id — verify it belongs to this agent
    run = await db.get(ChecklistRun, body.checklist_run_id)
    if run is None or run.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="Checklist run not found")

    # 4. Approval validation sequence (CONTEXT.md §Approval Validation — T-08-04-02)
    if run.status != "complete":
        raise HTTPException(status_code=422, detail="Checklist is still running")
    if run.recommendation == "block":
        raise HTTPException(
            status_code=422,
            detail="Cannot approve a blocked deployment — resolve critical issues first",
        )
    if run.recommendation == "ship_with_warnings" and not run.all_warnings_acknowledged:
        raise HTTPException(
            status_code=422,
            detail="Acknowledge all warnings before approving",
        )
    # 2b. BACKLOG 5.1 / OPS-15 server-side gap. Guard 2 above validates the
    #     recommendation FROZEN at checklist time. A critical finding raised
    #     AFTER that — a red-team run finishing minutes later, or a human filing
    #     one — does not change it, so the API deployed an agent with an open
    #     critical finding against it. Observed doing exactly that in E2E-5:
    #     `recommendation=ship` then `deployment.approved`, 200.
    #
    #     The console was fixed to refuse; any script, curl or CI job holding a
    #     tenant key still took this path, so "fails closed" was true of the UI
    #     and not of the API.
    #
    #     This re-read deliberately calls the SAME function the checklist uses
    #     (`_fetch_red_team_summary_sync`) rather than a second hand-written
    #     query. Two readers of the same rule are how the approve-time answer
    #     and the checklist-time answer drift apart — which is the whole shape
    #     of this defect. `2.19` is the general form; the eval sibling of this
    #     hole was closed the same way at `8b124d4`, by re-reading at approve
    #     time instead of trusting the frozen verdict.
    await _refuse_if_a_critical_finding_is_open(agent)
    # 3b. Audit D1 / P3 review: the stored run's own eval evidence must claim
    #     the agent was invoked. `recommendation` is FROZEN at checklist time by
    #     whatever gate was running that day, so refusing an uninvoked run in
    #     apply_signal_evidence_gate closes nothing for a run that already
    #     completed — and every run completed before this release carries a
    #     'ship' computed over the tautology at eval.py:374-375. This is the
    #     same read the gate makes, made again here against the artifact the
    #     approve decision is actually taken from. Fail-closed on any shape it
    #     cannot read, exactly as the NULL envelope hash below does.
    #
    #     Placed AHEAD of the envelope check and BEHIND the three shipped
    #     validations: a blocked or incomplete run still reports its own, more
    #     severe 422, but an owner whose run measured nothing needs to know
    #     they must run a fresh eval FIRST, which is a step the envelope-drift
    #     message ("re-run the checklist") does not mention.
    if not stored_run_records_agent_invocation(run.report):
        raise HTTPException(status_code=422, detail=STORED_RUN_NOT_INVOKED_DETAIL)
    # 4b. BLR-02: the live capability envelope must still match what this
    #     checklist run recorded. envelope_drift returns True for a NULL
    #     recorded hash too — a pre-0019 historical run or a run whose hash
    #     collector failed is unapprovable, the deliberate fail-closed
    #     direction. This check runs after the three checks above, so a
    #     blocked or incomplete run still reports its own, more severe 422.
    live_hash = await _current_envelope_hash(agent_id, db)
    if envelope_drift(live_hash, run.envelope_hash):
        raise HTTPException(
            status_code=422,
            detail="Capability envelope changed since this checklist ran — re-run the checklist.",
        )

    # 5. Flip is_deployed and stamp approval metadata
    agent.is_deployed = True
    run.approved_at = datetime.now(timezone.utc)
    run.approved_by = str(tenant.id)
    # The approve call IS the BLR-02 acknowledgement gesture — no separate
    # acknowledgement endpoint exists for the envelope hash.
    run.envelope_acknowledged_at = datetime.now(timezone.utc)
    await db.commit()

    log.info(
        "deployment.approved",
        agent_id=str(agent_id),
        run_id=str(run.id),
        tenant_id=str(tenant.id),
        envelope_hash=run.envelope_hash,
    )

    return {
        "deployed": True,
        "agent_id": str(agent_id),
        "iframe_snippet": _make_iframe_snippet(str(agent_id)),
    }
