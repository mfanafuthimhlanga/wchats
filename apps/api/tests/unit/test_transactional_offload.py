"""
Unit tests for WR-03: blocking DB/Redis calls offloaded off the event loop (14-07).

Verifies that:
  - write_audit_row (audit.py) executes its blocking DB commit via asyncio.to_thread.
  - check_capability_access (enforcement.py) executes its blocking DB read via asyncio.to_thread.
  - apply_rate_and_constraint_checks (enforcement.py) executes its blocking Redis pipeline
    via asyncio.to_thread.

Strategy:
  Each test replaces asyncio.to_thread with a tracking wrapper that ALSO executes the callable
  (so the function under test works end-to-end) and records how many times it was called.
  In RED these tests fail because:
    - write_audit_row uses get_sync_db directly (no to_thread) → assert fails.
    - check_capability_access / apply_rate_and_constraint_checks do not exist → ImportError.
  In GREEN the implementations use asyncio.to_thread → all assertions pass.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from uuid import uuid4

# ---------------------------------------------------------------------------
# Shared helpers (duplicated here to avoid test-file coupling)
# ---------------------------------------------------------------------------

def _make_envelope_dict(agent_id, skill, enabled=True, rate_limit=None, constraints=None):
    """Return a plain dict mimicking a row from capability_envelopes."""
    return {
        "id": str(uuid4()),
        "agent_id": str(agent_id),
        "skill": skill,
        "enabled": enabled,
        "rate_limit": rate_limit,
        "constraints": constraints or {},
        "requires_confirmation": False,
        "requires_identity_verification": False,
        "updated_at": "2026-06-29T00:00:00Z",
    }


def _mock_db_ctx(row):
    """Return a contextmanager that yields a session whose execute().mappings().first() returns row."""
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


def _make_to_thread_tracker():
    """Return (tracker_async_fn, calls_list).

    tracker_async_fn replaces asyncio.to_thread; it records the call AND executes
    the callable so the caller function works end-to-end.
    """
    calls: list = []
    _orig = asyncio.to_thread  # captured before patch is applied

    async def _tracked(fn, *args, **kwargs):
        calls.append(fn)
        return await _orig(fn, *args, **kwargs)

    return _tracked, calls


# ---------------------------------------------------------------------------
# write_audit_row — audit.py blocking DB offload (WR-03)
# ---------------------------------------------------------------------------

class TestAuditOffload:
    """write_audit_row must offload its blocking DB commit via asyncio.to_thread."""

    def test_write_audit_row_uses_asyncio_to_thread(self):
        """write_audit_row must call asyncio.to_thread for the blocking DB write.

        RED: write_audit_row calls get_sync_db directly (no to_thread) → fails.
        GREEN: write_audit_row uses asyncio.to_thread → passes.
        """
        from app.services.transactional.audit import write_audit_row  # noqa: PLC0415

        tracked, calls = _make_to_thread_tracker()
        session = MagicMock()
        session.__enter__ = lambda s: s
        session.__exit__ = MagicMock(return_value=False)

        @contextmanager
        def _mock_db():
            yield session

        with (
            patch("asyncio.to_thread", tracked),
            patch("app.services.transactional.audit.get_sync_db", _mock_db),
        ):
            asyncio.run(
                write_audit_row(
                    agent_id=uuid4(),
                    conversation_id=uuid4(),
                    skill="place_order",
                    arguments={"idempotency_key": str(uuid4())},
                    result={"ok": True},
                    actor_decision="",
                    actor_rationale="",
                    capability_snapshot={"enabled": True},
                    latency_ms=10,
                    error=None,
                )
            )

        assert len(calls) >= 1, (
            "write_audit_row must use asyncio.to_thread to offload the blocking DB commit (WR-03). "
            f"asyncio.to_thread was called {len(calls)} time(s); expected at least 1."
        )


# ---------------------------------------------------------------------------
# check_capability_access — enforcement.py blocking DB offload (WR-03)
# ---------------------------------------------------------------------------

class TestCheckCapabilityAccessOffload:
    """check_capability_access must offload its blocking DB read via asyncio.to_thread."""

    def test_check_capability_access_uses_asyncio_to_thread(self):
        """check_capability_access must call asyncio.to_thread for the blocking DB SELECT.

        RED: function does not exist → ImportError → test fails.
        GREEN: function exists and uses asyncio.to_thread → passes.
        """
        from app.services.transactional.enforcement import check_capability_access  # noqa: PLC0415

        agent_id = uuid4()
        skill = "place_order"
        row = _make_envelope_dict(agent_id, skill, enabled=True)
        mock_cm = _mock_db_ctx(row)

        tracked, calls = _make_to_thread_tracker()

        with (
            patch("asyncio.to_thread", tracked),
            patch("app.services.transactional.enforcement.get_sync_db", mock_cm),
            patch("app.services.transactional.enforcement.log"),
        ):
            snapshot, denial = asyncio.run(check_capability_access(agent_id, skill))

        assert len(calls) >= 1, (
            "check_capability_access must use asyncio.to_thread for the blocking DB SELECT (WR-03). "
            f"asyncio.to_thread was called {len(calls)} time(s); expected at least 1."
        )
        # Smoke-check: function still works correctly through the offload
        assert denial is None
        assert isinstance(snapshot, dict)


# ---------------------------------------------------------------------------
# apply_rate_and_constraint_checks — enforcement.py blocking Redis offload (WR-03)
# ---------------------------------------------------------------------------

class TestApplyRateOffload:
    """apply_rate_and_constraint_checks must offload its blocking Redis pipeline via asyncio.to_thread."""

    def test_apply_rate_uses_asyncio_to_thread(self):
        """apply_rate_and_constraint_checks must call asyncio.to_thread for the Redis pipeline.

        RED: function does not exist → ImportError → test fails.
        GREEN: function exists and uses asyncio.to_thread → passes.
        """
        from app.services.transactional.enforcement import apply_rate_and_constraint_checks  # noqa: PLC0415

        agent_id = uuid4()
        skill = "place_order"
        snapshot = _make_envelope_dict(agent_id, skill, enabled=True, rate_limit="5/minute")
        args = MagicMock()
        args.amount_cents = None
        args.refund_amount_cents = None

        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_pipe.execute.return_value = (1, 1)  # count=1, within limit

        tracked, calls = _make_to_thread_tracker()

        with (
            patch("asyncio.to_thread", tracked),
            patch("app.services.transactional.enforcement._get_redis", return_value=mock_redis),
            patch("app.services.transactional.enforcement.log"),
        ):
            denial = asyncio.run(
                apply_rate_and_constraint_checks(agent_id, skill, snapshot, args)
            )

        assert len(calls) >= 1, (
            "apply_rate_and_constraint_checks must use asyncio.to_thread for the blocking Redis pipeline (WR-03). "
            f"asyncio.to_thread was called {len(calls)} time(s); expected at least 1."
        )
        assert denial is None
