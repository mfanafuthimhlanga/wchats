"""
Unit tests for app.services.transactional.confirmation_resolution — ACT-07's
execution core (22-02).

Mocked boundaries only: no Postgres, no Redis, no live Anthropic, no network.
Patches are applied at the names each module imports them under (not where
they are defined) — confirmation_resolution.py and tools.py each did their
own `from X import Y`, so each module's local binding is patched separately:

  - check_capability_access, apply_rate_and_constraint_checks,
    reserve_idempotency, release_idempotency, write_audit_row are patched at
    `app.services.transactional.confirmation_resolution.*` for the resolver's
    own steps 1-6.
  - get_adapter_for_skill, write_audit_row, mark_reservation_in_flight (CR-01),
    finalize_idempotency, release_idempotency are patched at
    `app.services.transactional.tools.*` for the shared
    `_execute_adapter_and_audit` helper's steps 6-7, which the resolver calls
    but does not reimplement (T-22-ACT-15).

Env preamble: tests/conftest.py already sets every required environment
variable at module level before any `app.*` import (pytest auto-loads
conftest.py for tests/unit/), matching the convention test_red_team_probe.py
documents. No os.environ.setdefault(...) block is duplicated here.

Covers:
  TestResolverAbsence
    test_resolver_never_references_call_actor_gate
    test_resolver_never_references_identity_verification
    test_resolver_reads_no_dispatcher_contextvar
  TestApprovedExecution
    test_approve_calls_adapter_exactly_once
    test_audit_row_written_on_every_terminal_outcome (parametrised, 5 cases)
  TestLiveEnvelope
    test_tightened_ceiling_denies_execution
    test_disabled_skill_denies_execution
    test_live_envelope_is_read_not_snapshot
  TestNullTolerance
    test_null_arguments_denied_not_crashed
    test_confirm_action_skill_is_not_executable
  TestIdempotency
    test_replay_and_in_progress_never_execute
"""

from __future__ import annotations

import pathlib
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.transactional import confirmation_resolution as cr
from app.services.transactional.idempotency import Reservation
from app.services.transactional.schemas import IssueRefundOutput

# ---------------------------------------------------------------------------
# Fixtures / constants
# ---------------------------------------------------------------------------

AGENT_ID = "agent-resolver-0001"
CONN_STR = "postgresql://tenant-conn-str-unused-under-mocks"
CONFIRMATION_ID = "11111111-1111-1111-1111-111111111111"

VALID_REFUND_ARGS: dict = {
    "idempotency_key": "idem-refund-0001",
    "order_id": "ORD-1",
    "refund_amount_cents": 2500,
    "reason": "customer request",
}

LIVE_SNAPSHOT: dict = {
    "id": "envelope-row-1",
    "agent_id": AGENT_ID,
    "skill": "issue_refund",
    "enabled": True,
    "rate_limit": None,
    "constraints": {"max_amount_cents": 100000},
    "requires_confirmation": True,
    "requires_identity_verification": True,
    "updated_at": "2026-07-28T00:00:00+00:00",
}


def _make_refund_output() -> IssueRefundOutput:
    return IssueRefundOutput(refund_id="refund-live-1", status="refunded", message="ok")


