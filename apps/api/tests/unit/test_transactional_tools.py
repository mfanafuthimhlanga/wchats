"""
Unit tests for Phase-14 transactional tool handlers, migrated for 14-08
reserve-before-execute dispatcher reorder.

Migration summary (14-08):
  - Dispatcher uses check_capability_access (auth-only, no side effects) FIRST
    on every call including replays (fail-closed for existence + enabled).
  - Then reserve_idempotency (atomic DB claim before the adapter).
  - replay state short-circuits and returns stored result BEFORE
    apply_rate_and_constraint_checks (WR-01 closed).
  - args_mismatch returns explicit is_error without executing (WR-02 closed).
  - apply_rate_and_constraint_checks runs ONLY for the fresh reserved winner.
  - release_idempotency called on rate-denial, actor-block, adapter-error paths.
  - finalize_idempotency called on success (replaces store_idempotency).
  - IN-03: empty agent_id guard returns precondition error before any DB touch.
  - confirm_action_tool: WR-05 capability gate added + minimal dedup.

Test isolation:
  - All external dependencies mocked (DB, Redis, actor gate, Claude SDK).
  - ContextVars set via _set_context() before each asyncio.run() call.
  - asyncio.run() throughout (NOT get_event_loop().run_until_complete).
  - Each test class is independently runnable.

NOTE: Patches for symbols not yet in tools.py (check_capability_access,
reserve_idempotency, etc.) use create=True so they work in both RED state
(tools.py still has old order) and GREEN state (tools.py rewired). In RED,
the old symbols (check_capability_envelope, check_idempotency, etc.) are NOT
patched, so the code tries to hit a real DB and fails — confirming RED.
"""

from __future__ import annotations

import ast
import asyncio
import os
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.exc import IntegrityError

# ---------------------------------------------------------------------------
# ContextVar setup — deferred import to avoid eagerly pulling in claude_agent_sdk
# at pytest collection time.
# ---------------------------------------------------------------------------

TEST_AGENT_ID = "agent-test-0001"
TEST_CONV_ID = "conv-test-0001"


def _set_context(agent_id: str = TEST_AGENT_ID, conv_id: str = TEST_CONV_ID) -> None:
    """Set ContextVars before asyncio.run() so the loop copies the correct context."""
    from app.services.agent_tools import _agent_id_var, _conversation_id_var  # noqa: PLC0415

    _agent_id_var.set(agent_id)
    _conversation_id_var.set(conv_id)


# ---------------------------------------------------------------------------
# Schema fixtures
# ---------------------------------------------------------------------------


def _valid_place_order_args(idempotency_key: str = "idem-001") -> dict:
    return {
        "idempotency_key": idempotency_key,
        "product_id": "SKU-001",
        "quantity": 1,
        "customer_email": "test@example.com",
        "shipping_address": "1 Main St, Cape Town",
        "amount_cents": 500,
    }


def _valid_cancel_order_args(idempotency_key: str = "idem-cancel") -> dict:
    return {
        "idempotency_key": idempotency_key,
        "order_id": "ORD-001",
        "reason": "Customer changed mind",
    }


# ---------------------------------------------------------------------------
# Mock factory helpers — new for 14-08 reserve-before-execute order
# ---------------------------------------------------------------------------


def _mock_access_pass(snapshot: dict | None = None) -> AsyncMock:
    """check_capability_access that always passes with the given snapshot."""
    return AsyncMock(return_value=(snapshot or {"enabled": True, "skill": "place_order"}, None))


def _mock_access_deny(denial: str = "disabled") -> AsyncMock:
    """check_capability_access that always returns a denial."""
    return AsyncMock(return_value=({}, denial))


def _mock_reserve(state: str, result: dict | None = None) -> AsyncMock:
    """reserve_idempotency returning a Reservation with the given state."""
    from app.services.transactional.idempotency import Reservation  # noqa: PLC0415

    return AsyncMock(return_value=Reservation(state=state, result=result))


def _mock_rate_pass() -> AsyncMock:
    """apply_rate_and_constraint_checks that always passes (returns None)."""
    return AsyncMock(return_value=None)


def _mock_rate_deny(denial: str = "rate_limit") -> AsyncMock:
    """apply_rate_and_constraint_checks that returns a denial reason."""
    return AsyncMock(return_value=denial)


def _mock_finalize() -> AsyncMock:
    """finalize_idempotency mock — always succeeds."""
    return AsyncMock(return_value=None)


def _mock_release() -> AsyncMock:
    """release_idempotency mock — always succeeds."""
    return AsyncMock(return_value=None)


def _mock_gate_approve() -> AsyncMock:
    """call_actor_gate that always returns approve."""
    return AsyncMock(return_value=("approve", ""))


def _mock_gate_block(rationale: str = "blocked by policy") -> AsyncMock:
    """call_actor_gate that always returns block."""
    return AsyncMock(return_value=("block", rationale))


def _mock_adapter(message: str = "Order placed [STUB]") -> MagicMock:
    """Minimal mock adapter whose place_order returns a PlaceOrderOutput."""
    from app.domain.transactional_schemas import PlaceOrderOutput  # noqa: PLC0415

    adapter = MagicMock()
    adapter.place_order = AsyncMock(
        return_value=PlaceOrderOutput(
            order_id="ORD-stub",
            status="pending_confirmation",
            message=message,
        )
    )
    return adapter


def _mock_db_session() -> tuple:
    """Return (contextmanager_factory, session_mock) for get_sync_db patching.

    session.query(...).filter(...).order_by(...).first() defaults to None —
    "no pre-existing duplicate row found" — matching the honest default state
    for both confirm_action_tool's post-IntegrityError dedup lookup and the
    require_human branch's WR-01 pre-insert dedup lookup (tools.py). Without
    this, a bare MagicMock() would make db.query(...)....first() return a
    truthy MagicMock by default, which every WR-01 require_human test would
    then misread as "a duplicate already exists" and skip the insert it
    means to assert on. Tests that DO want to simulate a duplicate hit
    override this explicitly (see TestConfirmActionTool's dedup test).
    """
    session = MagicMock()
    session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    session.__enter__ = lambda s: s
    session.__exit__ = MagicMock(return_value=False)

    @contextmanager
    def _ctx():
        yield session

    return _ctx, session


# ---------------------------------------------------------------------------
# Convenience: build a named patch with create=True for new-in-14-08 symbols
# ---------------------------------------------------------------------------
_T = "app.services.transactional.tools"


def _p(attr: str, mock):
    """patch(attr-on-tools-module, mock, create=True).

    create=True is safe: in RED state (old tools.py) it creates the attribute
    so the patch works, but the old code still calls the OLD un-patched symbols
    and fails. In GREEN state it patches the real imported symbol normally.
    """
    return patch(f"{_T}.{attr}", mock, create=True)


# ===========================================================================
# Schema validation — ValidationError path, no dispatcher call
# ===========================================================================


class TestBadSchemaRejection:
    """ValidationError path: bad args return is_error without touching any helper."""

    def test_place_order_missing_required_fields_returns_is_error(self):
        _set_context()
        access_mock = _mock_access_pass()
        audit_mock = AsyncMock()
        get_adapter_mock = AsyncMock()

        with (
            _p("check_capability_access", access_mock),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_adapter_for_skill", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler({}))

        assert result.get("is_error") is True, f"Expected is_error=True, got: {result}"
        access_mock.assert_not_called()
        audit_mock.assert_not_called()
        get_adapter_mock.assert_not_called()

    def test_cancel_order_wrong_types_returns_is_error(self):
        _set_context()
        access_mock = _mock_access_pass()
        audit_mock = AsyncMock()

        with (
            _p("check_capability_access", access_mock),
            patch(f"{_T}.write_audit_row", audit_mock),
        ):
            from app.services.transactional.tools import cancel_order_tool

            result = asyncio.run(cancel_order_tool.handler({"order_id": 999}))

        assert result.get("is_error") is True
        access_mock.assert_not_called()
        audit_mock.assert_not_called()


# ===========================================================================
# Capability denial — authorization check first (even on replays)
# ===========================================================================


class TestCapabilityDenial:
    """Disabled-skill path: no adapter call, exactly one audit row with capability.denial: prefix."""

    def test_disabled_skill_returns_is_error(self):
        _set_context()
        access_mock = _mock_access_deny("disabled")
        audit_mock = AsyncMock()
        reserve_mock = _mock_reserve("reserved")

        with (
            _p("check_capability_access", access_mock),
            _p("reserve_idempotency", reserve_mock),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert result.get("is_error") is True

    def test_disabled_skill_no_adapter_call(self):
        _set_context()
        adapter_mock = _mock_adapter()
        get_adapter_mock = AsyncMock(return_value=adapter_mock)

        with (
            _p("check_capability_access", _mock_access_deny("disabled")),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        get_adapter_mock.assert_not_called()
        adapter_mock.place_order.assert_not_called()

    def test_disabled_skill_writes_exactly_one_audit_row_with_denial_error(self):
        """AUD-01 symmetry: capability denial writes one audit row with error='capability.denial:<reason>'."""
        _set_context()
        audit_mock = AsyncMock()

        with (
            _p("check_capability_access", _mock_access_deny("disabled")),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert audit_mock.call_count == 1, f"Expected 1 audit call, got {audit_mock.call_count}"
        call_kwargs = audit_mock.call_args.kwargs
        error_val = call_kwargs.get("error", "")
        assert error_val.startswith("capability.denial:"), (
            f"Expected error starting with 'capability.denial:', got {error_val!r}"
        )

    def test_no_envelope_row_writes_audit_row(self):
        """AUD-01 symmetry: no_envelope_row denial also writes audit row."""
        _set_context()
        audit_mock = AsyncMock()

        with (
            _p("check_capability_access", _mock_access_deny("no_envelope_row")),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert audit_mock.call_count == 1
        error_val = audit_mock.call_args.kwargs.get("error", "")
        assert "no_envelope_row" in error_val

    def test_disabled_skill_reserve_never_called(self):
        """Authorization runs before reservation; no DB reservation on capability denial."""
        _set_context()
        reserve_mock = _mock_reserve("reserved")

        with (
            _p("check_capability_access", _mock_access_deny("disabled")),
            _p("reserve_idempotency", reserve_mock),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        reserve_mock.assert_not_called()


# ===========================================================================
# Fresh happy path — reserve("reserved") + rate pass + gate approve + adapter ok
# ===========================================================================


class TestDispatcherHappyPath:
    """Fresh execution: reserved win → rate pass → actor approve → adapter ok → finalize."""

    def test_happy_path_adapter_called_once(self):
        _set_context()
        adapter_mock = _mock_adapter("Order placed [STUB]")
        release_mock = _mock_release()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("release_idempotency", release_mock),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=adapter_mock)),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert adapter_mock.place_order.call_count == 1
        release_mock.assert_not_called()

    def test_happy_path_finalize_called_once(self):
        _set_context()
        finalize_mock = _mock_finalize()
        release_mock = _mock_release()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("finalize_idempotency", finalize_mock),
            _p("release_idempotency", release_mock),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=_mock_adapter())),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        finalize_mock.assert_called_once()
        release_mock.assert_not_called()

    def test_happy_path_one_audit_row_no_error(self):
        _set_context()
        audit_mock = AsyncMock()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=_mock_adapter("ok"))),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert audit_mock.call_count == 1
        call_kwargs = audit_mock.call_args.kwargs
        assert call_kwargs.get("error") is None, (
            f"Expected no error on success, got {call_kwargs.get('error')!r}"
        )
        assert call_kwargs.get("result") is not None

    def test_happy_path_returns_adapter_message(self):
        _set_context()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=_mock_adapter("Order confirmed"))),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert result.get("is_error") is not True
        assert "content" in result


