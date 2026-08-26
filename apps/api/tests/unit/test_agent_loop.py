"""Tests for app.services.agent_loop, the owned bounded tool loop (ticket #48).

WHAT THE DOUBLES STAND IN FOR
    The loop never imports `openai`, so these tests never import it either. A
    completion is a plain object carrying `choices[0].message.content`,
    `choices[0].message.tool_calls` and `choices[0].finish_reason`, which is the
    whole of what the loop reads off a response. A fake client records every
    request body, so the wire shape is asserted against the bytes the loop would
    send rather than against a mock that was told to expect them.

    TWO KINDS OF TOOL, AND EACH PROVES A DIFFERENT THING. Most loop tests hand
    in duck-typed doubles carrying `.name`, `.description`, `.input_schema` and
    `.handler`, because that is the whole of what the loop reads. A double is
    what shows the loop needs no more than those four attributes, which is what
    keeps `claude_agent_sdk` out of `app.services.agent_loop`.

    A few tests drive the real thing instead. `TestOneModelCall`'s
    `test_the_eleven_real_tools_reach_the_wire` and every test in `TestTheSeam`
    go through `agent_tool_definitions()`, which returns
    `claude_agent_sdk.SdkMcpTool` objects today. That is also correct, and it is
    the other half of the claim. The duck typing says the loop asks for nothing
    more; these say the objects the product actually hands it satisfy that,
    SDK-built or not. The day #49 replaces those definitions, these tests notice.

WHAT THE SEAM TESTS DRIVE FOR REAL
    `build_agent_turn` calls `bind_tool_context` for real, because that binding
    IS the contract. `current_side_effect_mode()` is then read back to show the
    mode reached the tool layer, which is the property BACKLOG 2.5 exists for.
    Only the client factory is patched, since building one asks Settings for an
    API key, and the seam takes no client of its own.

WHAT THE BUDGET TESTS PRICE
    Real `ModelCall` rows through `app.domain.pricing`, not a stubbed number.
    The ceiling is derived from the ledger at read time (ADR 0008), so a test
    that stubbed the derivation would pin the guard's arithmetic to itself.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import settings
from app.core.model_client import route_for
from app.domain.model_call import ModelCall, ModelSource
from app.services.agent_loop import (
    MAX_MODEL_CALLS_PER_TURN,
    RETRIEVE_CHUNKS_KEY,
    RETRIEVE_CHUNKS_PARSED,
    RETRIEVE_CHUNKS_SOURCE_KEY,
    RETRIEVE_CHUNKS_UNPARSED,
    RETRIEVE_JUDGE_CHUNKS_KEY,
    RETRIEVE_RESULT_IS_ERROR_KEY,
    AgentTurn,
    build_agent_turn,
    record_turn_calls,
    run_agent_loop,
)
from app.services.agent_prompt import build_system_prompt
from app.services.agent_tools import (
    agent_tool_definitions,
    current_side_effect_mode,
    get_recorded_side_effects,
    reset_side_effect_context,
)

TENANT = "11111111-1111-1111-1111-111111111111"
JOB = "33333333-3333-3333-3333-333333333333"
CONVERSATION = "44444444-4444-4444-4444-444444444444"
CONN_STR = "postgresql://test:test@localhost/tenant_probe"

# 08:30 CAT on a Tuesday. Luna is priced flat, so the instant only has to be aware.
AT = datetime(2026, 8, 25, 6, 30, tzinfo=timezone.utc)

#: The capability surface one turn grants, in registration order. A literal,
#: because comparing the turn's tools against `agent_tool_definitions()` compares
#: that function with itself, and an empty tuple satisfies both sides.
ELEVEN_TOOLS = [
    "retrieve",
    "lookup_structured",
    "escalate_to_human",
    "clarify",
    "place_order",
    "cancel_order",
    "issue_refund",
    "update_subscription",
    "book_slot",
    "update_customer_record",
    "confirm_action",
]


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


def _tool_call(call_id: str, name: str, arguments: str):
    """One tool call as the OpenAI SDK hands it to the loop."""
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _completion(content=None, tool_calls=(), finish_reason="stop"):
    """One chat completion, read the way the loop reads it."""
    message = SimpleNamespace(content=content, tool_calls=list(tool_calls) or None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)]
    )


class _Completions:
    """Hands back the scripted replies in order, repeating the last one."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests: list[dict] = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        index = min(len(self.requests) - 1, len(self.replies) - 1)
        return self.replies[index]


class _Client:
    """The factory-built async client, reduced to what the loop touches.

    `close` is here because the loop owns closing it. openai 2.45.0's
    `AsyncOpenAI.close` is an async method that aclose()s the underlying httpx
    client, so the double is async too.
    """

    def __init__(self, *replies):
        self.completions = _Completions(replies)
        self.chat = SimpleNamespace(completions=self.completions)
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1

    @property
    def requests(self) -> list[dict]:
        return self.completions.requests