@contextmanager
def _patch_resolver_boundary(
    *,
    capability_denial: str | None = None,
    capability_snapshot: dict | None = None,
    rate_denial: str | None = None,
    reservation: Reservation | None = None,
    adapter_side_effect: Exception | None = None,
):
    """Patch every DB/Redis/network boundary the resolver and the shared
    steps 6-7 helper touch, at the names each module imports them under.

    Returns a dict of the active mocks so a test can assert on call counts
    and kwargs.
    """
    snapshot = capability_snapshot if capability_snapshot is not None else dict(LIVE_SNAPSHOT)
    reservation = reservation or Reservation(state="reserved")

    mock_adapter = MagicMock()
    if adapter_side_effect is not None:
        mock_adapter.issue_refund = AsyncMock(side_effect=adapter_side_effect)
    else:
        mock_adapter.issue_refund = AsyncMock(return_value=_make_refund_output())

    with (
        patch(
            "app.services.transactional.confirmation_resolution.check_capability_access",
            AsyncMock(return_value=(snapshot, capability_denial)),
        ) as mock_capability,
        patch(
            "app.services.transactional.confirmation_resolution.apply_rate_and_constraint_checks",
            AsyncMock(return_value=rate_denial),
        ) as mock_rate,
        patch(
            "app.services.transactional.confirmation_resolution.reserve_idempotency",
            AsyncMock(return_value=reservation),
        ) as mock_reserve,
        patch(
            "app.services.transactional.confirmation_resolution.release_idempotency",
            AsyncMock(),
        ) as mock_release_resolver,
        patch(
            "app.services.transactional.confirmation_resolution.write_audit_row",
            AsyncMock(),
        ) as mock_audit_resolver,
        patch(
            "app.services.transactional.tools.get_adapter_for_skill",
            AsyncMock(return_value=mock_adapter),
        ) as mock_get_adapter,
        patch(
            "app.services.transactional.tools.write_audit_row",
            AsyncMock(),
        ) as mock_audit_helper,
        patch(
            "app.services.transactional.tools.mark_reservation_in_flight",
            AsyncMock(),
        ) as mock_mark_in_flight,
        patch(
            "app.services.transactional.tools.finalize_idempotency",
            AsyncMock(),
        ) as mock_finalize,
        patch(
            "app.services.transactional.tools.release_idempotency",
            AsyncMock(),
        ) as mock_release_helper,
    ):
        yield {
            "capability": mock_capability,
            "rate": mock_rate,
            "reserve": mock_reserve,
            "release_resolver": mock_release_resolver,
            "audit_resolver": mock_audit_resolver,
            "get_adapter": mock_get_adapter,
            "adapter": mock_adapter,
            "audit_helper": mock_audit_helper,
            "mark_in_flight": mock_mark_in_flight,
            "finalize": mock_finalize,
            "release_helper": mock_release_helper,
        }


_UNSET = object()


async def _resolve(skill: str = "issue_refund", arguments: dict | None = _UNSET) -> cr.ResolutionOutcome:
    return await cr.execute_approved_confirmation(
        confirmation_id=CONFIRMATION_ID,
        agent_id=AGENT_ID,
        skill=skill,
        arguments=VALID_REFUND_ARGS if arguments is _UNSET else arguments,
        conn_str=CONN_STR,
    )


# ---------------------------------------------------------------------------
# TestResolverAbsence — the tests that must be seen to fail (18-05 pattern)
# ---------------------------------------------------------------------------


class TestResolverAbsence:
    """Source-absence assertions, matching the shape 18-05 established for
    derive_blast_radius_warnings. Each assertion names the symbol found and
    says why its presence is a defect — a bare `assert x not in src` gives
    no diagnosis when it goes red."""

    def _source(self) -> str:
        return pathlib.Path(cr.__file__).read_text(encoding="utf-8")

    def test_resolver_never_references_call_actor_gate(self):
        src = self._source()
        assert "call_actor_gate" not in src, (
            "confirmation_resolution.py references call_actor_gate — re-entering "
            "the Actor seam here would return the same human-approval-required "
            "verdict a second time and the approval could never terminate "
            "(T-22-ACT-05). The human approval this module executes IS that "
            "seam's verdict; it must not be re-asked."
        )

    def test_resolver_never_references_identity_verification(self):
        src = self._source()
        for symbol in ("check_verified_session", "identity_service"):
            assert symbol not in src, (
                f"confirmation_resolution.py references {symbol} — there is no "
                "customer session available to a resolver outside a live turn "
                "(OD-1, T-22-ACT-08). Re-adding an identity check here would "
                "require synthesizing a session on the customer's behalf, which "
                "would assert something false in the audit trail."
            )

    def test_resolver_reads_no_dispatcher_contextvar(self):
        src = self._source()
        for symbol in (
            "_agent_id_var",
            "_conn_str_var",
            "_conversation_id_var",
            "_verified_session_token_var",
            "agent_tools",
            "build_tool_server",
        ):
            assert symbol not in src, (
                f"confirmation_resolution.py references {symbol} — agent_id and "
                "conn_str are explicit keyword-only parameters (OD-5); a "
                "resolver must never read ambient per-turn ContextVar state or "
                "call build_tool_server to seed it."
            )


# ---------------------------------------------------------------------------
# TestApprovedExecution
# ---------------------------------------------------------------------------