# ===========================================================================
# WR-01 — Replay short-circuits BEFORE rate-limit INCR
# ===========================================================================


class TestReplayShortCircuit:
    """Replay path: stored result returned, rate INCR and adapter NOT called (WR-01)."""

    def _cached(self) -> dict:
        return {"content": [{"type": "text", "text": "Order placed [cached]"}]}

    def test_replay_returns_stored_result(self):
        _set_context()
        cached = self._cached()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("replay", result=cached)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args("idem-replay")))

        assert result == cached

    def test_replay_does_not_call_rate_checks(self):
        """WR-01: replay short-circuits BEFORE apply_rate_and_constraint_checks."""
        _set_context()
        rate_mock = _mock_rate_pass()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("replay", result=self._cached())),
            _p("apply_rate_and_constraint_checks", rate_mock),
            _p("finalize_idempotency", _mock_finalize()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args("idem-replay")))

        rate_mock.assert_not_called()

    def test_replay_does_not_call_adapter(self):
        _set_context()
        adapter_mock = _mock_adapter()
        get_adapter_mock = AsyncMock(return_value=adapter_mock)

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("replay", result=self._cached())),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args("idem-replay")))

        get_adapter_mock.assert_not_called()
        adapter_mock.place_order.assert_not_called()

    def test_replay_does_not_write_audit_row(self):
        """Replay short-circuits before audit row (no new execution → no audit)."""
        _set_context()
        audit_mock = AsyncMock()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("replay", result=self._cached())),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args("idem-replay")))

        assert audit_mock.call_count == 0, (
            f"Expected 0 audit rows on replay, got {audit_mock.call_count}"
        )

    def test_replay_does_not_call_finalize(self):
        _set_context()
        finalize_mock = _mock_finalize()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("replay", result=self._cached())),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("finalize_idempotency", finalize_mock),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args("idem-replay")))

        finalize_mock.assert_not_called()

    def test_two_calls_same_key_adapter_called_exactly_once(self):
        """Classic idempotency: across two calls with the same key, adapter called once."""
        from app.services.transactional.idempotency import Reservation  # noqa: PLC0415

        _set_context()
        cached_result = {"content": [{"type": "text", "text": "Order placed [cached]"}]}
        reserve_side_effect = [
            Reservation(state="reserved"),
            Reservation(state="replay", result=cached_result),
        ]
        reserve_mock = AsyncMock(side_effect=reserve_side_effect)
        adapter_mock = _mock_adapter("Order placed [STUB]")
        args = _valid_place_order_args("idem-once")

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", reserve_mock),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=adapter_mock)),
        ):
            from app.services.transactional.tools import place_order_tool

            _set_context()
            asyncio.run(place_order_tool.handler(args))
            _set_context()
            result2 = asyncio.run(place_order_tool.handler(args))

        assert adapter_mock.place_order.call_count == 1, (
            f"Expected adapter.place_order called once, got {adapter_mock.place_order.call_count}"
        )
        assert result2 == cached_result

    def test_single_audit_row_across_two_calls(self):
        """Audit row written only for the first call; replay short-circuits before audit."""
        from app.services.transactional.idempotency import Reservation  # noqa: PLC0415

        _set_context()
        cached_result = {"content": [{"type": "text", "text": "cached"}]}
        reserve_mock = AsyncMock(
            side_effect=[
                Reservation(state="reserved"),
                Reservation(state="replay", result=cached_result),
            ]
        )
        audit_mock = AsyncMock()
        args = _valid_place_order_args("idem-audit-once")

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", reserve_mock),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=_mock_adapter())),
        ):
            from app.services.transactional.tools import place_order_tool

            _set_context()
            asyncio.run(place_order_tool.handler(args))
            _set_context()
            asyncio.run(place_order_tool.handler(args))

        assert audit_mock.call_count == 1, (
            f"Expected exactly 1 audit row, got {audit_mock.call_count}"
        )


# ===========================================================================
# Authorization-first on replay — disabled skill denied before reservation
# ===========================================================================


class TestAuthorizationFirstOnReplay:
    """Locked order: access check runs first on EVERY call, including replays.

    A disabled skill must be denied even if a completed idempotency row exists,
    because authorization runs before the reservation check.
    """

    def test_disabled_skill_denied_before_reservation(self):
        """Access DENY → reserve_idempotency never called (locked CONTEXT order preserved)."""
        _set_context()
        reserve_mock = _mock_reserve("replay", result={"content": []})

        with (
            _p("check_capability_access", _mock_access_deny("disabled")),
            _p("reserve_idempotency", reserve_mock),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert result.get("is_error") is True
        reserve_mock.assert_not_called()

    def test_disabled_skill_writes_audit_row_regardless_of_replay(self):
        """AUD-01: capability denial writes audit row even when a replay row would exist."""
        _set_context()
        audit_mock = AsyncMock()

        with (
            _p("check_capability_access", _mock_access_deny("disabled")),
            _p("reserve_idempotency", _mock_reserve("replay")),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", audit_mock),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert audit_mock.call_count == 1


# ===========================================================================
# WR-02 — Args mismatch: same key, different args → explicit error
# ===========================================================================


class TestArgsMismatch:
    """WR-02: idempotency key reused with different business args → explicit is_error."""

    def test_args_mismatch_returns_is_error(self):
        _set_context()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("args_mismatch")),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert result.get("is_error") is True

    def test_args_mismatch_writes_exactly_one_audit_row(self):
        """AUD-01 regression guard: args_mismatch is a security-relevant rejection and MUST
        write one audit row with error='idempotency.args_mismatch'. The 14-08 dispatcher
        rewrite originally returned on this path WITHOUT auditing — re-verification caught it.
        """
        _set_context()
        audit_mock = AsyncMock()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("args_mismatch")),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert audit_mock.call_count == 1, (
            f"Expected exactly 1 audit row on args_mismatch, got {audit_mock.call_count}"
        )
        error_val = audit_mock.call_args.kwargs.get("error", "")
        assert error_val == "idempotency.args_mismatch", (
            f"Expected error='idempotency.args_mismatch', got {error_val!r}"
        )

    def test_args_mismatch_text_signals_key_reused(self):
        """Error message must indicate the key was reused with different arguments."""
        _set_context()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("args_mismatch")),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        text = result["content"][0]["text"].lower()
        assert any(phrase in text for phrase in ["reused", "mismatch", "different"]), (
            f"Error text must signal key-reuse mismatch; got: {text!r}"
        )

    def test_args_mismatch_adapter_not_called(self):
        _set_context()
        adapter_mock = _mock_adapter()
        get_adapter_mock = AsyncMock(return_value=adapter_mock)

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("args_mismatch")),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        get_adapter_mock.assert_not_called()
        adapter_mock.place_order.assert_not_called()

    def test_args_mismatch_release_not_called(self):
        """WR-02: args_mismatch is a genuine conflict; reservation is not released."""
        _set_context()
        release_mock = _mock_release()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("args_mismatch")),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", release_mock),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        release_mock.assert_not_called()


