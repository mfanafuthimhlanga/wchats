"""
transactional.tools — 6 mutating @tool handlers + confirm_action_tool + shared dispatcher.

The single _execute_transactional_tool dispatcher encodes the enforcement order ONCE:
  1. IN-03 agent_id guard — if empty/unset, return precondition error before any DB touch
  2. Capability check (check_capability_access — auth-only, no side effects; fail-closed
     on EVERY call including replays — T-14-04-03)
  3. Reserve idempotency (reserve_idempotency — atomic INSERT ON CONFLICT DO NOTHING;
     DB decides single winner — CR-02 closed):
       "replay"       → return stored result immediately (WR-01: BEFORE rate checks)
       "args_mismatch"→ denied, and audited beside capability.denial (WR-02 closed)
       "in_progress"  → denied, a concurrent duplicate the reservation refused
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

What the dispatcher returns (ticket #45, edge moved by #49):
  `_execute_transactional_tool` returns a `ToolResult` from every branch, and
  `run_transactional_skill` is the seam that validates then calls it. All seven
  `@tool` handlers hand that result to `_published_wire`, the ONE place an outcome
  becomes the `is_error` bit and the one place the typed verdict is published to
  the turn's sink. The wire is byte for byte what it was.

  The bit is why the type exists. `ok` and `requires_human` both leave the
  dispatcher with no `is_error`, so a caller reading the wire could not tell a
  completed order from one an approver still has to sign off, except by reading
  the English text. `Outcome` carries that distinction; the eight `is_error`
  branches split into `denied`, meaning a gate refused, and `error`, meaning
  something broke.

AUD-01 symmetry:
  Every entry into a transactional tool that is NOT a replay or an in_progress produces
  exactly one tool_calls_audit row: capability denial, rate denial, actor block, adapter error,
  and success paths all write one row. Replays and in_progress do NOT write audit rows.

confirm_action_tool, over its own typed seam run_confirm_action (mutating=False, WR-05):
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
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.database import get_sync_db
from app.core.model_client import LedgerContext, ledger_recorder
from app.domain.tool_def import tool
from app.domain.tool_result import Outcome, ToolResult, to_wire, wire_text
from app.domain.transactional_schemas import (
    SKILL_INPUT_MODELS,
    BookSlotInput,
    CancelOrderInput,
    ConfirmActionInput,
    IssueRefundInput,
    PlaceOrderInput,
    UpdateCustomerRecordInput,
    UpdateSubscriptionInput,
)
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

#: Detail on the ToolResult step 5.5 returns, and the needle
#: `red_team_probe._VERDICT_PATTERNS` derives its `would_have_executed` tag
#: from. Step 5.5 is the ONE branch where every gate allowed the call and only
#: the recorded seam stopped the money, which makes it the opposite cell of the
#: matrix from RECORDED_DECLINED above: "would have executed" against "the
#: envelope refused". Without a detail the two cells hand the agent the same
#: sentence, and the probe's matcher fell through to "succeeded" for both. That
#: happens to be the right answer for this cell, by the mechanism that gave
#: BACKLOG 5.8 the wrong answer for identity blocks.
#:
#: The wording carries no evaluation frame, for the reason `_not_executed_result`
#: below gives at length. The agent reads this text and conditions the rest of
#: its turn on it, and a provider that accepted the request and then failed
#: produces this same sentence in production.
GATES_PASSED_DETAIL: str = "Every check passed and the request stopped at the provider."

#: Detail on the two ToolResults recorded mode returns when the agent asked an
#: approver instead of acting: the dispatcher's require_human arm, and
#: confirm_action's own arm. Both carry it so both tag `awaiting_approval`, the
#: tag live mode gives the same decision, which keeps the probe's vocabulary
#: mode-invariant everywhere except the one branch above.
#:
#: confirm_action is why this is a constant rather than two literals. Its
#: recorded text carried none of the matcher's approval vocabulary, so a probe
#: on the recorded path tagged an agent that routed the attack to a human as
#: `succeeded`. That is RTX-01's critical finding, raised for an action nobody
#: performed.
APPROVAL_NOT_QUEUED_DETAIL: str = (
    "It requires human approval and no approval request was created."
)


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


def _not_executed_result(skill: str, detail: str = "") -> ToolResult:
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

    **The outcome is `denied`, not `error`.** The text above is deliberately
    shaped like a provider outage so the agent reasons the way production makes
    it reason, and on the wire that is `is_error=True` either way. What the
    SYSTEM knows is different. No fault occurred, and the recorded-mode seam
    refused to execute. Calling it `error` would page someone on every eval run.
    All three call sites, the step-3 replay, the step-5 require_human arm and
    step 5.5, are that same refusal.
    """
    tail = f" {detail}" if detail else ""
    return ToolResult(
        skill=skill,
        outcome=Outcome.denied,
        text=(
            f"NOT EXECUTED: the {skill} request did not reach the provider "
            f"and nothing was changed. No money moved and no record was "
            f"updated.{tail}"
        ),
    )


