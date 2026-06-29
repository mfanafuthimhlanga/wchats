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
import json
from contextlib import contextmanager
from datetime import datetime, timezone
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
        """Redis INCR over limit must yield a rate_limit denial.

        Updated for IN-01 (14-07): INCR+EXPIRE now issued via a pipeline.
        Use pipeline mock so pipe.execute() returns [count, expire_result] with count=3 > max=2.
        """
        agent_id = uuid4()
        skill = "place_order"
        args = _make_args()
        row = _make_envelope_mapping(agent_id, skill, enabled=True, rate_limit="2/minute")

        mock_cm = _mock_db_session(row)
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        # count=3 > limit=2 → rate_limit denial
        mock_pipe.execute.return_value = [3, 1]

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


class TestSnapshotJsonSerializable:
    """Regression guard for CR-01.

    A real text() SELECT returns the envelope's UUID id/agent_id and TIMESTAMPTZ
    updated_at as native uuid.UUID / datetime objects. The snapshot is written to the
    tool_calls_audit.capability_snapshot JSONB column, where stock json.dumps would raise
    TypeError on those types at db.commit() — defeating AUD-01 on every pass and disabled
    denial path. The earlier fixtures used string values, masking the bug. These tests
    drive check_capability_envelope with native UUID/datetime values and assert the
    returned snapshot is json.dumps-able.
    """

    def _row_with_native_types(self, agent_id, skill, enabled=True):
        return {
            "id": uuid4(),
            "agent_id": agent_id,
            "skill": skill,
            "enabled": enabled,
            "rate_limit": None,
            "constraints": {},
            "requires_confirmation": False,
            "requires_identity_verification": False,
            "updated_at": datetime(2026, 6, 29, tzinfo=timezone.utc),
        }

    def test_pass_path_snapshot_is_json_serializable(self):
        agent_id = uuid4()
        skill = "place_order"
        row = self._row_with_native_types(agent_id, skill, enabled=True)
        mock_cm = _mock_db_session(row)

        with (
            patch("app.services.transactional.enforcement.get_sync_db", mock_cm),
            patch("app.services.transactional.enforcement.log"),
        ):
            snapshot, denial = asyncio.run(
                check_capability_envelope(agent_id, skill, _make_args())
            )

        assert denial is None
        # Must not raise — this is the CR-01 regression guard (datetime + UUID coercion).
        json.dumps(snapshot)

    def test_disabled_denial_snapshot_is_json_serializable(self):
        agent_id = uuid4()
        skill = "place_order"
        row = self._row_with_native_types(agent_id, skill, enabled=False)
        mock_cm = _mock_db_session(row)

        with (
            patch("app.services.transactional.enforcement.get_sync_db", mock_cm),
            patch("app.services.transactional.enforcement.log"),
        ):
            snapshot, denial = asyncio.run(
                check_capability_envelope(agent_id, skill, _make_args())
            )

        assert denial == "disabled"
        # The disabled-denial path returns the full row snapshot and write_audit_row
        # serialises it — must not raise.
        json.dumps(snapshot)


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


# ---------------------------------------------------------------------------
# Task 1 (14-07 RED) — TestEnforcementSplit
# Split: check_capability_access + apply_rate_and_constraint_checks + facade
# TLS (WR-04), pipeline (IN-01), falsy-zero (IN-02)
#
# Imports of the new symbols are DEFERRED inside each method so the existing
# TestCheckCapabilityEnvelope / TestSnapshotJsonSerializable tests remain
# unaffected at collection time if the symbols do not yet exist.
# ---------------------------------------------------------------------------