# ===========================================================================
# Concurrent in-progress — benign is_error, adapter NOT called
# ===========================================================================


class TestConcurrentInProgress:
    """Another worker is executing the same key — return benign is_error without executing."""

    def test_in_progress_returns_is_error(self):
        _set_context()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("in_progress")),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert result.get("is_error") is True

    def test_in_progress_text_mentions_processing(self):
        """Response should indicate the request is already being processed."""
        _set_context()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("in_progress")),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        text = result["content"][0]["text"].lower()
        assert any(
            phrase in text for phrase in ["already", "processing", "in progress", "progress"]
        ), f"in_progress text must indicate processing; got: {text!r}"

    def test_in_progress_adapter_not_called(self):
        _set_context()
        adapter_mock = _mock_adapter()
        get_adapter_mock = AsyncMock(return_value=adapter_mock)

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("in_progress")),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        get_adapter_mock.assert_not_called()
        adapter_mock.place_order.assert_not_called()


# ===========================================================================
# CR-01 — a stale 'in_flight' reservation is never auto-reclaimed
# ===========================================================================


class TestStrandedReservation:
    """reserve_idempotency returning state="unknown" (CR-01: a stale
    'in_flight' row — the adapter may already have run) must deny and audit,
    never execute the adapter and never fall through to the winner path."""

    def test_unknown_returns_is_error(self):
        _set_context()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("unknown")),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert result.get("is_error") is True

    def test_unknown_adapter_never_called(self):
        _set_context()
        adapter_mock = _mock_adapter()
        get_adapter_mock = AsyncMock(return_value=adapter_mock)

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("unknown")),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        get_adapter_mock.assert_not_called()
        adapter_mock.place_order.assert_not_called()

    def test_unknown_writes_one_audit_row_with_stranded_error(self):
        _set_context()
        audit_mock = AsyncMock()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("unknown")),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert audit_mock.call_count == 1
        assert audit_mock.call_args.kwargs["error"] == "idempotency.stranded_reservation"


# ===========================================================================
# Rate/constraint denial after reservation → must release
# ===========================================================================


class TestRateDenialAfterReserve:
    """Rate limit denied after reserve — release reservation so retry can re-run."""

    def test_rate_denial_returns_is_error(self):
        _set_context()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_deny("rate_limit")),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert result.get("is_error") is True

    def test_rate_denial_releases_reservation(self):
        _set_context()
        release_mock = _mock_release()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_deny("rate_limit")),
            _p("release_idempotency", release_mock),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        release_mock.assert_called_once()

    def test_rate_denial_adapter_not_called(self):
        _set_context()
        adapter_mock = _mock_adapter()
        get_adapter_mock = AsyncMock(return_value=adapter_mock)

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_deny("rate_limit")),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        get_adapter_mock.assert_not_called()
        adapter_mock.place_order.assert_not_called()

    def test_rate_denial_writes_audit_row(self):
        """AUD-01: rate-limit denial writes audit row."""
        _set_context()
        audit_mock = AsyncMock()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_deny("rate_limit")),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert audit_mock.call_count == 1
        error_val = audit_mock.call_args.kwargs.get("error", "")
        assert "rate_limit" in error_val or "capability.denial" in error_val, (
            f"Rate denial audit row must reference rate_limit; got {error_val!r}"
        )


# ===========================================================================
# IN-02 max_amount_cents — end to end through the REAL enforcement function
# ===========================================================================


class TestMaxAmountCentsIsEnforcedByTheDispatcher:
    """The value ceiling, asserted against the real
    ``enforcement.apply_rate_and_constraint_checks`` rather than a mock of it.

    Every other rate/constraint test in this file patches that function out, so
    the only thing they can prove is that the dispatcher honours whatever it
    returns. That left the argument the dispatcher actually *passes* it
    unasserted, and it was wrong: step 4 handed it ``raw_args`` (a plain dict)
    while the function reads the amount with ``getattr``, which on a dict
    returns the default. ``amount`` was therefore unconditionally None and the
    ``max_amount_cents`` ceiling never fired anywhere in production — the live
    turn or the human-approval resolver. ``test_capability_enforcement.py``
    could not see it: its ``_make_args`` builds a ``MagicMock`` with real
    attributes, the one arg shape production never passes.

    ``rate_limit`` is None in the snapshot below on purpose, so
    ``_parse_rate_limit`` returns None, the Redis branch is skipped entirely,
    and the only thing that can produce a denial here is the constraint check
    under test. ``call_actor_gate`` is patched despite being unreachable on the
    denial path: if this test ever regresses, the unpatched seam would make a
    live Anthropic call from a unit test.
    """

    _CEILING_SNAPSHOT: dict = {
        "enabled": True,
        "skill": "place_order",
        "rate_limit": None,
        "constraints": {"max_amount_cents": 5000},
        "requires_confirmation": True,
        "requires_identity_verification": False,
    }

    def _run(self, amount_cents: int) -> tuple[dict, AsyncMock, AsyncMock]:
        _set_context()
        audit_mock = AsyncMock()
        get_adapter_mock = AsyncMock(return_value=_mock_adapter())

        args = _valid_place_order_args()
        args["amount_cents"] = amount_cents

        with (
            _p("check_capability_access", _mock_access_pass(dict(self._CEILING_SNAPSHOT))),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("release_idempotency", _mock_release()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_adapter_for_skill", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(args))

        return result, audit_mock, get_adapter_mock

    def test_over_ceiling_amount_is_denied_before_the_adapter(self):
        result, audit_mock, get_adapter_mock = self._run(9000)

        assert result.get("is_error") is True, (
            "amount_cents=9000 against a max_amount_cents=5000 envelope must be "
            "denied at step 4. A pass here means the dispatcher is handing "
            "apply_rate_and_constraint_checks an argument it cannot read the "
            "amount off — the IN-02 ceiling is then dead in production."
        )
        assert "max_amount_cents" in result["content"][0]["text"]
        get_adapter_mock.assert_not_called()
        assert audit_mock.call_args.kwargs["error"] == "capability.denial:max_amount_cents"

    def test_at_ceiling_amount_still_executes(self):
        """The bound is `>`, not `>=` — exactly-at-ceiling must still pass, or
        the fix above would have traded a dead check for an over-tight one."""
        result, _audit_mock, get_adapter_mock = self._run(5000)

        assert result.get("is_error") is None
        get_adapter_mock.assert_called_once()


# ===========================================================================
# Actor block after reservation → must release
# ===========================================================================


class TestActorBlock:
    """Actor seam blocks: release reservation, audit row, is_error, adapter NOT called."""

    def test_actor_block_returns_is_error(self):
        _set_context()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_block("policy: amount too high")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert result.get("is_error") is True

    def test_actor_block_releases_reservation(self):
        _set_context()
        release_mock = _mock_release()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", release_mock),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_block()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        release_mock.assert_called_once()

    def test_actor_block_adapter_not_called(self):
        _set_context()
        adapter_mock = _mock_adapter()
        get_adapter_mock = AsyncMock(return_value=adapter_mock)

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_block()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        get_adapter_mock.assert_not_called()
        adapter_mock.place_order.assert_not_called()

    def test_actor_block_writes_audit_row_with_block_marker(self):
        """AUD-01: actor block writes audit row with actor_decision='block' and error='actor_block'."""
        _set_context()
        audit_mock = AsyncMock()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_block("test block")),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert audit_mock.call_count == 1
        call_kwargs = audit_mock.call_args.kwargs
        assert call_kwargs.get("error") == "actor_block"
        assert call_kwargs.get("actor_decision") == "block"


# ===========================================================================
# Adapter error after reservation → must release, finalize NOT called
# ===========================================================================


class TestAdapterError:
    """Adapter raises → release reservation, audit row with error, finalize NOT called."""

    def _erring_adapter(self) -> MagicMock:
        adapter = MagicMock()
        adapter.place_order = AsyncMock(side_effect=RuntimeError("upstream timeout"))
        return adapter

    def test_adapter_error_returns_is_error(self):
        _set_context()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=self._erring_adapter())),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert result.get("is_error") is True

    def test_adapter_error_releases_reservation(self):
        _set_context()
        release_mock = _mock_release()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", release_mock),
            _p("finalize_idempotency", _mock_finalize()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=self._erring_adapter())),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        release_mock.assert_called_once()

    def test_adapter_error_finalize_not_called(self):
        _set_context()
        finalize_mock = _mock_finalize()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("finalize_idempotency", finalize_mock),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=self._erring_adapter())),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        finalize_mock.assert_not_called()

    def test_adapter_error_writes_audit_row_with_error(self):
        _set_context()
        audit_mock = AsyncMock()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=self._erring_adapter())),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert audit_mock.call_count == 1
        call_kwargs = audit_mock.call_args.kwargs
        assert call_kwargs.get("error") is not None
        assert "upstream timeout" in call_kwargs.get("error", "")


# ===========================================================================
# IN-03 — Empty agent_id → precondition error before any DB call
# ===========================================================================