def _published_wire(result: ToolResult) -> dict:
    """Publish the typed verdict to this turn's sink, then convert for the wire.

    THE SEVEN HANDLERS CONVERT HERE AND NOWHERE ELSE, which is what makes the
    publish unmissable: an eighth handler that skips this function returns
    something the loop will not accept, so the two steps cannot come apart.

    Both halves are needed because they carry different things. `to_wire` spends
    the outcome on one bit for the model to read, and `publish_tool_result` keeps
    the outcome itself for whoever assembled the turn. The red-team victim turn is
    the caller that needs the second half: it reports each mutating call's real
    dispatcher verdict, and until #49 it re-derived one by matching this module's
    prose through the SDK's ToolResultBlocks. BACKLOG 5.8 records what one
    hand-copied substring cost.

    The lazy import is the one `run_confirm_action` already takes for the
    ContextVars. `agent_tools.agent_tool_definitions` imports this module, so a
    module-level import back would close the cycle.
    """
    from app.services.agent_tools import publish_tool_result  # noqa: PLC0415

    publish_tool_result(result)
    return to_wire(result)


def _confirm_result(outcome: Outcome, text: str) -> ToolResult:
    """Build one confirm_action verdict.

    confirm_action does not route through `_execute_transactional_tool`
    (mutating=False, no adapter), so it decides its own outcome here. It still
    reaches the wire through the one `to_wire` every other branch uses, so
    outcome maps to `is_error` in exactly one place.

    `Outcome.requires_human` is what a written confirmation row means. That is
    the whole job of this tool, and on the wire it is a non-error response,
    unchanged.
    """
    return ToolResult(skill="confirm_action", outcome=outcome, text=text)


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
) -> ToolResult:
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
        ToolResult. Outcome.ok when the adapter ran; Outcome.error when the
        provider is not configured, the credential will not decrypt, or the
        adapter itself raised. Every one of those three is a fault, never a
        gate's refusal, which is why none of them is Outcome.denied.
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
        return ToolResult(skill=skill, outcome=Outcome.error, text=str(exc))
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
            # The prefix names the layer, the way `capability.denial:` and
            # `provider.not_configured:` already do. A reader of this column has
            # nothing else to go on: a gate's refusal and a provider's outage
            # both arrive as a non-NULL error, and the owner's queue reports
            # them with different words (#73). Without it the queue told an
            # owner whose Stripe was down that the platform had refused them.
            error=f"adapter.error:{error_str}",
        )
        return ToolResult(
            skill=skill,
            outcome=Outcome.error,
            text=f"Tool execution failed: {error_str}. Please try again.",
        )

    # -------------------------------------------------------- 7. Audit row + finalize + return
    result = ToolResult(
        skill=skill,
        outcome=Outcome.ok,
        text=response.get("message", str(response)),
    )

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

    # The WIRE dict is stored, not the ToolResult. A later replay hands these
    # bytes straight back to the agent, and they have to be the same bytes the
    # first call sent.
    await finalize_idempotency(agent_id, skill, validated.idempotency_key, to_wire(result))

    log.info(
        "transactional_tool.success",
        agent_id=agent_id,
        skill=skill,
        latency_ms=latency_ms,
    )
    return result


# ---------------------------------------------------------------------------
# Shared dispatcher — encodes the enforcement order ONCE
# ---------------------------------------------------------------------------


