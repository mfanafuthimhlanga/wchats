"""
transactional.confirmation_resolution — ACT-07's execution core.

Terminates a human approval of a `pending_confirmations` row. A human clicking
Approve is not, by itself, an executed mutation — this module is the narrow
dispatcher subset that turns "approved" into "executed", re-running every
check whose live answer can have changed since the confirmation was created,
and skipping only what a resolver structurally cannot or must not re-run.

Re-run (against the LIVE state, never a stored snapshot):
    Step 2   — check_capability_access:            has the owner disabled this
                                                     skill, or does no envelope
                                                     row exist any more?
    Step 3   — reserve_idempotency (fresh):          a Celery redelivery of the
                                                     resolve task must find
                                                     replay/in_progress/unknown,
                                                     never re-execute the adapter
                                                     a second time (T-22-ACT-13,
                                                     CLAUDE.md rule 5's
                                                     idempotency half). CR-01
                                                     closed the gap where a
                                                     redelivery arriving after
                                                     _RESERVATION_LEASE_SECONDS
                                                     (120s) but within Celery's
                                                     broker visibility_timeout
                                                     (3600s) would have found a
                                                     stale 'pending' row and
                                                     silently reclaimed it: the
                                                     row is now flipped to
                                                     'in_flight' before the
                                                     adapter call
                                                     (mark_reservation_in_flight),
                                                     and a stale 'in_flight' row
                                                     is NEVER auto-reclaimed — it
                                                     surfaces as "unknown"
                                                     instead (idempotency.py's
                                                     CR-01 anchor).
    Step 4   — apply_rate_and_constraint_checks:     has the owner tightened
                                                     the ceiling or rate limit
                                                     below what the confirmation
                                                     was created under?
    Step 6-7 — _execute_adapter_and_audit (tools.py): the shared adapter-call
                                                     and audit-write implementation
                                                     ALSO used by the live-turn
                                                     dispatcher (T-22-ACT-15) —
                                                     one implementation, two
                                                     callers.

Deliberately absent:
    Step 2.5 — the identity-verification gate is not re-entered. There is no
        customer session available to a resolver — a resolver runs outside any
        live agent turn, hours after the request that created the confirmation
        row, driven by an administrator's click rather than a customer message.
        Approving an action and verifying a customer are different acts, and a
        resolver conflating them by manufacturing a stand-in session would
        assert something false in the audit trail. This is OD-1
        (`22-01-PLAN.md § Open Decisions Resolved`), accepted as a named,
        bounded residual (`T-22-ACT-08`, severity medium): the request that
        created this row was itself gated by that same check at creation
        time, and the confirmation's 24-hour time-to-live bounds how stale
        that earlier verification can be by the time an approver acts on it.
    Step 5 — the seam that decides whether a mutating call needs human
        approval is not re-entered. A human's Approve click on this exact row
        IS that seam's verdict for this call — re-running the same decision
        function here would hand back the same "needs a human" answer a
        second time and the approval could never terminate. Re-entering it
        would also require restoring the exact live-turn calling context
        (the four per-turn values a live dispatch call reads implicitly),
        which a resolver — running from an admin route or a Celery task, with
        no active conversation — does not have and must not synthesize.

OD-5 (`22-01-PLAN.md § Open Decisions Resolved`): the resolver's execution
context is an explicit, keyword-only parameter contract — `agent_id` and
`conn_str` are threaded in by the caller (the claimed `pending_confirmations`
row plus the decrypted tenant connection string), never read from ambient
per-turn state. `conversation_id` is minted fresh inside this module
(`uuid4()`) because a resolver has no live turn to anchor to; it is
informational on the audit row only, never a join key back to a real chat —
matching the one shipped precedent for entering the dispatcher outside a live
turn (`red_team_probe.py`'s `_build_transactional_probe_fn`).

The two absences above are asserted by literal source-absence, not by this
docstring's word: a test reads this file from disk and asserts the two
forbidden call sites appear zero times, so a future change that quietly
re-adds either one trips a red test rather than a silent regression
(`test_confirmation_resolution.py::TestResolverAbsence`).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import structlog
from pydantic import ValidationError

from app.services.transactional.audit import write_audit_row
from app.services.transactional.enforcement import (
    apply_rate_and_constraint_checks,
    check_capability_access,
)
from app.services.transactional.idempotency import (
    compute_args_hash,
    release_idempotency,
    reserve_idempotency,
)
from app.services.transactional.schemas import SKILL_INPUT_MODELS
from app.services.transactional.tools import _execute_adapter_and_audit

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ResolutionOutcome:
    """Result of execute_approved_confirmation.

    outcome:
        One of "executed", "denied", "invalid", "replay", "in_progress".
    reason:
        The raw error string that was written to the tool_calls_audit row
        (or, for the terminal adapter step, the helper's error text) — None
        on a successful execution or on a no-op replay/in_progress return.
    response:
        The shared adapter-and-audit helper's SDK-shaped return dict on a
        successful execution; the stored result on a replay hit; None
        otherwise.
    """

    outcome: str
    reason: str | None = None
    response: dict | None = None


async def execute_approved_confirmation(
    *,
    confirmation_id: str,
    agent_id: str,
    skill: str,
    arguments: dict | None,
    conn_str: str,
) -> ResolutionOutcome:
    """Execute a human-approved pending_confirmations row against the LIVE envelope.

    All parameters are keyword-only (OD-5's explicit parameter contract — no
    ContextVar is read anywhere in this module).

    Args:
        confirmation_id: The pending_confirmations row id being resolved —
            used only as the audit-row rationale suffix, never as a lookup
            key here (the caller has already claimed and validated the row).
        agent_id: UUID string of the agent the confirmation belongs to.
        skill: Canonical skill name stored on the row (e.g. "issue_refund").
        arguments: The row's stored `arguments` JSONB, or None.
        conn_str: Decrypted tenant DB connection string, fetched by the
            caller — never read from a ContextVar.

    Returns:
        A ResolutionOutcome describing exactly what happened.
    """
    # Fresh per resolution — informational only on the audit row (see module
    # docstring). Never a join key back to a real conversation.
    conversation_id = str(uuid4())

    # ---------------------------------------------------------------- 1. Input guard
    # A confirm_action row (mutating=False) or any skill outside the six
    # mutating skills is not in SKILL_INPUT_MODELS and can never reach an
    # adapter. Checked before arguments so an unsupported skill is diagnosed
    # as such regardless of what its stored arguments look like.
    if skill not in SKILL_INPUT_MODELS:
        await write_audit_row(
            agent_id=agent_id,
            conversation_id=conversation_id,
            skill=skill,
            arguments=arguments,
            result=None,
            actor_decision="",
            actor_rationale="",
            capability_snapshot={},
            latency_ms=None,
            error="confirmation.unsupported_skill",
        )
        log.warning(
            "confirmation_resolution.unsupported_skill",
            agent_id=agent_id,
            skill=skill,
            outcome="invalid",
        )
        return ResolutionOutcome(outcome="invalid", reason="confirmation.unsupported_skill")

    if not isinstance(arguments, dict) or not arguments:
        await write_audit_row(
            agent_id=agent_id,
            conversation_id=conversation_id,
            skill=skill,
            arguments=arguments,
            result=None,
            actor_decision="",
            actor_rationale="",
            capability_snapshot={},
            latency_ms=None,
            error="confirmation.missing_arguments",
        )
        log.warning(
            "confirmation_resolution.missing_arguments",
            agent_id=agent_id,
            skill=skill,
            outcome="invalid",
        )
        return ResolutionOutcome(outcome="invalid", reason="confirmation.missing_arguments")

    # ---------------------------------------------------------------- 2. Re-validate
    # The stored arguments were written hours earlier by a live agent turn —
    # untrusted input to this resolver, re-validated through the same typed
    # Input model the live-turn dispatcher validates against.
    input_model = SKILL_INPUT_MODELS[skill]
    try:
        validated = input_model(**arguments)
    except ValidationError:
        await write_audit_row(
            agent_id=agent_id,
            conversation_id=conversation_id,
            skill=skill,
            arguments=arguments,
            result=None,
            actor_decision="",
            actor_rationale="",
            capability_snapshot={},
            latency_ms=None,
            error="confirmation.arguments_invalid",
        )
        log.warning(
            "confirmation_resolution.arguments_invalid",
            agent_id=agent_id,
            skill=skill,
            outcome="invalid",
        )
        return ResolutionOutcome(outcome="invalid", reason="confirmation.arguments_invalid")

    # ---------------------------------------------------------------- 3. Capability (LIVE)
    # SC3, capability half: this reads the live capability_envelopes row —
    # never the capability_snapshot stored on the confirmation's original
    # audit row. An owner who disabled or tightened this skill after the
    # confirmation was created is honoured here, not bypassed.
    snapshot, denial = await check_capability_access(agent_id, skill)
    if denial is not None:
        await write_audit_row(
            agent_id=agent_id,
            conversation_id=conversation_id,
            skill=skill,
            arguments=arguments,
            result=None,
            actor_decision="",
            actor_rationale="",
            capability_snapshot=snapshot,
            latency_ms=None,
            error=f"capability.denial:{denial}",
        )
        log.warning(
            "confirmation_resolution.capability_denied",
            agent_id=agent_id,
            skill=skill,
            outcome="denied",
        )
        return ResolutionOutcome(outcome="denied", reason=f"capability.denial:{denial}")

    # Step 2.5 (identity verification) is deliberately not re-entered here —
    # see the module docstring's "Deliberately absent" section (OD-1).

    # ---------------------------------------------------------------- 5. Idempotency (fresh)
    # A fresh reservation, keyed on the idempotency_key carried in the row's
    # own stored arguments. A Celery redelivery of the resolve task re-enters
    # this function and finds replay or in_progress here rather than
    # executing the adapter a second time (T-22-ACT-13).
    args_hash = compute_args_hash(arguments)
    reservation = await reserve_idempotency(agent_id, skill, validated.idempotency_key, args_hash)  # type: ignore[attr-defined]  # every SKILL_INPUT_MODELS member declares idempotency_key

    if reservation.state == "replay":
        log.info(
            "confirmation_resolution.idempotency_replay",
            agent_id=agent_id,
            skill=skill,
            outcome="replay",
        )
        return ResolutionOutcome(outcome="replay", response=reservation.result)

    if reservation.state == "in_progress":
        log.info(
            "confirmation_resolution.idempotency_in_progress",
            agent_id=agent_id,
            skill=skill,
            outcome="in_progress",
        )
        return ResolutionOutcome(outcome="in_progress")

    if reservation.state == "args_mismatch":
        await write_audit_row(
            agent_id=agent_id,
            conversation_id=conversation_id,
            skill=skill,
            arguments=arguments,
            result=None,
            actor_decision="",
            actor_rationale="",
            capability_snapshot=snapshot,
            latency_ms=None,
            error="idempotency.args_mismatch",
        )
        log.warning(
            "confirmation_resolution.args_mismatch",
            agent_id=agent_id,
            skill=skill,
            outcome="denied",
        )
        return ResolutionOutcome(outcome="denied", reason="idempotency.args_mismatch")

    if reservation.state == "unknown":
        # CR-01: a stale 'in_flight' reservation exists on this key — a prior
        # attempt (this same task, redelivered after the worker that ran it
        # vanished, or a live-turn call on the same key) may already have
        # called the adapter. Never auto-reclaimed here either: re-running
        # this resolver's own adapter call would risk a second, real
        # provider call. Fail closed and surface for manual reconciliation,
        # exactly like the live-turn dispatcher's own handling of this state
        # (tools.py::_execute_transactional_tool).
        await write_audit_row(
            agent_id=agent_id,
            conversation_id=conversation_id,
            skill=skill,
            arguments=arguments,
            result=None,
            actor_decision="",
            actor_rationale="",
            capability_snapshot=snapshot,
            latency_ms=None,
            error="idempotency.stranded_reservation",
        )
        log.error(
            "confirmation_resolution.idempotency_stranded",
            agent_id=agent_id,
            skill=skill,
            outcome="denied",
        )
        return ResolutionOutcome(outcome="denied", reason="idempotency.stranded_reservation")

    # reservation.state == "reserved" — proceed as the winner.

    # ---------------------------------------------------------------- 6. Rate + constraints (LIVE)
    # SC3, rate/ceiling half: this reads the SAME live snapshot fetched in
    # step 3 above, never a stored one — a ceiling tightened after the
    # confirmation was created denies execution here.
    rate_denial = await apply_rate_and_constraint_checks(agent_id, skill, snapshot, arguments)
    if rate_denial is not None:
        await release_idempotency(agent_id, skill, validated.idempotency_key)  # type: ignore[attr-defined]  # every SKILL_INPUT_MODELS member declares idempotency_key
        await write_audit_row(
            agent_id=agent_id,
            conversation_id=conversation_id,
            skill=skill,
            arguments=arguments,
            result=None,
            actor_decision="",
            actor_rationale="",
            capability_snapshot=snapshot,
            latency_ms=None,
            error=f"capability.denial:{rate_denial}",
        )
        log.warning(
            "confirmation_resolution.rate_or_constraint_denied",
            agent_id=agent_id,
            skill=skill,
            outcome="denied",
        )
        return ResolutionOutcome(outcome="denied", reason=f"capability.denial:{rate_denial}")

    # Step 5 (the Actor seam) is deliberately not re-entered here — see the
    # module docstring's "Deliberately absent" section. The human approval
    # this function is executing IS that seam's verdict for this call.

    # ---------------------------------------------------------------- 8. Adapter + audit
    # Steps 6-7, shared with the live-turn dispatcher (T-22-ACT-15).
    # decision="approved_by_human" is written to the audit row and is
    # deliberate and load-bearing twice over: it keeps a human-approved
    # execution visibly distinguishable from an Actor-approved one in the
    # audit trail, and it is the discriminator a later execution-outcome
    # lookup uses to tell this row apart from the original
    # human-approval-required row, which shares agent_id, skill and arguments.
    result = await _execute_adapter_and_audit(
        skill=skill,
        validated=validated,
        raw_args=arguments,
        adapter_method=skill,
        agent_id=agent_id,
        conn_str=conn_str,
        conversation_id=conversation_id,
        snapshot=snapshot,
        decision="approved_by_human",
        rationale=f"pending_confirmation:{confirmation_id}",
    )

    if result.get("is_error"):
        content = result.get("content") or []
        reason_text = content[0].get("text") if content else None
        log.warning(
            "confirmation_resolution.adapter_denied",
            agent_id=agent_id,
            skill=skill,
            outcome="denied",
        )
        return ResolutionOutcome(outcome="denied", reason=reason_text)

    log.info(
        "confirmation_resolution.executed",
        agent_id=agent_id,
        skill=skill,
        outcome="executed",
    )
    return ResolutionOutcome(outcome="executed", response=result)
