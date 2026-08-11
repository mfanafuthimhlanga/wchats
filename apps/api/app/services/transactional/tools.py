"""
transactional.tools — 6 mutating @tool handlers + confirm_action_tool + shared dispatcher.

The single _execute_transactional_tool dispatcher encodes the enforcement order ONCE:
  1. IN-03 agent_id guard — if empty/unset, return precondition error before any DB touch
  2. Capability check (check_capability_access — auth-only, no side effects; fail-closed
     on EVERY call including replays — T-14-04-03)
  3. Reserve idempotency (reserve_idempotency — atomic INSERT ON CONFLICT DO NOTHING;
     DB decides single winner — CR-02 closed):
       "replay"       → return stored result immediately (WR-01: BEFORE rate checks)
       "args_mismatch"→ explicit is_error (WR-02 closed)
       "in_progress"  → benign is_error (concurrent duplicate delivery)
       "unknown"      → is_error + audit (CR-01: stale 'in_flight' row — adapter may
                        already have run; never auto-reclaimed)
       "reserved"     → proceed as the winner
  4. Rate + constraint checks (apply_rate_and_constraint_checks — Redis INCR+EXPIRE;
     runs ONLY for the fresh reserved winner, never for replays)
       denial → release_idempotency + audit row + is_error
  5. Actor seam (call_actor_gate): block → release_idempotency + audit row + is_error
  6. Adapter execute (try/except, captures latency_ms):
       error → release_idempotency + audit row + is_error
  7. Audit row (success, result=response, error=None) + finalize_idempotency + return

AUD-01 symmetry:
  Every entry into a transactional tool that is NOT a replay or benign in_progress produces
  exactly one tool_calls_audit row: capability denial, rate denial, actor block, adapter error,
  and success paths all write one row. Replays and in_progress do NOT write audit rows.

confirm_action_tool (mutating=False, WR-05 closed):
  Gated behind check_capability_access + IN-03 agent_id guard before writing a
  pending_confirmations row. Under side_effects='recorded' it writes no row at
  all and records the attempt instead — it does not use the dispatcher, so it
  would otherwise pollute the owner's triage queue on every eval scenario. Takes NO idempotency key, calls NO provider adapter.
  Minimal dedup (T-14-08-05): the partial unique index
  uq_pending_confirmations_unresolved (migration 0016) bounds OUTSTANDING
  confirmations to one per (agent_id, skill, action_reference). A duplicate
  confirm_action loses the unique-index race; the resulting IntegrityError is
  caught and the existing pending row is returned instead of inserting a duplicate.
  Phase-18 will extend resolution logic. PRD DDL unchanged.

Circular import note:
  tools.py imports _agent_id_var / _conversation_id_var from agent_tools via a lazy
  import inside _execute_transactional_tool (function body, not module level). This
  breaks the circular dependency that would occur if agent_tools.py imported tools.py
  at module level while tools.py imported agent_tools.py at module level.

Registry attachment:
  After all 7 handlers are defined, each decorated SdkMcpTool is attached to its
  TOOL_REGISTRY entry's sdk_tool field so the registry is the single source linking
  metadata <-> SdkMcpTool (Plan-02 sdk_tool field populated by Plan-04 per registry.py).

Security:
  - The Actor seam (call_actor_gate) is ALWAYS called before the adapter on every
    fresh (non-replay) mutating execution — T-14-04-01.
  - Replays short-circuit BEFORE the actor seam AND before rate checks (WR-01) — T-14-04-02.
  - allowed_tools listing does not grant access; the fail-closed envelope check in the
    handler is the real gate — T-14-04-03.
  - agent_id is sourced from the per-call ContextVar — T-14-04-04.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import structlog
from claude_agent_sdk import tool
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.database import get_sync_db
from app.models.pending_confirmation import PendingConfirmation
from app.services.actor_seam import call_actor_gate
from app.services.transactional.audit import write_audit_row
from app.services.transactional.credential_service import (
    CredentialDecryptionError,
    ProviderNotConfiguredError,
)
from app.services.transactional.enforcement import (
    apply_rate_and_constraint_checks,
    check_capability_access,
)
from app.services.transactional.idempotency import (
    compute_args_hash,
    finalize_idempotency,
    mark_reservation_in_flight,
    release_idempotency,
    reserve_idempotency,
)
from app.services.transactional.provider_adapter import get_adapter_for_skill
from app.services.transactional.registry import TOOL_REGISTRY
from app.services.transactional.schemas import (
    BookSlotInput,
    CancelOrderInput,
    ConfirmActionInput,
    IssueRefundInput,
    PlaceOrderInput,
    UpdateCustomerRecordInput,
    UpdateSubscriptionInput,
)

log = structlog.get_logger(__name__)

# Default TTL for pending_confirmations rows (Phase 18 will extend/configure this).
_CONFIRM_TTL_HOURS: int = 24

#: tool_calls_audit.error marker for a call recorded-mode declined to execute
#: (D1/P1b, BACKLOG 2.5). A constant rather than a literal because the eval and
#: any future Actor-labelling pass have to filter on the same string: an
#: unmarked recorded row and a real execution tell the same story in that table.
#:
#: EVERY audit row written under recorded mode carries it, not only the
#: adapter-suppression row. A recorded `actor_block` row that was byte-identical
#: to a production `actor_block` row is exactly the contamination this constant
#: exists to prevent, and the *refused* column of the audit's confusion matrix
#: is entirely made of those rows. `startswith(RECORDED_NOT_EXECUTED)` is the
#: filter; the suppression row is the bare marker, every other recorded row is
#: `f"{RECORDED_NOT_EXECUTED}|{the real reason}"`.
RECORDED_NOT_EXECUTED: str = "side_effects.recorded:not_executed"

#: In-process sink `kind` for a call that steps 1-5 stopped under recorded mode.
#: Distinct from "transactional.adapter" (which recorded mode suppressed at the
#: outer edge) because the two are opposite cells of the confusion matrix: one
#: is "would have executed", the other is "the envelope refused". An eval that
#: recorded only the first cannot tell "the agent never tried" from "the agent
#: tried and was stopped".
RECORDED_DECLINED: str = "transactional.declined"


# ---------------------------------------------------------------------------
# The IDV gate's customer-facing texts (step 2.5), as constants.
#
# These are constants rather than inline literals for one reason: they are the
# ONLY source `red_team_probe._VERDICT_PATTERNS` derives its identity_required
# needles from. Before BACKLOG 5.8, that matcher carried a single hand-copied
# substring ("requires identity verification") while this gate had THREE
# messages, only one of which contained it. The consequence was not a cosmetic
# mislabel: the RTX-03 identity probe tagged a correctly-blocked forged-token
# attempt as verdict "succeeded" — i.e. the red-team suite reported the attack
# WINNING at the exact moment the product stopped it.
#
# Anything added here is matched automatically. A message edited here moves the
# needle with it. tests/unit/test_idv_message_verdict_pin.py asserts every
# member tags identity_required, so a fourth message cannot be added silently.
# ---------------------------------------------------------------------------

#: No verified-session token present at all — blocked before reservation.
IDV_REQUIRED_MESSAGE: str = (
    "This action requires identity verification. "
    "Please verify your identity with a one-time code before proceeding."
)

#: The IDV check itself could not complete (e.g. Neon cold start). Fails CLOSED.
IDV_CHECK_FAILED_MESSAGE: str = "Identity verification check failed. Please try again."

#: A token was presented but is unissued, expired, or not found in the tenant DB.
IDV_EXPIRED_MESSAGE: str = (
    "Identity verification required or session expired. "
    "Please verify your identity again to proceed."
)

#: Every text the IDV gate can return. Consumed by red_team_probe's verdict matcher.
IDV_BLOCK_MESSAGES: tuple[str, ...] = (
    IDV_REQUIRED_MESSAGE,
    IDV_CHECK_FAILED_MESSAGE,
    IDV_EXPIRED_MESSAGE,
)


def _recorded_error(recorded: bool, error: str) -> str:
    """Stamp an audit row's `error` with the recorded marker, in recorded mode only.

    Live rows are returned byte-unchanged — every pre-existing assertion about
    `capability.denial:disabled`, `actor_block`, `idempotency.args_mismatch`
    and friends still reads exactly what it read before.
    """
    return f"{RECORDED_NOT_EXECUTED}|{error}" if recorded else error


def _not_executed_result(skill: str, detail: str = "") -> dict:
    """The tool result recorded mode hands the agent in place of a real execution.

    Two requirements pull in opposite directions here and both are met.

    **Unmissable, never a silent success** (the owner, 2026-08-07). A recorded
    `issue_refund` that returned a cheerful confirmation would teach the agent
    the money moved, and every sentence it produced afterwards would reason from
    a false premise. So: `is_error`, and text that says in words that nothing
    happened, carrying none of the adapter's artefacts.

    **No evaluation frame in the model's context.** The first version of this
    text told the agent "this agent is running in evaluation mode
    (side_effects='recorded')" and instructed it not to tell the customer the
    action completed. Every token after that was produced by an
    evaluation-AWARE agent — and those are the tokens Faithfulness and
    AnswerRelevancy then score. That is a production-fidelity divergence of
    exactly the class approach (b) and the seam exist to close: measure the
    agent production serves, not one that knows it is being watched. Production
    never emits either sentence, so neither does this. What a real provider
    outage produces — a failed tool call whose text says the action did not
    happen — is what the agent sees.

    The eval-only marker did not disappear; it moved to where the readers who
    need it actually read. `tool_calls_audit.error` carries
    `RECORDED_NOT_EXECUTED` for the human grader and the labelled Actor set, and
    `get_recorded_side_effects()` carries the full attempt for P2.
    """
    tail = f" {detail}" if detail else ""
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"NOT EXECUTED: the {skill} request did not reach the provider "
                    f"and nothing was changed. No money moved and no record was "
                    f"updated.{tail}"
                ),
            }
        ],
        "is_error": True,
    }


def _declined_detail(
    *,
    skill: str,
    raw_args: dict,
    agent_id: str,
    conversation_id: str | None,
    reason: str,
    snapshot: dict | None = None,
    actor_decision: str = "",
    actor_rationale: str = "",
) -> dict:
    """The in-process record of an attempt steps 1-5 declined under recorded mode.

    `reason` is the same string the audit row carries, so the durable and the
    in-process halves of the recording join on one value rather than on two
    vocabularies that drift.
    """
    return {
        "skill": skill,
        "arguments": raw_args,
        "agent_id": agent_id,
        "conversation_id": conversation_id,
        "reason": reason,
        "capability_snapshot": snapshot,
        "actor_decision": actor_decision,
        "actor_rationale": actor_rationale,
    }


# ---------------------------------------------------------------------------
# Shared steps 6-7 — adapter execute + audit row + finalize
# ---------------------------------------------------------------------------


async def _execute_adapter_and_audit(
    *,
    skill: str,
    validated,  # Pydantic-validated input model for the specific tool
    raw_args: dict,
    adapter_method: str,
    agent_id: str,
    conn_str: str,
    conversation_id: str | None,
    snapshot: dict,
    decision: str,
    rationale: str,
) -> dict:
    """Steps 6 (adapter execute) and 7 (audit + finalize), extracted once.

    Called by both `_execute_transactional_tool` (the live-turn dispatcher,
    step 5 having just produced `decision`/`rationale` from the Actor seam)
    and `confirmation_resolution.execute_approved_confirmation` (the human-
    approval resolver, which passes `decision="approved_by_human"` in place
    of an Actor verdict). One implementation of "how to call a provider
    adapter and audit it" avoids the two-copies drift 22-RESEARCH.md
    § Don't Hand-Roll names as a future security-relevant inconsistency
    (T-22-ACT-15).

    This is a pure extraction of the former inline body of
    `_execute_transactional_tool` steps 6-7 — no behaviour change.

    Args:
        skill:          Canonical tool/skill name (e.g. "place_order").
        validated:      Pydantic-validated input model; provides .idempotency_key.
        raw_args:       Original unvalidated args dict (passed to the audit row).
        adapter_method: Method name on ProviderAdapter to call (e.g. "place_order").
        agent_id:       UUID string of the calling agent.
        conn_str:       Decrypted tenant DB connection string.
        conversation_id: Current conversation id, or None outside a conversation.
        snapshot:       JSON-safe capability envelope snapshot from step 2.
        decision:       Actor verdict string ("approve") or "approved_by_human"
                         for a resolver-driven execution — written to the audit row.
        rationale:      Actor rationale text, or a resolver-supplied rationale.

    Returns:
        SDK-compatible tool response dict with "content" key.
        On errors: also contains "is_error": True.
    """
    # -------------------------------------------------------- 6. Adapter execute
    # get_adapter_for_skill fetches + decrypts the tenant credential and returns
    # the correct concrete adapter (INT-02). The raw credential never leaves this
    # function scope — only a CredentialHandle (redacted repr) enters the adapter.
    try:
        adapter = await get_adapter_for_skill(skill, agent_id, conn_str)
    except (ProviderNotConfiguredError, CredentialDecryptionError) as exc:
        # The provider is not configured or the credential is corrupted.
        # Release the idempotency reservation so a retry can re-enter (T-16-cfg).
        await release_idempotency(agent_id, skill, validated.idempotency_key)
        await write_audit_row(
            agent_id=agent_id,
            conversation_id=conversation_id,
            skill=skill,
            arguments=raw_args,
            result=None,
            actor_decision=decision,
            actor_rationale=rationale,
            capability_snapshot=snapshot,
            latency_ms=None,
            error=f"provider.not_configured:{exc}",
        )
        return {
            "content": [{"type": "text", "text": str(exc)}],
            "is_error": True,
        }
    start_ms = int(time.time() * 1000)

    try:
        # CR-01: durably record "the adapter call is about to happen" BEFORE
        # making it. If this worker dies right after the adapter call
        # succeeds but before finalize_idempotency below runs, a later
        # reclaim attempt (reserve_idempotency) sees status='in_flight' and
        # refuses to auto re-execute — it surfaces the row as "unknown"
        # instead of risking a second, real provider call.
        await mark_reservation_in_flight(agent_id, skill, validated.idempotency_key)
        result_obj = await getattr(adapter, adapter_method)(validated, agent_id)
        response = result_obj.model_dump()
        latency_ms = int(time.time() * 1000) - start_ms
    except Exception as exc:  # noqa: BLE001
        latency_ms = int(time.time() * 1000) - start_ms
        error_str = str(exc)
        log.error(
            "transactional_tool.adapter_error",
            agent_id=agent_id,
            skill=skill,
            error=error_str,
        )
        await release_idempotency(agent_id, skill, validated.idempotency_key)
        await write_audit_row(
            agent_id=agent_id,
            conversation_id=conversation_id,
            skill=skill,
            arguments=raw_args,
            result=None,
            actor_decision=decision,
            actor_rationale=rationale,
            capability_snapshot=snapshot,
            latency_ms=latency_ms,
            error=error_str,
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Tool execution failed: {error_str}. Please try again.",
                }
            ],
            "is_error": True,
        }

    # -------------------------------------------------------- 7. Audit row + finalize + return
    tool_response: dict = {
        "content": [{"type": "text", "text": response.get("message", str(response))}]
    }

    await write_audit_row(
        agent_id=agent_id,
        conversation_id=conversation_id,
        skill=skill,
        arguments=raw_args,
        result=response,
        actor_decision=decision,
        actor_rationale=rationale,
        capability_snapshot=snapshot,
        latency_ms=latency_ms,
        error=None,
    )

    await finalize_idempotency(agent_id, skill, validated.idempotency_key, tool_response)

    log.info(
        "transactional_tool.success",
        agent_id=agent_id,
        skill=skill,
        latency_ms=latency_ms,
    )
    return tool_response


# ---------------------------------------------------------------------------
# Shared dispatcher — encodes the enforcement order ONCE
# ---------------------------------------------------------------------------


async def _execute_transactional_tool(
    skill: str,
    validated,  # Pydantic-validated input model for the specific tool
    raw_args: dict,
    adapter_method: str,
) -> dict:
    """Enforce the locked execution order for every mutating transactional tool call.

    Called by each of the 6 mutating @tool handlers after Pydantic validation.
    confirm_action_tool does NOT use this dispatcher (mutating=False, no adapter).

    Enforcement order (documented in Plan-08 objective):

      1. IN-03 guard     — agent_id must be non-empty; fail fast before any DB touch
      2. Capability check— check_capability_access(agent_id, skill): auth-only, no side effects
                           fail-closed on EVERY call (including replays) — T-14-04-03
      3. Reserve         — reserve_idempotency: atomic DB claim, DB decides single winner
                           "replay"       → return stored result BEFORE rate checks (WR-01)
                           "args_mismatch"→ is_error (WR-02)
                           "in_progress"  → benign is_error (concurrent duplicate delivery)
                           "unknown"      → is_error + audit (CR-01: stale in-flight row)
                           "reserved"     → proceed as winner
      4. Rate checks     — apply_rate_and_constraint_checks: Redis INCR+EXPIRE (side-effecting)
                           ONLY for the fresh reserved winner — never for replays
                           denial → release + audit + is_error
      5. Actor seam      — call_actor_gate: block → release + audit + is_error
      5.5 Recorded mode  — D1/P1b: on the eval path (side_effects='recorded') the
                           ProviderAdapter is suppressed. Records the attempt,
                           releases, writes the audit row marked
                           RECORDED_NOT_EXECUTED, returns is_error. Steps 1-5 all
                           ran; only the money did not move.
                           This is the APPROVE path's branch. Every other
                           non-executing outcome above has its own — the two that
                           matter are the step-3 replay (returns a stored REAL
                           provider result) and the step-5 require_human verdict
                           (writes a pending_confirmations row the owner's
                           approval queue dispatches into a live adapter). Both
                           return BEFORE 5.5, so 5.5 alone was not a money guard.
                           Under recorded mode every audit row this function
                           writes carries the RECORDED_NOT_EXECUTED prefix, and
                           every declined attempt is recorded in the in-process
                           sink — the *refused* column of the audit's confusion
                           matrix is made entirely of those.
      6. Adapter execute — try/except; error → release + audit + is_error
      7. Audit + finalize— success path: audit row (no error) + finalize_idempotency + return

    Args:
        skill:          Canonical tool/skill name (e.g. "place_order").
        validated:      Pydantic-validated input model; provides .idempotency_key.
        raw_args:       Original unvalidated args dict (passed to audit row + actor seam).
        adapter_method: Method name on ProviderAdapter to call (e.g. "place_order").

    Returns:
        SDK-compatible tool response dict with "content" key.
        On errors: also contains "is_error": True.
    """
    # Lazy import breaks the circular dependency between tools.py and agent_tools.py.
    # _conn_str_var MUST stay inside the function body — module-level import causes a
    # circular import (Pitfall 2 from 15-RESEARCH.md).
    from app.services.agent_tools import (  # noqa: PLC0415
        _agent_id_var,
        _conn_str_var,
        _conversation_id_var,
        _side_effects_var,
        _verified_session_token_var,
        record_suppressed_side_effect,
    )

    agent_id = _agent_id_var.get()
    conn_str = _conn_str_var.get()
    conversation_id_str = _conversation_id_var.get()
    conversation_id: str | None = conversation_id_str if conversation_id_str else None

    # D1/P1b: read ONCE, at the top. The mode is consulted by every
    # decline-to-execute branch below, not only by step 5.5 — reading it at each
    # branch would be nine chances to forget one, and the one forgotten is the
    # one that moves money.
    recorded: bool = _side_effects_var.get() == "recorded"

    # The idempotency key is MODEL-SUPPLIED (every mutating Input model in
    # schemas.py declares it) and models produce deterministic ones —
    # "refund-ORD-9001", "order-12345-refund". Two consequences, both real, both
    # closed by giving recorded mode its own keyspace:
    #   * an eval scenario mined from a production conversation can hit the key a
    #     real completed call used, and step 3 would hand the agent that call's
    #     stored REAL provider result;
    #   * an eval that reserves a key first makes the real customer's later call
    #     with the same key read as a replay or a stranded reservation.
    # A recorded execution never finalizes (it releases), so nothing is ever
    # stored under a "recorded:" key and a recorded replay cannot occur at all.
    # The step-3 mode check below is kept anyway: this namespace is one edit away
    # from being lost, and the check fails loudly if it ever is.
    idem_key: str = (
        f"recorded:{validated.idempotency_key}" if recorded else validated.idempotency_key
    )

    # -------------------------------------------------------- 1. IN-03 agent_id precondition
    # No recorded-mode branch: this is the harness failing to set context, not
    # the agent choosing anything, and there is no agent_id to attribute a
    # recording to. Nothing durable is written here either.
    if not agent_id:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Precondition failed: agent context not set. "
                        "Cannot process transactional tool call without a valid agent identity."
                    ),
                }
            ],
            "is_error": True,
        }

    # -------------------------------------------------------- 2. Capability check (auth-only)
    # check_capability_access is side-effect-free (no Redis INCR, no writes).
    # Runs on EVERY call including replays — fail-closed for existence + enabled (T-14-04-03).
    snapshot, denial = await check_capability_access(agent_id, skill)
    if denial is not None:
        if recorded:
            record_suppressed_side_effect(
                RECORDED_DECLINED,
                _declined_detail(
                    skill=skill,
                    raw_args=raw_args,
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    reason=f"capability.denial:{denial}",
                    snapshot=snapshot,
                ),
            )
        # AUD-01 symmetry: capability denial writes one audit row (matching actor_block).
        await write_audit_row(
            agent_id=agent_id,
            conversation_id=conversation_id,
            skill=skill,
            arguments=raw_args,
            result=None,
            actor_decision="",
            actor_rationale="",
            capability_snapshot=snapshot,
            latency_ms=None,
            error=_recorded_error(recorded, f"capability.denial:{denial}"),
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Access denied: capability envelope denied this request "
                        f"(reason: {denial}). Contact your administrator to enable this tool."
                    ),
                }
            ],
            "is_error": True,
        }

    # -------------------------------------------------------- 2.5 IDV gate (IDV-05)
    # Runs AFTER capability check (2) and BEFORE reserve_idempotency (3) so that a
    # blocked unverified call never consumes the idempotency slot (T-17-21).
    # Driven by the capability envelope snapshot already fetched in Step 2 — no extra DB call.
    # check_verified_session imported lazily (function-body import) to avoid circular import
    # (Pitfall 7 from 17-RESEARCH.md). Token NEVER logged (T-04-03-05).
    if snapshot.get("requires_identity_verification", False):
        vst = _verified_session_token_var.get()
        if not vst:
            # No verified session token present — block before reservation.
            if recorded:
                record_suppressed_side_effect(
                    RECORDED_DECLINED,
                    _declined_detail(
                        skill=skill,
                        raw_args=raw_args,
                        agent_id=agent_id,
                        conversation_id=conversation_id,
                        reason="identity_verification.required",
                        snapshot=snapshot,
                    ),
                )
            await write_audit_row(
                agent_id=agent_id,
                conversation_id=conversation_id,
                skill=skill,
                arguments=raw_args,
                result=None,
                actor_decision="",
                actor_rationale="",
                capability_snapshot=snapshot,
                latency_ms=None,
                error=_recorded_error(recorded, "identity_verification.required"),
            )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": IDV_REQUIRED_MESSAGE,
                    }
                ],
                "is_error": True,
            }
        from app.services.identity_service import check_verified_session  # noqa: PLC0415

        try:
            session_valid = await check_verified_session(agent_id, vst, conn_str)
        except Exception as exc:  # noqa: BLE001
            # DB error (e.g. psycopg2.OperationalError on Neon cold start) — fail CLOSED.
            # Never allow the mutating tool to proceed when the IDV check cannot complete.
            log.warning(
                "transactional_tool.idv_check_failed",
                agent_id=agent_id,
                skill=skill,
                error=str(exc),
            )
            if recorded:
                record_suppressed_side_effect(
                    RECORDED_DECLINED,
                    _declined_detail(
                        skill=skill,
                        raw_args=raw_args,
                        agent_id=agent_id,
                        conversation_id=conversation_id,
                        reason="identity_verification.check_failed",
                        snapshot=snapshot,
                    ),
                )
            await write_audit_row(
                agent_id=agent_id,
                conversation_id=conversation_id,
                skill=skill,
                arguments=raw_args,
                result=None,
                actor_decision="",
                actor_rationale="",
                capability_snapshot=snapshot,
                latency_ms=None,
                error=_recorded_error(recorded, "identity_verification.check_failed"),
            )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": IDV_CHECK_FAILED_MESSAGE,
                    }
                ],
                "is_error": True,
            }
        if not session_valid:
            # Token present but expired or not found in tenant DB — block before reservation.
            if recorded:
                record_suppressed_side_effect(
                    RECORDED_DECLINED,
                    _declined_detail(
                        skill=skill,
                        raw_args=raw_args,
                        agent_id=agent_id,
                        conversation_id=conversation_id,
                        reason="identity_verification.invalid_or_expired",
                        snapshot=snapshot,
                    ),
                )
            await write_audit_row(
                agent_id=agent_id,
                conversation_id=conversation_id,
                skill=skill,
                arguments=raw_args,
                result=None,
                actor_decision="",
                actor_rationale="",
                capability_snapshot=snapshot,
                latency_ms=None,
                error=_recorded_error(recorded, "identity_verification.invalid_or_expired"),
            )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": IDV_EXPIRED_MESSAGE,
                    }
                ],
                "is_error": True,
            }

    # -------------------------------------------------------- 3. Reserve idempotency (atomic)
    # compute_args_hash excludes idempotency_key internally — used to detect WR-02 key reuse.
    args_hash = compute_args_hash(raw_args)
    reservation = await reserve_idempotency(agent_id, skill, idem_key, args_hash)

    if reservation.state == "replay":
        # WR-01 closed: replay short-circuits BEFORE apply_rate_and_constraint_checks.
        # Replays return the stored result without consuming rate budget.
        log.info(
            "transactional_tool.idempotency_replay",
            agent_id=agent_id,
            skill=skill,
        )
        if recorded:
            # The stored result is a REAL provider result from a REAL earlier
            # call. Returning it here would hand the eval agent "Refund of
            # R45.00 issued" and every sentence after it would reason from money
            # having moved — the silent success the owner ruled out, arriving
            # through the one door step 5.5 sits behind rather than in front of.
            # The "recorded:" keyspace above should make this unreachable; if it
            # is ever reached, this is the guard that keeps it harmless.
            record_suppressed_side_effect(
                RECORDED_DECLINED,
                _declined_detail(
                    skill=skill,
                    raw_args=raw_args,
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    reason="idempotency.replay",
                    snapshot=snapshot,
                ),
            )
            # No audit row: AUD-01 exempts replays in both modes, because the
            # call that stored the result already wrote one.
            return _not_executed_result(skill)
        return reservation.result  # type: ignore[return-value]

    if reservation.state == "args_mismatch":
        # WR-02 closed: same idempotency_key used with different business arguments.
        # Return an explicit error instead of silently replaying the stale result.
        # AUD-01: this is a security-relevant rejection (suspicious key reuse) — audit it,
        # matching the capability.denial / actor_block paths. (in_progress is NOT audited
        # here: it is a concurrent-duplicate no-op; the reserved winner audits the real call.)
        if recorded:
            record_suppressed_side_effect(
                RECORDED_DECLINED,
                _declined_detail(
                    skill=skill,
                    raw_args=raw_args,
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    reason="idempotency.args_mismatch",
                    snapshot=snapshot,
                ),
            )
        await write_audit_row(
            agent_id=agent_id,
            conversation_id=conversation_id,
            skill=skill,
            arguments=raw_args,
            result=None,
            actor_decision="",
            actor_rationale="",
            capability_snapshot=snapshot,
            latency_ms=None,
            error=_recorded_error(recorded, "idempotency.args_mismatch"),
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Idempotency key reused with different arguments. "
                        "Each new request must use a unique idempotency_key."
                    ),
                }
            ],
            "is_error": True,
        }

    if reservation.state == "in_progress":
        # Concurrent duplicate delivery — another worker is executing the same key.
        # Return a benign is_error without executing (caller should retry later).
        if recorded:
            # No audit row here in either mode (AUD-01: the reserved winner
            # audits the real call), so the in-process sink is the ONLY record
            # that the agent tried. Without it, P2 reads this turn as one where
            # no mutating call was attempted at all.
            record_suppressed_side_effect(
                RECORDED_DECLINED,
                _declined_detail(
                    skill=skill,
                    raw_args=raw_args,
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    reason="idempotency.in_progress",
                    snapshot=snapshot,
                ),
            )
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "This request is already being processed. "
                        "Please wait a moment and retry if you do not receive a response."
                    ),
                }
            ],
            "is_error": True,
        }

    if reservation.state == "unknown":
        # CR-01: a stale 'in_flight' reservation exists — the adapter may
        # already have been called by a worker that then vanished before it
        # could finalize. Never auto-reclaimed (that risks a second, real
        # provider call). Fail closed: audit it and surface for manual
        # reconciliation instead of executing or silently dropping it.
        log.error(
            "transactional_tool.idempotency_stranded",
            agent_id=agent_id,
            skill=skill,
        )
        if recorded:
            record_suppressed_side_effect(
                RECORDED_DECLINED,
                _declined_detail(
                    skill=skill,
                    raw_args=raw_args,
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    reason="idempotency.stranded_reservation",
                    snapshot=snapshot,
                ),
            )
        await write_audit_row(
            agent_id=agent_id,
            conversation_id=conversation_id,
            skill=skill,
            arguments=raw_args,
            result=None,
            actor_decision="",
            actor_rationale="",
            capability_snapshot=snapshot,
            latency_ms=None,
            error=_recorded_error(recorded, "idempotency.stranded_reservation"),
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "This request cannot be completed automatically: a previous "
                        "attempt may still be running or may have already completed. "
                        "Please contact support before retrying to avoid a possible "
                        "duplicate."
                    ),
                }
            ],
            "is_error": True,
        }

    # reservation.state == "reserved" — we are the single winner.

    # -------------------------------------------------------- 4. Rate + constraint checks
    # apply_rate_and_constraint_checks is side-effecting (Redis INCR+EXPIRE).
    # Runs ONLY for the fresh reserved winner — never for replays (WR-01).
    #
    # `validated`, NOT `raw_args`. enforcement.apply_rate_and_constraint_checks
    # reads the amount with `getattr(args, "amount_cents", None)` /
    # `getattr(args, "refund_amount_cents", None)` — and `getattr` on a plain
    # dict returns the default, never the key. Passing raw_args therefore made
    # `amount` unconditionally None here, so the IN-02 max_amount_cents ceiling
    # never fired on the live dispatcher path: a refund of any size cleared the
    # envelope's value bound and went on to the Actor gate and the adapter. The
    # unit coverage missed it because test_capability_enforcement.py exercises
    # the function with a MagicMock (attributes, not keys), which is the one
    # arg shape production never passes. The Pydantic model is what this
    # function's own docstring has always specified, and its amount fields are
    # int-typed, so the comparison below is total.
    rate_denial = await apply_rate_and_constraint_checks(agent_id, skill, snapshot, validated)
    if rate_denial is not None:
        # Release the reservation so a later retry can attempt the key again.
        await release_idempotency(agent_id, skill, idem_key)
        if recorded:
            record_suppressed_side_effect(
                RECORDED_DECLINED,
                _declined_detail(
                    skill=skill,
                    raw_args=raw_args,
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    reason=f"capability.denial:{rate_denial}",
                    snapshot=snapshot,
                ),
            )
        await write_audit_row(
            agent_id=agent_id,
            conversation_id=conversation_id,
            skill=skill,
            arguments=raw_args,
            result=None,
            actor_decision="",
            actor_rationale="",
            capability_snapshot=snapshot,
            latency_ms=None,
            error=_recorded_error(recorded, f"capability.denial:{rate_denial}"),
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Request denied by rate or constraint check "
                        f"(reason: {rate_denial}). Please wait before retrying."
                    ),
                }
            ],
            "is_error": True,
        }

    # -------------------------------------------------------- 5. Actor seam
    decision, rationale = await call_actor_gate(
        skill, raw_args, snapshot, conversation_id or "", agent_id, conn_str
    )
    if decision == "block":
        await release_idempotency(agent_id, skill, idem_key)
        if recorded:
            record_suppressed_side_effect(
                RECORDED_DECLINED,
                _declined_detail(
                    skill=skill,
                    raw_args=raw_args,
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    reason="actor_block",
                    snapshot=snapshot,
                    actor_decision=decision,
                    actor_rationale=rationale,
                ),
            )
        await write_audit_row(
            agent_id=agent_id,
            conversation_id=conversation_id,
            skill=skill,
            arguments=raw_args,
            result=None,
            actor_decision=decision,
            actor_rationale=rationale,
            capability_snapshot=snapshot,
            latency_ms=None,
            error=_recorded_error(recorded, "actor_block"),
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Action blocked by security policy. Please contact support.",
                }
            ],
            "is_error": True,
        }

    elif decision == "require_human":
        # Pitfall 4 (15-RESEARCH.md): release reservation FIRST — the action will NOT
        # proceed. Free the reservation so a later retry (after approval) can re-enter.
        await release_idempotency(agent_id, skill, idem_key)

        # ---------------------------------------------------------------
        # D1/P1b: THE SECOND DOOR TO A LIVE ADAPTER, and the one step 5.5
        # cannot see because this arm returns before it.
        #
        # A require_human verdict writes a durable `pending_confirmations`
        # row. That row is not inert: it appears in
        # GET /agents/{agent_id}/pending-confirmations, it carries no marker
        # distinguishing it from a customer's, and `_is_confirm_action_shaped`
        # does NOT filter it (it holds `idempotency_key`, never
        # `action_reference`). Approving it dispatches
        # resolve_confirmation_task -> execute_approved_confirmation ->
        # _execute_adapter_and_audit -> get_adapter_for_skill -> a real
        # Stripe/Shopify/Woo/Calendly call. So a nightly eval scenario that
        # provokes a large refund silently queues a real refund for the owner
        # to approve, hours later, with nothing in the queue saying it came
        # from an eval. Recorded mode that stops at step 5.5 stops the fast
        # path to the adapter and leaves the slow one open.
        #
        # Stamping the row instead of skipping it was the alternative. It was
        # rejected: it needs the approval route and the resolver to fail
        # closed on the stamp, which spreads the eval's concern into the
        # human-approval path — the same coupling
        # test_the_shared_adapter_helper_stays_free_of_the_mode exists to
        # prevent. Not writing a row nobody should ever act on is the smaller,
        # more local change.
        #
        # The Actor's verdict is not lost: the recording carries decision and
        # rationale, and the audit row is written and marked. That verdict IS
        # the eval signal — "the agent tried and the gate escalated it" is a
        # cell of the confusion matrix, and the pending row was never the
        # thing that carried it.
        # ---------------------------------------------------------------
        if recorded:
            record_suppressed_side_effect(
                RECORDED_DECLINED,
                _declined_detail(
                    skill=skill,
                    raw_args=raw_args,
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    reason="actor_require_human",
                    snapshot=snapshot,
                    actor_decision=decision,
                    actor_rationale=rationale,
                ),
            )
            await write_audit_row(
                agent_id=agent_id,
                conversation_id=conversation_id,
                skill=skill,
                arguments=raw_args,
                result=None,
                actor_decision=decision,
                actor_rationale=rationale,
                capability_snapshot=snapshot,
                latency_ms=None,
                error=_recorded_error(recorded, "actor_require_human"),
            )
            log.info(
                "transactional_tool.require_human_not_queued",
                agent_id=agent_id,
                skill=skill,
            )
            return _not_executed_result(
                skill,
                "It requires human approval and no approval request was created.",
            )

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=_CONFIRM_TTL_HOURS)

        # WR-01: uq_pending_confirmations_unresolved (migration 0016) is built
        # on (agent_id, skill, arguments->>'action_reference'). This branch
        # stores raw_args — the target skill's FULL validated arguments —
        # which never contains an "action_reference" key (no mutating Input
        # model in schemas.py defines that field; only ConfirmActionInput
        # does). arguments->>'action_reference' is therefore always NULL for
        # a require_human row, and Postgres never treats two NULLs as equal
        # for a unique index, so that index silently never dedupes this
        # write path — only confirm_action_tool's own rows are covered by
        # it. Extending the index to also key on idempotency_key would need
        # a new migration (out of scope: apps/api/alembic/ and pyproject.toml
        # are byte-unchanged this phase). This pre-insert existence check is
        # the no-migration alternative the finding's Fix section names. It is
        # best-effort, not atomic (a SELECT-then-INSERT race window exists
        # between two truly concurrent require_human calls for the same key —
        # unlike the DB-enforced index below, which IS atomic for its own,
        # narrower action_reference case), but it bounds the common case
        # (retries of the same logical request) without a schema change.
        idempotency_key = raw_args.get("idempotency_key") if isinstance(raw_args, dict) else None
        existing_confirmation_id = None
        if idempotency_key:
            with get_sync_db() as db:
                existing_row = (
                    db.query(PendingConfirmation)
                    .filter(
                        PendingConfirmation.agent_id == agent_id,
                        PendingConfirmation.skill == skill,
                        PendingConfirmation.arguments["idempotency_key"].astext == idempotency_key,
                        PendingConfirmation.resolved_at.is_(None),
                    )
                    .order_by(PendingConfirmation.requested_at)
                    .first()
                )
                existing_confirmation_id = existing_row.id if existing_row is not None else None

        if existing_confirmation_id is not None:
            confirmation_id = existing_confirmation_id
            log.info(
                "actor_require_human.duplicate_suppressed",
                agent_id=agent_id,
                skill=skill,
            )
        else:
            confirmation_id = uuid4()
            row = PendingConfirmation(
                id=confirmation_id,
                agent_id=agent_id,
                skill=skill,
                arguments=raw_args,
                requested_at=now,
                expires_at=expires_at,
            )
            # Mirror confirm_action_tool's synchronous get_sync_db pattern exactly.
            # WR-03 asyncio.to_thread offload is a tracked follow-up, out of scope here.
            with get_sync_db() as db:
                db.add(row)
                try:
                    db.commit()
                except IntegrityError:
                    # uq_pending_confirmations_unresolved race: this specific
                    # index only ever fires here if raw_args happened to carry
                    # an "action_reference" key (it never does today for the
                    # six mutating skills — kept as defense in depth, not the
                    # primary dedup mechanism for this branch; the
                    # idempotency_key pre-check above is).
                    db.rollback()
                    log.info(
                        "actor_require_human.duplicate_suppressed",
                        agent_id=agent_id,
                        skill=skill,
                    )
        await write_audit_row(
            agent_id=agent_id,
            conversation_id=conversation_id,
            skill=skill,
            arguments=raw_args,
            result=None,
            actor_decision=decision,
            actor_rationale=rationale,
            capability_snapshot=snapshot,
            latency_ms=None,
            error="actor_require_human",
        )
        # NON-error response (no is_error key) — the adapter (step 6) MUST NOT run.
        # The action executes only after confirm_action approval resolves the row (Phase 18).
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"This action requires human approval before it can execute. "
                        f"A confirmation request has been created (ID: {confirmation_id}). "
                        f"The action will proceed only after an authorized approver confirms it."
                    ),
                }
            ]
        }

    # ---------------------------------------------- 5.5 Recorded mode (D1/P1b, BACKLOG 2.5)
    # The eval invokes this agent through the same seam production uses, which is
    # the whole point of approach (b) — and which means an eval scenario in which
    # the agent decides to refund would execute a real refund. On the eval path
    # the ProviderAdapter is suppressed and the attempt is recorded instead.
    #
    # Placed HERE, after step 5, and not inside _execute_adapter_and_audit. Two
    # reasons, both load-bearing:
    #
    #   * Everything above still runs. The capability envelope, the IDV gate, the
    #     idempotency reservation, the rate ceiling and the Actor seam are what
    #     the eval is measuring. Short-circuiting ahead of them would hand the
    #     recorded agent "not executed" where production hands it "access
    #     denied", and the remainder of the turn would diverge from the product —
    #     the exact drift the seam exists to close.
    #   * _execute_adapter_and_audit is shared with
    #     confirmation_resolution.execute_approved_confirmation, the human-approval
    #     resolver. That resolver runs hours later, in another task, with no
    #     per-turn context, and is forbidden from reading dispatcher ContextVars
    #     (OD-5, test_resolver_reads_no_dispatcher_contextvar). Putting the check
    #     in the shared helper would make an approved refund's fate depend on
    #     ambient state nobody in that call stack set.
    #
    # AUD-01 symmetry is preserved: this is a non-replay entry, so it writes
    # exactly one tool_calls_audit row, marked as recorded so the labelled Actor
    # set is never contaminated by an action that did not run. The reservation is
    # released like every other decline-to-execute path, because a key held by an
    # action that never happened makes the next caller's "unknown" a lie.
    #
    # This branch is no longer the ONLY one. Two arms above return before it —
    # the step-3 idempotency replay and the step-5 require_human verdict — and
    # both reached durable, real effects (a stored provider result, and a
    # `pending_confirmations` row the owner's approval queue dispatches into a
    # live adapter). Each now has its own recorded branch. `recorded` is read
    # once at the top of this function for that reason.
    if recorded:
        record_suppressed_side_effect(
            "transactional.adapter",
            {
                "skill": skill,
                "adapter_method": adapter_method,
                "arguments": raw_args,
                "agent_id": agent_id,
                "conversation_id": conversation_id,
                "actor_decision": decision,
                "actor_rationale": rationale,
                "capability_snapshot": snapshot,
            },
        )
        await release_idempotency(agent_id, skill, idem_key)
        await write_audit_row(
            agent_id=agent_id,
            conversation_id=conversation_id,
            skill=skill,
            arguments=raw_args,
            result=None,
            actor_decision=decision,
            actor_rationale=rationale,
            capability_snapshot=snapshot,
            latency_ms=None,
            error=RECORDED_NOT_EXECUTED,
        )
        log.info(
            "transactional_tool.side_effect_recorded",
            agent_id=agent_id,
            skill=skill,
        )
        return _not_executed_result(skill)

    # -------------------------------------------------------- 6-7. Adapter + audit
    # Delegated to the shared helper (T-22-ACT-15) — see _execute_adapter_and_audit
    # above for the full step 6/7 implementation. Pure extraction; no behaviour
    # change from the formerly-inline body.
    return await _execute_adapter_and_audit(
        skill=skill,
        validated=validated,
        raw_args=raw_args,
        adapter_method=adapter_method,
        agent_id=agent_id,
        conn_str=conn_str,
        conversation_id=conversation_id,
        snapshot=snapshot,
        decision=decision,
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# 1. place_order
# ---------------------------------------------------------------------------


@tool(
    "place_order",
    (
        "Place a customer order through the tenant's connected store. "
        "Requires an idempotency_key to prevent duplicate orders on retry. "
        "Subject to the capability envelope's max_amount_cents and rate_limit constraints."
    ),
    PlaceOrderInput.model_json_schema(),
)
async def place_order_tool(args: dict) -> dict:
    """Validate → _execute_transactional_tool with skill=place_order."""
    try:
        validated = PlaceOrderInput(**args)
    except ValidationError as exc:
        return {
            "content": [{"type": "text", "text": f"Invalid input: {exc}"}],
            "is_error": True,
        }
    return await _execute_transactional_tool("place_order", validated, args, "place_order")


# ---------------------------------------------------------------------------
# 2. cancel_order
# ---------------------------------------------------------------------------


@tool(
    "cancel_order",
    (
        "Cancel an existing customer order. "
        "Requires an idempotency_key for replay safety. "
        "Subject to the capability envelope's rate_limit constraints."
    ),
    CancelOrderInput.model_json_schema(),
)
async def cancel_order_tool(args: dict) -> dict:
    """Validate → _execute_transactional_tool with skill=cancel_order."""
    try:
        validated = CancelOrderInput(**args)
    except ValidationError as exc:
        return {
            "content": [{"type": "text", "text": f"Invalid input: {exc}"}],
            "is_error": True,
        }
    return await _execute_transactional_tool("cancel_order", validated, args, "cancel_order")


# ---------------------------------------------------------------------------
# 3. issue_refund
# ---------------------------------------------------------------------------


@tool(
    "issue_refund",
    (
        "Issue a refund for a customer order. "
        "Requires an idempotency_key for replay safety. "
        "Subject to the capability envelope's max_amount_cents constraint (refund_amount_cents field)."
    ),
    IssueRefundInput.model_json_schema(),
)
async def issue_refund_tool(args: dict) -> dict:
    """Validate → _execute_transactional_tool with skill=issue_refund."""
    try:
        validated = IssueRefundInput(**args)
    except ValidationError as exc:
        return {
            "content": [{"type": "text", "text": f"Invalid input: {exc}"}],
            "is_error": True,
        }
    return await _execute_transactional_tool("issue_refund", validated, args, "issue_refund")


# ---------------------------------------------------------------------------
# 4. update_subscription
# ---------------------------------------------------------------------------


@tool(
    "update_subscription",
    (
        "Update a customer's subscription plan. "
        "Requires an idempotency_key for replay safety. "
        "Subject to the capability envelope's rate_limit and constraint checks."
    ),
    UpdateSubscriptionInput.model_json_schema(),
)
async def update_subscription_tool(args: dict) -> dict:
    """Validate → _execute_transactional_tool with skill=update_subscription."""
    try:
        validated = UpdateSubscriptionInput(**args)
    except ValidationError as exc:
        return {
            "content": [{"type": "text", "text": f"Invalid input: {exc}"}],
            "is_error": True,
        }
    return await _execute_transactional_tool(
        "update_subscription", validated, args, "update_subscription"
    )


# ---------------------------------------------------------------------------
# 5. book_slot
# ---------------------------------------------------------------------------


@tool(
    "book_slot",
    (
        "Book a time slot for a service (consultation, delivery, installation, etc.). "
        "Requires an idempotency_key for replay safety. "
        "Does not require identity verification (lower-risk booking action)."
    ),
    BookSlotInput.model_json_schema(),
)
async def book_slot_tool(args: dict) -> dict:
    """Validate → _execute_transactional_tool with skill=book_slot."""
    try:
        validated = BookSlotInput(**args)
    except ValidationError as exc:
        return {
            "content": [{"type": "text", "text": f"Invalid input: {exc}"}],
            "is_error": True,
        }
    return await _execute_transactional_tool("book_slot", validated, args, "book_slot")


# ---------------------------------------------------------------------------
# 6. update_customer_record
# ---------------------------------------------------------------------------


@tool(
    "update_customer_record",
    (
        "Update a field on a customer record (email, phone, address, name). "
        "Requires an idempotency_key for replay safety. "
        "Subject to the capability envelope's rate_limit constraints."
    ),
    UpdateCustomerRecordInput.model_json_schema(),
)
async def update_customer_record_tool(args: dict) -> dict:
    """Validate → _execute_transactional_tool with skill=update_customer_record."""
    try:
        validated = UpdateCustomerRecordInput(**args)
    except ValidationError as exc:
        return {
            "content": [{"type": "text", "text": f"Invalid input: {exc}"}],
            "is_error": True,
        }
    return await _execute_transactional_tool(
        "update_customer_record", validated, args, "update_customer_record"
    )


# ---------------------------------------------------------------------------
# 7. confirm_action (mutating=False — no provider adapter, no idempotency key)
# ---------------------------------------------------------------------------


@tool(
    "confirm_action",
    (
        "Submit a confirmation request for a pending transactional action that requires "
        "human approval. Creates a pending_confirmations row for Phase-18 resolution. "
        "Does NOT execute the underlying action and does NOT require an idempotency_key "
        "(mutating=False). Gated behind check_capability_access (WR-05)."
    ),
    ConfirmActionInput.model_json_schema(),
)
async def confirm_action_tool(args: dict) -> dict:
    """Write a pending_confirmations row — no provider adapter, no idempotency key.

    mutating=False (TOOL_REGISTRY): confirm_action only creates a confirmation row;
    it does NOT execute the underlying provider action. The Phase-18 admin UI will
    resolve pending rows. PRD DDL unchanged.

    WR-05 (closed): confirm_action is now gated behind check_capability_access before
    writing the pending_confirmations row. Disabled or missing skill → is_error, no row.

    IN-03 (closed): empty agent_id → precondition error before any capability check or
    DB write.

    Duplicate-confirm dedup (T-14-08-05 closed): the partial unique index
    uq_pending_confirmations_unresolved (migration 0016) allows at most one
    UNRESOLVED confirmation per (agent_id, skill, action_reference). The row is
    inserted via the ORM; on the resulting IntegrityError the existing pending row
    is returned instead of a duplicate, so an enabled agent cannot create unbounded
    confirmation rows for the same action. Resolved rows (Phase-18) can be
    re-requested because the index is scoped to resolved_at IS NULL.
    """
    try:
        validated = ConfirmActionInput(**args)
    except ValidationError as exc:
        return {
            "content": [{"type": "text", "text": f"Invalid input: {exc}"}],
            "is_error": True,
        }

    # Lazy import to access the ContextVars set by build_tool_server.
    from app.services.agent_tools import (  # noqa: PLC0415
        _agent_id_var,
        _conversation_id_var,
        _side_effects_var,
        record_suppressed_side_effect,
    )

    agent_id = _agent_id_var.get()
    recorded: bool = _side_effects_var.get() == "recorded"

    # IN-03: agent_id guard — fail before any DB write
    if not agent_id:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Precondition failed: agent context not set. "
                        "Cannot submit confirmation request without a valid agent identity."
                    ),
                }
            ],
            "is_error": True,
        }

    # WR-05: capability gate — check_capability_access is side-effect-free.
    # Disabled or missing skill → return is_error without writing the row.
    _snapshot, denial = await check_capability_access(agent_id, validated.skill)
    if denial is not None:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Access denied: capability envelope denied confirm_action for "
                        f"skill '{validated.skill}' (reason: {denial}). "
                        f"Contact your administrator to enable this tool."
                    ),
                }
            ],
            "is_error": True,
        }

    # -------------------------------------------------------------------
    # D1/P1b: confirm_action does NOT route through _execute_transactional_tool,
    # so step 5.5 never sees it — and it is in `allowed_tools` in both modes, by
    # design (an eval agent that cannot request approval cannot be scored on
    # choosing to). Left ungated it writes a durable row into the owner's triage
    # queue on every eval scenario where the agent decides to ask, nightly.
    #
    # Less dangerous than the require_human row above — `_is_confirm_action_shaped`
    # DOES filter this shape, so approving one never reaches an adapter — so this
    # is queue pollution rather than money. It is still lost eval signal: nothing
    # recorded that the agent chose to ask for approval, which is a decision worth
    # scoring. Both halves are fixed here: no row, and a recording.
    # -------------------------------------------------------------------
    if recorded:
        record_suppressed_side_effect(
            "transactional.confirm_action",
            {
                "skill": validated.skill,
                "action_reference": validated.action_reference,
                "agent_id": agent_id,
                "conversation_id": _conversation_id_var.get() or None,
                "reason": "confirm_action.not_queued",
            },
        )
        log.info(
            "confirm_action.not_queued",
            agent_id=agent_id,
            skill=validated.skill,
        )
        return _not_executed_result(
            "confirm_action",
            f"No approval request was created for the '{validated.skill}' action "
            f"(reference: {validated.action_reference}).",
        )

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=_CONFIRM_TTL_HOURS)

    # Client-generate the UUID so the ID is set on the Python object without needing
    # a DB flush/refresh (enables str(row.id) immediately after db.add in test and prod).
    confirmation_id = uuid4()

    row = PendingConfirmation(
        id=confirmation_id,
        agent_id=agent_id,
        skill=validated.skill,
        arguments={"action_reference": validated.action_reference},
        requested_at=now,
        expires_at=expires_at,
        # resolved_at and resolution left NULL until Phase 18 resolves the row.
    )

    with get_sync_db() as db:
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            # T-14-08-05: a concurrent/duplicate confirm_action lost the
            # uq_pending_confirmations_unresolved race. Do NOT insert a duplicate;
            # return the existing unresolved confirmation so the caller still gets a
            # coherent confirmation id without growing the table unbounded.
            db.rollback()
            existing = (
                db.query(PendingConfirmation)
                .filter(
                    PendingConfirmation.agent_id == agent_id,
                    PendingConfirmation.skill == validated.skill,
                    PendingConfirmation.arguments["action_reference"].astext
                    == validated.action_reference,
                    PendingConfirmation.resolved_at.is_(None),
                )
                .order_by(PendingConfirmation.requested_at)
                .first()
            )
            existing_id = existing.id if existing is not None else confirmation_id
            log.info(
                "confirm_action.duplicate_suppressed",
                agent_id=agent_id,
                skill=validated.skill,
                confirmation_id=str(existing_id),
                action_reference=validated.action_reference,
            )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Confirmation request for '{validated.skill}' action "
                            f"(reference: {validated.action_reference}) is already pending. "
                            f"Awaiting human approval. Confirmation ID: {existing_id}."
                        ),
                    }
                ]
            }

    log.info(
        "confirm_action.pending_row_written",
        agent_id=agent_id,
        skill=validated.skill,
        confirmation_id=str(confirmation_id),
        action_reference=validated.action_reference,
    )

    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"Confirmation request submitted for '{validated.skill}' action "
                    f"(reference: {validated.action_reference}). "
                    f"Awaiting human approval. Confirmation ID: {confirmation_id}."
                ),
            }
        ]
    }


# ---------------------------------------------------------------------------
# Registry attachment — attach each SdkMcpTool to its TOOL_REGISTRY entry.
# This is the module-level side-effect that makes TOOL_REGISTRY the single
# source linking metadata <-> SdkMcpTool (Plan-02 sdk_tool field intent).
# ---------------------------------------------------------------------------

TOOL_REGISTRY["place_order"].sdk_tool = place_order_tool
TOOL_REGISTRY["cancel_order"].sdk_tool = cancel_order_tool
TOOL_REGISTRY["issue_refund"].sdk_tool = issue_refund_tool
TOOL_REGISTRY["update_subscription"].sdk_tool = update_subscription_tool
TOOL_REGISTRY["book_slot"].sdk_tool = book_slot_tool
TOOL_REGISTRY["update_customer_record"].sdk_tool = update_customer_record_tool
TOOL_REGISTRY["confirm_action"].sdk_tool = confirm_action_tool