def _tool(name: str, handler, description="does one thing", input_schema=None):
    """A tool object carrying the four attributes the loop duck-types."""
    return SimpleNamespace(
        name=name,
        description=description,
        input_schema=input_schema or {"type": "object", "properties": {}},
        handler=handler,
    )


def _text_wire(text: str, is_error: bool = False) -> dict:
    wire: dict = {"content": [{"type": "text", "text": text}]}
    if is_error:
        wire["is_error"] = True
    return wire


async def _echo_handler(args):
    return _text_wire(f"echo {args.get('query', '')}")


def _turn(client, *, tools=(), max_model_calls=MAX_MODEL_CALLS_PER_TURN,
          max_budget_usd=1.0, calls=None, ledger=None) -> AgentTurn:
    return AgentTurn(
        client=client,
        route=route_for("agent_turn"),
        system_prompt="you are a returns specialist",
        tools=tuple(tools),
        max_model_calls=max_model_calls,
        max_budget_usd=max_budget_usd,
        calls=[] if calls is None else calls,
        ledger=(lambda call: None) if ledger is None else ledger,
    )


async def _drive(turn, message="what is the return window?", history=()):
    """One loop run with the two event sinks captured rather than emitted."""
    events: list[tuple] = []
    with patch(
        "app.services.agent_loop.emit",
        side_effect=lambda job_id, event_type, payload, db, redis: events.append(
            (event_type, payload)
        ),
    ):
        out = await run_agent_loop(
            message,
            history=list(history),
            turn=turn,
            job_id=JOB,
            db=MagicMock(),
            redis=MagicMock(),
        )
    return out, events


def _luna_call(input_tokens: int, output_tokens: int, model="gpt-5.6-luna") -> ModelCall:
    """One ledger row the price book can read, or deliberately cannot."""
    return ModelCall(
        purpose="agent_turn",
        provider="openai",
        requested_model=model,
        served_model=model,
        model_source=ModelSource.REPORTED,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        at=AT,
        tenant_id=TENANT,
    )


def _agent() -> MagicMock:
    agent = MagicMock()
    agent.id = uuid.uuid4()
    agent.tenant_id = uuid.uuid4()
    agent.name = "Loop Test Agent"
    agent.retrieval_strategy = {}
    agent.soul_role = "a returns specialist"
    agent.soul_voice = "plain and unhurried"
    agent.soul_do_list = ["cite the policy clause"]
    agent.soul_donot_list = ["never invent a refund amount"]
    return agent


def _seam_kwargs(**overrides) -> dict:
    """The arguments every seam test shares. The factory builds the client."""
    kwargs = {
        "agent": _agent(),
        "conn_str": CONN_STR,
        "conversation_id": CONVERSATION,
        "job_id": JOB,
        "side_effects": "live",
        "ledger": [].append,
    }
    kwargs.update(overrides)
    return kwargs


def _build(client=None, **overrides) -> AgentTurn:
    """The seam, with only the client factory patched.

    Patched because building a real client asks Settings for an API key. The seam
    takes no client argument. One handed in would be a client with no ledger hook
    on it, which is the failure #46 ended.
    """
    factory = patch(
        "app.services.agent_loop.make_async_client",
        return_value=client if client is not None else _Client(_completion(content="ok")),
    )
    with factory:
        return build_agent_turn(**_seam_kwargs(**overrides))


@pytest.fixture(autouse=True)
def _clean_side_effect_context():
    """The mode is process sticky, so each test starts and ends at the default."""
    reset_side_effect_context()
    yield
    reset_side_effect_context()


# ---------------------------------------------------------------------------
# The seam: build_agent_turn
# ---------------------------------------------------------------------------


