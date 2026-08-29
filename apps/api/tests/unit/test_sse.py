"""
Unit tests for app.services.sse event_generator.

Tests the SSE event generator:
    - TERMINAL_EVENTS constant contains the correct event types
    - DB replay phase: yields events from DB history
    - DB replay phase: returns immediately on terminal event in history
    - DB replay phase: returns immediately on client disconnect
    - CUSTOMER_TERMINAL_EVENTS closes the widget stream on agent.response, while the
      default set leaves the admin stream open for the judge chain (BACKLOG 7.3)
    - public=True filters every payload through PUBLIC_EVENT_FIELDS and fails closed,
      while the authenticated stream reading the same rows keeps the detail (#104, #83)
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


# ---------------------------------------------------------------------------
# #104 / #83 — what a PUBLIC reader of the widget stream may see
#
# GET /widget/jobs/{job_id}/events authenticates on nothing but the job id. These
# tests fix the persist-versus-publish split: job_events keeps every field, and
# PUBLIC_EVENT_FIELDS decides which of them leave the process to a caller who
# proved nothing.
# ---------------------------------------------------------------------------

# The two exception strings #83 measured, verbatim.
OPENAI_429_BODY = (
    'Error code: 429 - {"error": {"message": "Rate limited. Your key '
    'sk-proj-SECRETKEY123 exceeded quota", "type": "rate_limit"}}'
)
LIBPQ_DSN_ECHO = (
    'invalid dsn: missing key/value separator "=" in URI query parameter: "bad"'
)

CARD_IN_A_TOOL_INPUT = {"query": "refund on card 4111 1111 1111 1111"}


def _replay_rows(pairs):
    """(request, db, redis) mocks whose DB replay yields (event_type, payload) rows."""
    rows = []
    for event_type, payload in pairs:
        evt = MagicMock()
        evt.id = uuid4()
        evt.event_type = event_type
        evt.payload = payload
        rows.append(evt)

    scalars = MagicMock()
    scalars.__iter__ = MagicMock(return_value=iter(rows))
    result = MagicMock()
    result.scalars.return_value = scalars
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    request = AsyncMock()
    request.is_disconnected = AsyncMock(return_value=False)

    pubsub = AsyncMock()
    pubsub.__aenter__ = AsyncMock(return_value=pubsub)
    pubsub.__aexit__ = AsyncMock(return_value=None)
    pubsub.subscribe = AsyncMock()
    pubsub.listen = AsyncMock()
    redis = AsyncMock()
    redis.pubsub = MagicMock(return_value=pubsub)

    return request, db, redis


async def _drain(pairs, **kwargs):
    """Every ServerSentEvent the replay phase yields for `pairs`."""
    from app.services.sse import CUSTOMER_TERMINAL_EVENTS, event_generator

    request, db, redis = _replay_rows(pairs)
    return [
        e
        async for e in event_generator(
            request, uuid4(), db, redis,
            terminal_events=CUSTOMER_TERMINAL_EVENTS,
            **kwargs,
        )
    ]


class TestPublicPayload:
    def test_a_tool_call_publishes_the_tool_name_and_not_the_arguments(self):
        """`input` is model-authored. Widget.jsx reads p.tool_name and nothing else."""
        from app.services.sse import public_payload

        visible = public_payload(
            "agent.tool_call",
            {
                "tool_name": "retrieve",
                "input": CARD_IN_A_TOOL_INPUT,
                "at": "2026-08-28T09:00:00Z",
            },
        )

        assert visible == {"tool_name": "retrieve", "at": "2026-08-28T09:00:00Z"}

    def test_a_tool_result_publishes_the_tool_name_and_not_the_summary(self):
        """The persisted half of this split is TestSummaryStaysOnThePersistedRow."""
        from app.services.sse import public_payload

        visible = public_payload(
            "agent.tool_result",
            {"tool_name": "retrieve", "summary": "customer.name@example.com, order 8812"},
        )

        assert visible == {"tool_name": "retrieve"}

    def test_an_escalation_reason_carrying_an_address_publishes_the_deflection(self):
        """EscalationPanel renders `reason`, so the allowlist alone is not enough."""
        from app.domain.pii_firewall import PII_DEFLECTION
        from app.services.sse import public_payload

        row = {
            "reason": "Customer asked us to write to customer.name@example.com",
            "context": "card 4111 1111 1111 1111, order 8812",
            "conversation_id": "c-1",
        }
        visible = public_payload("agent.escalated", row)

        assert visible == {"reason": PII_DEFLECTION}
        assert row["reason"].endswith("customer.name@example.com"), (
            "public_payload must not mutate the caller's row: the persisted payload "
            "and the tenant's escalation email keep the real text"
        )

    def test_a_clean_escalation_reason_is_published_verbatim(self):
        """The control. The deflection is a detection, never a blanket replacement."""
        from app.services.sse import public_payload

        visible = public_payload("agent.escalated", {"reason": "Customer is frustrated"})

        assert visible == {"reason": "Customer is frustrated"}

    def test_a_failure_publishes_the_error_type_and_not_the_provider_body(self):
        """#83. str(APIStatusError) is 'Error code: N - <response body verbatim>'."""
        from app.services.sse import public_payload

        visible = public_payload(
            "agent.failed", {"error_type": "APIStatusError", "error": OPENAI_429_BODY}
        )

        assert visible == {"error_type": "APIStatusError"}
        assert "sk-proj-SECRETKEY123" not in str(visible)

    def test_a_failure_publishes_the_error_type_and_not_the_dsn_fragment(self):
        """#83. libpq echoes the offending token of a malformed connection string."""
        from app.services.sse import public_payload

        visible = public_payload(
            "agent.failed", {"error_type": "OperationalError", "error": LIBPQ_DSN_ECHO}
        )

        assert visible == {"error_type": "OperationalError"}

    def test_the_answer_the_widget_renders_survives_the_filter(self):
        """The control for every drop above.

        agent.response.text was scanned in the turn seam (#50) WITH that turn's
        published_context, and is deliberately not rescanned here, where no
        published_context exists. A second scan would deflect a correct answer that
        quotes the tenant's own published address (BACKLOG 7.29).
        """
        from app.services.sse import public_payload

        row = {
            "text": "Write to us at hello@acme.example.",
            "citations": [{"document_name": "FAQ.pdf", "section": "1"}],
            "conversation_id": "c-1",
            "message_id": "m-1",
        }

        assert public_payload("agent.response", row) == row

    def test_an_event_type_the_map_does_not_name_publishes_an_empty_payload(self):
        """Fail closed. A new event type says nothing until someone decides."""
        from app.services.sse import public_payload

        assert public_payload("job.failed", {"error": LIBPQ_DSN_ECHO}) == {}
        assert public_payload("query.complete", {"results": ["chunk text"]}) == {}

    def test_a_judge_verdict_publishes_nothing(self):
        """The chain writes to the same job_id and can share a poll batch with the answer."""
        from app.services.sse import public_payload

        verdict = {"verdict": "grounded", "confidence": 0.9, "reasoning": "the customer said"}

        assert public_payload("gatekeeper.complete", verdict) == {}
        assert public_payload("auditor.complete", verdict) == {}
        assert public_payload("strategist.complete", verdict) == {}