def _turn_ledger(agent_id: str, conn_str: str) -> LedgerContext:
    """Who the Actor gate's model call is billed to, and where its row is written.

    Every id comes from the per-turn ContextVars build_tool_server set, which is
    the same source the dispatcher reads agent_id and conn_str from. An empty
    job id becomes None, because the ledger column holds a job or nothing.
    """
    from app.services.agent_tools import _job_id_var, _tenant_id_var  # noqa: PLC0415

    return LedgerContext(
        tenant_id=_tenant_id_var.get(),
        agent_id=agent_id,
        job_id=_job_id_var.get() or None,
        recorder=ledger_recorder(conn_str),
    )


async def _execute_transactional_tool(
    skill: str,
    validated,  # Pydantic-validated input model for the specific tool
    raw_args: dict,
    adapter_method: str,
) -> ToolResult:
    """Enforce the locked execution order for every mutating transactional tool call.

    Reached through `run_transactional_skill`, which validates first. The six
    mutating @tool handlers and the red-team probe both enter that way.
    confirm_action_tool does NOT use this dispatcher (mutating=False, no adapter).

    Enforcement order (documented in Plan-08 objective):

      1. IN-03 guard     — agent_id must be non-empty; fail fast before any DB touch
      2. Capability check— check_capability_access(agent_id, skill): auth-only, no side effects
                           fail-closed on EVERY call (including replays) — T-14-04-03
      3. Reserve         — reserve_idempotency: atomic DB claim, DB decides single winner
                           "replay"       → return stored result BEFORE rate checks (WR-01)
                           "args_mismatch"→ denied + audit (WR-02)
                           "in_progress"  → denied (concurrent duplicate delivery)
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
        ToolResult, from every branch. The outcome each branch carries:

          Outcome.denied          a gate refused. The capability envelope, the
                                  IDV gate's two token cases, the reused key
                                  and the concurrent duplicate the reservation
                                  refuses, the rate and constraint ceiling, the
                                  Actor seam's block, and every recorded-mode
                                  refusal.
          Outcome.requires_human  the Actor seam escalated and a
                                  pending_confirmations row now exists.
          Outcome.error           something broke. No agent identity, the IDV
                                  check that could not reach a verdict, a
                                  stranded reservation, and the adapter's own
                                  two failures.
          Outcome.ok              the adapter ran, or a completed earlier call's
                                  stored result is being replayed.

        `to_wire` turns any of them into the SDK dict, unchanged from what this
        function returned directly before the type existed.
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
    # transactional_schemas.py declares it) and models produce deterministic ones —
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
        return ToolResult(
            skill=skill,
            outcome=Outcome.error,
            text=(
                "Precondition failed: agent context not set. "
                "Cannot process transactional tool call without a valid agent identity."
            ),
        )

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
        return ToolResult(
            skill=skill,
            outcome=Outcome.denied,
            text=(
                f"Access denied: capability envelope denied this request "
                f"(reason: {denial}). Contact your administrator to enable this tool."
            ),
        )

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
            return ToolResult(
                skill=skill, outcome=Outcome.denied, text=IDV_REQUIRED_MESSAGE
            )
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
            # Outcome.error, not denied: the gate did not refuse this call, it
            # could not reach a verdict. Failing closed is the right behaviour
            # and a broken IDV check is still something to page someone about.
            return ToolResult(
                skill=skill, outcome=Outcome.error, text=IDV_CHECK_FAILED_MESSAGE
            )
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
            return ToolResult(
                skill=skill, outcome=Outcome.denied, text=IDV_EXPIRED_MESSAGE
            )

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
        # stored_wire, not text: these bytes were written by the call that
        # succeeded, they are read back from the tenant DB as arbitrary JSON,
        # and the agent gets them untouched. Rebuilding them from a parsed text
        # field would rewrite any stored result that is not one text block.
        return ToolResult(
            skill=skill,
            outcome=Outcome.ok,
            text=wire_text(reservation.result),
            stored_wire=reservation.result,
        )

    if reservation.state == "args_mismatch":
        # WR-02 closed: same idempotency_key used with different business arguments.
        # Refuse the call outright instead of silently replaying the stale result.
        # AUD-01: a security-relevant rejection (suspicious key reuse), so it is
        # audited like capability.denial and actor_block. in_progress writes no row.
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
        # Outcome.denied: the idempotency gate refused this call, which is why the
        # audit row sits beside capability.denial as a security-relevant rejection.
        return ToolResult(
            skill=skill,
            outcome=Outcome.denied,
            text=(
                "Idempotency key reused with different arguments. "
                "Each new request must use a unique idempotency_key."
            ),
        )

    if reservation.state == "in_progress":
        # Concurrent duplicate delivery: another worker holds the same key.
        # Outcome.denied: the reservation refused this caller, and nothing broke.
        if recorded:
            # No audit row in either mode, because the reserved winner audits the
            # real call. The in-process sink is then the ONLY record that the agent
            # tried, and without it P2 reads this turn as one with no mutating call.
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
        return ToolResult(
            skill=skill,
            outcome=Outcome.denied,
            text=(
                "This request is already being processed. "
                "Please wait a moment and retry if you do not receive a response."
            ),
        )

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
        return ToolResult(
            skill=skill,
            outcome=Outcome.error,
            text=(
                "This request cannot be completed automatically: a previous "
                "attempt may still be running or may have already completed. "
                "Please contact support before retrying to avoid a possible "
                "duplicate."
            ),
        )

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
        # Outcome.denied: the rate and constraint ceiling refused this call.
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
        return ToolResult(
            skill=skill,
            outcome=Outcome.denied,
            text=(
                f"Request denied by rate or constraint check "
                f"(reason: {rate_denial}). Please wait before retrying."
            ),
        )

    # -------------------------------------------------------- 5. Actor seam
    decision, rationale = await call_actor_gate(
        skill, raw_args, snapshot, conversation_id or "", agent_id, conn_str,
        ledger=_turn_ledger(agent_id, conn_str))
    if decision == "block":
        # Outcome.denied: the Actor gate refused this call.
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
        return ToolResult(
            skill=skill,
            outcome=Outcome.denied,
            text="Action blocked by security policy. Please contact support.",
        )

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
                APPROVAL_NOT_QUEUED_DETAIL,
            )

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=_CONFIRM_TTL_HOURS)

        # WR-01: uq_pending_confirmations_unresolved (migration 0016) is built
        # on (agent_id, skill, arguments->>'action_reference'). This branch
        # stores raw_args — the target skill's FULL validated arguments —
        # which never contains an "action_reference" key (no mutating Input
        # model in transactional_schemas.py defines that field; only ConfirmActionInput
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
        # NON-error response (no is_error key on the wire). The adapter (step 6)
        # MUST NOT run. The action executes only after confirm_action approval
        # resolves the row (Phase 18).
        #
        # THIS IS THE BRANCH ToolResult EXISTS FOR. On the wire it is
        # indistinguishable from the success at step 7: same keys, no is_error
        # on either, only the prose differs. A caller asking "did this happen?"
        # had to read English. Outcome.requires_human answers it.
        return ToolResult(
            skill=skill,
            outcome=Outcome.requires_human,
            text=(
                f"This action requires human approval before it can execute. "
                f"A confirmation request has been created (ID: {confirmation_id}). "
                f"The action will proceed only after an authorized approver confirms it."
            ),
        )

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
        return _not_executed_result(skill, GATES_PASSED_DETAIL)

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
# The typed seam: validate, then dispatch, for the six mutating skills
# ---------------------------------------------------------------------------


class UnknownSkillError(KeyError):
    """`run_transactional_skill` was handed a name SKILL_INPUT_MODELS does not hold.

    A bare KeyError says a dict lookup missed. This names the contract that
    broke, the way `InvalidJobDict` and `InvalidRetrievedContext` do for theirs.
    It subclasses KeyError so a caller that already catches KeyError keeps
    catching it.
    """


async def run_transactional_skill(skill: str, args: dict) -> ToolResult:
    """Validate `args` against `skill`'s Input model, then run the dispatcher.

    Two callers, one path. Each `@tool` handler enters here and hands the result
    to `_published_wire`; the red-team probe's deterministic vectors enter here
    directly and read the outcome, so no assertion has to fuzzy-match prose.

    SKILL_INPUT_MODELS is a definition-time mapping written by hand, so this
    stays inside the registry's rule that nothing is inferred from a tool name
    at runtime. Every one of the six names its adapter method identically, which
    is why `skill` is passed twice below.

    Args:
        skill: One of the six mutating skills in SKILL_INPUT_MODELS.
        args:  The raw, unvalidated argument dict the model produced.

    Returns:
        ToolResult. Bad arguments come back as Outcome.error, which is the
        wire's is_error=True that a ValidationError has always produced.

    Raises:
        UnknownSkillError: `skill` is not one of the six. A caller naming a
            skill this mapping does not hold is a bug upstream, never a
            customer input, so it raises rather than returning a verdict.
    """
    try:
        model = SKILL_INPUT_MODELS[skill]
    except KeyError as exc:
        raise UnknownSkillError(
            f"Unknown transactional skill {skill!r}. "
            f"Known skills: {sorted(SKILL_INPUT_MODELS)}"
        ) from exc
    try:
        validated = model(**args)
    except ValidationError as exc:
        return ToolResult(skill=skill, outcome=Outcome.error, text=f"Invalid input: {exc}")
    return await _execute_transactional_tool(skill, validated, args, skill)


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
    """Validate, dispatch, publish the verdict, then convert. skill=place_order."""
    return _published_wire(await run_transactional_skill("place_order", args))


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
    """Validate, dispatch, publish the verdict, then convert. skill=cancel_order."""
    return _published_wire(await run_transactional_skill("cancel_order", args))


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
    """Validate, dispatch, publish the verdict, then convert. skill=issue_refund."""
    return _published_wire(await run_transactional_skill("issue_refund", args))


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
    """Validate, dispatch, publish the verdict, then convert. skill=update_subscription."""
    return _published_wire(await run_transactional_skill("update_subscription", args))


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
    """Validate, dispatch, publish the verdict, then convert. skill=book_slot."""
    return _published_wire(await run_transactional_skill("book_slot", args))


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
    """Validate, dispatch, publish the verdict, then convert. skill=update_customer_record."""
    return _published_wire(await run_transactional_skill("update_customer_record", args))


# ---------------------------------------------------------------------------
# 7. confirm_action (mutating=False — no provider adapter, no idempotency key)
# ---------------------------------------------------------------------------


def _confirm_action_recorded(agent_id: str, validated: ConfirmActionInput) -> ToolResult:
    """Record that the agent asked for approval, and write no row.

    D1/P1b: confirm_action does NOT route through _execute_transactional_tool,
    so step 5.5 never sees it, and it sits in `allowed_tools` in both modes by
    design (an eval agent that cannot request approval cannot be scored on
    choosing to). Left ungated it writes a durable row into the owner's triage
    queue on every eval scenario where the agent decides to ask, nightly.

    This is queue pollution rather than money. `_is_confirm_action_shaped` DOES
    filter this shape, so approving one never reaches an adapter, which is what
    makes it less dangerous than the require_human row the dispatcher writes. It
    is still lost eval signal. Nothing recorded that the agent chose to ask for
    approval, and that decision is worth scoring. Both halves are fixed here:
    no row, and a recording.
    """
    from app.services.agent_tools import (  # noqa: PLC0415
        _conversation_id_var,
        record_suppressed_side_effect,
    )

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
    log.info("confirm_action.not_queued", agent_id=agent_id, skill=validated.skill)
    # APPROVAL_NOT_QUEUED_DETAIL leads, and the constant's own comment says what
    # it cost when this text did not carry it: the red-team probe tagged an agent
    # that routed its attack to a human approver as `succeeded`.
    return _not_executed_result(
        "confirm_action",
        f"{APPROVAL_NOT_QUEUED_DETAIL} The action was '{validated.skill}' "
        f"(reference: {validated.action_reference}).",
    )


def _existing_confirmation_id(db, agent_id: str, validated: ConfirmActionInput):
    """The unresolved confirmation already holding this (agent, skill, reference).

    Read after the unique index rejects an insert, so the caller hands back a
    coherent confirmation id instead of growing the table. None when the losing
    row cannot be found, which leaves the caller its own id.
    """
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
    return existing.id if existing is not None else None


def _write_pending_confirmation(agent_id: str, validated: ConfirmActionInput) -> ToolResult:
    """Insert the pending_confirmations row, or return the one already there.

    Duplicate-confirm dedup (T-14-08-05 closed): the partial unique index
    uq_pending_confirmations_unresolved (migration 0016) allows at most one
    UNRESOLVED confirmation per (agent_id, skill, action_reference). A duplicate
    confirm_action loses that race, so this catches the IntegrityError and
    returns the existing row rather than inserting a second one. Resolved rows
    can be re-requested, because the index is scoped to resolved_at IS NULL.
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=_CONFIRM_TTL_HOURS)
    # Client-generate the UUID so the id is set on the Python object without a
    # DB flush or refresh (str(row.id) reads right after db.add, in test and prod).
    confirmation_id = uuid4()
    row = PendingConfirmation(
        id=confirmation_id,
        agent_id=agent_id,
        skill=validated.skill,
        arguments={"action_reference": validated.action_reference},
        requested_at=now,
        expires_at=expires_at,
        # resolved_at and resolution stay NULL until Phase 18 resolves the row.
    )

    with get_sync_db() as db:
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing_id = _existing_confirmation_id(db, agent_id, validated) or confirmation_id
            log.info(
                "confirm_action.duplicate_suppressed",
                agent_id=agent_id,
                skill=validated.skill,
                confirmation_id=str(existing_id),
                action_reference=validated.action_reference,
            )
            return _confirm_result(
                Outcome.requires_human,
                f"Confirmation request for '{validated.skill}' action "
                f"(reference: {validated.action_reference}) is already pending. "
                f"Awaiting human approval. Confirmation ID: {existing_id}.",
            )

    log.info(
        "confirm_action.pending_row_written",
        agent_id=agent_id,
        skill=validated.skill,
        confirmation_id=str(confirmation_id),
        action_reference=validated.action_reference,
    )
    return _confirm_result(
        Outcome.requires_human,
        f"Confirmation request submitted for '{validated.skill}' action "
        f"(reference: {validated.action_reference}). "
        f"Awaiting human approval. Confirmation ID: {confirmation_id}.",
    )