class TestTheSeam:
    def test_the_route_comes_from_the_purpose_table(self):
        assert _build().route == route_for("agent_turn")

    def test_the_route_names_luna_at_effort_none(self):
        """Decision #34 priced this turn at effort none and nowhere else."""
        route = _build().route
        assert route.model == "gpt-5.6-luna"
        assert route.reasoning_effort == "none"

    def test_the_system_prompt_is_the_one_build_system_prompt_assembles(self):
        agent = _agent()
        turn = _build(agent=agent)
        assert turn.system_prompt == build_system_prompt(agent, soul_override=None)

    def test_a_soul_override_reaches_the_prompt(self):
        agent = _agent()
        override = {"soul_role": "the canary persona under test"}
        turn = _build(agent=agent, soul_override=override)
        assert turn.system_prompt == build_system_prompt(agent, soul_override=override)

    def test_the_turn_carries_the_eleven_tools(self):
        """The names, in order. Comparing the seam's list against
        `agent_tool_definitions()` compares that function with itself, and stays
        green if it ever returned nothing at all."""
        assert [t.name for t in _build().tools] == ELEVEN_TOOLS

    def test_the_ceilings_are_the_named_ones(self):
        turn = _build()
        assert turn.max_model_calls == MAX_MODEL_CALLS_PER_TURN
        assert turn.max_budget_usd == settings.AGENT_MAX_BUDGET_USD

    def test_the_calls_list_starts_empty(self):
        assert _build().calls == []

    def test_the_turn_carries_the_client_the_factory_built(self):
        """The seam takes no client. A caller cannot hand one in that carries no
        ledger hook, which is how a turn spends money and records nothing."""
        client = _Client(_completion(content="ok"))
        assert _build(client=client).client is client

    def test_the_seam_takes_no_client(self):
        with pytest.raises(TypeError, match="client"):
            build_agent_turn(**_seam_kwargs(), client=_Client(_completion(content="ok")))

    def test_a_missing_mode_is_a_type_error(self):
        """No default. A caller that does not say which mode it wants raises."""
        with pytest.raises(TypeError):
            build_agent_turn(
                agent=_agent(),
                conn_str=CONN_STR,
                conversation_id=CONVERSATION,
                job_id=JOB,
                ledger=[].append,
            )

    def test_an_unrecognised_mode_is_refused(self):
        with pytest.raises(ValueError, match="side_effects"):
            _build(side_effects="dry-run")

    def test_a_refused_mode_leaves_the_tool_layer_at_the_safe_default(self):
        """The reset runs before the validation, so a raising turn leaves nothing behind."""
        _build(side_effects="recorded")
        assert current_side_effect_mode() == "recorded"

        with pytest.raises(ValueError):
            _build(side_effects="dry-run")

        assert current_side_effect_mode() == "live"

    def test_the_ledger_is_mandatory(self):
        """A client that records nothing is the failure #46 ended."""
        with pytest.raises(TypeError):
            build_agent_turn(
                agent=_agent(),
                conn_str=CONN_STR,
                conversation_id=CONVERSATION,
                job_id=JOB,
                side_effects="live",
            )

    def test_the_mode_reaches_the_tool_layer(self):
        _build(side_effects="recorded")
        assert current_side_effect_mode() == "recorded"

    def test_live_mode_reaches_the_tool_layer_too(self):
        _build(side_effects="live")
        assert current_side_effect_mode() == "live"

    def test_recorded_mode_records_the_escalation_instead_of_sending_it(self):
        agent = _agent()
        with patch("app.services.agent_loop.send_escalation_email") as send:
            _build(agent=agent, side_effects="recorded")
            from app.services.agent_tools import _notify_fn_var

            _notify_fn_var.get()("frustrated customer", "third repeat")

        send.assert_not_called()
        recorded = get_recorded_side_effects()
        assert [entry["kind"] for entry in recorded] == ["escalation.notify"]
        assert recorded[0]["detail"]["reason"] == "frustrated customer"
        assert recorded[0]["detail"]["context"] == "third repeat"
        assert recorded[0]["detail"]["agent_id"] == str(agent.id)

    def test_live_mode_sends_the_escalation(self):
        agent = _agent()
        with patch("app.services.agent_loop.send_escalation_email") as send:
            _build(agent=agent, side_effects="live")
            from app.services.agent_tools import _notify_fn_var

            _notify_fn_var.get()("frustrated customer", "third repeat")

        send.assert_called_once_with(agent, "frustrated customer", "third repeat")


class TestTheClientFactory:
    def test_the_factory_is_asked_for_the_agent_turn_purpose(self):
        agent = _agent()
        with patch("app.services.agent_loop.make_async_client") as factory:
            turn = build_agent_turn(**_seam_kwargs(agent=agent))

        purpose = factory.call_args.args[0]
        kwargs = factory.call_args.kwargs
        assert purpose == "agent_turn"
        assert kwargs["tenant_id"] == str(agent.tenant_id)
        assert kwargs["agent_id"] == str(agent.id)
        assert kwargs["job_id"] == JOB
        assert turn.client is factory.return_value

    def test_a_recorded_call_lands_in_the_turn_and_not_in_the_ledger_yet(self):
        """The recorder only appends. `record_turn_calls` is what writes the row.

        `ledger_recorder` opens, commits and closes a tenant connection per call,
        and this recorder runs inside the async response hook, on the event loop
        the customer is waiting on. A tenant endpoint waking takes 8 to 20 seconds.
        """
        written: list[ModelCall] = []
        with patch("app.services.agent_loop.make_async_client") as factory:
            turn = build_agent_turn(**_seam_kwargs(ledger=written.append))

        call = _luna_call(1000, 500)
        factory.call_args.kwargs["recorder"](call)

        assert turn.calls == [call]
        assert written == []

        assert record_turn_calls(turn) == 1
        assert written == [call]
        assert turn.calls == [], "a second call would write the same row twice"


# ---------------------------------------------------------------------------
# The ledger, after the turn
# ---------------------------------------------------------------------------