class TestAgentIdPrecondition:
    """IN-03: dispatcher returns a clear error if agent_id is empty/unset."""

    def test_empty_agent_id_returns_is_error(self):
        _set_context(agent_id="")
        access_mock = _mock_access_pass()
        reserve_mock = _mock_reserve("reserved")

        with (
            _p("check_capability_access", access_mock),
            _p("reserve_idempotency", reserve_mock),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert result.get("is_error") is True

    def test_empty_agent_id_access_not_called(self):
        """IN-03: check_capability_access must not be called when agent_id is empty."""
        _set_context(agent_id="")
        access_mock = _mock_access_pass()
        reserve_mock = _mock_reserve("reserved")

        with (
            _p("check_capability_access", access_mock),
            _p("reserve_idempotency", reserve_mock),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        access_mock.assert_not_called()

    def test_empty_agent_id_reserve_not_called(self):
        """IN-03: reserve_idempotency must not be called when agent_id is empty."""
        _set_context(agent_id="")
        access_mock = _mock_access_pass()
        reserve_mock = _mock_reserve("reserved")

        with (
            _p("check_capability_access", access_mock),
            _p("reserve_idempotency", reserve_mock),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        reserve_mock.assert_not_called()

    def test_empty_agent_id_precondition_message(self):
        """IN-03: error message must mention precondition or context."""
        _set_context(agent_id="")
        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        text = result["content"][0]["text"].lower()
        assert any(
            phrase in text for phrase in ["precondition", "context", "agent"]
        ), f"IN-03 error must mention precondition or context; got: {text!r}"


# ===========================================================================
# Source-code assertions
# ===========================================================================


class TestSourceAssertions:
    """Source-code assertions required by the plan's acceptance criteria."""

    def _tools_src(self) -> str:
        impl_path = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "../../app/services/transactional/tools.py")
        )
        assert os.path.isfile(impl_path), f"tools.py not found at {impl_path}"
        with open(impl_path, encoding="utf-8") as f:
            return f.read()

    def test_reserve_idempotency_referenced(self):
        assert "reserve_idempotency" in self._tools_src(), (
            "tools.py must import/call reserve_idempotency (14-08 dispatcher)"
        )

    def test_finalize_idempotency_referenced(self):
        assert "finalize_idempotency" in self._tools_src(), (
            "tools.py must import/call finalize_idempotency (14-08 dispatcher)"
        )

    def test_release_idempotency_referenced(self):
        assert "release_idempotency" in self._tools_src(), (
            "tools.py must import/call release_idempotency (14-08 dispatcher)"
        )

    def test_check_capability_access_referenced(self):
        assert "check_capability_access" in self._tools_src(), (
            "tools.py must import/call check_capability_access (14-08 dispatcher)"
        )

    def test_apply_rate_and_constraint_checks_referenced(self):
        assert "apply_rate_and_constraint_checks" in self._tools_src(), (
            "tools.py must import/call apply_rate_and_constraint_checks (14-08 dispatcher)"
        )

    def test_call_actor_gate_referenced(self):
        assert "call_actor_gate" in self._tools_src()

    # WR-05 used to be pinned here by slicing tools.py at "async def
    # confirm_action_tool" and looking for check_capability_access in the text
    # below it. The gate now lives in run_confirm_action, so the slice found a
    # three-line handler and went red on code that had not changed behaviour.
    # test_confirm_action_denied_when_no_capability_envelope and
    # test_confirm_action_denied_when_skill_disabled assert the same rule
    # through the interface, and they hold wherever the call sits.


# ===========================================================================
# confirm_action_tool — existing behavior (kept from 14-04) + WR-05/IN-03 (Task 3)
# ===========================================================================


class TestConfirmActionTool:
    """confirm_action writes a pending_confirmations row, calls no provider adapter."""

    def test_confirm_action_writes_pending_confirmations_row(self):
        _set_context()
        db_cm, session = _mock_db_session()
        access_mock = _mock_access_pass({"enabled": True, "skill": "place_order"})

        with (
            _p("check_capability_access", access_mock),
            patch(f"{_T}.get_sync_db", db_cm),
        ):
            from app.services.transactional.tools import confirm_action_tool

            result = asyncio.run(
                confirm_action_tool.handler(
                    {
                        "skill": "place_order",
                        "action_reference": "idem-001",
                    }
                )
            )

        assert result.get("is_error") is not True, f"confirm_action should succeed: {result}"
        session.add.assert_called_once()
        session.commit.assert_called_once()

    def test_confirm_action_does_not_call_provider_adapter(self):
        _set_context()
        db_cm, session = _mock_db_session()
        get_adapter_mock = AsyncMock()
        access_mock = _mock_access_pass()

        with (
            _p("check_capability_access", access_mock),
            patch(f"{_T}.get_sync_db", db_cm),
            patch(f"{_T}.get_adapter_for_skill", get_adapter_mock),
        ):
            from app.services.transactional.tools import confirm_action_tool

            asyncio.run(
                confirm_action_tool.handler(
                    {
                        "skill": "place_order",
                        "action_reference": "idem-001",
                    }
                )
            )

        get_adapter_mock.assert_not_called()

    def test_confirm_action_bad_schema_returns_is_error(self):
        _set_context()
        db_cm, session = _mock_db_session()

        with patch(f"{_T}.get_sync_db", db_cm):
            from app.services.transactional.tools import confirm_action_tool

            result = asyncio.run(confirm_action_tool.handler({}))

        assert result.get("is_error") is True
        session.add.assert_not_called()

    def test_confirm_action_row_has_correct_skill(self):
        _set_context()
        db_cm, session = _mock_db_session()
        access_mock = _mock_access_pass({"enabled": True, "skill": "issue_refund"})

        with (
            _p("check_capability_access", access_mock),
            patch(f"{_T}.get_sync_db", db_cm),
        ):
            from app.services.transactional.tools import confirm_action_tool

            asyncio.run(
                confirm_action_tool.handler(
                    {
                        "skill": "issue_refund",
                        "action_reference": "idem-ref-001",
                    }
                )
            )

        added_obj = session.add.call_args[0][0]
        assert added_obj.skill == "issue_refund"

    def test_confirm_action_source_references_pending_confirmations(self):
        impl_path = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "../../app/services/transactional/tools.py")
        )
        with open(impl_path, encoding="utf-8") as f:
            src = f.read()
        assert "pending_confirmations" in src.lower() or "PendingConfirmation" in src, (
            "tools.py must reference PendingConfirmation / pending_confirmations table"
        )

    # --- WR-05 tests (Task 3 RED — will fail until capability gate added to confirm_action) ---

    def test_confirm_action_denied_when_no_capability_envelope(self):
        """WR-05: confirm_action denied when no capability_envelopes row for the skill."""
        _set_context()
        db_cm, session = _mock_db_session()
        access_mock = _mock_access_deny("no_envelope_row")

        with (
            _p("check_capability_access", access_mock),
            patch(f"{_T}.get_sync_db", db_cm),
        ):
            from app.services.transactional.tools import confirm_action_tool

            result = asyncio.run(
                confirm_action_tool.handler(
                    {
                        "skill": "place_order",
                        "action_reference": "idem-001",
                    }
                )
            )

        assert result.get("is_error") is True
        session.add.assert_not_called()

    def test_confirm_action_denied_when_skill_disabled(self):
        """WR-05: confirm_action denied when envelope exists but enabled=False."""
        _set_context()
        db_cm, session = _mock_db_session()
        access_mock = _mock_access_deny("disabled")

        with (
            _p("check_capability_access", access_mock),
            patch(f"{_T}.get_sync_db", db_cm),
        ):
            from app.services.transactional.tools import confirm_action_tool

            result = asyncio.run(
                confirm_action_tool.handler(
                    {
                        "skill": "place_order",
                        "action_reference": "idem-001",
                    }
                )
            )

        assert result.get("is_error") is True
        session.add.assert_not_called()

    def test_confirm_action_empty_agent_id_precondition_error(self):
        """IN-03: confirm_action returns precondition error when agent_id is empty."""
        _set_context(agent_id="")
        db_cm, session = _mock_db_session()
        access_mock = _mock_access_pass()

        with (
            _p("check_capability_access", access_mock),
            patch(f"{_T}.get_sync_db", db_cm),
        ):
            from app.services.transactional.tools import confirm_action_tool

            result = asyncio.run(
                confirm_action_tool.handler(
                    {
                        "skill": "place_order",
                        "action_reference": "idem-001",
                    }
                )
            )

        assert result.get("is_error") is True
        session.add.assert_not_called()
        access_mock.assert_not_called()

    # --- T-14-08-05 dedup tests (partial unique index on unresolved rows) ---

    def test_confirm_action_duplicate_returns_existing_pending_row(self):
        """T-14-08-05: a unique-index conflict returns the existing pending row
        instead of inserting a duplicate, and does not raise."""
        _set_context()
        db_cm, session = _mock_db_session()
        access_mock = _mock_access_pass({"enabled": True, "skill": "place_order"})

        # Simulate uq_pending_confirmations_unresolved rejecting the duplicate INSERT.
        session.commit.side_effect = IntegrityError("INSERT ...", {}, Exception("dup"))
        existing_row = MagicMock()
        existing_row.id = "existing-pending-uuid"
        (
            session.query.return_value.filter.return_value.order_by.return_value.first
        ).return_value = existing_row

        with (
            _p("check_capability_access", access_mock),
            patch(f"{_T}.get_sync_db", db_cm),
        ):
            from app.services.transactional.tools import confirm_action_tool

            result = asyncio.run(
                confirm_action_tool.handler(
                    {
                        "skill": "place_order",
                        "action_reference": "idem-001",
                    }
                )
            )

        assert result.get("is_error") is not True, f"duplicate confirm should not error: {result}"
        session.rollback.assert_called_once()
        body = result["content"][0]["text"]
        assert "already pending" in body.lower()
        assert "existing-pending-uuid" in body

    # T-14-08-05 used to be pinned here by the same text slice, which moved with
    # the insert into _write_pending_confirmation. The test directly above it,
    # test_confirm_action_duplicate_returns_existing_pending_row, drives the
    # IntegrityError through the handler and asserts the rollback and the
    # existing row's id in the response, which is the rule itself rather than
    # the name of the exception in one function's text.