@pytest.mark.asyncio
class TestTheStreamFiltersOnlyForPublicReaders:
    async def test_the_public_stream_drops_the_card_number_in_a_tool_call(self):
        rows = [
            ("agent.tool_call", {"tool_name": "retrieve", "input": CARD_IN_A_TOOL_INPUT}),
            ("agent.response", {"text": "ok", "citations": [], "conversation_id": "c", "message_id": "m"}),
        ]

        events = await _drain(rows, public=True)

        assert json.loads(events[0].data) == {"tool_name": "retrieve"}
        assert "4111" not in events[0].data

    async def test_the_authenticated_stream_still_carries_it(self):
        """The control. Same rows, same generator, no public flag.

        event_generator is shared with GET /jobs/{job_id}/events, the authenticated
        operator stream, which must keep the full detail.
        """
        rows = [
            ("agent.tool_call", {"tool_name": "retrieve", "input": CARD_IN_A_TOOL_INPUT}),
            ("agent.response", {"text": "ok", "citations": [], "conversation_id": "c", "message_id": "m"}),
        ]

        events = await _drain(rows)

        assert json.loads(events[0].data)["input"] == CARD_IN_A_TOOL_INPUT

    async def test_the_public_stream_deflects_an_escalation_reason(self):
        from app.domain.pii_firewall import PII_DEFLECTION

        rows = [
            ("agent.escalated", {"reason": "write to customer.name@example.com", "context": "order 8812"}),
            # agent.escalated is not terminal; the answer that follows it is.
            ("agent.response", {"text": "ok", "citations": [], "conversation_id": "c", "message_id": "m"}),
        ]

        events = await _drain(rows, public=True)

        assert json.loads(events[0].data) == {"reason": PII_DEFLECTION}

    async def test_the_public_stream_drops_the_raw_error(self):
        rows = [("agent.failed", {"error_type": "APIStatusError", "error": OPENAI_429_BODY})]

        events = await _drain(rows, public=True)

        assert json.loads(events[0].data) == {"error_type": "APIStatusError"}
        assert "SECRETKEY123" not in events[0].data

    async def test_the_public_stream_empties_an_event_type_the_map_does_not_name(self):
        rows = [("job.failed", {"error": LIBPQ_DSN_ECHO})]

        events = await _drain(rows, public=True)

        assert json.loads(events[0].data) == {}
        assert events[0].event == "job.failed", "the event type itself still reaches the client"