class _RecordingClient(_Client):
    """A client that records one `ModelCall` per response, the way the hook does.

    `attach_async_ledger_hook` calls the recorder from inside the httpx response
    hook, which runs on the event loop the customer's turn is waiting on. That is
    the position this double reproduces.
    """

    def __init__(self, recorder, *replies):
        super().__init__(*replies)
        reply = self.completions.create

        async def create(**kwargs):
            completion = await reply(**kwargs)
            recorder(_luna_call(1000, 500))
            return completion

        self.completions.create = create


class TestTheLedgerRunsAfterTheTurn:
    def _turn_over_a_real_recorder(self, ledger):
        """A turn whose client records the way the production hook records."""
        with patch("app.services.agent_loop.make_async_client") as factory:
            built = build_agent_turn(**_seam_kwargs(ledger=ledger))
        client = _RecordingClient(
            factory.call_args.kwargs["recorder"], _completion(content="ok")
        )
        return _turn(client, calls=built.calls, ledger=built.ledger)

    async def test_no_tenant_connection_is_opened_while_the_loop_runs(self):
        """Six blocking connects inside the turn is the whole 90s budget.

        `record_model_call` opens, commits and closes a tenant connection per row,
        and a per-tenant Neon endpoint takes 8 to 20 seconds to wake. The recorder
        that runs during the turn only appends.
        """
        from app.core.model_client import ledger_recorder

        turn = self._turn_over_a_real_recorder(ledger_recorder(CONN_STR))

        with patch("app.core.model_client.record_model_call") as write:
            await _drive(turn)

            assert write.call_args_list == [], (
                "a tenant connection was opened from inside the turn. That is up "
                f"to 20 seconds of the customer's wall clock per row: {write.call_args_list}"
            )
            assert turn.calls, "the turn's recorder appended nothing to price"

            assert record_turn_calls(turn) == 1
            assert write.call_count == 1
            assert write.call_args.args[1] == CONN_STR, (
                "the row was written against something other than the tenant dsn "
                f"the seam was given: {write.call_args.args[1]!r}"
            )

    async def test_every_recorded_call_reaches_the_ledger_after_the_turn(self):
        """Deferring the write may not lose a row. Every call the turn made lands."""
        written: list[ModelCall] = []
        turn = self._turn_over_a_real_recorder(written.append)

        await _drive(turn)

        assert written == [], "the ledger was written during the turn"
        made = list(turn.calls)
        assert made, "the turn recorded no calls, so this proves nothing"

        assert record_turn_calls(turn) == len(made)
        assert written == made

    async def test_a_ledger_that_raises_never_reaches_the_caller(self):
        """Telemetry may not fail a turn a customer was already served."""
        def _refuse(call):
            raise RuntimeError("the tenant endpoint is suspended")

        turn = self._turn_over_a_real_recorder(_refuse)
        await _drive(turn)

        assert record_turn_calls(turn) == 0
        assert turn.calls == []


# ---------------------------------------------------------------------------
# One model call
# ---------------------------------------------------------------------------


