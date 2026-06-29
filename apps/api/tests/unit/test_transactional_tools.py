"""
Unit tests for Phase-14 transactional tool handlers (Plan 04).

TDD RED→GREEN:
  Task 1 RED: tests fail because tools.py does not exist.
  Task 1 GREEN: tools.py created; dispatcher + 6 mutating handlers; tests pass.
  Task 2 RED: confirm_action and registry-attachment tests fail because confirm_action_tool absent.
  Task 2 GREEN: confirm_action_tool added; registry attached; tests pass.
  Task 3 RED: build_tool_server and allowed_tools tests fail because agent_tools.py not updated.
  Task 3 GREEN: agent_tools.py + agent.py updated; all tests pass.

Test isolation:
  - All helper dependencies (check_capability_envelope, check_idempotency, store_idempotency,
    call_actor_gate, get_adapter, write_audit_row, get_sync_db) are mocked so no DB, Redis,
    or SDK binary is required.
  - ContextVars (_agent_id_var, _conversation_id_var) are set before each asyncio.run() call.
  - asyncio.run() is used throughout — NOT asyncio.get_event_loop().run_until_complete()
    (Python 3.12 closed-loop protection, per CLAUDE.md and prior Phase-14 fix).
  - Each test class is independently runnable and passes together with test_transactional_contract.py.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest

# ---------------------------------------------------------------------------
# ContextVar setup — must happen before any asyncio.run() call
# ---------------------------------------------------------------------------
from app.services.agent_tools import _agent_id_var, _conversation_id_var

TEST_AGENT_ID = "agent-test-0001"
TEST_CONV_ID = "conv-test-0001"


def _set_context(agent_id: str = TEST_AGENT_ID, conv_id: str = TEST_CONV_ID) -> None:
    """Set ContextVars in the current sync context so asyncio.run() copies them."""
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
# Mock factory helpers
# ---------------------------------------------------------------------------

def _mock_cap_pass(snapshot: dict | None = None) -> AsyncMock:
    """check_capability_envelope that always passes with the given snapshot."""
    return AsyncMock(return_value=(snapshot or {"enabled": True, "skill": "place_order"}, None))


def _mock_cap_deny(denial: str = "disabled") -> AsyncMock:
    """check_capability_envelope that always returns a denial."""
    return AsyncMock(return_value=({}, denial))


def _mock_idem_miss() -> AsyncMock:
    """check_idempotency that always returns None (cache miss)."""
    return AsyncMock(return_value=None)


def _mock_gate_approve() -> AsyncMock:
    """call_actor_gate that always returns approve."""
    return AsyncMock(return_value=("approve", ""))


def _mock_gate_block(rationale: str = "blocked by policy") -> AsyncMock:
    """call_actor_gate that always returns block."""
    return AsyncMock(return_value=("block", rationale))


def _mock_adapter(message: str = "Order placed [STUB]") -> MagicMock:
    """Minimal mock adapter whose place_order returns a PlaceOrderOutput."""
    from app.services.transactional.schemas import PlaceOrderOutput
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


# ===========================================================================
# Task 1 — _execute_transactional_tool dispatcher + 6 mutating handlers
# ===========================================================================


class TestBadSchemaRejection:
    """ValidationError path: bad args return is_error without touching any helper."""

    def test_place_order_missing_required_fields_returns_is_error(self):
        _set_context()
        cap_mock = _mock_cap_pass()
        audit_mock = AsyncMock()
        adapter_mock = _mock_adapter()
        get_adapter_mock = MagicMock(return_value=adapter_mock)

        with (
            patch("app.services.transactional.tools.check_capability_envelope", cap_mock),
            patch("app.services.transactional.tools.write_audit_row", audit_mock),
            patch("app.services.transactional.tools.get_adapter", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool
            result = asyncio.run(place_order_tool.handler({}))

        assert result.get("is_error") is True, f"Expected is_error=True, got: {result}"
        # No DB/adapter calls for a schema-only error
        cap_mock.assert_not_called()
        audit_mock.assert_not_called()
        get_adapter_mock.assert_not_called()

    def test_cancel_order_wrong_types_returns_is_error(self):
        _set_context()
        cap_mock = _mock_cap_pass()
        audit_mock = AsyncMock()

        with (
            patch("app.services.transactional.tools.check_capability_envelope", cap_mock),
            patch("app.services.transactional.tools.write_audit_row", audit_mock),
        ):
            from app.services.transactional.tools import cancel_order_tool
            result = asyncio.run(cancel_order_tool.handler({"order_id": 999}))  # missing required + wrong type

        assert result.get("is_error") is True
        cap_mock.assert_not_called()
        audit_mock.assert_not_called()


class TestCapabilityDenial:
    """Disabled-skill path: no adapter call, exactly one audit row with capability.denial: prefix."""

    def test_disabled_skill_returns_is_error(self):
        _set_context()
        cap_mock = _mock_cap_deny("disabled")
        audit_mock = AsyncMock()
        idem_mock = _mock_idem_miss()
        gate_mock = _mock_gate_approve()
        adapter_mock = _mock_adapter()
        get_adapter_mock = MagicMock(return_value=adapter_mock)

        with (
            patch("app.services.transactional.tools.check_capability_envelope", cap_mock),
            patch("app.services.transactional.tools.check_idempotency", idem_mock),
            patch("app.services.transactional.tools.call_actor_gate", gate_mock),
            patch("app.services.transactional.tools.write_audit_row", audit_mock),
            patch("app.services.transactional.tools.get_adapter", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool
            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert result.get("is_error") is True

    def test_disabled_skill_no_adapter_call(self):
        _set_context()
        cap_mock = _mock_cap_deny("disabled")
        audit_mock = AsyncMock()
        adapter_mock = _mock_adapter()
        get_adapter_mock = MagicMock(return_value=adapter_mock)

        with (
            patch("app.services.transactional.tools.check_capability_envelope", cap_mock),
            patch("app.services.transactional.tools.write_audit_row", audit_mock),
            patch("app.services.transactional.tools.get_adapter", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool
            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        # Adapter must NOT be called on capability denial
        get_adapter_mock.assert_not_called()
        adapter_mock.place_order.assert_not_called()

    def test_disabled_skill_writes_exactly_one_audit_row_with_denial_error(self):
        """AUD-01 symmetry: capability denial writes one audit row with error='capability.denial:<reason>'."""
        _set_context()
        cap_mock = _mock_cap_deny("disabled")
        audit_mock = AsyncMock()
        get_adapter_mock = MagicMock()

        with (
            patch("app.services.transactional.tools.check_capability_envelope", cap_mock),
            patch("app.services.transactional.tools.write_audit_row", audit_mock),
            patch("app.services.transactional.tools.get_adapter", get_adapter_mock),
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
        cap_mock = _mock_cap_deny("no_envelope_row")
        audit_mock = AsyncMock()
        get_adapter_mock = MagicMock()

        with (
            patch("app.services.transactional.tools.check_capability_envelope", cap_mock),
            patch("app.services.transactional.tools.write_audit_row", audit_mock),
            patch("app.services.transactional.tools.get_adapter", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool
            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert audit_mock.call_count == 1
        error_val = audit_mock.call_args.kwargs.get("error", "")
        assert "no_envelope_row" in error_val


class TestIdempotencyReplay:
    """Two calls with same key: adapter called once, single audit row."""

    def test_adapter_called_exactly_once_across_two_calls(self):
        _set_context()
        snap = {"enabled": True, "skill": "place_order"}
        cached_result = {"content": [{"type": "text", "text": "Order placed [cached]"}]}

        cap_mock = _mock_cap_pass(snap)
        # First call: miss; Second call: hit
        idem_check_mock = AsyncMock(side_effect=[None, cached_result])
        idem_store_mock = AsyncMock()
        gate_mock = _mock_gate_approve()
        audit_mock = AsyncMock()
        adapter_mock = _mock_adapter("Order placed [STUB]")
        get_adapter_mock = MagicMock(return_value=adapter_mock)

        args = _valid_place_order_args("idem-replay")

        with (
            patch("app.services.transactional.tools.check_capability_envelope", cap_mock),
            patch("app.services.transactional.tools.check_idempotency", idem_check_mock),
            patch("app.services.transactional.tools.store_idempotency", idem_store_mock),
            patch("app.services.transactional.tools.call_actor_gate", gate_mock),
            patch("app.services.transactional.tools.write_audit_row", audit_mock),
            patch("app.services.transactional.tools.get_adapter", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool
            # First call
            _set_context()
            result1 = asyncio.run(place_order_tool.handler(args))
            # Second call (same idempotency key)
            _set_context()
            result2 = asyncio.run(place_order_tool.handler(args))

        # Adapter must have been called only once (first call)
        assert adapter_mock.place_order.call_count == 1, (
            f"Expected adapter.place_order called once, got {adapter_mock.place_order.call_count}"
        )

    def test_single_audit_row_written_across_two_calls(self):
        _set_context()
        cached_result = {"content": [{"type": "text", "text": "cached"}]}
        cap_mock = _mock_cap_pass()
        idem_check_mock = AsyncMock(side_effect=[None, cached_result])
        idem_store_mock = AsyncMock()
        gate_mock = _mock_gate_approve()
        audit_mock = AsyncMock()
        adapter_mock = _mock_adapter()
        get_adapter_mock = MagicMock(return_value=adapter_mock)

        args = _valid_place_order_args("idem-audit-once")

        with (
            patch("app.services.transactional.tools.check_capability_envelope", cap_mock),
            patch("app.services.transactional.tools.check_idempotency", idem_check_mock),
            patch("app.services.transactional.tools.store_idempotency", idem_store_mock),
            patch("app.services.transactional.tools.call_actor_gate", gate_mock),
            patch("app.services.transactional.tools.write_audit_row", audit_mock),
            patch("app.services.transactional.tools.get_adapter", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool
            _set_context()
            asyncio.run(place_order_tool.handler(args))
            _set_context()
            asyncio.run(place_order_tool.handler(args))

        # Audit row written only for the first call (replay short-circuits before audit)
        assert audit_mock.call_count == 1, (
            f"Expected exactly 1 audit row, got {audit_mock.call_count}"
        )

    def test_replay_returns_cached_result(self):
        _set_context()
        cached_result = {"content": [{"type": "text", "text": "cached response"}]}
        cap_mock = _mock_cap_pass()
        idem_check_mock = AsyncMock(side_effect=[None, cached_result])
        idem_store_mock = AsyncMock()
        gate_mock = _mock_gate_approve()
        audit_mock = AsyncMock()
        adapter_mock = _mock_adapter()
        get_adapter_mock = MagicMock(return_value=adapter_mock)

        args = _valid_place_order_args("idem-cache-check")

        with (
            patch("app.services.transactional.tools.check_capability_envelope", cap_mock),
            patch("app.services.transactional.tools.check_idempotency", idem_check_mock),
            patch("app.services.transactional.tools.store_idempotency", idem_store_mock),
            patch("app.services.transactional.tools.call_actor_gate", gate_mock),
            patch("app.services.transactional.tools.write_audit_row", audit_mock),
            patch("app.services.transactional.tools.get_adapter", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool
            _set_context()
            asyncio.run(place_order_tool.handler(args))
            _set_context()
            result2 = asyncio.run(place_order_tool.handler(args))

        assert result2 == cached_result


class TestActorBlock:
    """Actor seam block: adapter NOT called, is_error returned, audit row written."""

    def test_actor_block_returns_is_error(self):
        _set_context()
        cap_mock = _mock_cap_pass()
        idem_mock = _mock_idem_miss()
        gate_mock = _mock_gate_block("policy: amount too high")
        audit_mock = AsyncMock()
        adapter_mock = _mock_adapter()
        get_adapter_mock = MagicMock(return_value=adapter_mock)

        with (
            patch("app.services.transactional.tools.check_capability_envelope", cap_mock),
            patch("app.services.transactional.tools.check_idempotency", idem_mock),
            patch("app.services.transactional.tools.store_idempotency", AsyncMock()),
            patch("app.services.transactional.tools.call_actor_gate", gate_mock),
            patch("app.services.transactional.tools.write_audit_row", audit_mock),
            patch("app.services.transactional.tools.get_adapter", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool
            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert result.get("is_error") is True

    def test_actor_block_adapter_not_called(self):
        _set_context()
        cap_mock = _mock_cap_pass()
        idem_mock = _mock_idem_miss()
        gate_mock = _mock_gate_block()
        audit_mock = AsyncMock()
        adapter_mock = _mock_adapter()
        get_adapter_mock = MagicMock(return_value=adapter_mock)

        with (
            patch("app.services.transactional.tools.check_capability_envelope", cap_mock),
            patch("app.services.transactional.tools.check_idempotency", idem_mock),
            patch("app.services.transactional.tools.store_idempotency", AsyncMock()),
            patch("app.services.transactional.tools.call_actor_gate", gate_mock),
            patch("app.services.transactional.tools.write_audit_row", audit_mock),
            patch("app.services.transactional.tools.get_adapter", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool
            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        # get_adapter must NOT be called on actor block
        get_adapter_mock.assert_not_called()
        adapter_mock.place_order.assert_not_called()

    def test_actor_block_writes_audit_row(self):
        _set_context()
        cap_mock = _mock_cap_pass()
        idem_mock = _mock_idem_miss()
        gate_mock = _mock_gate_block("test block")
        audit_mock = AsyncMock()
        get_adapter_mock = MagicMock()

        with (
            patch("app.services.transactional.tools.check_capability_envelope", cap_mock),
            patch("app.services.transactional.tools.check_idempotency", idem_mock),
            patch("app.services.transactional.tools.store_idempotency", AsyncMock()),
            patch("app.services.transactional.tools.call_actor_gate", gate_mock),
            patch("app.services.transactional.tools.write_audit_row", audit_mock),
            patch("app.services.transactional.tools.get_adapter", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool
            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert audit_mock.call_count == 1
        call_kwargs = audit_mock.call_args.kwargs
        assert call_kwargs.get("error") == "actor_block"
        # actor_decision should be "block"
        assert call_kwargs.get("actor_decision") == "block"


class TestAuditRowOnAllPaths:
    """Audit row written on both success and adapter-error paths."""

    def test_audit_written_on_success(self):
        _set_context()
        cap_mock = _mock_cap_pass()
        idem_mock = _mock_idem_miss()
        gate_mock = _mock_gate_approve()
        audit_mock = AsyncMock()
        idem_store_mock = AsyncMock()
        adapter_mock = _mock_adapter("success message")
        get_adapter_mock = MagicMock(return_value=adapter_mock)

        with (
            patch("app.services.transactional.tools.check_capability_envelope", cap_mock),
            patch("app.services.transactional.tools.check_idempotency", idem_mock),
            patch("app.services.transactional.tools.store_idempotency", idem_store_mock),
            patch("app.services.transactional.tools.call_actor_gate", gate_mock),
            patch("app.services.transactional.tools.write_audit_row", audit_mock),
            patch("app.services.transactional.tools.get_adapter", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool
            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert audit_mock.call_count == 1
        call_kwargs = audit_mock.call_args.kwargs
        assert call_kwargs.get("error") is None, f"Expected no error on success, got {call_kwargs.get('error')!r}"
        assert call_kwargs.get("result") is not None

    def test_audit_written_on_adapter_error(self):
        _set_context()
        cap_mock = _mock_cap_pass()
        idem_mock = _mock_idem_miss()
        gate_mock = _mock_gate_approve()
        audit_mock = AsyncMock()
        idem_store_mock = AsyncMock()

        # Adapter raises an exception
        adapter_mock = MagicMock()
        adapter_mock.place_order = AsyncMock(side_effect=RuntimeError("upstream timeout"))
        get_adapter_mock = MagicMock(return_value=adapter_mock)

        with (
            patch("app.services.transactional.tools.check_capability_envelope", cap_mock),
            patch("app.services.transactional.tools.check_idempotency", idem_mock),
            patch("app.services.transactional.tools.store_idempotency", idem_store_mock),
            patch("app.services.transactional.tools.call_actor_gate", gate_mock),
            patch("app.services.transactional.tools.write_audit_row", audit_mock),
            patch("app.services.transactional.tools.get_adapter", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool
            result = asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        assert result.get("is_error") is True
        assert audit_mock.call_count == 1
        call_kwargs = audit_mock.call_args.kwargs
        assert call_kwargs.get("error") is not None
        assert "upstream timeout" in call_kwargs.get("error", "")

    def test_idempotency_stored_on_success(self):
        _set_context()
        cap_mock = _mock_cap_pass()
        idem_mock = _mock_idem_miss()
        gate_mock = _mock_gate_approve()
        audit_mock = AsyncMock()
        idem_store_mock = AsyncMock()
        adapter_mock = _mock_adapter()
        get_adapter_mock = MagicMock(return_value=adapter_mock)

        with (
            patch("app.services.transactional.tools.check_capability_envelope", cap_mock),
            patch("app.services.transactional.tools.check_idempotency", idem_mock),
            patch("app.services.transactional.tools.store_idempotency", idem_store_mock),
            patch("app.services.transactional.tools.call_actor_gate", gate_mock),
            patch("app.services.transactional.tools.write_audit_row", audit_mock),
            patch("app.services.transactional.tools.get_adapter", get_adapter_mock),
        ):
            from app.services.transactional.tools import place_order_tool
            asyncio.run(place_order_tool.handler(_valid_place_order_args()))

        # store_idempotency must be called on success
        idem_store_mock.assert_called_once()


class TestSourceAssertions:
    """Source-code assertions required by the plan's acceptance criteria."""

    def test_call_actor_gate_referenced_in_tools_py(self):
        impl_path = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "../../app/services/transactional/tools.py")
        )
        assert os.path.isfile(impl_path), f"tools.py not found at {impl_path}"
        with open(impl_path) as f:
            src = f.read()
        assert "call_actor_gate" in src, "call_actor_gate must be referenced in tools.py"

    def test_check_capability_envelope_referenced_in_tools_py(self):
        impl_path = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "../../app/services/transactional/tools.py")
        )
        with open(impl_path) as f:
            src = f.read()
        assert "check_capability_envelope" in src, "check_capability_envelope must be referenced in tools.py"


