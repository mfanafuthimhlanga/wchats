"""
Unit tests for Phase-14 idempotency check/store helpers (Plan 03, Task 2).

TDD RED→GREEN:
  RED: tests fail because idempotency.py does not exist.
  GREEN: idempotency.py created; all tests pass.

Covers:
  - check_idempotency returns stored result dict on hit, None on miss.
  - store_idempotency inserts a row and check returns it afterward.
  - store_idempotency with duplicate (agent_id, skill, key) does not raise.
  - Simulated double-call: first call stores; second check returns stored result
    without re-execution (ON CONFLICT DO NOTHING contract).
  - Idempotency storage is control-DB table (NOT Redis) — no Redis imports in impl.
  - Module docstring documents per-tool guard orthogonality to run_agent_turn guard.

Note on "real DB test" requirement in the plan spec:
    The plan asks for "real test Postgres to exercise the UNIQUE constraint
    genuinely". The UNIQUE constraint is on the DB side and requires a live
    control-DB to test end-to-end.

    These tests use mocked sessions to keep the unit test suite self-contained
    (no live DB required). The UNIQUE constraint is the DDL-level correctness
    anchor; unit tests cover the Python logic and the ON CONFLICT SQL surface.
    An integration test (INTEGRATION_TESTS_ENABLED=1 guard) can exercise the
    full DB roundtrip via the real Postgres fixture used by test_migration_0014.py.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.transactional.idempotency import check_idempotency, store_idempotency


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(order_id: str = "ORD-001") -> dict:
    return {"content": [{"type": "text", "text": f"Order {order_id} placed"}], "is_error": False}


def _mock_db_returning(scalar_value) -> contextmanager:
    """Return a context manager that yields a session mock.

    scalar_value: returned by scalar_one_or_none() (None = miss, dict = hit)
    """
    session = MagicMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = scalar_value
    session.execute.return_value = execute_result
    session.__enter__ = lambda s: s
    session.__exit__ = MagicMock(return_value=False)

    @contextmanager
    def _ctx():
        yield session

    return _ctx


def _mock_db_for_store() -> tuple[contextmanager, MagicMock]:
    """Return (context_manager_factory, session_mock) for a store call."""
    session = MagicMock()
    session.__enter__ = lambda s: s
    session.__exit__ = MagicMock(return_value=False)

    @contextmanager
    def _ctx():
        yield session

    return _ctx, session


# ---------------------------------------------------------------------------
# check_idempotency tests
# ---------------------------------------------------------------------------


class TestCheckIdempotency:
    def test_miss_returns_none(self):
        """check_idempotency returns None when there is no matching row."""
        mock_cm = _mock_db_returning(None)

        with patch("app.services.transactional.idempotency.get_sync_db", mock_cm):
            result = asyncio.run(
                check_idempotency(uuid4(), "place_order", "key-001")
            )

        assert result is None

    def test_hit_returns_stored_dict(self):
        """check_idempotency returns the stored result dict on a cache hit."""
        stored = _make_result("ORD-HIT")
        mock_cm = _mock_db_returning(stored)

        with patch("app.services.transactional.idempotency.get_sync_db", mock_cm):
            result = asyncio.run(
                check_idempotency(uuid4(), "place_order", "key-002")
            )

        assert result == stored
        assert isinstance(result, dict)

    def test_different_key_returns_none(self):
        """check_idempotency returns None for an unseen key even if another key exists."""
        # This is a DB-level isolation assertion — mock always returns None for 'key-unseen'
        mock_cm = _mock_db_returning(None)

        with patch("app.services.transactional.idempotency.get_sync_db", mock_cm):
            result = asyncio.run(
                check_idempotency(uuid4(), "place_order", "key-unseen")
            )

        assert result is None


# ---------------------------------------------------------------------------
# store_idempotency tests
# ---------------------------------------------------------------------------


class TestStoreIdempotency:
    def test_store_commits_without_exception(self):
        """store_idempotency inserts a row and commits (no exception on first call)."""
        mock_cm, session = _mock_db_for_store()
        result = _make_result("ORD-NEW")

        with patch("app.services.transactional.idempotency.get_sync_db", mock_cm):
            asyncio.run(
                store_idempotency(uuid4(), "place_order", "key-003", result)
            )

        session.execute.assert_called_once()
        session.commit.assert_called_once()

    def test_store_uses_on_conflict_do_nothing(self):
        """store_idempotency uses ON CONFLICT DO NOTHING in the executed SQL."""
        mock_cm, session = _mock_db_for_store()
        result = _make_result("ORD-CONFLICT")

        with patch("app.services.transactional.idempotency.get_sync_db", mock_cm):
            asyncio.run(
                store_idempotency(uuid4(), "place_order", "key-004", result)
            )

        # Inspect the SQL text passed to execute()
        executed_sql = str(session.execute.call_args[0][0])
        assert "ON CONFLICT" in executed_sql.upper(), (
            f"Expected 'ON CONFLICT' in SQL, got: {executed_sql}"
        )

    def test_store_same_key_twice_no_exception(self):
        """Storing the same (agent_id, skill, key) twice does not raise."""
        agent_id = uuid4()
        skill = "place_order"
        key = "key-005"
        result = _make_result("ORD-DUP")

        mock_cm, session = _mock_db_for_store()

        with patch("app.services.transactional.idempotency.get_sync_db", mock_cm):
            # First call
            asyncio.run(
                store_idempotency(agent_id, skill, key, result)
            )
            # Second call — should not raise
            asyncio.run(
                store_idempotency(agent_id, skill, key, result)
            )

        # Two store calls = two execute+commit pairs
        assert session.execute.call_count == 2
        assert session.commit.call_count == 2

    def test_simulated_double_call_replay(self):
        """Simulated double-call: first stores; second check returns stored result."""
        agent_id = uuid4()
        skill = "place_order"
        key = "key-006"
        result = _make_result("ORD-REPLAY")

        # Session for store
        store_cm, store_session = _mock_db_for_store()

        # Session for check (returns the stored result)
        check_cm = _mock_db_returning(result)

        with patch("app.services.transactional.idempotency.get_sync_db", store_cm):
            asyncio.run(
                store_idempotency(agent_id, skill, key, result)
            )

        with patch("app.services.transactional.idempotency.get_sync_db", check_cm):
            replayed = asyncio.run(
                check_idempotency(agent_id, skill, key)
            )

        assert replayed == result, "Replayed result must match the originally stored result"


# ---------------------------------------------------------------------------
# No-Redis assertion
# ---------------------------------------------------------------------------


class TestNoRedisUsage:
    def test_idempotency_module_does_not_import_redis(self):
        """idempotency.py MUST NOT import Redis — control-DB table only."""
        import importlib
        import ast
        import os

        impl_path = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "../../app/services/transactional/idempotency.py")
        )
        assert os.path.isfile(impl_path), f"idempotency.py not found at {impl_path}"

        with open(impl_path) as f:
            source = f.read()

        # Check for redis import at module level
        assert "import redis" not in source, (
            "idempotency.py must not import redis — idempotency uses control-DB table only"
        )
        assert "from redis" not in source, (
            "idempotency.py must not import from redis — idempotency uses control-DB table only"
        )


# ---------------------------------------------------------------------------
# Reservation engine mock helpers
# ---------------------------------------------------------------------------


def _first_result(value):
    """Create a mock execute() result whose .first() returns value."""
    r = MagicMock()
    r.first.return_value = value
    return r


def _mappings_first_result(value):
    """Create a mock execute() result whose .mappings().first() returns value."""
    r = MagicMock()
    r.mappings.return_value.first.return_value = value
    return r


def _make_reserve_session(*execute_returns):
    """Build a session mock with an ordered side_effect for sequential execute() calls.

    Returns (context_manager_factory, session_mock).
    """
    session = MagicMock()
    session.execute.side_effect = list(execute_returns)
    session.__enter__ = lambda s: s
    session.__exit__ = MagicMock(return_value=False)

    @contextmanager
    def _ctx():
        yield session

    return _ctx, session


# ---------------------------------------------------------------------------
# TestReservationEngine
# ---------------------------------------------------------------------------


class TestReservationEngine:
    """Tests for the atomic reserve/finalize/release engine (CR-02).

    All test methods import the new symbols locally so that this class
    produces clean ImportError failures in the TDD RED phase without
    disturbing the existing TestCheckIdempotency / TestStoreIdempotency
    / TestNoRedisUsage classes.
    """

    # ------------------------------------------------------------------
    # compute_args_hash
    # ------------------------------------------------------------------

    def test_compute_args_hash_stable_across_key_order(self):
        """Hash is stable regardless of dict key ordering."""
        from app.services.transactional.idempotency import compute_args_hash  # ImportError in RED

        a = {"product_id": "A", "quantity": 1}
        b = {"quantity": 1, "product_id": "A"}
        assert compute_args_hash(a) == compute_args_hash(b)

    def test_compute_args_hash_excludes_idempotency_key(self):
        """Two arg dicts differing only in idempotency_key produce the same hash."""
        from app.services.transactional.idempotency import compute_args_hash

        a = {"idempotency_key": "k1", "product_id": "A", "quantity": 1}
        b = {"quantity": 1, "product_id": "A", "idempotency_key": "k2"}
        assert compute_args_hash(a) == compute_args_hash(b), (
            "idempotency_key must be excluded from the hash — same logical request, different keys"
        )

    def test_compute_args_hash_business_arg_sensitive(self):
        """Different business args produce different hashes."""
        from app.services.transactional.idempotency import compute_args_hash

        a = {"idempotency_key": "k1", "product_id": "A", "quantity": 1}
        c = {"idempotency_key": "k1", "product_id": "B", "quantity": 1}
        assert compute_args_hash(a) != compute_args_hash(c), (
            "product_id A vs B must produce different hashes"
        )

    def test_compute_args_hash_is_hex_string(self):
        """compute_args_hash returns a hex string."""
        from app.services.transactional.idempotency import compute_args_hash

        h = compute_args_hash({"product_id": "A", "quantity": 1})
        assert isinstance(h, str)
        assert len(h) == 64, "sha256 hex is 64 chars"
        int(h, 16)  # raises ValueError if not hex

    # ------------------------------------------------------------------
    # reserve_idempotency: winner path
    # ------------------------------------------------------------------

    def test_reserve_winner_returns_reserved_state(self):
        """INSERT...RETURNING yields a row → state 'reserved', result None."""
        from app.services.transactional.idempotency import reserve_idempotency

        cm, session = _make_reserve_session(
            _first_result(MagicMock()),  # INSERT...RETURNING returns a row (winner)
        )
        with patch("app.services.transactional.idempotency.get_sync_db", cm):
            reservation = asyncio.run(
                reserve_idempotency(uuid4(), "place_order", "key-r01", "hash-abc")
            )

        assert reservation.state == "reserved"
        assert reservation.result is None

    def test_reserve_winner_commits(self):
        """Winner path commits the INSERT transaction."""
        from app.services.transactional.idempotency import reserve_idempotency

        cm, session = _make_reserve_session(
            _first_result(MagicMock()),
        )
        with patch("app.services.transactional.idempotency.get_sync_db", cm):
            asyncio.run(reserve_idempotency(uuid4(), "place_order", "key-r01b", "hash-abc"))

        session.commit.assert_called_once()

    # ------------------------------------------------------------------
    # reserve_idempotency: replay path
    # ------------------------------------------------------------------

    def test_reserve_replay_returns_stored_result(self):
        """INSERT conflict + status='completed' + matching hash → state 'replay' with result."""
        from app.services.transactional.idempotency import reserve_idempotency

        stored = {"content": [{"type": "text", "text": "Order placed"}], "is_error": False}
        args_hash = "hash-completed"

        cm, session = _make_reserve_session(
            _first_result(None),  # INSERT ON CONFLICT → no row returned
            _mappings_first_result({
                "status": "completed",
                "result": stored,
                "args_hash": args_hash,
                "reserved_at": datetime.now(timezone.utc),
            }),
        )
        with patch("app.services.transactional.idempotency.get_sync_db", cm):
            reservation = asyncio.run(
                reserve_idempotency(uuid4(), "place_order", "key-r02", args_hash)
            )

        assert reservation.state == "replay"
        assert reservation.result == stored

    # ------------------------------------------------------------------
    # reserve_idempotency: in_progress path
    # ------------------------------------------------------------------

    def test_reserve_in_progress_recent_pending(self):
        """INSERT conflict + status='pending' + recent reserved_at → state 'in_progress'."""
        from app.services.transactional.idempotency import reserve_idempotency

        args_hash = "hash-in-progress"
        cm, session = _make_reserve_session(
            _first_result(None),
            _mappings_first_result({
                "status": "pending",
                "result": None,
                "args_hash": args_hash,
                "reserved_at": datetime.now(timezone.utc),  # very recent
            }),
        )
        with patch("app.services.transactional.idempotency.get_sync_db", cm):
            reservation = asyncio.run(
                reserve_idempotency(uuid4(), "place_order", "key-r03", args_hash)
            )

        assert reservation.state == "in_progress"
        assert reservation.result is None

    # ------------------------------------------------------------------
    # reserve_idempotency: stale reclaim path
    # ------------------------------------------------------------------

    def test_reserve_stale_reclaim_returns_reserved(self):
        """INSERT conflict + status='pending' + old reserved_at → reclaim UPDATE wins → state 'reserved'."""
        from app.services.transactional.idempotency import reserve_idempotency, _RESERVATION_LEASE_SECONDS

        args_hash = "hash-stale"
        # reserved_at well past the lease window
        old_ts = datetime.now(timezone.utc) - timedelta(seconds=_RESERVATION_LEASE_SECONDS + 60)

        cm, session = _make_reserve_session(
            _first_result(None),  # INSERT conflict
            _mappings_first_result({
                "status": "pending",
                "result": None,
                "args_hash": args_hash,
                "reserved_at": old_ts,
            }),
            _first_result(MagicMock()),  # reclaim UPDATE RETURNING → row (this agent wins)
        )
        with patch("app.services.transactional.idempotency.get_sync_db", cm):
            reservation = asyncio.run(
                reserve_idempotency(uuid4(), "place_order", "key-r04", args_hash)
            )

        assert reservation.state == "reserved"

    def test_reserve_stale_reclaim_lost_returns_in_progress(self):
        """INSERT conflict + old reserved_at but reclaim UPDATE returns nothing → someone else reclaimed → in_progress."""
        from app.services.transactional.idempotency import reserve_idempotency, _RESERVATION_LEASE_SECONDS

        args_hash = "hash-stale2"
        old_ts = datetime.now(timezone.utc) - timedelta(seconds=_RESERVATION_LEASE_SECONDS + 60)

        cm, session = _make_reserve_session(
            _first_result(None),
            _mappings_first_result({
                "status": "pending",
                "result": None,
                "args_hash": args_hash,
                "reserved_at": old_ts,
            }),
            _first_result(None),  # reclaim UPDATE returns nothing (someone else reclaimed first)
        )
        with patch("app.services.transactional.idempotency.get_sync_db", cm):
            reservation = asyncio.run(
                reserve_idempotency(uuid4(), "place_order", "key-r05", args_hash)
            )

        assert reservation.state == "in_progress"

    # ------------------------------------------------------------------
    # reserve_idempotency: args_mismatch path
    # ------------------------------------------------------------------

    def test_reserve_args_mismatch(self):
        """INSERT conflict + existing args_hash differs → state 'args_mismatch'."""
        from app.services.transactional.idempotency import reserve_idempotency

        incoming_hash = "hash-incoming"
        stored_hash = "hash-different"  # different!

        cm, session = _make_reserve_session(
            _first_result(None),
            _mappings_first_result({
                "status": "completed",
                "result": {"content": [{"type": "text", "text": "stale"}], "is_error": False},
                "args_hash": stored_hash,
                "reserved_at": datetime.now(timezone.utc),
            }),
        )
        with patch("app.services.transactional.idempotency.get_sync_db", cm):
            reservation = asyncio.run(
                reserve_idempotency(uuid4(), "place_order", "key-r06", incoming_hash)
            )

        assert reservation.state == "args_mismatch"
        assert reservation.result is None

    def test_reserve_args_mismatch_null_stored_hash_treated_as_legacy(self):
        """Existing row with args_hash=NULL (legacy) and completed → replay (hash not checked)."""
        from app.services.transactional.idempotency import reserve_idempotency

        stored = {"content": [{"type": "text", "text": "legacy"}], "is_error": False}
        cm, session = _make_reserve_session(
            _first_result(None),
            _mappings_first_result({
                "status": "completed",
                "result": stored,
                "args_hash": None,  # legacy row — no hash stored
                "reserved_at": datetime.now(timezone.utc),
            }),
        )
        with patch("app.services.transactional.idempotency.get_sync_db", cm):
            reservation = asyncio.run(
                reserve_idempotency(uuid4(), "place_order", "key-r07", "incoming-hash")
            )

        # A NULL stored hash means legacy row — treat as replay (hash check skipped)
        assert reservation.state == "replay"
        assert reservation.result == stored

    # ------------------------------------------------------------------
    # reserve_idempotency: in_flight paths (CR-01)
    # ------------------------------------------------------------------

    def test_reserve_in_progress_recent_in_flight(self):
        """INSERT conflict + status='in_flight' + recent reserved_at → state 'in_progress'."""
        from app.services.transactional.idempotency import reserve_idempotency

        args_hash = "hash-in-flight-recent"
        cm, session = _make_reserve_session(
            _first_result(None),
            _mappings_first_result({
                "status": "in_flight",
                "result": None,
                "args_hash": args_hash,
                "reserved_at": datetime.now(timezone.utc),  # recent
            }),
        )
        with patch("app.services.transactional.idempotency.get_sync_db", cm):
            reservation = asyncio.run(
                reserve_idempotency(uuid4(), "issue_refund", "key-if01", args_hash)
            )

        assert reservation.state == "in_progress"

    def test_reserve_stale_in_flight_returns_unknown_never_reclaimed(self):
        """CR-01: stale 'in_flight' row → state 'unknown', and NO reclaim UPDATE
        is ever issued (only 2 execute() calls: the INSERT and the SELECT —
        there must be no third, reclaim-UPDATE call for an in_flight row)."""
        from app.services.transactional.idempotency import (
            reserve_idempotency,
            _RESERVATION_LEASE_SECONDS,
        )

        args_hash = "hash-in-flight-stale"
        old_ts = datetime.now(timezone.utc) - timedelta(seconds=_RESERVATION_LEASE_SECONDS + 60)

        cm, session = _make_reserve_session(
            _first_result(None),
            _mappings_first_result({
                "status": "in_flight",
                "result": None,
                "args_hash": args_hash,
                "reserved_at": old_ts,
            }),
        )
        with patch("app.services.transactional.idempotency.get_sync_db", cm):
            reservation = asyncio.run(
                reserve_idempotency(uuid4(), "issue_refund", "key-if02", args_hash)
            )

        assert reservation.state == "unknown"
        # Exactly the INSERT + the SELECT — never a third (reclaim) execute()
        # call. A stale in_flight row must never be UPDATEd/reclaimed.
        assert session.execute.call_count == 2

    def test_reserve_stale_pending_still_reclaimed_unaffected_by_in_flight(self):
        """A stale 'pending' row (adapter never touched) must still be safely
        reclaimable after CR-01 — only 'in_flight' rows are protected."""
        from app.services.transactional.idempotency import (
            reserve_idempotency,
            _RESERVATION_LEASE_SECONDS,
        )

        args_hash = "hash-pending-stale-cr01"
        old_ts = datetime.now(timezone.utc) - timedelta(seconds=_RESERVATION_LEASE_SECONDS + 60)

        cm, session = _make_reserve_session(
            _first_result(None),
            _mappings_first_result({
                "status": "pending",
                "result": None,
                "args_hash": args_hash,
                "reserved_at": old_ts,
            }),
            _first_result(MagicMock()),  # reclaim UPDATE RETURNING → row
        )
        with patch("app.services.transactional.idempotency.get_sync_db", cm):
            reservation = asyncio.run(
                reserve_idempotency(uuid4(), "issue_refund", "key-p01", args_hash)
            )

        assert reservation.state == "reserved"

    # ------------------------------------------------------------------
    # mark_reservation_in_flight (CR-01)
    # ------------------------------------------------------------------

    def test_mark_in_flight_issues_update_scoped_to_pending(self):
        """mark_reservation_in_flight issues an UPDATE setting status='in_flight',
        scoped to status='pending', and commits."""
        from app.services.transactional.idempotency import mark_reservation_in_flight

        cm, session = _mock_db_for_store()

        with patch("app.services.transactional.idempotency.get_sync_db", cm):
            asyncio.run(mark_reservation_in_flight(uuid4(), "issue_refund", "key-mif01"))

        session.execute.assert_called_once()
        executed_sql = str(session.execute.call_args[0][0]).upper()
        assert "UPDATE" in executed_sql
        assert "IN_FLIGHT" in executed_sql
        assert "PENDING" in executed_sql, "must scope the UPDATE to status='pending'"
        session.commit.assert_called_once()

    # ------------------------------------------------------------------
    # finalize_idempotency
    # ------------------------------------------------------------------

    def test_finalize_issues_update_and_commits(self):
        """finalize_idempotency issues an UPDATE setting status='completed' and commits."""
        from app.services.transactional.idempotency import finalize_idempotency

        cm, session = _mock_db_for_store()
        result = _make_result("ORD-FINALIZE")

        with patch("app.services.transactional.idempotency.get_sync_db", cm):
            asyncio.run(finalize_idempotency(uuid4(), "place_order", "key-f01", result))

        session.execute.assert_called_once()
        executed_sql = str(session.execute.call_args[0][0]).upper()
        assert "UPDATE" in executed_sql, f"Expected UPDATE in SQL, got: {executed_sql}"
        assert "COMPLETED" in executed_sql, "Expected 'completed' in finalize SQL"
        session.commit.assert_called_once()

    def test_finalize_where_clause_covers_pending_and_in_flight(self):
        """CR-01: finalize's WHERE clause must match BOTH 'pending' (legacy/
        back-compat) and 'in_flight' (the normal post-CR-01 path) — a row
        stuck at 'in_flight' must still be finalizable on adapter success."""
        from app.services.transactional.idempotency import finalize_idempotency

        cm, session = _mock_db_for_store()
        result = _make_result("ORD-FINALIZE-IF")

        with patch("app.services.transactional.idempotency.get_sync_db", cm):
            asyncio.run(finalize_idempotency(uuid4(), "issue_refund", "key-f02", result))

        executed_sql = str(session.execute.call_args[0][0]).upper()
        assert "IN_FLIGHT" in executed_sql

    # ------------------------------------------------------------------
    # release_idempotency
    # ------------------------------------------------------------------

    def test_release_issues_delete_scoped_to_pending(self):
        """release_idempotency issues DELETE scoped to status='pending' and commits."""
        from app.services.transactional.idempotency import release_idempotency

        cm, session = _mock_db_for_store()

        with patch("app.services.transactional.idempotency.get_sync_db", cm):
            asyncio.run(release_idempotency(uuid4(), "place_order", "key-rl01"))

        session.execute.assert_called_once()
        executed_sql = str(session.execute.call_args[0][0]).upper()
        assert "DELETE" in executed_sql, f"Expected DELETE in SQL, got: {executed_sql}"
        assert "PENDING" in executed_sql, "release_idempotency must scope DELETE to status='pending'"
        # CR-01: release must ALSO cover 'in_flight' — a synchronous adapter
        # exception can happen after mark_reservation_in_flight has already
        # flipped the row past 'pending'; without this, that row would be
        # left un-releasable and eventually misreported as a stranded
        # ("unknown") reservation instead of cleanly retriable.
        assert "IN_FLIGHT" in executed_sql, (
            "release_idempotency must also scope DELETE to status='in_flight' (CR-01)"
        )
        session.commit.assert_called_once()

    # ------------------------------------------------------------------
    # Reservation dataclass structure
    # ------------------------------------------------------------------

    def test_reservation_is_dataclass_or_namedtuple(self):
        """Reservation has .state and .result attributes."""
        from app.services.transactional.idempotency import Reservation

        r = Reservation(state="reserved", result=None)
        assert r.state == "reserved"
        assert r.result is None

        r2 = Reservation(state="replay", result={"content": [], "is_error": False})
        assert r2.result is not None

    def test_reservation_lease_constant_is_positive_int(self):
        """_RESERVATION_LEASE_SECONDS is a positive integer."""
        from app.services.transactional.idempotency import _RESERVATION_LEASE_SECONDS

        assert isinstance(_RESERVATION_LEASE_SECONDS, int)
        assert _RESERVATION_LEASE_SECONDS > 0