class TestOneModelCall:
    async def test_a_turn_with_no_tool_call_returns_the_text(self):
        turn = _turn(_Client(_completion(content="Fourteen days, unopened.")))

        out, _ = await _drive(turn)

        assert out["response_text"] == "Fourteen days, unopened."
        assert out["tool_calls_log"] == []
        assert out["escalated"] is False
        assert out["num_turns"] == 1

    async def test_the_stop_reason_is_the_finish_reason_the_provider_sent(self):
        turn = _turn(_Client(_completion(content="ok", finish_reason="length")))

        out, _ = await _drive(turn)

        assert out["stop_reason"] == "length"

    async def test_a_reply_with_no_text_and_no_tool_call_yields_empty_text(self):
        """D-10's failure mode, which the module docstring cites and nothing drove.

        The model stops without saying anything and without asking for a tool. The
        turn is over with no text to serve, and the provider's own finish_reason is
        the only thing that says why. An empty answer that reports "stop" is a served
        silence; one that reports "length" is a truncation the ops room can read.
        """
        turn = _turn(_Client(_completion(content=None, finish_reason="length")))

        out, _ = await _drive(turn)

        assert out["response_text"] == ""
        assert out["stop_reason"] == "length"
        assert out["num_turns"] == 1
        assert out["tool_calls_log"] == []

    async def test_the_request_names_the_model_and_the_effort(self):
        client = _Client(_completion(content="ok"))

        await _drive(_turn(client))

        body = client.requests[0]
        assert body["model"] == "gpt-5.6-luna"
        assert body["reasoning_effort"] == "none"

    async def test_an_absent_effort_sends_no_field_at_all(self):
        """An absent field and an explicit null are different requests."""
        from app.core.model_client import OPENAI_PROVIDER, ModelRoute

        client = _Client(_completion(content="ok"))
        turn = _turn(client)
        turn = AgentTurn(
            client=client,
            route=ModelRoute(OPENAI_PROVIDER, "gpt-5.6-luna"),
            system_prompt=turn.system_prompt,
            tools=turn.tools,
            max_model_calls=turn.max_model_calls,
            max_budget_usd=turn.max_budget_usd,
            calls=turn.calls,
            ledger=turn.ledger,
        )

        await _drive(turn)

        assert "reasoning_effort" not in client.requests[0]

    async def test_the_request_carries_every_tool_schema(self):
        tools = [
            _tool("retrieve", _echo_handler, description="search the corpus"),
            _tool("clarify", _echo_handler),
        ]
        client = _Client(_completion(content="ok"))

        await _drive(_turn(client, tools=tools))

        assert client.requests[0]["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "retrieve",
                    "description": "search the corpus",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "clarify",
                    "description": "does one thing",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

    async def test_the_eleven_real_tools_reach_the_wire(self):
        client = _Client(_completion(content="ok"))

        await _drive(_turn(client, tools=agent_tool_definitions()))

        assert [t["function"]["name"] for t in client.requests[0]["tools"]] == ELEVEN_TOOLS

    async def test_history_rides_between_the_system_prompt_and_the_message(self):
        client = _Client(_completion(content="ok"))
        history = [
            {"role": "user", "content": "do you ship to Cape Town?"},
            {"role": "assistant", "content": "yes, in two days."},
        ]

        await _drive(_turn(client), message="and to Durban?", history=history)

        assert client.requests[0]["messages"] == [
            {"role": "system", "content": "you are a returns specialist"},
            {"role": "user", "content": "do you ship to Cape Town?"},
            {"role": "assistant", "content": "yes, in two days."},
            {"role": "user", "content": "and to Durban?"},
        ]


# ---------------------------------------------------------------------------
# The tool round trip
# ---------------------------------------------------------------------------


class TestTheToolRoundTrip:
    def _client(self):
        return _Client(
            _completion(
                content="let me look that up",
                tool_calls=[_tool_call("call-1", "retrieve", '{"query": "returns"}')],
                finish_reason="tool_calls",
            ),
            _completion(content="Fourteen days.", finish_reason="stop"),
        )

    async def test_the_loop_reaches_a_final_answer(self):
        client = self._client()

        out, _ = await _drive(_turn(client, tools=[_tool("retrieve", _echo_handler)]))

        assert out["response_text"] == "let me look that up\nFourteen days."
        assert out["num_turns"] == 2
        assert out["stop_reason"] == "stop"

    async def test_the_assistant_turn_is_replayed_with_its_tool_calls(self):
        client = self._client()

        await _drive(_turn(client, tools=[_tool("retrieve", _echo_handler)]))

        assert client.requests[1]["messages"][2] == {
            "role": "assistant",
            "content": "let me look that up",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "retrieve", "arguments": '{"query": "returns"}'},
                }
            ],
        }

    async def test_the_tool_message_carries_the_call_id_and_the_text(self):
        client = self._client()

        await _drive(_turn(client, tools=[_tool("retrieve", _echo_handler)]))

        assert client.requests[1]["messages"][3] == {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": "echo returns",
        }

    async def test_the_audit_entry_names_the_tool_its_input_and_its_id(self):
        client = self._client()

        out, _ = await _drive(_turn(client, tools=[_tool("retrieve", _echo_handler)]))

        entry = out["tool_calls_log"][0]
        assert entry["tool_name"] == "retrieve"
        assert entry["input"] == {"query": "returns"}
        assert entry["tool_use_id"] == "call-1"

    async def test_an_unknown_tool_comes_back_as_an_error_result(self):
        client = self._client()

        out, _ = await _drive(_turn(client, tools=[_tool("clarify", _echo_handler)]))

        tool_message = client.requests[1]["messages"][3]
        assert tool_message["role"] == "tool"
        assert "retrieve" in tool_message["content"]
        assert out["tool_calls_log"][0][RETRIEVE_RESULT_IS_ERROR_KEY] is True
        assert out["response_text"].endswith("Fourteen days.")

    async def test_arguments_that_are_not_json_come_back_as_an_error_result(self):
        client = _Client(
            _completion(
                tool_calls=[_tool_call("call-1", "retrieve", "{not json")],
                finish_reason="tool_calls",
            ),
            _completion(content="sorry, retrying.", finish_reason="stop"),
        )
        handled: list = []

        async def _handler(args):
            handled.append(args)
            return _text_wire("never reached")

        out, _ = await _drive(_turn(client, tools=[_tool("retrieve", _handler)]))

        assert handled == []
        assert client.requests[1]["messages"][3]["role"] == "tool"
        assert out["response_text"] == "sorry, retrying."
        assert out["tool_calls_log"][0]["input"] == {}

    async def test_a_raising_handler_comes_back_as_an_error_result(self):
        client = self._client()

        async def _boom(args):
            raise RuntimeError("the tenant database refused the connection")

        out, _ = await _drive(_turn(client, tools=[_tool("retrieve", _boom)]))

        tool_message = client.requests[1]["messages"][3]
        assert "RuntimeError" in tool_message["content"]
        assert out["stop_reason"] == "stop"
        assert out["tool_calls_log"][0][RETRIEVE_RESULT_IS_ERROR_KEY] is True

    async def test_two_tool_calls_in_one_reply_run_in_order(self):
        client = _Client(
            _completion(
                tool_calls=[
                    _tool_call("call-1", "retrieve", '{"query": "one"}'),
                    _tool_call("call-2", "retrieve", '{"query": "two"}'),
                ],
                finish_reason="tool_calls",
            ),
            _completion(content="done.", finish_reason="stop"),
        )

        out, _ = await _drive(_turn(client, tools=[_tool("retrieve", _echo_handler)]))

        assert [entry["tool_use_id"] for entry in out["tool_calls_log"]] == [
            "call-1",
            "call-2",
        ]
        assert [m["content"] for m in client.requests[1]["messages"][3:5]] == [
            "echo one",
            "echo two",
        ]

    async def test_a_handler_that_returns_no_dict_comes_back_as_an_error_result(self):
        """The fourth malformed-tool case, and it used to kill the turn.

        `wire_text` and the audit entry both call `.get` on what the dispatcher
        returns, outside the try that catches a raising handler, so a handler
        returning a string raised AttributeError out of the loop and the customer
        got nothing. The other three malformed cases are survivable by design.
        """
        client = self._client()

        async def _a_string(args):
            return "Fourteen days, unopened."

        out, _ = await _drive(_turn(client, tools=[_tool("retrieve", _a_string)]))

        tool_message = client.requests[1]["messages"][3]
        assert tool_message["role"] == "tool"
        assert "retrieve" in tool_message["content"]
        assert "str" in tool_message["content"]
        assert out["tool_calls_log"][0][RETRIEVE_RESULT_IS_ERROR_KEY] is True
        assert out["response_text"].endswith("Fourteen days.")