class TestSummaryStaysOnThePersistedRow:
    """The regression that would otherwise be silent.

    retrieval_eval._fetch_turn_context reads payload["summary"] off persisted
    agent.tool_result rows as its retrieved-context proxy. #104 drops `summary` from
    what is PUBLISHED and must not touch what is WRITTEN.
    """

    @staticmethod
    def _db(response_payload, tool_result_payloads):
        response_result = MagicMock()
        response_result.fetchone.return_value = (response_payload,)
        tool_result = MagicMock()
        tool_result.fetchall.return_value = [(p,) for p in tool_result_payloads]
        db = MagicMock()
        db.execute.side_effect = [response_result, tool_result]
        return db

    def test_fetch_turn_context_still_finds_the_summary(self):
        from app.worker.tasks.runtime.retrieval_eval import _fetch_turn_context

        db = self._db(
            {"text": "14 days.", "citations": [], "conversation_id": "c-1"},
            [{"tool_name": "retrieve", "summary": "Returns accepted within 14 days."}],
        )

        _, _, _, contexts = _fetch_turn_context(db, "job-1")

        assert contexts == ["Returns accepted within 14 days."]

    def test_a_non_retrieve_tool_result_is_not_context(self):
        """The control. The join is on tool_name, and it still excludes."""
        from app.worker.tasks.runtime.retrieval_eval import _fetch_turn_context

        db = self._db(
            {"text": "14 days.", "citations": [], "conversation_id": "c-1"},
            [{"tool_name": "clarify", "summary": "Which order?"}],
        )

        _, _, _, contexts = _fetch_turn_context(db, "job-1")

        assert contexts == []