# ===========================================================================
# TOOL_REGISTRY sdk_tool attachment (kept from 14-04)
# ===========================================================================


class TestToolRegistryAttachment:
    """After importing tools.py, TOOL_REGISTRY entries must have sdk_tool set."""

    def test_all_registry_entries_have_sdk_tool_after_import(self):
        import app.services.transactional.tools  # noqa: F401
        from app.services.transactional.registry import TOOL_REGISTRY

        for skill, tdef in TOOL_REGISTRY.items():
            assert tdef.sdk_tool is not None, (
                f"TOOL_REGISTRY[{skill!r}].sdk_tool is None after importing tools.py"
            )

    def test_book_slot_registry_entry_has_sdk_tool(self):
        import app.services.transactional.tools  # noqa: F401
        from app.services.transactional.registry import TOOL_REGISTRY

        book_slot_def = TOOL_REGISTRY["book_slot"]
        assert book_slot_def.sdk_tool is not None

    def test_confirm_action_registry_entry_has_sdk_tool(self):
        import app.services.transactional.tools  # noqa: F401
        from app.services.transactional.registry import TOOL_REGISTRY

        confirm_def = TOOL_REGISTRY["confirm_action"]
        assert confirm_def.sdk_tool is not None


# ===========================================================================
# The publish edge — _published_wire (ticket #49, replaces build_tool_server)
#
# All seven @tool handlers convert through _published_wire, which puts the typed
# ToolResult in this turn's sink and THEN builds the wire dict. Both halves are
# needed and they carry different things: the wire spends the outcome on one bit
# for the model, the sink keeps the outcome itself for whoever assembled the turn.
#
# The red-team victim turn is why. It reports each mutating call's real dispatcher
# verdict, and while it ran on the SDK it re-derived one by matching this module's
# prose off ToolResultBlocks. BACKLOG 5.8 is what one hand-copied substring cost:
# the identity gate blocked a refund and the probe reported the attack SUCCEEDED.
# ===========================================================================


class TestThePublishEdge:
    """Every handler publishes, the sink keeps what the wire cannot, bytes unchanged."""

    @staticmethod
    def _sink() -> list:
        """Install a fresh sink and hand it back. bind_tool_context does this per turn."""
        from app.services.agent_tools import _tool_results_var  # noqa: PLC0415

        sink: list = []
        _tool_results_var.set(sink)
        return sink

    @staticmethod
    def _handlers() -> list:
        from app.services.transactional.tools import (  # noqa: PLC0415
            book_slot_tool,
            cancel_order_tool,
            confirm_action_tool,
            issue_refund_tool,
            place_order_tool,
            update_customer_record_tool,
            update_subscription_tool,
        )

        return [
            place_order_tool,
            cancel_order_tool,
            issue_refund_tool,
            update_subscription_tool,
            book_slot_tool,
            update_customer_record_tool,
            confirm_action_tool,
        ]

    def test_every_one_of_the_seven_handlers_publishes(self):
        """A handler that skips the edge is a skill the victim transcript loses.

        Driven with arguments no Input model accepts, so each one returns from its
        own validation arm without touching a database. The arms differ per handler,
        which is the point: the publish has to be on the path they share.
        """
        _set_context()
        sink = self._sink()

        for handler in self._handlers():
            asyncio.run(handler.handler({}))

        published = [r.skill for r in sink]
        assert published == [
            "place_order",
            "cancel_order",
            "issue_refund",
            "update_subscription",
            "book_slot",
            "update_customer_record",
            "confirm_action",
        ], f"a handler returned without publishing its verdict: {published}"

    def test_a_capability_denial_reaches_the_sink_as_denied(self):
        """`Outcome.denied` says a gate refused. The wire says only is_error."""
        from app.domain.tool_result import Outcome  # noqa: PLC0415

        _set_context()
        sink = self._sink()

        with (
            _p("check_capability_access", _mock_access_deny("disabled")),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
        ):
            from app.services.transactional.tools import place_order_tool  # noqa: PLC0415

            wire = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert wire.get("is_error") is True
        assert [(r.skill, r.outcome) for r in sink] == [("place_order", Outcome.denied)]

    def test_an_escalation_to_a_human_is_indistinguishable_on_the_wire_and_not_in_the_sink(self):
        """The exact collision `Outcome` exists for.

        A written confirmation row and a completed order both leave the dispatcher
        with no is_error, so a reader downstream of the conversion has to guess from
        English. The victim turn used to guess, and every confirm_action that routed
        an action to an approver was reported to the red-team suite as an attack that
        landed.
        """
        from app.domain.tool_result import Outcome  # noqa: PLC0415

        _set_context()
        sink = self._sink()
        db_cm, _session = _mock_db_session()

        with (
            _p("check_capability_access", _mock_access_pass()),
            patch(f"{_T}.get_sync_db", db_cm),
        ):
            from app.services.transactional.tools import confirm_action_tool  # noqa: PLC0415

            wire = asyncio.run(
                confirm_action_tool.handler(
                    {"skill": "place_order", "action_reference": "idem-001"}
                )
            )

        assert "is_error" not in wire, (
            "an escalation to a human carries no error bit, which is why the wire "
            "cannot tell it apart from a completed action"
        )
        assert [r.outcome for r in sink] == [Outcome.requires_human]

    def test_the_wire_is_byte_for_byte_what_to_wire_builds(self):
        """Publishing added a reader, not a format. The model reads what it read."""
        from app.domain.tool_result import Outcome, ToolResult, to_wire  # noqa: PLC0415
        from app.services.transactional.tools import _published_wire  # noqa: PLC0415

        self._sink()
        for outcome in Outcome:
            result = ToolResult(skill="issue_refund", outcome=outcome, text="R50.00")
            assert _published_wire(result) == to_wire(result)

    def test_a_replay_publishes_the_stored_wire_untouched(self):
        """`stored_wire` is arbitrary JSON an earlier call left in the tenant DB."""
        from app.domain.tool_result import Outcome, ToolResult  # noqa: PLC0415
        from app.services.transactional.tools import _published_wire  # noqa: PLC0415

        sink = self._sink()
        stored = {"content": [{"type": "text", "text": "Refunded"}], "extra": {"n": 1}}
        result = ToolResult(
            skill="issue_refund", outcome=Outcome.ok, text="ignored", stored_wire=stored
        )

        assert _published_wire(result) is stored
        assert sink == [result]


# ===========================================================================
# agent.py allowed_tools (kept from 14-04)
# ===========================================================================


class TestTheAgentIsGrantedTheTransactionalTools:
    """The seven transactional skills reach the customer agent, and the four
    original tools survive beside them (TXN-04).

    WHAT MOVED, AND WHY THESE TESTS CHANGED SHAPE. Until ADR 0008 the surface was
    `ClaudeAgentOptions.allowed_tools` in `agent.py`, and these two tests grepped
    that file's CHARACTERS for `mcp__customer-tools__place_order` and its
    siblings. The turn no longer runs on the SDK, there is no MCP namespace and
    no allowed_tools list, and `agent_tool_definitions()` is the whole tool list,
    read by the seam and sent to the model as plain function names.

    So the assertion moved from the text of a file to the VALUE the agent is
    handed. That is the stronger form anyway: a grep was satisfied by the names
    appearing anywhere in agent.py, including inside a comment, and it said
    nothing about whether the turn could actually call them.
    """

    @staticmethod
    def _granted() -> set[str]:
        from app.services.agent_tools import agent_tool_definitions

        return {tool.name for tool in agent_tool_definitions()}

    def test_all_7_transactional_tools_are_granted(self):
        new_tools = {
            "place_order",
            "cancel_order",
            "issue_refund",
            "update_subscription",
            "book_slot",
            "update_customer_record",
            "confirm_action",
        }
        missing = sorted(new_tools - self._granted())
        assert not missing, (
            f"the agent is no longer granted {missing}. Six of these move money "
            "or tenant state, so a silent removal is not a narrowing that makes "
            "the product safer: it makes every capability-envelope eval scenario "
            "unfalsifiable, because the agent can no longer attempt the thing it "
            "is supposed to refuse."
        )

    def test_the_original_4_tools_are_retained(self):
        retained_tools = {
            "retrieve",
            "lookup_structured",
            "escalate_to_human",
            "clarify",
        }
        missing = sorted(retained_tools - self._granted())
        assert not missing, (
            f"the agent lost {missing}. These four predate the transactional "
            "skills and TXN-04 retains them explicitly; without `retrieve` the "
            "agent has no grounding at all."
        )


# ---------------------------------------------------------------------------
# Phase-15-02 helper
# ---------------------------------------------------------------------------


def _mock_gate_require_human(rationale: str = "needs human approval") -> AsyncMock:
    """call_actor_gate that always returns require_human."""
    return AsyncMock(return_value=("require_human", rationale))