# ---------------------------------------------------------------------------
# The client, and what the loop does with the provider's failures
# ---------------------------------------------------------------------------


class _RaisingCompletions:
    """A provider that refuses. One transport error per call, as the SDK raises it."""

    def __init__(self, exc):
        self.exc = exc
        self.requests: list[dict] = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        raise self.exc


class _RaisingClient(_Client):
    def __init__(self, exc):
        super().__init__()
        self.completions = _RaisingCompletions(exc)
        self.chat = SimpleNamespace(completions=self.completions)


class _RateLimited(Exception):
    """Stands in for openai.RateLimitError. The loop never names a provider type."""


class _ServerError(Exception):
    """Stands in for openai.InternalServerError."""


class TestTheClientIsClosed:
    async def test_the_client_is_closed_when_the_turn_ends(self):
        """One httpx transport per turn, in a worker that runs for weeks."""
        client = _Client(_completion(content="ok"))

        await _drive(_turn(client))

        assert client.closed == 1

    @pytest.mark.parametrize(
        "exc",
        [_RateLimited("429 rate limit reached"), _ServerError("500 upstream error")],
        ids=["429", "500"],
    )
    async def test_a_provider_failure_propagates_and_still_closes_the_client(self, exc):
        """The Celery task's retry is the handler. The loop may not swallow this.

        `run_agent_turn` catches, emits agent.failed and retries; a loop that
        turned a 429 into an empty answer would serve the customer silence and
        cost the turn its retry.
        """
        client = _RaisingClient(exc)

        with pytest.raises(type(exc)):
            await _drive(_turn(client))

        assert client.closed == 1, (
            "the turn died and left its httpx transport open. asyncio.run tears "
            "the loop down under it, one leaked transport per failed turn."
        )

    async def test_the_client_is_closed_when_a_tool_kills_the_turn(self):
        """Any raise out of the loop body, not only the provider's."""
        client = _Client(
            _completion(
                tool_calls=[_tool_call("call-1", "retrieve", '{"query": "x"}')],
                finish_reason="tool_calls",
            )
        )

        with patch(
            "app.services.agent_loop.wire_text", side_effect=RuntimeError("unreadable")
        ), pytest.raises(RuntimeError):
            await _drive(_turn(client, tools=[_tool("retrieve", _echo_handler)]))

        assert client.closed == 1


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------


class TestEscalation:
    async def test_escalation_is_read_off_the_tool_the_model_called(self):
        """Evidence, never prose (T-04-03-03)."""
        client = _Client(
            _completion(
                tool_calls=[
                    _tool_call(
                        "call-1",
                        "escalate_to_human",
                        '{"reason": "asked three times", "context": "order 9912"}',
                    )
                ],
                finish_reason="tool_calls",
            ),
            _completion(content="a colleague will call you.", finish_reason="stop"),
        )

        out, _ = await _drive(
            _turn(client, tools=[_tool("escalate_to_human", _echo_handler)])
        )

        assert out["escalated"] is True
        assert out["escalation_reason"] == "asked three times"
        assert out["escalation_context"] == "order 9912"

    async def test_prose_about_escalating_does_not_escalate(self):
        turn = _turn(_Client(_completion(content="I will escalate_to_human for you.")))

        out, _ = await _drive(turn)

        assert out["escalated"] is False
        assert out["escalation_reason"] is None


