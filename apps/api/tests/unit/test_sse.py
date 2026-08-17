"""
Unit tests for app.services.sse event_generator.

Tests the SSE event generator:
    - TERMINAL_EVENTS constant contains the correct event types
    - DB replay phase: yields events from DB history
    - DB replay phase: returns immediately on terminal event in history
    - DB replay phase: returns immediately on client disconnect
    - CUSTOMER_TERMINAL_EVENTS closes the widget stream on agent.response, while the
      default set leaves the admin stream open for the judge chain (BACKLOG 7.3)
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


class TestCustomerTerminalEvents:
    """BACKLOG 7.3 — the customer stream must terminate itself."""

    def test_customer_set_contains_agent_response(self):
        """agent.response ends the customer stream — the whole point of 7.3."""
        from app.services.sse import CUSTOMER_TERMINAL_EVENTS
        assert "agent.response" in CUSTOMER_TERMINAL_EVENTS

    def test_customer_set_contains_agent_failed(self):
        """The failure path leaks a socket the same way the success path did."""
        from app.services.sse import CUSTOMER_TERMINAL_EVENTS
        assert "agent.failed" in CUSTOMER_TERMINAL_EVENTS

    def test_customer_set_is_a_superset_of_the_job_lifecycle_set(self):
        """job.complete / job.failed still close the customer stream too."""
        from app.services.sse import CUSTOMER_TERMINAL_EVENTS, TERMINAL_EVENTS
        assert TERMINAL_EVENTS <= CUSTOMER_TERMINAL_EVENTS

    def test_default_set_excludes_agent_events(self):
        """The admin stream must NOT close on agent.response.

        run_agent_turn dispatches the judge chain after emitting agent.response, so
        gatekeeper/auditor/strategist land on the same job_id afterwards. Putting
        agent.response in the default set would make them unreachable there.
        """
        from app.services.sse import TERMINAL_EVENTS
        assert "agent.response" not in TERMINAL_EVENTS
        assert "agent.failed" not in TERMINAL_EVENTS


def _replay_only_mocks(event_types):
    """Build (request, db, redis) mocks whose DB replay yields *event_types* in order."""
    events = []
    for event_type in event_types:
        evt = MagicMock()
        evt.id = uuid4()
        evt.event_type = event_type
        evt.payload = {}
        events.append(evt)

    mock_scalars = MagicMock()
    mock_scalars.__iter__ = MagicMock(return_value=iter(events))
    mock_db_result = MagicMock()
    mock_db_result.scalars.return_value = mock_scalars
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_db_result)

    mock_request = AsyncMock()
    mock_request.is_disconnected = AsyncMock(return_value=False)

    pubsub = AsyncMock()
    pubsub.__aenter__ = AsyncMock(return_value=pubsub)
    pubsub.__aexit__ = AsyncMock(return_value=None)
    pubsub.subscribe = AsyncMock()
    pubsub.listen = AsyncMock()  # must NOT be called once a terminal event lands
    mock_redis = AsyncMock()
    mock_redis.pubsub = MagicMock(return_value=pubsub)

    return mock_request, mock_db, mock_redis, pubsub


@pytest.mark.asyncio
class TestTerminalSetIsPerStream:
    async def test_customer_stream_stops_at_agent_response(self):
        """With CUSTOMER_TERMINAL_EVENTS the generator returns on agent.response.

        Without this the server kept yielding keepalives to the 120s cap, holding one
        of the 50 per-agent SSE slots for the whole two minutes.
        """
        from app.services.sse import CUSTOMER_TERMINAL_EVENTS, event_generator

        request, db, redis, pubsub = _replay_only_mocks(
            ["agent.thinking", "agent.response", "gatekeeper.complete"]
        )

        events = [
            e
            async for e in event_generator(
                request, uuid4(), db, redis,
                terminal_events=CUSTOMER_TERMINAL_EVENTS,
            )
        ]

        assert [e.event for e in events] == ["agent.thinking", "agent.response"]
        pubsub.listen.assert_not_called()

    async def test_customer_stream_stops_at_agent_failed(self):
        """The failure path terminates too."""
        from app.services.sse import CUSTOMER_TERMINAL_EVENTS, event_generator

        request, db, redis, pubsub = _replay_only_mocks(
            ["agent.thinking", "agent.failed"]
        )

        events = [
            e
            async for e in event_generator(
                request, uuid4(), db, redis,
                terminal_events=CUSTOMER_TERMINAL_EVENTS,
            )
        ]

        assert [e.event for e in events] == ["agent.thinking", "agent.failed"]
        pubsub.listen.assert_not_called()

    async def test_default_stream_does_not_stop_at_agent_response(self):
        """The admin stream still reaches the judge verdicts after agent.response.

        Same event history as the customer test; only the terminal set differs. This
        is what a global TERMINAL_EVENTS change would have broken.
        """
        from app.services.sse import event_generator

        request, db, redis, _ = _replay_only_mocks(
            ["agent.thinking", "agent.response", "gatekeeper.complete", "job.complete"]
        )

        events = []
        async for sse_event in event_generator(request, uuid4(), db, redis):
            events.append(sse_event)

        assert [e.event for e in events] == [
            "agent.thinking",
            "agent.response",
            "gatekeeper.complete",
            "job.complete",
        ]

    async def test_late_join_replays_full_history_before_terminating(self):
        """A client that connects after the answer still gets everything before it.

        Terminating the stream must not truncate the replay itself — only end it once
        the customer-visible answer has been delivered.
        """
        from app.services.sse import CUSTOMER_TERMINAL_EVENTS, event_generator

        request, db, redis, _ = _replay_only_mocks(
            [
                "agent.thinking",
                "agent.tool_call",
                "agent.tool_result",
                "agent.escalated",
                "agent.response",
            ]
        )

        events = [
            e
            async for e in event_generator(
                request, uuid4(), db, redis,
                terminal_events=CUSTOMER_TERMINAL_EVENTS,
            )
        ]

        assert [e.event for e in events] == [
            "agent.thinking",
            "agent.tool_call",
            "agent.tool_result",
            "agent.escalated",
            "agent.response",
        ]


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
