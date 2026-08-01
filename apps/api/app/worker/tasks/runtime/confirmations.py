"""
worker.tasks.runtime.confirmations — ACT-07's runtime-queue execution task.

resolve_approved_confirmation(confirmation_id) is the sole task in this
module. It is dispatched by
app.api.v1.pending_confirmations.resolve_pending_confirmation AFTER the
atomic claim has committed (OD-6) and ONLY when the claimed resolution is
'approved' and the claimed skill is a key of SKILL_INPUT_MODELS — a rejected,
expired, or non-mutating-skill row is never dispatched here.

Takes only the confirmation id (CLAUDE.md rule 4) — never a connection
string, the arguments, or the agent id. Everything else is re-read inside
the task body from the control DB, which is also what makes the task's view
of the row the POST-CLAIM view rather than whatever the route happened to
hold at dispatch time.

Idempotency (CLAUDE.md rule 5's second half) is provided entirely by the
fresh idempotency reservation execute_approved_confirmation takes inside
itself (22-02) — NOT by a second bespoke existence check here. A redelivered
task (acks_late=True's own retry path) re-enters that function and finds a
replay-or-in-progress reservation state rather than executing the adapter a
second time (T-22-ACT-13). Do not add a second guard here; it would only
drift from the resolver's own reservation over time.
"""

from __future__ import annotations

import asyncio

import structlog

from app.core.database import get_sync_db
from app.core.security import fernet_decrypt
from app.models.agent import Agent
from app.models.pending_confirmation import PendingConfirmation
from app.services.transactional.confirmation_resolution import execute_approved_confirmation
from app.worker.celery_app import celery_app

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    acks_late=True,
    queue="runtime",
    name="app.worker.tasks.runtime.confirmations.resolve_approved_confirmation",
)
def resolve_approved_confirmation(self, confirmation_id: str) -> dict:
    """Execute a human-approved pending_confirmations row.

    WR-07 (22-REVIEW-FIX.md): this task previously declared
    `max_retries=2, default_retry_delay=5` on the decorator, but the task
    body never calls `self.retry(...)` and the decorator carried no
    `autoretry_for=` — those two kwargs did nothing. They read as a bounded,
    automatic Celery-level retry that this task does not actually have,
    which is misleading both to a future maintainer and to CR-01's
    crash-safety argument (this module's own docstring, above). Removed
    rather than wired: distinguishing which failures inside
    execute_approved_confirmation are safely self.retry()-able (a transient
    DB connectivity blip) from which are not (a business-logic denial that
    would just be denied identically on retry) is a design decision this fix
    pass is not making unilaterally. `bind=True` is kept — `self` is still a
    normal, harmless parameter even with no retry call using it.

    Args:
        confirmation_id: UUID string of the pending_confirmations row to
            execute. The row is re-read from the control DB inside this
            function body — this is the ONLY argument (CLAUDE.md rule 4);
            never a connection string, the arguments, or the agent id.

    Returns:
        A status dict. Never raises for a missing row, a mismatched state,
        or a missing agent — a redelivered or stale task finding a row
        already resolved differently (or gone) is an expected outcome, not
        a task failure.

    Security:
        conn_str is decrypted at runtime from agent.neon_connection_string
        (Fernet) and NEVER appears in task args, logs, or return values
        (CLAUDE.md rule 4).
    """
    with get_sync_db() as db:
        # ------------------------------------------------------------------
        # 1. Re-read the row by id. Missing row -> report, do not raise.
        # ------------------------------------------------------------------
        confirmation = db.get(PendingConfirmation, confirmation_id)
        if confirmation is None:
            log.error(
                "resolve_approved_confirmation.not_found",
                confirmation_id=confirmation_id,
            )
            return {"status": "not_found", "confirmation_id": confirmation_id}

        # ------------------------------------------------------------------
        # 2. Guard the row's state. A task that arrives for a rejected,
        #    expired, or somehow-unresolved row must do nothing, not
        #    re-decide what the route already claimed.
        # ------------------------------------------------------------------
        if confirmation.resolved_at is None or confirmation.resolution != "approved":
            log.info(
                "resolve_approved_confirmation.not_approved",
                confirmation_id=confirmation_id,
                resolution=confirmation.resolution,
            )
            return {
                "status": "not_approved",
                "confirmation_id": confirmation_id,
                "resolution": confirmation.resolution,
            }

        agent_id = str(confirmation.agent_id)
        skill = confirmation.skill
        arguments = confirmation.arguments

        # ------------------------------------------------------------------
        # 3. Load the agent. Missing agent -> report, do not raise.
        # ------------------------------------------------------------------
        agent = db.get(Agent, confirmation.agent_id)
        if agent is None:
            log.error(
                "resolve_approved_confirmation.agent_not_found",
                confirmation_id=confirmation_id,
                agent_id=agent_id,
            )
            return {"status": "agent_not_found", "confirmation_id": confirmation_id}

        # ------------------------------------------------------------------
        # 4. Decrypt connection string at runtime — NEVER in task args
        #    (CLAUDE.md rule 4). conn_str is intentionally not logged.
        # ------------------------------------------------------------------
        conn_str = fernet_decrypt(agent.neon_connection_string)

    # ----------------------------------------------------------------------
    # 5. Bridge into the async resolver, matching how run_agent_turn bridges
    #    its own synchronous task body into an event loop.
    # ----------------------------------------------------------------------
    outcome = asyncio.run(
        execute_approved_confirmation(
            confirmation_id=confirmation_id,
            agent_id=agent_id,
            skill=skill,
            arguments=arguments,
            conn_str=conn_str,
        )
    )

    log.info(
        "resolve_approved_confirmation.done",
        confirmation_id=confirmation_id,
        agent_id=agent_id,
        skill=skill,
        outcome=outcome.outcome,
    )
    return {
        "status": outcome.outcome,
        "confirmation_id": confirmation_id,
        "reason": outcome.reason,
    }