# ---------------------------------------------------------------------------
# The two ceilings
# ---------------------------------------------------------------------------


class TestTheCeilings:
    def _always_calls_a_tool(self):
        return _Client(
            _completion(
                tool_calls=[_tool_call("call-1", "retrieve", '{"query": "again"}')],
                finish_reason="tool_calls",
            )
        )

    async def test_the_loop_stops_at_max_model_calls(self):
        client = self._always_calls_a_tool()
        turn = _turn(client, tools=[_tool("retrieve", _echo_handler)], max_model_calls=3)

        out, _ = await _drive(turn)

        assert out["num_turns"] == 3
        assert out["stop_reason"] == "max_model_calls"
        assert len(client.requests) == 3

    async def test_spend_over_the_ceiling_stops_the_turn(self):
        """1000 in and 500 out of Luna is $0.0008, over a $0.0005 ceiling."""
        client = self._always_calls_a_tool()
        calls: list[ModelCall] = []
        turn = _turn(
            client,
            tools=[_tool("retrieve", _echo_handler)],
            max_budget_usd=0.0005,
            calls=calls,
        )
        calls.append(_luna_call(1000, 500))

        out, _ = await _drive(turn)

        assert out["stop_reason"] == "budget_exceeded"
        assert out["num_turns"] == 1
        assert len(client.requests) == 1

    async def test_spend_under_the_ceiling_runs_the_turn_out(self):
        client = self._always_calls_a_tool()
        calls = [_luna_call(1000, 500)]
        turn = _turn(
            client,
            tools=[_tool("retrieve", _echo_handler)],
            max_model_calls=2,
            max_budget_usd=5.0,
            calls=calls,
        )

        out, _ = await _drive(turn)

        assert out["stop_reason"] == "max_model_calls"
        assert out["num_turns"] == 2

    async def test_the_first_call_is_never_blocked_by_the_guard(self):
        """A turn that has spent nothing yet cannot be over its own ceiling."""
        client = _Client(_completion(content="ok"))
        turn = _turn(client, max_budget_usd=0.0)

        out, _ = await _drive(turn)

        assert out["num_turns"] == 1
        assert out["stop_reason"] == "stop"

    async def test_an_unpriced_call_degrades_the_guard_and_the_turn_runs_on(self):
        """The guard is telemetry shaped. A served turn never dies for it."""
        client = self._always_calls_a_tool()
        calls = [_luna_call(1000, 500, model="gpt-5.6-nobody-priced")]
        turn = _turn(
            client,
            tools=[_tool("retrieve", _echo_handler)],
            max_model_calls=2,
            max_budget_usd=0.0000001,
            calls=calls,
        )

        out, _ = await _drive(turn)

        assert out["stop_reason"] == "max_model_calls"
        assert out["num_turns"] == 2


# ---------------------------------------------------------------------------
# The retrieve capture
# ---------------------------------------------------------------------------

RIDE_ALONG = {
    "query": "what is the return window?",
    "strategy": "rerank",
    "chunks": [
        {
            "chunk_id": "c1",
            "document_id": "d1",
            "content": "Unopened bags, 14 days.",
            "score": 0.95,
            "rank": 1,
        },
        {
            "chunk_id": "c2",
            "document_id": "d2",
            "content": "Refunds take 5 days.",
            "score": 0.8,
            "rank": 2,
        },
    ],
}


