"""
Unit tests for app.services.events.emit() — RED phase (TDD).

Tests:
    - emit() inserts one row into job_events via db.add() + db.commit()
    - emit() publishes to Redis channel job_events:{job_id} exactly once
    - The Redis message is JSON with keys "event_type" and "payload"
    - The payload always contains key "at" with UTC ISO timestamp
    - emit() with payload=None treats it as {}
    - emit() does not mutate the caller's original dict
"""

# Set minimal env so settings module can load.
# Generate a valid Fernet key (URL-safe base64-encoded 32 bytes).
import base64
import json
import os
import threading
from datetime import datetime
from unittest.mock import MagicMock
from uuid import uuid4

_TEST_KEY = base64.urlsafe_b64encode(os.urandom(32)).decode()
os.environ.setdefault("NEON_ENCRYPTION_KEY", _TEST_KEY)
os.environ.setdefault("NEON_API_KEY", "test_neon")
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://user:pass@localhost/testdb")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://user:pass@localhost/testdb")
os.environ.setdefault("ADMIN_KEY", "test_admin")

from app.services.events import emit, emit_async  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mocks():
    """Return (job_id, mock_db, mock_redis) with fresh mocks."""
    return uuid4(), MagicMock(), MagicMock()


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------


class TestEmitDbPersistence:
    def test_calls_db_add_once(self):
        job_id, mock_db, mock_redis = make_mocks()
        emit(job_id, "job.started", {}, mock_db, mock_redis)
        mock_db.add.assert_called_once()

    def test_calls_db_commit_once(self):
        job_id, mock_db, mock_redis = make_mocks()
        emit(job_id, "job.started", {}, mock_db, mock_redis)
        mock_db.commit.assert_called_once()

    def test_db_add_receives_job_event_instance(self):
        """db.add() must be called with a JobEvent ORM instance."""
        from app.models.job_event import JobEvent

        job_id, mock_db, mock_redis = make_mocks()
        emit(job_id, "job.started", {}, mock_db, mock_redis)
        added_obj = mock_db.add.call_args[0][0]
        assert isinstance(added_obj, JobEvent)

    def test_job_event_has_correct_job_id(self):

        job_id, mock_db, mock_redis = make_mocks()
        emit(job_id, "job.started", {"k": "v"}, mock_db, mock_redis)
        added_obj = mock_db.add.call_args[0][0]
        assert added_obj.job_id == job_id

    def test_job_event_has_correct_event_type(self):

        job_id, mock_db, mock_redis = make_mocks()
        emit(job_id, "neon.project.ready", {}, mock_db, mock_redis)
        added_obj = mock_db.add.call_args[0][0]
        assert added_obj.event_type == "neon.project.ready"


# ---------------------------------------------------------------------------
# Redis publish
# ---------------------------------------------------------------------------


class TestEmitRedisPublish:
    def test_calls_redis_publish_once(self):
        job_id, mock_db, mock_redis = make_mocks()
        emit(job_id, "job.started", {}, mock_db, mock_redis)
        mock_redis.publish.assert_called_once()

    def test_redis_channel_name(self):
        job_id, mock_db, mock_redis = make_mocks()
        emit(job_id, "job.started", {}, mock_db, mock_redis)
        channel = mock_redis.publish.call_args[0][0]
        assert channel == f"job_events:{job_id}"

    def test_redis_message_is_valid_json(self):
        job_id, mock_db, mock_redis = make_mocks()
        emit(job_id, "job.started", {}, mock_db, mock_redis)
        message_str = mock_redis.publish.call_args[0][1]
        # Should not raise
        parsed = json.loads(message_str)
        assert isinstance(parsed, dict)

    def test_redis_message_has_event_type(self):
        job_id, mock_db, mock_redis = make_mocks()
        emit(job_id, "migrations.running", {}, mock_db, mock_redis)
        message = json.loads(mock_redis.publish.call_args[0][1])
        assert message["event_type"] == "migrations.running"

    def test_redis_message_has_payload(self):
        job_id, mock_db, mock_redis = make_mocks()
        emit(job_id, "job.started", {"project_id": "abc"}, mock_db, mock_redis)
        message = json.loads(mock_redis.publish.call_args[0][1])
        assert "payload" in message