class TestApprovedExecution:
    async def test_approve_calls_adapter_exactly_once(self):
        with _patch_resolver_boundary() as mocks:
            outcome = await _resolve()

        assert outcome.outcome == "executed"
        assert mocks["adapter"].issue_refund.await_count == 1

    @pytest.mark.parametrize(
        "case_name,skill,arguments,capability_denial,rate_denial,expected_outcome,which_mock",
        [
            ("executed", "issue_refund", None, None, None, "executed", "helper"),
            ("capability_denied", "issue_refund", None, "disabled", None, "denied", "resolver"),
            ("rate_denied", "issue_refund", None, None, "max_amount_cents", "denied", "resolver"),
            (
                "invalid_arguments",
                "issue_refund",
                {"idempotency_key": "idem-bad-0001"},
                None,
                None,
                "invalid",
                "resolver",
            ),
            (
                "unsupported_skill",
                "confirm_action",
                {"skill": "issue_refund", "action_reference": "ref-1"},
                None,
                None,
                "invalid",
                "resolver",
            ),
        ],
    )
    async def test_audit_row_written_on_every_terminal_outcome(
        self, case_name, skill, arguments, capability_denial, rate_denial, expected_outcome, which_mock
    ):
        resolved_args = dict(VALID_REFUND_ARGS) if arguments is None else arguments
        with _patch_resolver_boundary(
            capability_denial=capability_denial, rate_denial=rate_denial
        ) as mocks:
            outcome = await _resolve(skill=skill, arguments=resolved_args)

        assert outcome.outcome == expected_outcome, case_name

        resolver_calls = mocks["audit_resolver"].await_count
        helper_calls = mocks["audit_helper"].await_count
        assert resolver_calls + helper_calls == 1, (
            f"{case_name}: expected exactly one audit row, got "
            f"resolver={resolver_calls} helper={helper_calls}"
        )

        if which_mock == "helper":
            assert helper_calls == 1 and resolver_calls == 0, case_name
            written_error = mocks["audit_helper"].await_args.kwargs["error"]
        else:
            assert resolver_calls == 1 and helper_calls == 0, case_name
            written_error = mocks["audit_resolver"].await_args.kwargs["error"]

        if expected_outcome == "executed":
            assert written_error is None, case_name
            assert outcome.reason is None, case_name
        else:
            assert written_error is not None, case_name
            assert outcome.reason is not None, case_name

        # No adapter call on any denial or invalid outcome.
        if expected_outcome != "executed":
            assert mocks["adapter"].issue_refund.await_count == 0, case_name


# ---------------------------------------------------------------------------
# TestLiveEnvelope — SC3: live envelope, never the stored snapshot
# ---------------------------------------------------------------------------


class TestLiveEnvelope:
    async def test_tightened_ceiling_denies_execution(self):
        """The live snapshot's ceiling has since been tightened below the
        stored amount — apply_rate_and_constraint_checks (re-run against the
        LIVE envelope) denies. No adapter call; the audit error carries the
        capability-denial prefix."""
        with _patch_resolver_boundary(rate_denial="max_amount_cents") as mocks:
            outcome = await _resolve()

        assert outcome.outcome == "denied"
        assert outcome.reason == "capability.denial:max_amount_cents"
        assert mocks["adapter"].issue_refund.await_count == 0
        # Re-run against the LIVE snapshot check_capability_access returned
        # this call — not a stored capability_snapshot from a prior audit row.
        mocks["rate"].assert_awaited_once()
        assert mocks["rate"].await_args.args[2] == dict(LIVE_SNAPSHOT)

    async def test_disabled_skill_denies_execution(self):
        """The live capability check denies (owner disabled the skill after
        the confirmation was created) — no adapter await."""
        with _patch_resolver_boundary(capability_denial="disabled") as mocks:
            outcome = await _resolve()

        assert outcome.outcome == "denied"
        assert outcome.reason == "capability.denial:disabled"
        assert mocks["adapter"].issue_refund.await_count == 0
        # Rate/constraint check must never run after a capability denial.
        mocks["rate"].assert_not_awaited()

    async def test_live_envelope_is_read_not_snapshot(self):
        """The only capability_snapshot passed downstream (to
        apply_rate_and_constraint_checks and to the adapter/audit helper) is
        the one check_capability_access returned THIS call — never a value
        read from a stored tool_calls_audit row."""
        distinct_live_snapshot = dict(LIVE_SNAPSHOT, updated_at="2026-07-28T12:00:00+00:00")
        with _patch_resolver_boundary(capability_snapshot=distinct_live_snapshot) as mocks:
            outcome = await _resolve()

        assert outcome.outcome == "executed"
        assert mocks["rate"].await_args.args[2] == distinct_live_snapshot
        assert mocks["audit_helper"].await_args.kwargs["capability_snapshot"] == distinct_live_snapshot