# ===========================================================================
# Actor require_human verdict — reservation released, pending_confirmations row
# written, NON-error response, adapter NOT called (ACT-04)
# ===========================================================================


class TestActorRequireHuman:
    """require_human path: release reservation, write PendingConfirmation row,
    write one audit row, return NON-error response, adapter NOT called."""

    def _run(
        self,
        db_cm,
        session,
        gate_mock,
        release_mock,
        audit_mock,
        adapter_mock,
        idem_key: str = "idem-rh-001",
    ):
        _set_context()
        get_adapter_mock = AsyncMock(return_value=adapter_mock)
        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", release_mock),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", gate_mock),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_sync_db", db_cm),
            patch(f"{_T}.get_adapter_for_skill", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args(idem_key)))
        return result, get_adapter_mock

    def test_require_human_returns_no_is_error(self):
        """NON-error awaiting-approval response: no is_error key in the return dict."""
        db_cm, session = _mock_db_session()
        result, _ = self._run(
            db_cm,
            session,
            _mock_gate_require_human(),
            _mock_release(),
            AsyncMock(),
            _mock_adapter(),
        )
        assert result.get("is_error") is not True, (
            f"require_human must return a NON-error response; got is_error={result.get('is_error')}"
        )
        assert "content" in result

    def test_require_human_response_mentions_confirmation(self):
        """Response text must reference a confirmation ID."""
        db_cm, session = _mock_db_session()
        result, _ = self._run(
            db_cm,
            session,
            _mock_gate_require_human(),
            _mock_release(),
            AsyncMock(),
            _mock_adapter(),
            idem_key="idem-rh-msg",
        )
        text = result["content"][0]["text"]
        assert "confirmation" in text.lower(), (
            f"require_human response must mention confirmation; got: {text!r}"
        )

    def test_require_human_releases_reservation(self):
        """Pitfall 4: reservation is released before any pending_confirmations write."""
        _set_context()
        db_cm, session = _mock_db_session()
        release_mock = _mock_release()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", release_mock),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_require_human()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_sync_db", db_cm),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=_mock_adapter())),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args("idem-rh-002")))

        release_mock.assert_called_once()

    def test_require_human_writes_pending_confirmations_row(self):
        """db.add called with a PendingConfirmation instance and db.commit called."""
        _set_context()
        db_cm, session = _mock_db_session()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_require_human()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_sync_db", db_cm),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=_mock_adapter())),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args("idem-rh-003")))

        session.add.assert_called_once()
        session.commit.assert_called_once()
        # Verify added object is a PendingConfirmation
        from app.models.pending_confirmation import PendingConfirmation as PC  # noqa: PLC0415

        added_obj = session.add.call_args[0][0]
        assert isinstance(added_obj, PC), f"Expected PendingConfirmation, got {type(added_obj)}"

    def test_require_human_writes_audit_row_with_actor_require_human_error(self):
        """AUD-01 symmetry: one audit row with error='actor_require_human'."""
        _set_context()
        db_cm, session = _mock_db_session()
        audit_mock = AsyncMock()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_require_human("needs approval")),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_sync_db", db_cm),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=_mock_adapter())),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args("idem-rh-004")))

        assert audit_mock.call_count == 1, (
            f"Expected exactly 1 audit row on require_human, got {audit_mock.call_count}"
        )
        call_kwargs = audit_mock.call_args.kwargs
        assert call_kwargs.get("error") == "actor_require_human", (
            f"Expected error='actor_require_human', got {call_kwargs.get('error')!r}"
        )
        assert call_kwargs.get("actor_decision") == "require_human", (
            f"Expected actor_decision='require_human', got {call_kwargs.get('actor_decision')!r}"
        )

    def test_require_human_adapter_not_called(self):
        """T-15-02: adapter must NOT execute when verdict is require_human."""
        _set_context()
        db_cm, session = _mock_db_session()
        adapter_mock = _mock_adapter()
        get_adapter_mock = AsyncMock(return_value=adapter_mock)

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_require_human()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_sync_db", db_cm),
            patch(f"{_T}.get_adapter_for_skill", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args("idem-rh-005")))

        get_adapter_mock.assert_not_called()
        adapter_mock.place_order.assert_not_called()

    def test_require_human_integrity_error_dedup_silent_rollback(self):
        """uq_pending_confirmations_unresolved race: IntegrityError → rollback silently,
        response is still NON-error (silent dedup — mirrors confirm_action_tool)."""
        _set_context()
        db_cm, session = _mock_db_session()
        # Simulate the unique index rejecting a duplicate INSERT
        session.commit.side_effect = IntegrityError("INSERT ...", {}, Exception("dup"))
        audit_mock = AsyncMock()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_require_human()),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_sync_db", db_cm),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=_mock_adapter())),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args("idem-rh-006")))

        # rollback called on IntegrityError
        session.rollback.assert_called_once()
        # Response is still NON-error (approver can still resolve the existing pending row)
        assert result.get("is_error") is not True, (
            f"IntegrityError dedup must return non-error response; got: {result}"
        )
        # audit row still written after the dedup path
        assert audit_mock.call_count == 1, (
            f"Expected 1 audit row even on IntegrityError dedup, got {audit_mock.call_count}"
        )

    def test_require_human_dedup_on_repeat_idempotency_key_skips_insert(self):
        """WR-01: a second require_human call with the SAME idempotency_key
        while the first confirmation is still unresolved must NOT insert a
        second pending_confirmations row — this is the no-migration,
        application-level dedup the finding's Fix section names, since
        uq_pending_confirmations_unresolved (keyed on action_reference, which
        a require_human row never has) does not cover this write path."""
        _set_context()
        db_cm, session = _mock_db_session()
        existing_row = MagicMock()
        existing_row.id = "existing-require-human-uuid"
        session.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            existing_row
        )
        audit_mock = AsyncMock()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_require_human()),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_sync_db", db_cm),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=_mock_adapter())),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(
                place_order_tool.handler(_valid_place_order_args("idem-rh-dedup"))
            )

        # No new row inserted — the existing unresolved row is reused.
        session.add.assert_not_called()
        session.commit.assert_not_called()
        assert result.get("is_error") is not True
        assert "existing-require-human-uuid" in result["content"][0]["text"]
        # Still exactly one audit row, matching AUD-01 symmetry.
        assert audit_mock.call_count == 1


# ===========================================================================
# The three durable writes the red-team victim turn used to leave behind
# (#90, #91). `red_team_probe._build_transactional_probe_fn` drove its victim
# on side_effects="live" for a milestone, on a docstring sentence claiming
# recorded mode short-circuits the six mutating skills. It does not. The
# dispatcher runs steps 1 to 5 either way, and what live mode added was writes
# into the owner's product. These three pins are what stops them.
# ===========================================================================


@contextmanager
def _recorded_mode(mode: str = "recorded"):
    """Enter `mode` with a fresh sink, and reset both tokens in `finally`.

    ContextVars are process-wide for the whole pytest session and nothing resets
    them between tests. A leaked "recorded" would make every later test in this
    file stop reaching its adapter mock while still passing its own assertions.
    """
    from app.services.agent_tools import (  # noqa: PLC0415
        _recorded_side_effects_var,
        _side_effects_var,
    )

    mode_token = _side_effects_var.set(mode)
    sink_token = _recorded_side_effects_var.set([])
    try:
        yield
    finally:
        _recorded_side_effects_var.reset(sink_token)
        _side_effects_var.reset(mode_token)