class TestEnforcementSplit:
    """Tests for the check_capability_access + apply_rate_and_constraint_checks split (14-07).

    Acceptance: all tests in this class fail (RED) until enforcement.py exposes the
    split symbols and their behaviours.
    """

    # ------------------------------------------------------------------
    # check_capability_access — side-effect-free authorization checks
    # ------------------------------------------------------------------

    def test_access_missing_row_returns_no_envelope_row(self):
        """check_capability_access on missing row → ({}, 'no_envelope_row'), no Redis call."""
        from app.services.transactional.enforcement import check_capability_access  # noqa: PLC0415

        agent_id = uuid4()
        skill = "place_order"
        mock_cm = _mock_db_session(None)
        mock_redis = MagicMock()

        with (
            patch("app.services.transactional.enforcement.get_sync_db", mock_cm),
            patch("app.services.transactional.enforcement._get_redis", return_value=mock_redis),
            patch("app.services.transactional.enforcement.log"),
        ):
            snapshot, denial = asyncio.run(check_capability_access(agent_id, skill))

        assert denial == "no_envelope_row"
        assert snapshot == {}
        mock_redis.incr.assert_not_called()
        mock_redis.pipeline.assert_not_called()

    def test_access_disabled_returns_snapshot_and_disabled(self):
        """check_capability_access on enabled=False → (snapshot, 'disabled'), no Redis call."""
        from app.services.transactional.enforcement import check_capability_access  # noqa: PLC0415

        agent_id = uuid4()
        skill = "place_order"
        row = _make_envelope_mapping(agent_id, skill, enabled=False)
        mock_cm = _mock_db_session(row)
        mock_redis = MagicMock()

        with (
            patch("app.services.transactional.enforcement.get_sync_db", mock_cm),
            patch("app.services.transactional.enforcement._get_redis", return_value=mock_redis),
            patch("app.services.transactional.enforcement.log"),
        ):
            snapshot, denial = asyncio.run(check_capability_access(agent_id, skill))

        assert denial == "disabled"
        assert isinstance(snapshot, dict)
        mock_redis.incr.assert_not_called()
        mock_redis.pipeline.assert_not_called()

    def test_access_enabled_returns_snapshot_no_denial_and_no_redis(self):
        """check_capability_access on enabled row → (snapshot, None); no INCR/pipeline call."""
        from app.services.transactional.enforcement import check_capability_access  # noqa: PLC0415

        agent_id = uuid4()
        skill = "place_order"
        row = _make_envelope_mapping(agent_id, skill, enabled=True, rate_limit="2/minute")
        mock_cm = _mock_db_session(row)
        mock_redis = MagicMock()

        with (
            patch("app.services.transactional.enforcement.get_sync_db", mock_cm),
            patch("app.services.transactional.enforcement._get_redis", return_value=mock_redis),
            patch("app.services.transactional.enforcement.log"),
        ):
            snapshot, denial = asyncio.run(check_capability_access(agent_id, skill))

        assert denial is None
        assert isinstance(snapshot, dict)
        # The authorization check must NOT touch Redis — rate limiting is a side effect
        mock_redis.incr.assert_not_called()
        mock_redis.pipeline.assert_not_called()

    # ------------------------------------------------------------------
    # apply_rate_and_constraint_checks — side-effecting half
    # ------------------------------------------------------------------

    def test_rate_limit_exceeded_returns_rate_limit(self):
        """apply_rate_and_constraint_checks at count > max → 'rate_limit'."""
        from app.services.transactional.enforcement import apply_rate_and_constraint_checks  # noqa: PLC0415

        agent_id = uuid4()
        skill = "place_order"
        snapshot = _make_envelope_mapping(agent_id, skill, enabled=True, rate_limit="2/minute")
        args = _make_args()

        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_pipe.execute.return_value = (3, 1)  # count=3 > max=2

        with (
            patch("app.services.transactional.enforcement._get_redis", return_value=mock_redis),
            patch("app.services.transactional.enforcement.log"),
        ):
            denial = asyncio.run(
                apply_rate_and_constraint_checks(agent_id, skill, snapshot, args)
            )

        assert denial == "rate_limit"

    def test_rate_limit_under_limit_returns_none(self):
        """apply_rate_and_constraint_checks under rate limit → None."""
        from app.services.transactional.enforcement import apply_rate_and_constraint_checks  # noqa: PLC0415

        agent_id = uuid4()
        skill = "place_order"
        snapshot = _make_envelope_mapping(agent_id, skill, enabled=True, rate_limit="5/minute")
        args = _make_args()

        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_pipe.execute.return_value = (2, 1)  # count=2 <= max=5

        with (
            patch("app.services.transactional.enforcement._get_redis", return_value=mock_redis),
            patch("app.services.transactional.enforcement.log"),
        ):
            denial = asyncio.run(
                apply_rate_and_constraint_checks(agent_id, skill, snapshot, args)
            )

        assert denial is None

    def test_max_amount_cents_exceeded_returns_denial(self):
        """apply_rate_and_constraint_checks with amount > max_amount_cents → 'max_amount_cents'."""
        from app.services.transactional.enforcement import apply_rate_and_constraint_checks  # noqa: PLC0415

        agent_id = uuid4()
        skill = "place_order"
        snapshot = _make_envelope_mapping(
            agent_id, skill, enabled=True, constraints={"max_amount_cents": 5000}
        )
        args = _make_args(amount_cents=9000)

        with (
            patch("app.services.transactional.enforcement._get_redis"),
            patch("app.services.transactional.enforcement.log"),
        ):
            denial = asyncio.run(
                apply_rate_and_constraint_checks(agent_id, skill, snapshot, args)
            )

        assert denial == "max_amount_cents"

    def test_in02_zero_amount_cents_is_not_falsy_fallthrough(self):
        """IN-02: amount_cents=0 is a valid real amount; refund_amount_cents must NOT be consulted.

        With the buggy `or`-based selection, 0 falls through to refund_amount_cents=999,
        which then exceeds max_amount_cents=100 → wrongly denies. The fix: explicit None check.
        """
        from app.services.transactional.enforcement import apply_rate_and_constraint_checks  # noqa: PLC0415

        agent_id = uuid4()
        skill = "place_order"
        snapshot = _make_envelope_mapping(
            agent_id, skill, enabled=True, constraints={"max_amount_cents": 100}
        )
        # amount_cents=0 is a valid amount (0 <= 100 → pass); refund_amount_cents=999 must
        # never be reached because amount_cents is not None
        args = _make_args(amount_cents=0, refund_amount_cents=999)

        with (
            patch("app.services.transactional.enforcement._get_redis"),
            patch("app.services.transactional.enforcement.log"),
        ):
            denial = asyncio.run(
                apply_rate_and_constraint_checks(agent_id, skill, snapshot, args)
            )

        assert denial is None, (
            "amount_cents=0 must be treated as a real amount (0 <= 100 → pass); "
            "refund_amount_cents=999 must NOT be consulted because amount_cents is not None."
        )

    def test_in01_rate_limit_uses_pipeline_not_separate_calls(self):
        """IN-01: INCR and EXPIRE are issued atomically via a Redis pipeline (not two top-level calls)."""
        from app.services.transactional.enforcement import apply_rate_and_constraint_checks  # noqa: PLC0415

        agent_id = uuid4()
        skill = "place_order"
        snapshot = _make_envelope_mapping(agent_id, skill, enabled=True, rate_limit="10/minute")
        args = _make_args()

        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_pipe.execute.return_value = (1, 1)  # count=1, under limit

        with (
            patch("app.services.transactional.enforcement._get_redis", return_value=mock_redis),
            patch("app.services.transactional.enforcement.log"),
        ):
            asyncio.run(
                apply_rate_and_constraint_checks(agent_id, skill, snapshot, args)
            )

        # IN-01: pipeline() must be used; execute() must be called on the pipeline
        mock_redis.pipeline.assert_called_once()
        mock_pipe.execute.assert_called_once()
        # INCR and EXPIRE must NOT appear as direct top-level redis client calls
        mock_redis.incr.assert_not_called()
        mock_redis.expire.assert_not_called()

    # ------------------------------------------------------------------
    # WR-04: Redis TLS verification posture
    # ------------------------------------------------------------------

    def test_wr04_rediss_url_with_insecure_false_uses_cert_required(self):
        """WR-04: rediss:// with REDIS_TLS_INSECURE=False must pass ssl_cert_reqs=ssl.CERT_REQUIRED."""
        import ssl
        import app.services.transactional.enforcement as enf_module  # noqa: PLC0415

        enf_module._rate_limit_redis = None  # reset singleton so factory re-runs

        captured: dict = {}

        def _fake_from_url(url, **kwargs):
            captured.update(kwargs)
            return MagicMock()

        with (
            patch("app.services.transactional.enforcement.redis_lib.from_url", side_effect=_fake_from_url),
            patch.object(enf_module.settings, "REDIS_URL", "rediss://example.upstash.io:6380/0"),
            patch.object(enf_module.settings, "REDIS_TLS_INSECURE", False, create=True),
        ):
            enf_module._get_redis()

        enf_module._rate_limit_redis = None  # cleanup

        assert captured.get("ssl_cert_reqs") == ssl.CERT_REQUIRED, (
            f"Expected ssl_cert_reqs=ssl.CERT_REQUIRED ({ssl.CERT_REQUIRED!r}), "
            f"got {captured.get('ssl_cert_reqs')!r}. "
            "WR-04: certificate verification must be ON by default for rediss:// connections."
        )

    def test_wr04_redis_tls_insecure_true_relaxes_and_warns(self):
        """WR-04: REDIS_TLS_INSECURE=True allows cert relaxation and MUST emit a warning log."""
        import app.services.transactional.enforcement as enf_module  # noqa: PLC0415

        enf_module._rate_limit_redis = None

        def _fake_from_url(url, **kwargs):
            return MagicMock()

        with (
            patch("app.services.transactional.enforcement.redis_lib.from_url", side_effect=_fake_from_url),
            patch.object(enf_module.settings, "REDIS_URL", "rediss://example.upstash.io:6380/0"),
            patch.object(enf_module.settings, "REDIS_TLS_INSECURE", True, create=True),
            patch("app.services.transactional.enforcement.log") as mock_log,
        ):
            enf_module._get_redis()

        enf_module._rate_limit_redis = None  # cleanup

        mock_log.warning.assert_called_once()
        event_name = mock_log.warning.call_args[0][0]
        assert "tls" in event_name.lower(), (
            f"Expected a TLS-related warning log event, got {event_name!r}. "
            "WR-04: relaxation must be logged as a warning."
        )

    # ------------------------------------------------------------------
    # Facade (check_capability_envelope) — contract preservation
    # ------------------------------------------------------------------

    def test_facade_no_row_returns_denial(self):
        """Facade: check_capability_envelope(missing row) → (empty_dict, non-None reason)."""
        agent_id = uuid4()
        skill = "place_order"
        args = _make_args()
        mock_cm = _mock_db_session(None)

        with (
            patch("app.services.transactional.enforcement.get_sync_db", mock_cm),
            patch("app.services.transactional.enforcement.log"),
        ):
            snapshot, denial = asyncio.run(
                check_capability_envelope(agent_id, skill, args)
            )

        assert denial is not None
        assert snapshot == {}

    def test_facade_disabled_returns_disabled_denial(self):
        """Facade: check_capability_envelope(disabled row) → (snapshot, 'disabled')."""
        agent_id = uuid4()
        skill = "place_order"
        args = _make_args()
        row = _make_envelope_mapping(agent_id, skill, enabled=False)
        mock_cm = _mock_db_session(row)

        with (
            patch("app.services.transactional.enforcement.get_sync_db", mock_cm),
            patch("app.services.transactional.enforcement.log"),
        ):
            snapshot, denial = asyncio.run(
                check_capability_envelope(agent_id, skill, args)
            )

        assert denial == "disabled"
        assert isinstance(snapshot, dict)

    def test_facade_rate_limit_returns_rate_limit_denial(self):
        """Facade: check_capability_envelope(rate-limited) → denial == 'rate_limit'."""
        agent_id = uuid4()
        skill = "place_order"
        args = _make_args()
        row = _make_envelope_mapping(agent_id, skill, enabled=True, rate_limit="2/minute")
        mock_cm = _mock_db_session(row)

        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_pipe.execute.return_value = (3, 1)

        with (
            patch("app.services.transactional.enforcement.get_sync_db", mock_cm),
            patch("app.services.transactional.enforcement._get_redis", return_value=mock_redis),
            patch("app.services.transactional.enforcement.log"),
        ):
            snapshot, denial = asyncio.run(
                check_capability_envelope(agent_id, skill, args)
            )

        assert denial == "rate_limit"

    def test_facade_full_pass_returns_none_denial(self):
        """Facade: check_capability_envelope(all checks pass) → (snapshot, None)."""
        agent_id = uuid4()
        skill = "place_order"
        args = _make_args(amount_cents=1000)
        row = _make_envelope_mapping(
            agent_id, skill, enabled=True,
            rate_limit="5/minute",
            constraints={"max_amount_cents": 5000},
        )
        mock_cm = _mock_db_session(row)

        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_pipe.execute.return_value = (1, 1)

        with (
            patch("app.services.transactional.enforcement.get_sync_db", mock_cm),
            patch("app.services.transactional.enforcement._get_redis", return_value=mock_redis),
            patch("app.services.transactional.enforcement.log"),
        ):
            snapshot, denial = asyncio.run(
                check_capability_envelope(agent_id, skill, args)
            )

        assert denial is None
        assert isinstance(snapshot, dict)
