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

import asyncio
import os
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

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
    from app.services.transactional.schemas import PlaceOrderOutput  # noqa: PLC0415

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
    """Return (contextmanager_factory, session_mock) for get_sync_db patching."""
    session = MagicMock()
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
        get_adapter_mock = MagicMock()

        with (
            _p("check_capability_access", access_mock),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_adapter", get_adapter_mock),
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
            patch(f"{_T}.get_adapter", MagicMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert result.get("is_error") is True

    def test_disabled_skill_no_adapter_call(self):
        _set_context()
        adapter_mock = _mock_adapter()
        get_adapter_mock = MagicMock(return_value=adapter_mock)

        with (
            _p("check_capability_access", _mock_access_deny("disabled")),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter", get_adapter_mock),
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
            patch(f"{_T}.get_adapter", MagicMock()),
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
            patch(f"{_T}.get_adapter", MagicMock()),
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
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("release_idempotency", release_mock),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter", MagicMock(return_value=adapter_mock)),
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
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("finalize_idempotency", finalize_mock),
            _p("release_idempotency", release_mock),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter", MagicMock(return_value=_mock_adapter())),
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
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_adapter", MagicMock(return_value=_mock_adapter("ok"))),
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
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter", MagicMock(return_value=_mock_adapter("Order confirmed"))),
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
            patch(f"{_T}.get_adapter", MagicMock()),
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
            patch(f"{_T}.get_adapter", MagicMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args("idem-replay")))

        rate_mock.assert_not_called()

    def test_replay_does_not_call_adapter(self):
        _set_context()
        adapter_mock = _mock_adapter()
        get_adapter_mock = MagicMock(return_value=adapter_mock)

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("replay", result=self._cached())),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter", get_adapter_mock),
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
            patch(f"{_T}.get_adapter", MagicMock()),
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
            patch(f"{_T}.get_adapter", MagicMock()),
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
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter", MagicMock(return_value=adapter_mock)),
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
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_adapter", MagicMock(return_value=_mock_adapter())),
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
            patch(f"{_T}.get_adapter", MagicMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert result.get("is_error") is True

    def test_args_mismatch_text_signals_key_reused(self):
        """Error message must indicate the key was reused with different arguments."""
        _set_context()

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("args_mismatch")),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter", MagicMock()),
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
        get_adapter_mock = MagicMock(return_value=adapter_mock)

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("args_mismatch")),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter", get_adapter_mock),
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
            patch(f"{_T}.get_adapter", MagicMock()),
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
            patch(f"{_T}.get_adapter", MagicMock()),
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
            patch(f"{_T}.get_adapter", MagicMock()),
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
        get_adapter_mock = MagicMock(return_value=adapter_mock)

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("in_progress")),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        get_adapter_mock.assert_not_called()
        adapter_mock.place_order.assert_not_called()


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
            _p("apply_rate_and_constraint_checks", _mock_rate_deny("rate_limit")),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter", MagicMock()),
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
            _p("apply_rate_and_constraint_checks", _mock_rate_deny("rate_limit")),
            _p("release_idempotency", release_mock),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter", MagicMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        release_mock.assert_called_once()

    def test_rate_denial_adapter_not_called(self):
        _set_context()
        adapter_mock = _mock_adapter()
        get_adapter_mock = MagicMock(return_value=adapter_mock)

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("apply_rate_and_constraint_checks", _mock_rate_deny("rate_limit")),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter", get_adapter_mock),
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
            _p("apply_rate_and_constraint_checks", _mock_rate_deny("rate_limit")),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_adapter", MagicMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert audit_mock.call_count == 1
        error_val = audit_mock.call_args.kwargs.get("error", "")
        assert "rate_limit" in error_val or "capability.denial" in error_val, (
            f"Rate denial audit row must reference rate_limit; got {error_val!r}"
        )


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
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_block("policy: amount too high")),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter", MagicMock()),
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
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", release_mock),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_block()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter", MagicMock()),
        ):
            from app.services.transactional.tools import place_order_tool

            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        release_mock.assert_called_once()

    def test_actor_block_adapter_not_called(self):
        _set_context()
        adapter_mock = _mock_adapter()
        get_adapter_mock = MagicMock(return_value=adapter_mock)

        with (
            _p("check_capability_access", _mock_access_pass()),
            _p("reserve_idempotency", _mock_reserve("reserved")),
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_block()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter", get_adapter_mock),
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
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_block("test block")),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_adapter", MagicMock()),
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
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter", MagicMock(return_value=self._erring_adapter())),
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
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", release_mock),
            _p("finalize_idempotency", _mock_finalize()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter", MagicMock(return_value=self._erring_adapter())),
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
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("finalize_idempotency", finalize_mock),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", AsyncMock()),
            patch(f"{_T}.get_adapter", MagicMock(return_value=self._erring_adapter())),
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
            _p("apply_rate_and_constraint_checks", _mock_rate_pass()),
            _p("release_idempotency", _mock_release()),
            _p("finalize_idempotency", _mock_finalize()),
            _p("compute_args_hash", MagicMock(return_value="fakehash")),
            patch(f"{_T}.call_actor_gate", _mock_gate_approve()),
            patch(f"{_T}.write_audit_row", audit_mock),
            patch(f"{_T}.get_adapter", MagicMock(return_value=self._erring_adapter())),
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

    def test_check_capability_access_in_confirm_action_segment(self):
        """WR-05: confirm_action_tool must reference check_capability_access."""
        src = self._tools_src()
        confirm_seg = src[src.index("async def confirm_action_tool"):]
        assert "check_capability_access" in confirm_seg, (
            "confirm_action_tool must call check_capability_access (WR-05)"
        )


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
        get_adapter_mock = MagicMock()
        access_mock = _mock_access_pass()

        with (
            _p("check_capability_access", access_mock),
            patch(f"{_T}.get_sync_db", db_cm),
            patch(f"{_T}.get_adapter", get_adapter_mock),
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
# build_tool_server (kept from 14-04)
# ===========================================================================


class TestBuildToolServerRegistration:
    """build_tool_server must pass all 11 tools to create_sdk_mcp_server."""

    def test_build_tool_server_includes_all_11_tools(self):
        from app.services.agent_tools import build_tool_server

        with patch("app.services.agent_tools.create_sdk_mcp_server") as mock_create:
            mock_create.return_value = {
                "type": "sdk",
                "name": "customer-tools",
                "instance": MagicMock(),
            }
            build_tool_server(
                conn_str="postgresql://test",
                agent_id=TEST_AGENT_ID,
                agent_name="Test Agent",
                strategy=MagicMock(),
                conversation_id=TEST_CONV_ID,
                notify_fn=None,
            )

        mock_create.assert_called_once()
        tools_arg = mock_create.call_args.kwargs.get("tools")
        assert tools_arg is not None, "tools= must be passed as keyword to create_sdk_mcp_server"
        tool_names = [t.name for t in tools_arg]

        assert len(tool_names) == 11, (
            f"Expected 11 tools in build_tool_server, got {len(tool_names)}: {tool_names}"
        )

    def test_build_tool_server_original_4_tools_retained(self):
        from app.services.agent_tools import build_tool_server

        with patch("app.services.agent_tools.create_sdk_mcp_server") as mock_create:
            mock_create.return_value = {
                "type": "sdk",
                "name": "customer-tools",
                "instance": MagicMock(),
            }
            build_tool_server(
                conn_str="postgresql://test",
                agent_id=TEST_AGENT_ID,
                agent_name="Test Agent",
                strategy=MagicMock(),
                conversation_id=TEST_CONV_ID,
                notify_fn=None,
            )

        tools_arg = mock_create.call_args.kwargs.get("tools", [])
        tool_names = {t.name for t in tools_arg}

        assert "retrieve" in tool_names
        assert "lookup_structured" in tool_names
        assert "escalate_to_human" in tool_names
        assert "clarify" in tool_names

    def test_build_tool_server_has_all_7_new_tools(self):
        from app.services.agent_tools import build_tool_server

        with patch("app.services.agent_tools.create_sdk_mcp_server") as mock_create:
            mock_create.return_value = {
                "type": "sdk",
                "name": "customer-tools",
                "instance": MagicMock(),
            }
            build_tool_server(
                conn_str="postgresql://test",
                agent_id=TEST_AGENT_ID,
                agent_name="Test Agent",
                strategy=MagicMock(),
                conversation_id=TEST_CONV_ID,
                notify_fn=None,
            )

        tools_arg = mock_create.call_args.kwargs.get("tools", [])
        tool_names = {t.name for t in tools_arg}

        expected_new = {
            "place_order",
            "cancel_order",
            "issue_refund",
            "update_subscription",
            "book_slot",
            "update_customer_record",
            "confirm_action",
        }
        missing = expected_new - tool_names
        assert not missing, f"Missing new tools in build_tool_server: {missing}"


# ===========================================================================
# agent.py allowed_tools (kept from 14-04)
# ===========================================================================


class TestAgentPyAllowedTools:
    """agent.py allowed_tools must contain the 7 new mcp__customer-tools__ entries."""

    def test_agent_py_has_all_7_new_allowed_tools(self):
        agent_py_path = os.path.normpath(
            os.path.join(
                os.path.dirname(__file__),
                "../../app/worker/tasks/runtime/agent.py",
            )
        )
        assert os.path.isfile(agent_py_path), f"agent.py not found at {agent_py_path}"
        with open(agent_py_path, encoding="utf-8") as f:
            source = f.read()

        new_tools = [
            "mcp__customer-tools__place_order",
            "mcp__customer-tools__cancel_order",
            "mcp__customer-tools__issue_refund",
            "mcp__customer-tools__update_subscription",
            "mcp__customer-tools__book_slot",
            "mcp__customer-tools__update_customer_record",
            "mcp__customer-tools__confirm_action",
        ]
        for tool_name in new_tools:
            assert tool_name in source, f"agent.py allowed_tools missing: {tool_name!r}"

    def test_agent_py_retains_original_4_allowed_tools(self):
        agent_py_path = os.path.normpath(
            os.path.join(
                os.path.dirname(__file__),
                "../../app/worker/tasks/runtime/agent.py",
            )
        )
        with open(agent_py_path, encoding="utf-8") as f:
            source = f.read()

        retained_tools = [
            "mcp__customer-tools__retrieve",
            "mcp__customer-tools__lookup_structured",
            "mcp__customer-tools__escalate_to_human",
            "mcp__customer-tools__clarify",
        ]
        for tool_name in retained_tools:
            assert tool_name in source, (
                f"agent.py allowed_tools missing retained tool: {tool_name!r}"
            )