# ---------------------------------------------------------------------------
# str job_id — the form every Celery worker actually uses
# ---------------------------------------------------------------------------


class TestEmitAcceptsStringJobId:
    """emit() is declared ``job_id: UUID | str`` and both forms must agree.

    Celery task arguments are JSON, so every task in app/worker/tasks/ holds
    job_id as a ``str`` and calls emit() with it; API-side callers pass the ORM
    ``Job.id``, a ``UUID``.  Before these tests the str form — the one the entire
    worker fleet uses — had no unit coverage at all, and the annotation claimed
    UUID only.  If the two forms ever stop producing the same channel name, SSE
    subscribers (app/services/sse.py:79 subscribes with the UUID form) silently
    stop receiving worker events.
    """

    def test_str_and_uuid_produce_the_same_channel(self):
        job_uuid, mock_db, mock_redis = make_mocks()
        emit(job_uuid, "job.started", {}, mock_db, mock_redis)
        uuid_channel = mock_redis.publish.call_args[0][0]

        _, mock_db2, mock_redis2 = make_mocks()
        emit(str(job_uuid), "job.started", {}, mock_db2, mock_redis2)
        str_channel = mock_redis2.publish.call_args[0][0]

        assert str_channel == uuid_channel
        assert str_channel == f"job_events:{job_uuid}"

    def test_str_job_id_reaches_the_row_unchanged(self):
        job_uuid, mock_db, mock_redis = make_mocks()
        emit(str(job_uuid), "job.started", {}, mock_db, mock_redis)
        added_obj = mock_db.add.call_args[0][0]
        # Handed to SQLAlchemy as-is; the Uuid column coerces the canonical form.
        assert added_obj.job_id == str(job_uuid)

    def test_str_job_id_still_publishes_and_commits_once(self):
        job_uuid, mock_db, mock_redis = make_mocks()
        emit(str(job_uuid), "job.started", {}, mock_db, mock_redis)
        mock_redis.publish.assert_called_once()
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# "at" timestamp injection
# ---------------------------------------------------------------------------


class TestEmitTimestamp:
    def test_payload_contains_at_key(self):
        job_id, mock_db, mock_redis = make_mocks()
        emit(job_id, "job.started", {}, mock_db, mock_redis)
        message = json.loads(mock_redis.publish.call_args[0][1])
        assert "at" in message["payload"], "'at' timestamp must be in published payload"

    def test_at_is_iso_format(self):
        job_id, mock_db, mock_redis = make_mocks()
        emit(job_id, "job.started", {}, mock_db, mock_redis)
        message = json.loads(mock_redis.publish.call_args[0][1])
        at_value = message["payload"]["at"]
        # Should be parseable as datetime without error
        parsed = datetime.fromisoformat(at_value)
        assert parsed is not None

    def test_job_event_payload_contains_at(self):
        """The DB row's payload dict also has 'at'."""

        job_id, mock_db, mock_redis = make_mocks()
        emit(job_id, "job.started", {"extra": "data"}, mock_db, mock_redis)
        added_obj = mock_db.add.call_args[0][0]
        assert "at" in added_obj.payload


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEmitEdgeCases:
    def test_none_payload_does_not_raise(self):
        job_id, mock_db, mock_redis = make_mocks()
        # Should not raise
        emit(job_id, "job.started", None, mock_db, mock_redis)
        mock_db.add.assert_called_once()

    def test_none_payload_treated_as_empty_dict(self):
        job_id, mock_db, mock_redis = make_mocks()
        emit(job_id, "job.started", None, mock_db, mock_redis)
        message = json.loads(mock_redis.publish.call_args[0][1])
        # Payload should be a dict with at least the "at" key
        assert isinstance(message["payload"], dict)
        assert "at" in message["payload"]

    def test_does_not_mutate_caller_dict(self):
        """emit() must work on a copy; the caller's dict should NOT have 'at' added."""
        job_id, mock_db, mock_redis = make_mocks()
        original = {"project_id": "proj_123"}
        emit(job_id, "job.started", original, mock_db, mock_redis)
        # The caller's dict should still be unchanged
        assert "at" not in original, "emit() must not mutate the caller's payload dict"

    def test_returns_none(self):
        job_id, mock_db, mock_redis = make_mocks()
        result = emit(job_id, "job.started", {}, mock_db, mock_redis)
        assert result is None


