"""
Unit tests for app.services.sse event_generator.

Tests the SSE event generator:
    - TERMINAL_EVENTS constant contains the correct event types
    - DB replay phase: yields events from DB history
    - DB replay phase: returns immediately on terminal event in history
    - DB replay phase: returns immediately on client disconnect
"""

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

# conftest.py sets required env vars


class TestTerminalEvents:
    def test_terminal_events_contains_job_complete(self):
        """TERMINAL_EVENTS must contain 'job.complete'."""
        from app.services.sse import TERMINAL_EVENTS
        assert "job.complete" in TERMINAL_EVENTS

    def test_terminal_events_contains_job_failed(self):
        """TERMINAL_EVENTS must contain 'job.failed'."""
        from app.services.sse import TERMINAL_EVENTS
        assert "job.failed" in TERMINAL_EVENTS

    def test_terminal_events_is_frozenset(self):
        """TERMINAL_EVENTS must be a frozenset (immutable)."""
        from app.services.sse import TERMINAL_EVENTS
        assert isinstance(TERMINAL_EVENTS, frozenset)

    def test_terminal_events_does_not_contain_intermediate(self):
        """Non-terminal events must not be in TERMINAL_EVENTS."""
        from app.services.sse import TERMINAL_EVENTS
        assert "job.started" not in TERMINAL_EVENTS
        assert "neon.project.ready" not in TERMINAL_EVENTS
        assert "migrations.running" not in TERMINAL_EVENTS


@pytest.mark.asyncio
class TestEventGeneratorDbReplay:
    async def test_yields_past_events_from_db(self):
        """event_generator yields all past events from DB."""
        from app.services.sse import event_generator

        job_id = uuid4()

        # Build mock DB events
        mock_evt1 = MagicMock()
        mock_evt1.id = uuid4()
        mock_evt1.event_type = "job.started"
        mock_evt1.payload = {"agent_id": str(uuid4())}
        mock_evt1.created_at = "2024-01-01T00:00:00Z"

        mock_evt2 = MagicMock()
        mock_evt2.id = uuid4()
        mock_evt2.event_type = "neon.project.ready"
        mock_evt2.payload = {"project_id": "proj-123"}
        mock_evt2.created_at = "2024-01-01T00:00:01Z"

        mock_scalars = MagicMock()
        mock_scalars.__iter__ = MagicMock(return_value=iter([mock_evt1, mock_evt2]))

        mock_db_result = MagicMock()
        mock_db_result.scalars.return_value = mock_scalars

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_db_result)

        # Request not disconnected
        mock_request = AsyncMock()
        mock_request.is_disconnected = AsyncMock(return_value=False)

        # Redis client (not used in this test — DB replay returns before Redis phase)
        mock_redis = AsyncMock()
        pubsub = AsyncMock()
        pubsub.__aenter__ = AsyncMock(return_value=pubsub)
        pubsub.__aexit__ = AsyncMock(return_value=None)
        pubsub.subscribe = AsyncMock()

        # Simulate listening returns terminal event immediately
        terminal_msg = {
            "type": "message",
            "data": json.dumps({"event_type": "job.complete", "payload": {}}),
        }
        pubsub.listen = AsyncMock(return_value=AsyncMock(
            __aiter__=MagicMock(return_value=aiter_from([terminal_msg]))
        ))
        mock_redis.pubsub = MagicMock(return_value=pubsub)

        gen = event_generator(mock_request, job_id, mock_db, mock_redis)
        events = []
        async for sse_event in gen:
            events.append(sse_event)
            # Stop after we get two non-terminal events (won't have terminal here)
            if len(events) == 2:
                break

        assert len(events) == 2
        assert events[0].event == "job.started"
        assert events[1].event == "neon.project.ready"

    async def test_returns_immediately_on_terminal_event_in_db(self):
        """event_generator does not enter Redis listen phase if terminal event is in DB.

        After the CR-06 fix, pubsub is subscribed BEFORE the DB replay (to close the
        event-loss gap). So pubsub is always entered. What we verify here is that
        pubsub.listen() is NOT called — the generator returns early after the terminal
        event without entering the live-stream phase.
        """
        from app.services.sse import event_generator

        job_id = uuid4()

        # DB has job.complete (terminal)
        mock_evt = MagicMock()
        mock_evt.id = uuid4()
        mock_evt.event_type = "job.complete"
        mock_evt.payload = {"agent_id": str(uuid4())}

        mock_scalars = MagicMock()
        mock_scalars.__iter__ = MagicMock(return_value=iter([mock_evt]))

        mock_db_result = MagicMock()
        mock_db_result.scalars.return_value = mock_scalars

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_db_result)

        mock_request = AsyncMock()
        mock_request.is_disconnected = AsyncMock(return_value=False)

        # Set up pubsub as async context manager (required by CR-06 subscribe-first ordering)
        pubsub = AsyncMock()
        pubsub.__aenter__ = AsyncMock(return_value=pubsub)
        pubsub.__aexit__ = AsyncMock(return_value=None)
        pubsub.subscribe = AsyncMock()
        pubsub.listen = AsyncMock()  # Must NOT be called

        mock_redis = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=pubsub)

        gen = event_generator(mock_request, job_id, mock_db, mock_redis)
        events = []
        async for sse_event in gen:
            events.append(sse_event)

        # Generator should yield the terminal event then return
        assert len(events) == 1
        assert events[0].event == "job.complete"
        # pubsub IS entered (subscribe-before-replay), but listen() must NOT be called
        mock_redis.pubsub.assert_called_once()
        pubsub.listen.assert_not_called()

    async def test_returns_immediately_on_client_disconnect(self):
        """event_generator stops if client disconnects during DB replay."""
        from app.services.sse import event_generator

        job_id = uuid4()

        mock_evt = MagicMock()
        mock_evt.id = uuid4()
        mock_evt.event_type = "job.started"
        mock_evt.payload = {}

        mock_scalars = MagicMock()
        mock_scalars.__iter__ = MagicMock(return_value=iter([mock_evt]))

        mock_db_result = MagicMock()
        mock_db_result.scalars.return_value = mock_scalars

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_db_result)

        # Client is disconnected before any event is yielded
        mock_request = AsyncMock()
        mock_request.is_disconnected = AsyncMock(return_value=True)

        # Pubsub must be async context manager (subscribe-before-replay, CR-06)
        pubsub = AsyncMock()
        pubsub.__aenter__ = AsyncMock(return_value=pubsub)
        pubsub.__aexit__ = AsyncMock(return_value=None)
        pubsub.subscribe = AsyncMock()
        pubsub.listen = AsyncMock()

        mock_redis = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=pubsub)

        gen = event_generator(mock_request, job_id, mock_db, mock_redis)
        events = []
        async for sse_event in gen:
            events.append(sse_event)

        # Generator returned before yielding because is_disconnected() returned True
        assert len(events) == 0
        pubsub.listen.assert_not_called()


def aiter_from(lst):
    """Create an async iterator from a list."""
    class AsyncIter:
        def __init__(self, items):
            self._items = iter(items)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._items)
            except StopIteration:
                raise StopAsyncIteration

    return AsyncIter(lst)