# ---------------------------------------------------------------------------
# TestNullTolerance — never a crash, never an adapter call
# ---------------------------------------------------------------------------


class TestNullTolerance:
    async def test_null_arguments_denied_not_crashed(self):
        with _patch_resolver_boundary() as mocks:
            outcome = await _resolve(arguments=None)

        assert outcome.outcome == "invalid"
        assert outcome.reason == "confirmation.missing_arguments"
        assert mocks["adapter"].issue_refund.await_count == 0
        assert mocks["audit_resolver"].await_count == 1
        assert mocks["audit_resolver"].await_args.kwargs["error"] == "confirmation.missing_arguments"

    async def test_confirm_action_skill_is_not_executable(self):
        """confirm_action (mutating=False) has no adapter method and no
        idempotency_key — a row somehow stamped with this skill must be
        denied as unsupported, never reach an adapter."""
        with _patch_resolver_boundary() as mocks:
            outcome = await _resolve(
                skill="confirm_action",
                arguments={"skill": "issue_refund", "action_reference": "ref-1"},
            )

        assert outcome.outcome == "invalid"
        assert outcome.reason == "confirmation.unsupported_skill"
        assert mocks["adapter"].issue_refund.await_count == 0
        assert mocks["audit_resolver"].await_count == 1
        assert mocks["audit_resolver"].await_args.kwargs["error"] == "confirmation.unsupported_skill"
        # Capability, rate and reservation are never consulted for an
        # unsupported skill — the guard is the very first thing this
        # function does.
        mocks["capability"].assert_not_awaited()
        mocks["rate"].assert_not_awaited()
        mocks["reserve"].assert_not_awaited()


# ---------------------------------------------------------------------------
# TestIdempotency — a fresh reservation, matching the dispatcher's asymmetry
# ---------------------------------------------------------------------------


class TestIdempotency:
    @pytest.mark.parametrize(
        "state,expected_outcome",
        [
            ("replay", "replay"),
            ("in_progress", "in_progress"),
        ],
    )
    async def test_replay_and_in_progress_never_execute(self, state, expected_outcome):
        reservation = Reservation(state=state, result={"content": [{"type": "text", "text": "prior"}]})
        with _patch_resolver_boundary(reservation=reservation) as mocks:
            outcome = await _resolve()

        assert outcome.outcome == expected_outcome
        assert mocks["adapter"].issue_refund.await_count == 0
        # No audit row on replay or in_progress — matches the live-turn
        # dispatcher's own asymmetry (AUD-01), not normalised here.
        assert mocks["audit_resolver"].await_count == 0
        assert mocks["audit_helper"].await_count == 0

    async def test_args_mismatch_denies_with_one_audit_row(self):
        reservation = Reservation(state="args_mismatch")
        with _patch_resolver_boundary(reservation=reservation) as mocks:
            outcome = await _resolve()

        assert outcome.outcome == "denied"
        assert outcome.reason == "idempotency.args_mismatch"
        assert mocks["adapter"].issue_refund.await_count == 0
        assert mocks["audit_resolver"].await_count == 1
        assert mocks["audit_resolver"].await_args.kwargs["error"] == "idempotency.args_mismatch"

    async def test_unknown_reservation_denies_never_reclaims(self):
        """CR-01: a stale 'in_flight' reservation (Reservation(state="unknown"))
        must never be treated as a green light to execute the adapter — the
        resolver must deny and audit it, exactly like args_mismatch, not fall
        through to the "reserved" branch below it."""
        reservation = Reservation(state="unknown")
        with _patch_resolver_boundary(reservation=reservation) as mocks:
            outcome = await _resolve()

        assert outcome.outcome == "denied"
        assert outcome.reason == "idempotency.stranded_reservation"
        assert mocks["adapter"].issue_refund.await_count == 0
        assert mocks["audit_resolver"].await_count == 1
        assert mocks["audit_resolver"].await_args.kwargs["error"] == "idempotency.stranded_reservation"
        # A stranded reservation is never released here — releasing it would
        # let a fresh reserve_idempotency call reclaim and re-execute a key
        # that may already have hit the adapter once.
        mocks["release_resolver"].assert_not_awaited()