class TestTheMapCoversWhatTheTurnEmits:
    """The map is hand-written, so this pins it against what the code can emit.

    DERIVATION. `emit` is the one function that writes a job_events row, so the set of
    event types is the set of second arguments its call sites pass. Those are read off
    the COMPILED modules (`loader.get_code`, then `dis`), never off source text and
    never off a syntax tree. The modules scanned are the turn's entry point plus the
    app modules it imports, taken from the entry point's own IMPORT_NAME instructions
    and filtered to the ones that bind `emit`.

    WHAT THIS DOES NOT COVER:
      * An emitter two hops out, a task dispatched by a task the turn dispatches.
        Today there is none: retrieval_eval, the fourth link in the judge chain, emits
        nothing at all.
      * A task reached by name (celery send_task) rather than by import.
      * The pipeline's own event types, which reach this endpoint only if someone
        hands it an ingestion job id.
      * An event type built from a variable rather than a literal. The scanner would
        take a string from a later argument instead, which fails this test loudly
        rather than passing it quietly.

    In all four cases PUBLIC_EVENT_FIELDS still fails closed at runtime and publishes
    an empty payload, so what is uncovered is the WARNING, never the leak.
    """

    TURN_ENTRY = "app.worker.tasks.runtime.agent"

    @staticmethod
    def _module_code(name):
        """The compiled module. Not its source text, not a syntax tree."""
        import importlib

        return importlib.import_module(name).__spec__.loader.get_code(name)

    @classmethod
    def _nested(cls, code):
        import types

        yield code
        for const in code.co_consts:
            if isinstance(const, types.CodeType):
                yield from cls._nested(const)

    @classmethod
    def _event_types_in(cls, name):
        """(event types, emit call sites) for one module."""
        import dis

        found, sites = set(), 0
        for code in cls._nested(cls._module_code(name)):
            instructions = list(dis.get_instructions(code))
            for index, op in enumerate(instructions):
                if not (op.opname.startswith("LOAD_") and op.argval == "emit"):
                    continue
                sites += 1
                for following in instructions[index + 1:index + 12]:
                    if following.opname == "LOAD_CONST" and isinstance(following.argval, str):
                        found.add(following.argval)
                        break
        return found, sites

    @classmethod
    def _turn_path(cls):
        import dis
        import importlib

        entry = cls._module_code(cls.TURN_ENTRY)
        modules = {cls.TURN_ENTRY} | {
            op.argval
            for op in dis.get_instructions(entry)
            if op.opname == "IMPORT_NAME" and str(op.argval).startswith("app.")
        }
        found, sites = set(), 0
        for name in sorted(modules):
            if getattr(importlib.import_module(name), "emit", None) is None:
                continue
            module_found, module_sites = cls._event_types_in(name)
            found |= module_found
            sites += module_sites
        return found, sites

    def test_the_derivation_finds_the_turn_s_emit_call_sites(self):
        """Guards the guard. A derivation that finds nothing would pass the test below."""
        found, sites = self._turn_path()

        assert sites >= len(found) >= 6, (found, sites)
        assert "agent.response" in found and "agent.tool_call" in found

    def test_every_derived_string_looks_like_an_event_type(self):
        """If the scanner ever took the wrong constant, it says so here."""
        found, _ = self._turn_path()

        for event_type in sorted(found):
            assert "." in event_type and event_type == event_type.strip().lower(), event_type

    def test_every_event_type_the_turn_emits_has_a_public_entry(self):
        from app.services.sse import PUBLIC_EVENT_FIELDS

        found, _ = self._turn_path()
        missing = sorted(found - set(PUBLIC_EVENT_FIELDS))

        assert not missing, (
            "these event types reach the public widget stream with no entry in "
            "PUBLIC_EVENT_FIELDS, so they publish an empty payload: %s" % (missing,)
        )

    def test_every_scanned_field_is_also_allowlisted(self):
        """A scanned field that is not allowlisted is a scan that never runs."""
        from app.services.sse import PUBLIC_EVENT_FIELDS, PUBLIC_SCANNED_FIELDS

        for event_type, fields in PUBLIC_SCANNED_FIELDS.items():
            assert fields <= PUBLIC_EVENT_FIELDS[event_type], event_type