async def run_confirm_action(args: dict) -> ToolResult:
    """Validate, gate, then write the confirmation row. confirm_action's typed seam.

    The counterpart to `run_transactional_skill`, and it exists for the same
    reason. The `@tool` handler hands the result to `_published_wire`; the
    red-team probe reads the outcome, so a probe never decides from prose
    whether an approver was asked. Every row this writes is
    `Outcome.requires_human`, which on the wire is indistinguishable from a
    completed action.

    mutating=False (TOOL_REGISTRY): this creates a confirmation row and never
    executes the underlying provider action. It takes NO idempotency key and
    calls NO provider adapter. The Phase-18 admin UI resolves pending rows.
    PRD DDL unchanged.

    WR-05 (closed): the capability gate runs before the row is written, so a
    disabled or missing skill is denied and writes nothing. IN-03 (closed): an
    empty agent_id fails before the capability check and before any DB write.
    """
    try:
        validated = ConfirmActionInput(**args)
    except ValidationError as exc:
        return _confirm_result(Outcome.error, f"Invalid input: {exc}")

    # Lazy import to access the ContextVars set by build_tool_server.
    from app.services.agent_tools import _agent_id_var, _side_effects_var  # noqa: PLC0415

    agent_id = _agent_id_var.get()
    if not agent_id:
        return _confirm_result(
            Outcome.error,
            "Precondition failed: agent context not set. "
            "Cannot submit confirmation request without a valid agent identity.",
        )

    # check_capability_access is side-effect-free, so this costs nothing to run first.
    _snapshot, denial = await check_capability_access(agent_id, validated.skill)
    if denial is not None:
        return _confirm_result(
            Outcome.denied,
            f"Access denied: capability envelope denied confirm_action for "
            f"skill '{validated.skill}' (reason: {denial}). "
            f"Contact your administrator to enable this tool.",
        )

    if _side_effects_var.get() == "recorded":
        return _confirm_action_recorded(agent_id, validated)

    return _write_pending_confirmation(agent_id, validated)


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
    """Validate, gate, publish the verdict, then convert. skill=confirm_action."""
    return _published_wire(await run_confirm_action(args))


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