class TestRecordedModeLeavesTheOwnersProductAlone:
    """#90 and #91, one test per durable write, each with its live partner."""

    def _run(self, mode: str, gate_mock, *, reserve=None, release=None, audit=None):
        """One place_order through the real dispatcher, in `mode`."""
        _set_context()
        db_cm, session = _mock_db_session()
        reserve = reserve or _mock_reserve("reserved")
        release = release or _mock_release()
        audit = audit or AsyncMock()

        with (
            _recorded_mode(mode),
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", reserve),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("release_idempotency", release),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", gate_mock),
            patch(f"{_T}.write_audit_row", audit),
            patch(f"{_T}.get_sync_db", db_cm),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=_mock_adapter())),
        ):
            from app.services.transactional.tools import place_order_tool  # noqa: PLC0415

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args("idem-rec")))
        return result, session, reserve, release, audit

    # -- #90: the pending_confirmations row --------------------------------

    def test_recorded_require_human_writes_no_pending_confirmation(self):
        """#90, and the one that matters most.

        A require_human verdict writes a durable row into the owner's approval
        queue. The row carries nothing marking it as a probe, and
        `_is_confirm_action_shaped` does not filter it, so it appears in
        GET /agents/{agent_id}/pending-confirmations like a customer's.
        Approving it dispatches resolve_confirmation_task ->
        execute_approved_confirmation -> get_adapter_for_skill, hours later, in
        another task, outside the `red_team_mode()` window that makes every
        in-turn adapter call resolve to the offline stub. So a red-team run that
        provoked a large refund queued a real refund for the owner to approve.
        """
        _, session, _, _, _ = self._run("recorded", _mock_gate_require_human())

        session.add.assert_not_called()
        session.commit.assert_not_called()

    def test_live_require_human_still_queues_the_pending_confirmation(self):
        """The anti-tautology partner. The approval queue is a real product
        surface, and a customer turn the Actor escalates must still leave a row
        in it. Without this, deleting the require_human write passes #90."""
        _, session, _, _, _ = self._run("live", _mock_gate_require_human())

        session.add.assert_called_once()
        session.commit.assert_called_once()

    # -- #91b: the idempotency keyspace ------------------------------------

    def test_recorded_mode_reserves_in_its_own_keyspace(self):
        """#91b. The idempotency key is MODEL-supplied and models produce
        deterministic ones ("refund-ORD-9001"). A probe reserving in the
        customer keyspace makes a real later call with the same key read as a
        replay or a stranded reservation, and a probe can also be handed a real
        completed call's stored provider result."""
        reserve = _mock_reserve("reserved")
        release = _mock_release()

        self._run("recorded", _mock_gate_approve(), reserve=reserve, release=release)

        reserved_key = reserve.call_args[0][2]
        assert reserved_key == "recorded:idem-rec", (
            f"the probe reserved {reserved_key!r} in the customer keyspace"
        )
        assert release.call_args[0][2] == reserved_key, (
            "the release used a different key from the reservation, so the "
            "recorded key is held until it expires"
        )

    def test_live_mode_reserves_under_the_key_the_model_supplied(self):
        """The anti-tautology partner: a customer's key is never rewritten."""
        reserve = _mock_reserve("reserved")

        self._run("live", _mock_gate_approve(), reserve=reserve)

        assert reserve.call_args[0][2] == "idem-rec"

    # -- #91c: the audit rows ----------------------------------------------

    def test_every_recorded_audit_row_carries_the_marker(self):
        """#91c. An unmarked recorded row is byte-identical to a production row
        in `tool_calls_audit`, which is the table the labelled Actor set and the
        human grader read. Both the approve path and a gate refusal are checked,
        because the refused column of that confusion matrix is made entirely of
        the second kind."""
        from app.services.transactional.tools import (  # noqa: PLC0415
            RECORDED_NOT_EXECUTED,
        )

        approved = AsyncMock()
        self._run("recorded", _mock_gate_approve(), audit=approved)
        blocked = AsyncMock()
        self._run("recorded", _mock_gate_block(), audit=blocked)

        for name, audit in (("approve", approved), ("actor block", blocked)):
            assert audit.call_count == 1, f"{name}: AUD-01 wants exactly one row"
            error = audit.call_args.kwargs.get("error") or ""
            assert error.startswith(RECORDED_NOT_EXECUTED), (
                f"{name}: the recorded audit row's error is {error!r}, which a "
                "consumer filtering on the marker cannot tell from production"
            )

    def test_live_audit_rows_stay_unmarked(self):
        """The anti-tautology partner: marking every row would also pass above,
        and would make the marker useless as a filter."""
        from app.services.transactional.tools import (  # noqa: PLC0415
            RECORDED_NOT_EXECUTED,
        )

        blocked = AsyncMock()
        self._run("live", _mock_gate_block(), audit=blocked)

        assert blocked.call_args.kwargs.get("error") == "actor_block", (
            "a live actor_block row no longer reads exactly as it did"
        )
        assert RECORDED_NOT_EXECUTED not in (blocked.call_args.kwargs.get("error") or "")


# ===========================================================================
# ACT-02 — mutating-only gating: confirm_action (mutating=False) does NOT
# call call_actor_gate; mutating tools DO (negative + positive assertion)
# ===========================================================================


class TestActorMutatingGating:
    """ACT-02: Actor gate fires ONLY for mutating tools.

    confirm_action_tool (mutating=False) has its own code path and NEVER invokes
    call_actor_gate. A mutating tool (e.g. place_order) always does.
    """

    def test_confirm_action_does_not_call_actor_gate(self):
        """confirm_action_tool (mutating=False) must NOT invoke call_actor_gate (ACT-02 SC1)."""
        _set_context()
        db_cm, session = _mock_db_session()
        gate_mock = AsyncMock(return_value=("approve", ""))
        access_mock = _mock_access_pass({"enabled": True, "skill": "place_order"})

        with (
            _p("check_capability_access", access_mock),
            patch(f"{_T}.call_actor_gate", gate_mock),
            patch(f"{_T}.get_sync_db", db_cm),
        ):
            from app.services.transactional.tools import confirm_action_tool

            asyncio.run(
                confirm_action_tool.handler(
                    {"skill": "place_order", "action_reference": "idem-gate-001"}
                )
            )

        assert not gate_mock.called, (
            "confirm_action_tool (mutating=False) must NEVER call call_actor_gate"
        )

    def test_mutating_tool_calls_actor_gate(self):
        """A mutating tool (place_order) MUST await call_actor_gate (ACT-02 positive)."""
        _set_context()
        db_cm, session = _mock_db_session()
        gate_mock = _mock_gate_approve()
        adapter_mock = _mock_adapter()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", gate_mock),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_sync_db", db_cm),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=adapter_mock)),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args("idem-mut-001")))

        gate_mock.assert_called_once()


# ===========================================================================
# ACT-05 — Four-node structural assertion: Actor synchronous pre-mutation,
# Gatekeeper/Auditor/Strategist async post-response (agent.py unchanged)
# ===========================================================================


class TestFourNodeStructuralAssertion:
    """ACT-05: Structural proof that the four-node validation chain is wired correctly.

    Node 1 (synchronous pre-mutation): call_actor_gate awaited inside
        _execute_transactional_tool, before get_adapter is called.
    Nodes 2-4 (async post-response): celery_chain(run_gatekeeper,
        run_auditor, run_strategist).apply_async(queue="runtime") in agent.py,
        dispatched AFTER the response is emitted — agent.py UNCHANGED.
    """

    def _agent_src(self) -> str:
        agent_path = os.path.normpath(
            os.path.join(
                os.path.dirname(__file__),
                "../../app/worker/tasks/runtime/agent.py",
            )
        )
        assert os.path.isfile(agent_path), f"agent.py not found at {agent_path}"
        with open(agent_path, encoding="utf-8") as f:
            return f.read()

    def _tools_src(self) -> str:
        tools_path = os.path.normpath(
            os.path.join(
                os.path.dirname(__file__),
                "../../app/services/transactional/tools.py",
            )
        )
        assert os.path.isfile(tools_path), f"tools.py not found at {tools_path}"
        with open(tools_path, encoding="utf-8") as f:
            return f.read()

    def test_agent_py_celery_chain_dispatch_unchanged(self):
        """Nodes 2-4 still dispatched via celery_chain in agent.py (ACT-05)."""
        src = self._agent_src()
        assert "celery_chain(" in src, "agent.py must contain celery_chain("
        assert "run_gatekeeper" in src, "agent.py must reference run_gatekeeper"
        assert "run_auditor" in src, "agent.py must reference run_auditor"
        assert "run_strategist" in src, "agent.py must reference run_strategist"
        assert '.apply_async(queue="runtime")' in src, (
            'agent.py must dispatch chain via .apply_async(queue="runtime")'
        )

    def test_agent_py_celery_chain_uses_si_chaining(self):
        """Validators chained via .si() signature immutability (ACT-05)."""
        src = self._agent_src()
        assert "run_gatekeeper.si(" in src, "run_gatekeeper must use .si() for celery chain"
        assert "run_auditor.si(" in src, "run_auditor must use .si() for celery chain"
        assert "run_strategist.si(" in src, "run_strategist must use .si() for celery chain"

    def test_actor_gate_called_before_get_adapter_in_dispatcher(self):
        """Node 1 (Actor) is synchronous and runs before get_adapter_for_skill (step 6).

        Structural proof: in the source of _execute_transactional_tool, the
        call_actor_gate call site appears before the step 6/7 call site that
        leads to get_adapter_for_skill.

        Phase 16 update: get_adapter() replaced by get_adapter_for_skill() (INT-02 wiring).

        Phase 22 (22-02, T-22-ACT-15) update: steps 6-7 (adapter execute + audit +
        finalize) were extracted into the shared `_execute_adapter_and_audit`
        helper so the confirmation resolver can call the identical
        implementation instead of duplicating it. `get_adapter_for_skill(` is
        therefore no longer a literal token inside `_execute_transactional_tool`'s
        own source — it lives one level down, inside the helper. The dispatcher
        now calls `_execute_adapter_and_audit(` as its step 6/7 entry point, so
        that call site is the correct structural stand-in: call_actor_gate must
        still appear before it, preserving the exact "Actor runs before the
        adapter" invariant this test exists to prove.
        """
        src = self._tools_src()
        dispatcher_start = src.index("async def _execute_transactional_tool(")

        # The dispatcher body, sliced EXACTLY, via the parser rather than a
        # character count.
        #
        # This used to be `src[dispatcher_start : dispatcher_start + N]`, and N
        # had been raised three times — 14 000 -> 20 000 (17-06's IDV gate) ->
        # 22 000 (22-REVIEW-FIX's WR-01 dedup check) — each time because a step
        # landed BEFORE the adapter and pushed it past the window. D1/P1b's
        # recorded-mode branch (step 5.5) was the fourth, and the failure mode is
        # a ValueError from .index() rather than a readable assertion, which
        # sends the reader looking for a deleted call site instead of a stale
        # constant. A guard whose maintenance is "raise the magic number until it
        # passes" is one bump away from someone raising it far enough to swallow
        # the next function, where `_execute_adapter_and_audit(` would be found
        # in a body this test was never reading. ast.unparse gives the
        # dispatcher's own source and nothing else, permanently.
        module = ast.parse(src)
        dispatcher_node = next(
            node
            for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_execute_transactional_tool"
        )
        dispatcher_body = ast.unparse(dispatcher_node)

        call_actor_pos = dispatcher_body.index("call_actor_gate(")
        adapter_step_pos = dispatcher_body.index("_execute_adapter_and_audit(")

        assert call_actor_pos < adapter_step_pos, (
            f"call_actor_gate ({call_actor_pos}) must appear before "
            f"_execute_adapter_and_audit ({adapter_step_pos}) in "
            "_execute_transactional_tool source (Actor is synchronous pre-mutation node 1)"
        )

        # The adapter resolution itself must still happen strictly after the
        # Actor gate — proven one level down, inside the extracted helper,
        # since it is no longer inline in the dispatcher's own source.
        helper_start = src.index("async def _execute_adapter_and_audit(")
        assert helper_start < dispatcher_start, (
            "_execute_adapter_and_audit must be defined above _execute_transactional_tool"
        )
        helper_body = src[helper_start:dispatcher_start]
        assert "get_adapter_for_skill(" in helper_body, (
            "get_adapter_for_skill must be called inside the extracted "
            "_execute_adapter_and_audit helper"
        )

    def test_tools_py_contains_require_human_branch(self):
        """Structural: require_human branch present between block branch and adapter step."""
        src = self._tools_src()
        assert 'elif decision == "require_human":' in src, (
            'tools.py must contain elif decision == "require_human": branch'
        )
        assert 'error="actor_require_human"' in src, (
            'require_human branch must write audit row with error="actor_require_human"'
        )

    def test_tools_py_conn_str_var_lazy_import_only(self):
        """_conn_str_var import must NOT appear at module level (Pitfall 2 circular import)."""
        src = self._tools_src()
        # Find all lines with _conn_str_var
        for lineno, line in enumerate(src.splitlines(), 1):
            if "_conn_str_var" in line and "import" in line:
                # Must be inside the function body — the import line must be indented
                assert line.startswith("    "), (
                    f"_conn_str_var import at line {lineno} is NOT indented (module-level import "
                    "would cause circular import — Pitfall 2 from 15-RESEARCH.md)"
                )