# ---------------------------------------------------------------------------
# emit_async — the same event, for a caller on an event loop (#86)
# ---------------------------------------------------------------------------


class TestEmitAsync:
    """emit_async is emit with one line moved: the durable write runs in a thread.

    A Celery task holds a sync Session and a sync Redis client, so both halves of
    emit block. Inside `run_agent_loop` that blocking happens on the loop the
    customer's turn is running on, and the control DB is Neon.
    """

    async def test_commits_exactly_once(self):
        job_id, mock_db, mock_redis = make_mocks()
        await emit_async(job_id, "agent.tool_call", {"tool_name": "retrieve"}, mock_db, mock_redis)
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    async def test_the_write_runs_off_the_calling_thread(self):
        job_id, mock_db, mock_redis = make_mocks()
        seen: list[int] = []
        mock_db.commit.side_effect = lambda: seen.append(threading.get_ident())

        await emit_async(job_id, "agent.tool_call", {}, mock_db, mock_redis)

        assert seen and seen[0] != threading.get_ident(), (
            "the commit ran on the caller's thread, so a Neon round trip still "
            "blocks the event loop"
        )

    async def test_the_publish_stays_on_the_calling_thread(self):
        job_id, mock_db, mock_redis = make_mocks()
        seen: list[int] = []
        mock_redis.publish.side_effect = lambda *a: seen.append(threading.get_ident())

        await emit_async(job_id, "agent.tool_call", {}, mock_db, mock_redis)

        assert seen == [threading.get_ident()], (
            "the Redis publish moved off the caller's thread; live SSE delivery is "
            "the half that must not wait on a hop"
        )

    async def test_publishes_before_it_persists(self):
        """WR-05, unchanged by the offload: a Redis failure must leave no row.

        Redis first means a raise there happens before any commit, so a retry of
        the task cannot produce a duplicate job_events row.
        """
        job_id, mock_db, mock_redis = make_mocks()
        order: list[str] = []
        mock_redis.publish.side_effect = lambda *a: order.append("publish")
        mock_db.commit.side_effect = lambda: order.append("commit")

        await emit_async(job_id, "agent.tool_result", {}, mock_db, mock_redis)

        assert order == ["publish", "commit"]

    async def test_writes_the_same_row_emit_writes(self):
        """One event shape, two entry points. A drifting pair is two vocabularies."""
        from app.models.job_event import JobEvent

        job_id, sync_db, sync_redis = make_mocks()
        emit(job_id, "agent.tool_call", {"tool_name": "retrieve"}, sync_db, sync_redis)
        sync_row = sync_db.add.call_args[0][0]

        _, async_db, async_redis = make_mocks()
        await emit_async(
            job_id, "agent.tool_call", {"tool_name": "retrieve"}, async_db, async_redis
        )
        async_row = async_db.add.call_args[0][0]

        assert isinstance(async_row, JobEvent)
        assert (async_row.job_id, async_row.event_type) == (
            sync_row.job_id,
            sync_row.event_type,
        )
        assert set(async_row.payload) == set(sync_row.payload)

    async def test_does_not_mutate_the_callers_dict(self):
        job_id, mock_db, mock_redis = make_mocks()
        original = {"tool_name": "retrieve"}

        await emit_async(job_id, "agent.tool_call", original, mock_db, mock_redis)

        assert "at" not in original