# ===========================================================================
# Task 2 — confirm_action_tool + TOOL_REGISTRY sdk_tool attachment
# ===========================================================================


class TestConfirmActionTool:
    """confirm_action writes a pending_confirmations row, calls no provider adapter."""

    def test_confirm_action_writes_pending_confirmations_row(self):
        _set_context()
        db_cm, session = _mock_db_session()

        with patch("app.services.transactional.tools.get_sync_db", db_cm):
            from app.services.transactional.tools import confirm_action_tool
            result = asyncio.run(confirm_action_tool.handler({
                "skill": "place_order",
                "action_reference": "idem-001",
            }))

        assert result.get("is_error") is not True, f"confirm_action should succeed: {result}"
        # A PendingConfirmation row must have been added and committed
        session.add.assert_called_once()
        session.commit.assert_called_once()

    def test_confirm_action_does_not_call_provider_adapter(self):
        _set_context()
        db_cm, session = _mock_db_session()
        get_adapter_mock = MagicMock()

        with (
            patch("app.services.transactional.tools.get_sync_db", db_cm),
            patch("app.services.transactional.tools.get_adapter", get_adapter_mock),
        ):
            from app.services.transactional.tools import confirm_action_tool
            asyncio.run(confirm_action_tool.handler({
                "skill": "place_order",
                "action_reference": "idem-001",
            }))

        # get_adapter must NOT be called for confirm_action (mutating=False)
        get_adapter_mock.assert_not_called()

    def test_confirm_action_bad_schema_returns_is_error(self):
        _set_context()
        db_cm, session = _mock_db_session()

        with patch("app.services.transactional.tools.get_sync_db", db_cm):
            from app.services.transactional.tools import confirm_action_tool
            result = asyncio.run(confirm_action_tool.handler({}))

        assert result.get("is_error") is True
        session.add.assert_not_called()

    def test_confirm_action_row_has_correct_skill(self):
        _set_context()
        db_cm, session = _mock_db_session()

        with patch("app.services.transactional.tools.get_sync_db", db_cm):
            from app.services.transactional.tools import confirm_action_tool
            asyncio.run(confirm_action_tool.handler({
                "skill": "issue_refund",
                "action_reference": "idem-ref-001",
            }))

        added_obj = session.add.call_args[0][0]
        assert added_obj.skill == "issue_refund"

    def test_confirm_action_source_references_pending_confirmations(self):
        impl_path = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "../../app/services/transactional/tools.py")
        )
        with open(impl_path) as f:
            src = f.read()
        assert "pending_confirmations" in src.lower() or "PendingConfirmation" in src, (
            "tools.py must reference PendingConfirmation / pending_confirmations table"
        )