# ===========================================================================
# IDV-04 + IDV-05 — Step 2.5 identity verification gate (Phase 17 Plan 06)
# ===========================================================================


class TestIDVGate:
    """IDV-04 + IDV-05: Step 2.5 enforcement gate tests.

    Gate ordering invariant: capability check (2) → IDV gate (2.5) → reserve idempotency (3).
    AUD-01 symmetry: both block branches write exactly one audit row.
    T-17-21: blocked calls must NOT consume the idempotency slot (reserve_idempotency not called).
    T-17-01: check_verified_session uses the tenant conn_str (per-tenant lookup).

    Patch notes:
    - 'check_verified_session' is lazily imported inside _execute_transactional_tool, so it
      must be patched at its source module: 'app.services.identity_service.check_verified_session'.
    - '_verified_session_token_var' is set directly via ContextVar.set() in each test's setup.
    """

    def _set_vst(self, token: str = "") -> None:
        """Set the verified session token ContextVar for the current asyncio context."""
        from app.services.agent_tools import _verified_session_token_var  # noqa: PLC0415

        _verified_session_token_var.set(token)

    def _snapshot_idv(self, required: bool) -> dict:
        """Return a minimal capability snapshot with requires_identity_verification set."""
        return {
            "enabled": True,
            "skill": "place_order",
            "requires_identity_verification": required,
        }

    def test_idv_blocks_without_session(self):
        """IDV-05: requires_identity_verification=true + empty ContextVar token → is_error, no adapter call."""
        _set_context()
        self._set_vst("")  # no verified session token
        adapter_mock = _mock_adapter()
        get_adapter_mock = AsyncMock(return_value=adapter_mock)

        with (
            _p("check_capability_access", AsyncMock(return_value=(self._snapshot_idv(True), None))),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert result.get("is_error") is True, f"Expected is_error=True on no-token IDV block; got: {result}"
        get_adapter_mock.assert_not_called()
        adapter_mock.place_order.assert_not_called()

    def test_idv_blocks_expired_session(self):
        """IDV-05: token present but check_verified_session returns False → is_error, no adapter call."""
        _set_context()
        self._set_vst("expired-or-invalid-token")
        adapter_mock = _mock_adapter()
        get_adapter_mock = AsyncMock(return_value=adapter_mock)

        with (
            _p("check_capability_access", AsyncMock(return_value=(self._snapshot_idv(True), None))),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", get_adapter_mock),
            patch(
                "app.services.identity_service.check_verified_session",
                AsyncMock(return_value=False),
            ),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert result.get("is_error") is True, (
            f"Expected is_error=True on expired-token IDV block; got: {result}"
        )
        get_adapter_mock.assert_not_called()
        adapter_mock.place_order.assert_not_called()

    def test_idv_passes_with_valid_session(self):
        """IDV-05: valid verified session → gate passes → reaches adapter (reserve + adapter called)."""
        _set_context()
        self._set_vst("valid-verified-token")
        adapter_mock = _mock_adapter()
        cvs_mock = AsyncMock(return_value=True)

        with (
            _p("check_capability_access", AsyncMock(return_value=(self._snapshot_idv(True), None))),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=adapter_mock)),
            patch(
                "app.services.identity_service.check_verified_session",
                cvs_mock,
            ),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert result.get("is_error") is not True, (
            f"Valid session must NOT block; got is_error on: {result}"
        )
        assert adapter_mock.place_order.call_count == 1, (
            f"Adapter must be called on valid session; count={adapter_mock.place_order.call_count}"
        )
        # IDV-05: gate must have consulted check_verified_session exactly once
        cvs_mock.assert_called_once()

    def test_idv_skipped_when_not_required(self):
        """IDV-04: requires_identity_verification=false + empty token → gate is no-op, adapter called."""
        _set_context()
        self._set_vst("")  # no token — but gate must be entirely skipped
        adapter_mock = _mock_adapter()

        with (
            _p("check_capability_access", AsyncMock(return_value=(self._snapshot_idv(False), None))),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=adapter_mock)),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        # Gate skipped entirely (IDV-04 envelope-driven) — adapter reached and called normally
        assert result.get("is_error") is not True, (
            f"Unverified call with required=False must NOT be blocked; got: {result}"
        )
        assert adapter_mock.place_order.call_count == 1, (
            f"Adapter must be called when IDV not required; count={adapter_mock.place_order.call_count}"
        )

    def test_idv_audit_row_written(self):
        """AUD-01 symmetry: each IDV block path writes exactly one audit row with the correct error.

        Block 1 (no token):     error='identity_verification.required'
        Block 2 (invalid token): error='identity_verification.invalid_or_expired'
        """
        # ---- Block 1: no token → identity_verification.required ----
        _set_context()
        self._set_vst("")
        audit_mock_1 = AsyncMock()

        with (
            _p("check_capability_access", AsyncMock(return_value=(self._snapshot_idv(True), None))),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", audit_mock_1),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=_mock_adapter())),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args("idem-idv-audit-1")))

        assert audit_mock_1.call_count == 1, (
            f"Expected exactly 1 audit row on no-token block, got {audit_mock_1.call_count}"
        )
        err1 = audit_mock_1.call_args.kwargs.get("error", "")
        assert err1 == "identity_verification.required", (
            f"Expected error='identity_verification.required', got {err1!r}"
        )

        # ---- Block 2: token present but invalid/expired ----
        _set_context()
        self._set_vst("stale-token")
        audit_mock_2 = AsyncMock()

        with (
            _p("check_capability_access", AsyncMock(return_value=(self._snapshot_idv(True), None))),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("mark_reservation_in_flight", AsyncMock(return_value=None)),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", audit_mock_2),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=_mock_adapter())),
            patch(
                "app.services.identity_service.check_verified_session",
                AsyncMock(return_value=False),
            ),
        ):
            asyncio.run(place_order_tool.handler(_valid_place_order_args("idem-idv-audit-2")))

        assert audit_mock_2.call_count == 1, (
            f"Expected exactly 1 audit row on invalid-token block, got {audit_mock_2.call_count}"
        )
        err2 = audit_mock_2.call_args.kwargs.get("error", "")
        assert err2 == "identity_verification.invalid_or_expired", (
            f"Expected error='identity_verification.invalid_or_expired', got {err2!r}"
        )

    def test_idv_before_idempotency(self):
        """T-17-21: IDV block must NOT call reserve_idempotency; idempotency key stays reusable.

        Gate order: Step 2 → Step 2.5 (IDV) → Step 3 (reserve).
        A blocked unverified call returns before reserve_idempotency, leaving the
        idempotency key free for a retry after the customer verifies identity.
        """
        _set_context()
        self._set_vst("")  # no token → block
        reserve_mock = _mock_reserve("reserved")

        with (
            _p("check_capability_access", AsyncMock(return_value=(self._snapshot_idv(True), None))),
            _p("reserve_idempotency", reserve_mock),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter_for_skill", AsyncMock(return_value=_mock_adapter())),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert result.get("is_error") is True, (
            f"IDV block must return is_error; got: {result}"
        )
        reserve_mock.assert_not_called()