class TestTheRetrieveCapture:
    def _client(self):
        return _Client(
            _completion(
                tool_calls=[_tool_call("call-1", "retrieve", '{"query": "returns"}')],
                finish_reason="tool_calls",
            ),
            _completion(content="Fourteen days.", finish_reason="stop"),
        )

    async def _entry(self, wire):
        async def _handler(args):
            return wire

        out, _ = await _drive(
            _turn(self._client(), tools=[_tool("retrieve", _handler)])
        )
        return out["tool_calls_log"][0]

    async def test_the_ride_along_becomes_one_string_per_chunk(self):
        wire = {**_text_wire("framed"), "_retrieved_context": RIDE_ALONG}

        entry = await self._entry(wire)

        assert entry[RETRIEVE_CHUNKS_KEY] == [
            "Unopened bags, 14 days.",
            "Refunds take 5 days.",
        ]
        assert entry[RETRIEVE_CHUNKS_SOURCE_KEY] == RETRIEVE_CHUNKS_PARSED
        assert entry[RETRIEVE_RESULT_IS_ERROR_KEY] is False

    async def test_the_judge_capture_carries_the_provenance_the_agent_saw(self):
        wire = {**_text_wire("framed"), "_retrieved_context": RIDE_ALONG}

        entry = await self._entry(wire)

        assert entry[RETRIEVE_JUDGE_CHUNKS_KEY] == [
            "[source: d1 | chunk: c1 | score: 0.95]\nUnopened bags, 14 days.",
            "[source: d2 | chunk: c2 | score: 0.8]\nRefunds take 5 days.",
        ]

    async def test_an_absent_ride_along_is_the_unparsed_state(self):
        """A hand-built wire driven against a defensive branch, not a live shape.

        `retrieve_tool` attaches `_retrieved_context` on its one success path and
        every other producer of a retrieve wire sets `is_error`, so nothing in
        this tree returns a ride-along-less success. The wire below is written by
        hand to reach the branch that catches the first producer that does.
        """
        entry = await self._entry(_text_wire("no ride along here"))

        assert entry[RETRIEVE_CHUNKS_KEY] == []
        assert entry[RETRIEVE_JUDGE_CHUNKS_KEY] == []
        assert entry[RETRIEVE_CHUNKS_SOURCE_KEY] == RETRIEVE_CHUNKS_UNPARSED
        assert entry["result"]

    async def test_an_errored_retrieve_is_flagged(self):
        """A DoS-guard refusal is a control message, never a retrieved passage."""
        entry = await self._entry(
            _text_wire("Retrieve quota exceeded for this turn", is_error=True)
        )

        assert entry[RETRIEVE_RESULT_IS_ERROR_KEY] is True
        assert entry[RETRIEVE_CHUNKS_SOURCE_KEY] == RETRIEVE_CHUNKS_UNPARSED

    async def test_an_errored_retrieve_still_emits_its_event(self):
        """The refusal reaches the sampler, because a silent turn reads as no retrieval.

        `retrieval_eval._fetch_turn_context` selects `agent.tool_result` rows and
        joins them on `payload["tool_name"] == "retrieve"`. A turn whose retrieve
        errored and emitted nothing looks identical there to a turn that never
        retrieved, so the sampler would report a DoS-guard refusal as an absence.
        The old SDK reader had branches between the emit and the capture and this
        pin travelled with it; the loop emits before it captures, and this holds
        that line.
        """
        async def _handler(args):
            return _text_wire("Retrieve quota exceeded for this turn", is_error=True)

        _out, events = await _drive(
            _turn(self._client(), tools=[_tool("retrieve", _handler)])
        )

        results = [payload for name, payload in events if name == "agent.tool_result"]
        assert results == [
            {"tool_name": "retrieve", "summary": "Retrieve quota exceeded for this turn"}
        ]

    async def test_an_empty_chunk_list_is_parsed_and_empty(self):
        """Zero hits and an unreadable payload are different observations."""
        wire = {
            **_text_wire("framed"),
            "_retrieved_context": {**RIDE_ALONG, "chunks": []},
        }

        entry = await self._entry(wire)

        assert entry[RETRIEVE_CHUNKS_KEY] == []
        assert entry[RETRIEVE_CHUNKS_SOURCE_KEY] == RETRIEVE_CHUNKS_PARSED

    async def test_a_non_retrieve_tool_carries_no_capture_keys(self):
        client = _Client(
            _completion(
                tool_calls=[_tool_call("call-1", "clarify", '{"question": "which order?"}')],
                finish_reason="tool_calls",
            ),
            _completion(content="thanks.", finish_reason="stop"),
        )

        out, _ = await _drive(_turn(client, tools=[_tool("clarify", _echo_handler)]))

        entry = out["tool_calls_log"][0]
        assert RETRIEVE_CHUNKS_KEY not in entry
        assert RETRIEVE_RESULT_IS_ERROR_KEY not in entry


# ---------------------------------------------------------------------------
# The SSE events
# ---------------------------------------------------------------------------


class TestTheEvents:
    async def test_the_tool_call_event_names_the_tool_and_its_input(self):
        client = _Client(
            _completion(
                tool_calls=[_tool_call("call-1", "retrieve", '{"query": "returns"}')],
                finish_reason="tool_calls",
            ),
            _completion(content="done.", finish_reason="stop"),
        )

        _, events = await _drive(_turn(client, tools=[_tool("retrieve", _echo_handler)]))

        assert events[0] == (
            "agent.tool_call",
            {"tool_name": "retrieve", "input": {"query": "returns"}},
        )

    async def test_the_tool_result_event_is_the_shape_retrieval_eval_joins_on(self):
        """retrieval_eval selects on payload["tool_name"] and reads payload["summary"]."""
        client = _Client(
            _completion(
                tool_calls=[_tool_call("call-1", "retrieve", '{"query": "returns"}')],
                finish_reason="tool_calls",
            ),
            _completion(content="done.", finish_reason="stop"),
        )

        _, events = await _drive(_turn(client, tools=[_tool("retrieve", _echo_handler)]))

        assert events[1] == (
            "agent.tool_result",
            {"tool_name": "retrieve", "summary": "echo returns"},
        )

    async def test_a_turn_with_no_tool_call_emits_nothing(self):
        _, events = await _drive(_turn(_Client(_completion(content="ok"))))

        assert events == []
