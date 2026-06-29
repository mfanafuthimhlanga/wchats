"""
Unit tests for Phase-14 capability envelope enforcement, idempotency helpers,
and audit row writer.

TDD RED→GREEN:
  Task 1 RED (capability enforcement): tests fail because enforcement.py does not exist.
  Task 1 GREEN: enforcement.py created; capability tests pass.
  Task 2 RED (idempotency): tests fail because idempotency.py does not exist.
    → See test_tool_idempotency.py for idempotency-specific tests.
  Task 3 RED (audit): audit tests fail because audit.py does not exist.
  Task 3 GREEN: audit.py created; audit tests pass.

Covers:
  Task 1 — check_capability_envelope:
    - No-row denial: missing envelope row returns non-None denial + logs capability.denial
    - Disabled denial: enabled=false row returns denial + logs capability.denial
    - Rate-limit denial: Redis INCR over limit returns denial + logs capability.denial
    - max_amount_cents denial: amount_cents > constraint returns denial + logs capability.denial
    - Pass-through: enabled row within all limits returns snapshot dict + None denial
    - snapshot is a plain dict (not an ORM object)
    - _parse_rate_limit parses "N/<unit>" correctly

  Task 3 — write_audit_row:
    - Success path: row written with result set, error NULL, actor_decision/rationale empty
    - Error path: row written with result=None, error set
    - actor_decision and actor_rationale are empty strings
    - capability_snapshot is stored from a plain dict
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

# ---------------------------------------------------------------------------
# _parse_rate_limit — pure function, no DB/Redis needed
# ---------------------------------------------------------------------------

from app.services.transactional.enforcement import _parse_rate_limit


class TestParseRateLimit:
    def test_minute(self):
        assert _parse_rate_limit("10/minute") == (10, 60)

    def test_hour(self):
        assert _parse_rate_limit("5/hour") == (5, 3600)

    def test_day(self):
        assert _parse_rate_limit("100/day") == (100, 86400)

    def test_none_returns_none(self):
        assert _parse_rate_limit(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_rate_limit("") is None

    def test_malformed_returns_none(self):
        assert _parse_rate_limit("100") is None


# ---------------------------------------------------------------------------
# check_capability_envelope — mocked DB + Redis
# ---------------------------------------------------------------------------

from app.services.transactional.enforcement import check_capability_envelope


def _make_envelope_mapping(
    agent_id,
    skill,
    enabled=True,
    rate_limit=None,
    constraints=None,
    requires_confirmation=False,
    requires_identity_verification=False,
):
    """Return a dict that mimics a SQLAlchemy Row mapping for capability_envelopes."""
    from uuid import uuid4 as _uuid4
    return {
        "id": str(_uuid4()),
        "agent_id": str(agent_id),
        "skill": skill,
        "enabled": enabled,
        "rate_limit": rate_limit,
        "constraints": constraints or {},
        "requires_confirmation": requires_confirmation,
        "requires_identity_verification": requires_identity_verification,
        "updated_at": "2026-06-29T00:00:00Z",
    }


def _mock_db_session(row):
    """Return a contextmanager that yields a session mock whose execute().mappings().first() returns row."""
    session = MagicMock()
    execute_result = MagicMock()
    execute_result.mappings.return_value.first.return_value = row
    session.execute.return_value = execute_result
    session.__enter__ = lambda s: s
    session.__exit__ = MagicMock(return_value=False)

    @contextmanager
    def _ctx():
        yield session

    return _ctx


def _make_args(amount_cents=None, refund_amount_cents=None):
    """Return a simple namespace for args with optional amount fields."""
    args = MagicMock()
    args.amount_cents = amount_cents
    args.refund_amount_cents = refund_amount_cents
    return args


class TestCheckCapabilityEnvelope:
    """Tests for check_capability_envelope (mocked DB + Redis)."""

    def test_no_row_returns_denial(self):
        """Missing envelope row must yield a denial (fail-closed)."""
        agent_id = uuid4()
        skill = "place_order"
        args = _make_args()

        mock_cm = _mock_db_session(None)  # no row
        logs: list[dict] = []

        with (
            patch("app.services.transactional.enforcement.get_sync_db", mock_cm),
            patch("app.services.transactional.enforcement.log") as mock_log,
        ):
            snapshot, denial = asyncio.run(
                check_capability_envelope(agent_id, skill, args)
            )

        assert denial is not None, "Expected a denial reason for a missing row"
        assert isinstance(snapshot, dict)
        mock_log.warning.assert_called_once()
        call_kwargs = mock_log.warning.call_args
        assert "capability.denial" in call_kwargs[0] or call_kwargs[0][0] == "capability.denial"

    def test_disabled_row_returns_denial(self):
        """An enabled=false envelope must yield a denial."""
        agent_id = uuid4()
        skill = "place_order"
        args = _make_args()
        row = _make_envelope_mapping(agent_id, skill, enabled=False)

        mock_cm = _mock_db_session(row)

        with (
            patch("app.services.transactional.enforcement.get_sync_db", mock_cm),
            patch("app.services.transactional.enforcement.log") as mock_log,
        ):
            snapshot, denial = asyncio.run(
                check_capability_envelope(agent_id, skill, args)
            )

        assert denial is not None, "Expected a denial reason for disabled skill"
        assert isinstance(snapshot, dict)
        mock_log.warning.assert_called_once()
        call_args = mock_log.warning.call_args
        assert call_args[0][0] == "capability.denial"

    def test_rate_limit_denial(self):
        """Redis INCR over limit must yield a rate_limit denial."""
        agent_id = uuid4()
        skill = "place_order"
        args = _make_args()
        row = _make_envelope_mapping(agent_id, skill, enabled=True, rate_limit="2/minute")

        mock_cm = _mock_db_session(row)
        mock_redis = MagicMock()
        # Simulate count OVER limit: incr returns 3 (limit is 2)
        mock_redis.incr.return_value = 3
        mock_redis.expire.return_value = True

        with (
            patch("app.services.transactional.enforcement.get_sync_db", mock_cm),
            patch("app.services.transactional.enforcement._get_redis", return_value=mock_redis),
            patch("app.services.transactional.enforcement.log") as mock_log,
        ):
            snapshot, denial = asyncio.run(
                check_capability_envelope(agent_id, skill, args)
            )

        assert denial is not None, "Expected a denial reason for rate-limited call"
        mock_log.warning.assert_called_once()
        call_args = mock_log.warning.call_args
        assert call_args[0][0] == "capability.denial"

    def test_max_amount_cents_denial(self):
        """amount_cents > max_amount_cents constraint must yield a denial."""
        agent_id = uuid4()
        skill = "place_order"
        args = _make_args(amount_cents=9000)
        row = _make_envelope_mapping(
            agent_id, skill, enabled=True,
            constraints={"max_amount_cents": 5000}
        )

        mock_cm = _mock_db_session(row)

        with (
            patch("app.services.transactional.enforcement.get_sync_db", mock_cm),
            patch("app.services.transactional.enforcement.log") as mock_log,
        ):
            snapshot, denial = asyncio.run(
                check_capability_envelope(agent_id, skill, args)
            )

        assert denial is not None, "Expected denial for amount_cents exceeding max_amount_cents"
        mock_log.warning.assert_called_once()
        call_args = mock_log.warning.call_args
        assert call_args[0][0] == "capability.denial"

    def test_enabled_within_limits_passes(self):
        """Enabled row within all limits must return snapshot dict + None denial."""
        agent_id = uuid4()
        skill = "place_order"
        args = _make_args(amount_cents=1000)
        row = _make_envelope_mapping(
            agent_id, skill, enabled=True,
            constraints={"max_amount_cents": 5000}
        )

        mock_cm = _mock_db_session(row)
        mock_redis = MagicMock()
        # Under limit: incr returns 1 (limit is N/A; no rate_limit set)
        mock_redis.incr.return_value = 1
        mock_redis.expire.return_value = True

        with (
            patch("app.services.transactional.enforcement.get_sync_db", mock_cm),
            patch("app.services.transactional.enforcement._get_redis", return_value=mock_redis),
            patch("app.services.transactional.enforcement.log") as mock_log,
        ):
            snapshot, denial = asyncio.run(
                check_capability_envelope(agent_id, skill, args)
            )

        assert denial is None, f"Expected no denial, got {denial!r}"
        assert isinstance(snapshot, dict)
        mock_log.warning.assert_not_called()

    def test_snapshot_is_plain_dict(self):
        """snapshot must be a plain dict, not an ORM or mapping object."""
        agent_id = uuid4()
        skill = "place_order"
        args = _make_args()
        row = _make_envelope_mapping(agent_id, skill, enabled=True)

        mock_cm = _mock_db_session(row)

        with (
            patch("app.services.transactional.enforcement.get_sync_db", mock_cm),
            patch("app.services.transactional.enforcement.log"),
        ):
            snapshot, denial = asyncio.run(
                check_capability_envelope(agent_id, skill, args)
            )

        assert denial is None
        assert type(snapshot) is dict, f"snapshot must be dict, got {type(snapshot)}"
        assert "enabled" in snapshot
        assert "skill" in snapshot

    def test_refund_amount_cents_denial(self):
        """refund_amount_cents > max_amount_cents constraint must yield a denial."""
        agent_id = uuid4()
        skill = "issue_refund"
        args = _make_args(refund_amount_cents=6000)
        row = _make_envelope_mapping(
            agent_id, skill, enabled=True,
            constraints={"max_amount_cents": 5000}
        )

        mock_cm = _mock_db_session(row)

        with (
            patch("app.services.transactional.enforcement.get_sync_db", mock_cm),
            patch("app.services.transactional.enforcement.log") as mock_log,
        ):
            snapshot, denial = asyncio.run(
                check_capability_envelope(agent_id, skill, args)
            )

        assert denial is not None, "Expected denial for refund_amount_cents exceeding max_amount_cents"
        mock_log.warning.assert_called_once()


# ---------------------------------------------------------------------------
# Task 3 — write_audit_row tests
# ---------------------------------------------------------------------------

from app.services.transactional.audit import write_audit_row


class TestWriteAuditRow:
    """Tests for write_audit_row — always writes one row, on both success and error paths."""

    def test_success_path_writes_row(self):
        """write_audit_row writes a row when result is set and error is None."""
        agent_id = uuid4()
        conversation_id = uuid4()
        skill = "place_order"
        arguments = {"idempotency_key": str(uuid4()), "product_id": "SKU-001", "quantity": 1}
        result = {"message": "Order placed", "order_id": "ORD-1"}
        capability_snapshot = {"enabled": True, "skill": skill, "constraints": {}}

        session = MagicMock()
        session.__enter__ = lambda s: s
        session.__exit__ = MagicMock(return_value=False)

        @contextmanager
        def mock_db():
            yield session

        with patch("app.services.transactional.audit.get_sync_db", mock_db):
            asyncio.run(
                write_audit_row(
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    skill=skill,
                    arguments=arguments,
                    result=result,
                    actor_decision="",
                    actor_rationale="",
                    capability_snapshot=capability_snapshot,
                    latency_ms=42,
                    error=None,
                )
            )

        session.add.assert_called_once()
        session.commit.assert_called_once()

        added_row = session.add.call_args[0][0]
        assert added_row.result == result
        assert added_row.error is None
        assert added_row.actor_decision == ""
        assert added_row.actor_rationale == ""
        assert added_row.capability_snapshot == capability_snapshot
        assert added_row.latency_ms == 42

    def test_error_path_writes_row(self):
        """write_audit_row writes a row when result=None and error is set."""
        agent_id = uuid4()
        conversation_id = uuid4()
        skill = "place_order"
        arguments = {"idempotency_key": str(uuid4()), "product_id": "SKU-001", "quantity": 1}
        capability_snapshot = {"enabled": True, "skill": skill, "constraints": {}}

        session = MagicMock()
        session.__enter__ = lambda s: s
        session.__exit__ = MagicMock(return_value=False)

        @contextmanager
        def mock_db():
            yield session

        with patch("app.services.transactional.audit.get_sync_db", mock_db):
            asyncio.run(
                write_audit_row(
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    skill=skill,
                    arguments=arguments,
                    result=None,
                    actor_decision="",
                    actor_rationale="",
                    capability_snapshot=capability_snapshot,
                    latency_ms=99,
                    error="boom",
                )
            )

        session.add.assert_called_once()
        session.commit.assert_called_once()

        added_row = session.add.call_args[0][0]
        assert added_row.result is None
        assert added_row.error == "boom"

    def test_actor_fields_are_empty_strings(self):
        """actor_decision and actor_rationale are empty strings in Phase 14."""
        agent_id = uuid4()
        conversation_id = uuid4()
        skill = "place_order"
        arguments = {}
        capability_snapshot = {"enabled": True}

        session = MagicMock()
        session.__enter__ = lambda s: s
        session.__exit__ = MagicMock(return_value=False)

        @contextmanager
        def mock_db():
            yield session

        with patch("app.services.transactional.audit.get_sync_db", mock_db):
            asyncio.run(
                write_audit_row(
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    skill=skill,
                    arguments=arguments,
                    result={"ok": True},
                    actor_decision="",
                    actor_rationale="",
                    capability_snapshot=capability_snapshot,
                    latency_ms=10,
                    error=None,
                )
            )

        added_row = session.add.call_args[0][0]
        assert added_row.actor_decision == "", f"Expected '', got {added_row.actor_decision!r}"
        assert added_row.actor_rationale == "", f"Expected '', got {added_row.actor_rationale!r}"

    def test_capability_snapshot_roundtrips_as_dict(self):
        """capability_snapshot stored as plain dict round-trips correctly."""
        agent_id = uuid4()
        conversation_id = uuid4()
        skill = "place_order"
        snapshot = {"enabled": True, "rate_limit": "5/hour", "constraints": {"max_amount_cents": 5000}}

        session = MagicMock()
        session.__enter__ = lambda s: s
        session.__exit__ = MagicMock(return_value=False)

        @contextmanager
        def mock_db():
            yield session

        with patch("app.services.transactional.audit.get_sync_db", mock_db):
            asyncio.run(
                write_audit_row(
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    skill=skill,
                    arguments={},
                    result={"ok": True},
                    actor_decision="",
                    actor_rationale="",
                    capability_snapshot=snapshot,
                    latency_ms=5,
                    error=None,
                )
            )

        added_row = session.add.call_args[0][0]
        assert added_row.capability_snapshot == snapshot
        assert isinstance(added_row.capability_snapshot, dict)