class TestToolRegistryAttachment:
    """After importing tools.py, TOOL_REGISTRY entries must have sdk_tool set."""

    def test_all_registry_entries_have_sdk_tool_after_import(self):
        # Import tools — this triggers the module-level registry attachment
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
# Task 3 — build_tool_server has 11 tools + agent.py allowed_tools extended
# ===========================================================================


class TestBuildToolServerRegistration:
    """build_tool_server must pass all 11 tools to create_sdk_mcp_server."""

    def test_build_tool_server_includes_all_11_tools(self):
        from app.services.agent_tools import build_tool_server
        from unittest.mock import MagicMock, patch

        with patch("app.services.agent_tools.create_sdk_mcp_server") as mock_create:
            mock_create.return_value = {"type": "sdk", "name": "customer-tools", "instance": MagicMock()}
            build_tool_server(
                conn_str="postgresql://test",
                agent_id=TEST_AGENT_ID,
                agent_name="Test Agent",
                strategy=MagicMock(),
                conversation_id=TEST_CONV_ID,
                notify_fn=None,
            )

        mock_create.assert_called_once()
        # tools= is a keyword argument
        tools_arg = mock_create.call_args.kwargs.get("tools")
        assert tools_arg is not None, "tools= must be passed as keyword to create_sdk_mcp_server"
        tool_names = [t.name for t in tools_arg]

        assert len(tool_names) == 11, (
            f"Expected 11 tools in build_tool_server, got {len(tool_names)}: {tool_names}"
        )

    def test_build_tool_server_original_4_tools_retained(self):
        from app.services.agent_tools import build_tool_server

        with patch("app.services.agent_tools.create_sdk_mcp_server") as mock_create:
            mock_create.return_value = {"type": "sdk", "name": "customer-tools", "instance": MagicMock()}
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

        # Original 4 tools must be retained
        assert "retrieve" in tool_names, "retrieve tool must be retained"
        assert "lookup_structured" in tool_names, "lookup_structured tool must be retained"
        assert "escalate_to_human" in tool_names, "escalate_to_human tool must be retained"
        assert "clarify" in tool_names, "clarify tool must be retained"

    def test_build_tool_server_has_all_7_new_tools(self):
        from app.services.agent_tools import build_tool_server

        with patch("app.services.agent_tools.create_sdk_mcp_server") as mock_create:
            mock_create.return_value = {"type": "sdk", "name": "customer-tools", "instance": MagicMock()}
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
            "place_order", "cancel_order", "issue_refund",
            "update_subscription", "book_slot", "update_customer_record",
            "confirm_action",
        }
        missing = expected_new - tool_names
        assert not missing, f"Missing new tools in build_tool_server: {missing}"


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
        with open(agent_py_path) as f:
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
            assert tool_name in source, (
                f"agent.py allowed_tools missing: {tool_name!r}"
            )

    def test_agent_py_retains_original_4_allowed_tools(self):
        agent_py_path = os.path.normpath(
            os.path.join(
                os.path.dirname(__file__),
                "../../app/worker/tasks/runtime/agent.py",
            )
        )
        with open(agent_py_path) as f:
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
