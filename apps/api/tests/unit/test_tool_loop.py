"""Tests for app.services.tool_loop, the loop the Attacker and Orchestrator run (#49).

WHAT THE DOUBLES STAND IN FOR
    Same shape as `test_agent_loop.py`, and deliberately so. The loop never
    imports `openai`, so these tests never import it either. A completion is a
    plain object carrying `choices[0].message.content`,
    `choices[0].message.tool_calls` and `choices[0].finish_reason`, which is the
    whole of what the loop reads off a response.

WHY THE DIVERGENCE TEST IS THE FIRST ONE
    `tool_loop` exists so the customer turn, the red-team Attacker and the
    deployment Orchestrator dispatch tools the same way. A shared module makes
    that claim; it does not prove it. `TestTheTwoLoopsAgree` is the proof, and it
    was written because the first draft of `run_tool_loop` sent
    `json.dumps(payload)` as the tool message while `agent_loop._run_tool_call`
    sent `wire_text(payload)`. Both are defensible in isolation. Together they
    mean the Attacker reads the MCP envelope while the customer agent reads the
    text inside it, so a probe measures a model reasoning about different bytes
    from the one production serves. That is the whole failure the extraction
    exists to prevent, reintroduced by the extraction itself.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.domain.tool_def import ToolDefinition, tool
from app.services.tool_loop import (
    ToolLoopResult,
    dispatch,
    error_wire,
    first_choice,
    run_tool_loop,
    tool_arguments,
    tools_wire,
)

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

    No `close`, and that is the contract rather than an omission.
    `run_tool_loop` never closes its client, because the Attacker runs several
    sequences on one. `run_agent_loop` does close its own, because an
    `AgentTurn` is single-use.
    """

    def __init__(self, *replies):
        self.completions = _Completions(replies)
        self.chat = SimpleNamespace(completions=self.completions)

    @property
    def requests(self) -> list[dict]:
        return self.completions.requests


def _text_wire(text: str, is_error: bool = False) -> dict:
    wire: dict = {"content": [{"type": "text", "text": text}]}
    if is_error:
        wire["is_error"] = True
    return wire


def _echo_tool(name: str = "send_probe", reply: str = "the agent answered"):
    @tool(name, "sends one probe", {"type": "object", "properties": {}})
    async def _handler(args: dict) -> dict:
        return _text_wire(reply)

    return _handler


def _run(coro):
    return asyncio.run(coro)


def _drive(*replies, tools=None, **kwargs):
    """Run one loop over scripted replies and hand back the result and the client."""
    client = _Client(*replies)
    result = _run(
        run_tool_loop(
            "open",
            client=client,
            model="gpt-5.6-luna",
            system_prompt="you are an attacker",
            tools=tools if tools is not None else [_echo_tool()],
            max_turns=kwargs.pop("max_turns", 4),
            **kwargs,
        )
    )
    return result, client


# ---------------------------------------------------------------------------
# The claim the module is named after
# ---------------------------------------------------------------------------


class TestTheTwoLoopsAgree:
    """One dispatch, one tool-message shape, for the customer turn and the probe.

    See the module docstring. These assert against
    `app.services.agent_loop` directly rather than against a copied literal,
    because a literal is what drifts.
    """

    def test_both_loops_put_the_same_text_in_a_tool_message(self):
        from app.domain.tool_result import wire_text

        wire = _text_wire("Returns are accepted within 14 days.")
        _, client = _drive(
            _completion(tool_calls=[_tool_call("c1", "send_probe", "{}")]),
            _completion(content="done"),
            tools=[_echo_tool(reply="Returns are accepted within 14 days.")],
        )

        sent = [
            message
            for message in client.requests[-1]["messages"]
            if message["role"] == "tool"
        ]
        assert len(sent) == 1
        assert sent[0]["content"] == wire_text(wire), (
            "run_tool_loop put something other than wire_text in the tool message. "
            "agent_loop._run_tool_call sends wire_text, so the Attacker would be "
            "reasoning about different bytes from the ones a customer's agent reads."
        )

    def test_both_loops_dispatch_through_the_same_function(self):
        """`agent_loop` imports `dispatch` rather than owning a second copy."""
        import app.services.agent_loop as agent_loop
        import app.services.tool_loop as tool_loop

        assert agent_loop.dispatch is tool_loop.dispatch
        assert agent_loop.tool_arguments is tool_loop.tool_arguments
        assert agent_loop.tools_wire is tool_loop.tools_wire

    def test_neither_loop_owns_a_private_dispatch(self):
        """The extraction is not done while a shadow copy is still importable.

        Every name this branch moved out of `agent_loop`, not a sample of them.
        The list was four of the six until an adversarial reviewer pointed out
        that `_assistant_turn` and `_error_wire` could come back as private
        copies with this test green.
        """
        import app.services.agent_loop as agent_loop

        moved = (
            "_dispatch",
            "_dispatch_outcome",
            "_tools_wire",
            "_tool_arguments",
            "_first_choice",
            "_assistant_turn",
            "_error_wire",
        )
        for shadowed in moved:
            assert not hasattr(agent_loop, shadowed), (
                f"agent_loop.{shadowed} came back. Two implementations is how the "
                "probe path and the customer path start behaving differently."
            )


# ---------------------------------------------------------------------------
# The wire
# ---------------------------------------------------------------------------


class TestTheWire:
    def test_a_tool_reaches_the_wire_as_an_openai_function(self):
        [sent] = tools_wire([_echo_tool("clarify")])

        assert sent == {
            "type": "function",
            "function": {
                "name": "clarify",
                "description": "sends one probe",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    def test_the_system_prompt_opens_the_conversation(self):
        _, client = _drive(_completion(content="hello"))

        assert client.requests[0]["messages"][0] == {
            "role": "system",
            "content": "you are an attacker",
        }

    def test_an_absent_effort_sends_no_field_at_all(self):
        """An explicit null asks for the provider default, a different request."""
        _, client = _drive(_completion(content="hello"))

        assert "reasoning_effort" not in client.requests[0]

    def test_a_named_effort_travels_on_every_call(self):
        _, client = _drive(
            _completion(tool_calls=[_tool_call("c1", "send_probe", "{}")]),
            _completion(content="done"),
            reasoning_effort="none",
        )

        assert [request["reasoning_effort"] for request in client.requests] == [
            "none",
            "none",
        ]


# ---------------------------------------------------------------------------
# What ends a loop
# ---------------------------------------------------------------------------


class TestWhatStopsTheLoop:
    def test_a_reply_with_no_tool_calls_ends_it(self):
        result, client = _drive(_completion(content="nothing to probe"))

        assert result.stop_reason == "stop"
        assert result.num_turns == 1
        assert result.response_text == "nothing to probe"

    def test_the_turn_ceiling_ends_it_and_says_so(self):
        result, _ = _drive(
            _completion(tool_calls=[_tool_call("c1", "send_probe", "{}")]),
            max_turns=3,
        )

        assert result.stop_reason == "max_turns"
        assert result.num_turns == 3

    def test_a_reply_carrying_no_choices_ends_it_without_raising(self):
        """`choices[0]` on an empty list raised IndexError straight out of the loop."""
        result, _ = _drive(SimpleNamespace(choices=[]))

        assert result.stop_reason == "no_choices"
        assert result.response_text == ""

    def test_stop_after_ends_the_loop_on_the_named_tool(self):
        result, _ = _drive(
            _completion(tool_calls=[_tool_call("c1", "submit_report", "{}")]),
            tools=[_echo_tool("submit_report")],
            stop_after=frozenset({"submit_report"}),
        )

        assert result.stop_reason == "stop_after"
        assert result.num_turns == 1

    def test_stop_after_runs_the_handler_before_it_stops(self):
        """The Orchestrator's report is written BY the handler, so stopping first
        would leave the container empty and the loop reporting a report it never took."""
        ran: list[dict] = []

        @tool("submit_report", "files the report", {"type": "object", "properties": {}})
        async def _submit(args: dict) -> dict:
            ran.append(args)
            return _text_wire("report recorded")

        _drive(
            _completion(tool_calls=[_tool_call("c1", "submit_report", '{"verdict": "ship"}')]),
            tools=[_submit],
            stop_after=frozenset({"submit_report"}),
        )

        assert ran == [{"verdict": "ship"}], (
            "stop_after returned before the handler ran. The handler is what writes "
            "the report, so the caller would read an empty container."
        )

    def test_a_name_no_tool_carries_does_not_end_the_loop(self):
        """`stop_after` claims the handler ran, and the handler is what writes the
        report. A model naming a tool that does not exist produced no report, so
        stopping would hand the caller an empty container and a stop_reason saying
        the report arrived."""
        result, _ = _drive(
            _completion(tool_calls=[_tool_call("c1", "submit_report", "{}")]),
            _completion(content="I could not file that."),
            tools=[_echo_tool("send_probe")],
            stop_after=frozenset({"submit_report"}),
        )

        assert result.stop_reason == "stop"
        assert result.num_turns == 2

    def test_unreadable_arguments_do_not_end_the_loop_either(self):
        """Same claim. `tool_arguments` refused these, so the handler never ran."""
        result, _ = _drive(
            _completion(tool_calls=[_tool_call("c1", "submit_report", "{not json")]),
            _completion(content="retrying"),
            tools=[_echo_tool("submit_report")],
            stop_after=frozenset({"submit_report"}),
        )

        assert result.stop_reason == "stop"

    def test_a_raising_handler_does_not_end_the_loop(self):
        """The case the first version of this guard missed.

        `ran` was computed from the tool's EXISTENCE, so a `submit_report` whose
        handler raised stopped the loop with `stop_reason="stop_after"` and an
        empty container. Two adversarial reviewers found it independently on #49.
        """

        @tool("submit_report", "files the report", {"type": "object", "properties": {}})
        async def _boom(args: dict) -> dict:
            raise RuntimeError("the control DB is asleep")

        result, _ = _drive(
            _completion(tool_calls=[_tool_call("c1", "submit_report", "{}")]),
            _completion(content="I could not file that."),
            tools=[_boom],
            stop_after=frozenset({"submit_report"}),
        )

        assert result.stop_reason == "stop", (
            "the loop stopped on a handler that raised, so the caller reads an empty "
            "container and a stop_reason saying the report arrived."
        )

    def test_a_handler_returning_a_non_dict_does_not_end_the_loop_either(self):
        """Same claim, fourth path. `dispatch` synthesised the wire, not the handler."""

        @tool("submit_report", "files the report", {"type": "object", "properties": {}})
        async def _wrong(args: dict) -> dict:
            return "filed"  # type: ignore[return-value]

        result, _ = _drive(
            _completion(tool_calls=[_tool_call("c1", "submit_report", "{}")]),
            _completion(content="retrying"),
            tools=[_wrong],
            stop_after=frozenset({"submit_report"}),
        )

        assert result.stop_reason == "stop"

    def test_a_handler_that_ran_and_refused_still_ends_the_loop(self):
        """`ran` is not `succeeded`. A gate returning is_error ran perfectly well.

        The anti-tautology partner of the two above: without it, `ran = False`
        everywhere would pass all three.
        """

        @tool("submit_report", "files the report", {"type": "object", "properties": {}})
        async def _refused(args: dict) -> dict:
            return _text_wire("the envelope forbids this", is_error=True)

        result, _ = _drive(
            _completion(tool_calls=[_tool_call("c1", "submit_report", "{}")]),
            tools=[_refused],
            stop_after=frozenset({"submit_report"}),
        )

        assert result.stop_reason == "stop_after"

    def test_a_tool_outside_stop_after_does_not_end_the_loop(self):
        result, _ = _drive(
            _completion(tool_calls=[_tool_call("c1", "send_probe", "{}")]),
            _completion(content="done"),
            stop_after=frozenset({"submit_report"}),
        )

        assert result.stop_reason == "stop"
        assert result.num_turns == 2


# ---------------------------------------------------------------------------
# Observation, which the Attacker depends on
# ---------------------------------------------------------------------------


class TestObservation:
    def test_on_tool_use_fires_as_the_model_asks(self):
        seen: list[str] = []
        _drive(
            _completion(tool_calls=[_tool_call("c1", "send_probe", "{}")]),
            _completion(content="done"),
            on_tool_use=seen.append,
        )

        assert seen == ["send_probe"]

    def test_on_tool_use_fires_before_the_handler_runs(self):
        """One `wait_for` budget covers every attack sequence, so a timeout must keep
        what was already observed. A callback that fired after the handler would lose
        the probe that timed out, which is the one worth knowing about."""
        order: list[str] = []

        @tool("send_probe", "sends one probe", {"type": "object", "properties": {}})
        async def _slow(args: dict) -> dict:
            order.append("handler")
            return _text_wire("answered")

        _drive(
            _completion(tool_calls=[_tool_call("c1", "send_probe", "{}")]),
            _completion(content="done"),
            tools=[_slow],
            on_tool_use=lambda name: order.append("observed"),
        )

        assert order == ["observed", "handler"]

    def test_every_call_lands_on_tool_names_including_repeats(self):
        result, _ = _drive(
            _completion(
                tool_calls=[
                    _tool_call("c1", "send_probe", "{}"),
                    _tool_call("c2", "send_probe", "{}"),
                ]
            ),
            _completion(content="done"),
        )

        assert result.tool_names == ["send_probe", "send_probe"]

    def test_a_loop_with_no_callback_still_runs(self):
        result, _ = _drive(
            _completion(tool_calls=[_tool_call("c1", "send_probe", "{}")]),
            _completion(content="done"),
        )

        assert result.num_turns == 2


# ---------------------------------------------------------------------------
# Dispatch, which never raises into the loop
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_a_name_no_tool_carries_comes_back_as_an_error_result(self):
        wire = _run(dispatch([_echo_tool()], "rm_rf", {}))

        assert wire["is_error"] is True
        assert "rm_rf" in wire["content"][0]["text"]

    def test_the_tool_list_is_the_allowlist(self):
        """There is no separate allowlist, so a tool absent from the list cannot run."""
        ran: list[str] = []

        @tool("forbidden", "should never run", {"type": "object", "properties": {}})
        async def _forbidden(args: dict) -> dict:
            ran.append("ran")
            return _text_wire("ran")

        _run(dispatch([_echo_tool()], "forbidden", {}))

        assert ran == []

    def test_a_raising_handler_becomes_a_result_the_model_can_read(self):
        @tool("boom", "raises", {"type": "object", "properties": {}})
        async def _boom(args: dict) -> dict:
            raise RuntimeError("the tenant DB is asleep")

        wire = _run(dispatch([_boom], "boom", {}))

        assert wire["is_error"] is True
        assert "RuntimeError" in wire["content"][0]["text"]

    def test_the_message_names_the_type_and_never_the_exception_text(self):
        """A tenant connection string and a provider body both arrive inside
        exception text, and issue #83 is that reaching a reader it should not.
        `dispatch` reports the exception's TYPE, and the tool message it builds
        goes to the model, so the text has to stay out of it."""

        @tool("boom", "raises", {"type": "object", "properties": {}})
        async def _boom(args: dict) -> dict:
            raise RuntimeError("auth failed for your-tenant-token-here")

        wire = _run(dispatch([_boom], "boom", {}))

        assert "your-tenant-token-here" not in wire["content"][0]["text"]
        assert "RuntimeError" in wire["content"][0]["text"]

    def test_a_handler_returning_something_other_than_a_dict_is_caught(self):
        @tool("wrong", "returns a string", {"type": "object", "properties": {}})
        async def _wrong(args: dict) -> dict:
            return "just text"  # type: ignore[return-value]

        wire = _run(dispatch([_wrong], "wrong", {}))

        assert wire["is_error"] is True
        assert "str" in wire["content"][0]["text"]

    def test_a_tool_failure_does_not_end_the_turn(self):
        @tool("boom", "raises", {"type": "object", "properties": {}})
        async def _boom(args: dict) -> dict:
            raise RuntimeError("nope")

        result, _ = _drive(
            _completion(tool_calls=[_tool_call("c1", "boom", "{}")]),
            _completion(content="I could not check that."),
            tools=[_boom],
        )

        assert result.stop_reason == "stop"
        assert result.response_text == "I could not check that."


# ---------------------------------------------------------------------------
# Arguments a model wrote
# ---------------------------------------------------------------------------


class TestToolArguments:
    def test_an_object_reads_as_itself(self):
        args, refusal = tool_arguments("send_probe", '{"message": "hi"}')

        assert args == {"message": "hi"}
        assert refusal is None

    def test_an_empty_string_reads_as_no_arguments(self):
        assert tool_arguments("send_probe", "") == ({}, None)

    def test_malformed_json_comes_back_as_a_refusal_the_model_can_correct(self):
        args, refusal = tool_arguments("send_probe", "{not json")

        assert args == {}
        assert refusal is not None and refusal["is_error"] is True

    def test_a_json_scalar_is_refused_too(self):
        """`json.loads("4")` succeeds and hands the handler an int for `args`."""
        args, refusal = tool_arguments("send_probe", "4")

        assert args == {}
        assert refusal is not None and refusal["is_error"] is True

    def test_a_refusal_reaches_the_model_and_the_handler_never_runs(self):
        ran: list[str] = []

        @tool("send_probe", "sends one probe", {"type": "object", "properties": {}})
        async def _handler(args: dict) -> dict:
            ran.append("ran")
            return _text_wire("answered")

        _drive(
            _completion(tool_calls=[_tool_call("c1", "send_probe", "{not json")]),
            _completion(content="sorry"),
            tools=[_handler],
        )

        assert ran == []


# ---------------------------------------------------------------------------
# The replayed assistant turn
# ---------------------------------------------------------------------------


class TestTheAssistantTurnIsReplayed:
    def test_the_second_request_carries_the_assistant_turn_and_its_tool_result(self):
        _, client = _drive(
            _completion(content="checking", tool_calls=[_tool_call("c1", "send_probe", "{}")]),
            _completion(content="done"),
        )

        roles = [message["role"] for message in client.requests[-1]["messages"]]
        assert roles == ["system", "user", "assistant", "tool"]

    def test_the_replay_carries_only_the_fields_we_send(self):
        """A `model_dump` of the response would send back whatever the SDK holds."""
        _, client = _drive(
            _completion(tool_calls=[_tool_call("c1", "send_probe", '{"message": "hi"}')]),
            _completion(content="done"),
        )

        assistant = client.requests[-1]["messages"][2]
        assert set(assistant) == {"role", "content", "tool_calls"}
        assert assistant["tool_calls"][0]["function"]["arguments"] == '{"message": "hi"}'

    def test_the_tool_message_carries_the_call_id_it_answers(self):
        _, client = _drive(
            _completion(tool_calls=[_tool_call("c7", "send_probe", "{}")]),
            _completion(content="done"),
        )

        assert client.requests[-1]["messages"][3]["tool_call_id"] == "c7"


# ---------------------------------------------------------------------------
# Small pieces
# ---------------------------------------------------------------------------


class TestSmallPieces:
    def test_first_choice_hands_back_none_for_an_empty_reply(self):
        assert first_choice(SimpleNamespace(choices=[])) is None

    def test_first_choice_tolerates_a_response_with_no_choices_attribute(self):
        assert first_choice(SimpleNamespace()) is None

    def test_error_wire_is_readable_by_wire_text(self):
        from app.domain.tool_result import wire_text

        assert wire_text(error_wire("it broke")) == "it broke"

    def test_text_parts_across_turns_are_joined(self):
        result, _ = _drive(
            _completion(content="first", tool_calls=[_tool_call("c1", "send_probe", "{}")]),
            _completion(content="second"),
        )

        assert result.response_text == "first\nsecond"

    def test_a_result_starts_empty_rather_than_absent(self):
        result = ToolLoopResult()

        assert result.response_text == ""
        assert result.tool_names == []
        assert result.num_turns == 0
        assert result.stop_reason is None

    def test_two_results_do_not_share_a_tool_names_list(self):
        first, second = ToolLoopResult(), ToolLoopResult()
        first.tool_names.append("send_probe")

        assert second.tool_names == []


# ---------------------------------------------------------------------------
# The tool declaration itself
# ---------------------------------------------------------------------------


class TestTheOwnedToolDecorator:
    def test_the_decorator_builds_the_four_attributes_the_loop_reads(self):
        definition = _echo_tool("clarify")

        assert isinstance(definition, ToolDefinition)
        assert definition.name == "clarify"
        assert definition.input_schema == {"type": "object", "properties": {}}
        assert _run(definition.handler({}))["content"][0]["text"] == "the agent answered"

    def test_a_definition_is_frozen(self):
        import dataclasses

        with pytest.raises(dataclasses.FrozenInstanceError):
            _echo_tool().name = "something_else"  # type: ignore[misc]

    def test_a_schema_that_is_not_a_dict_is_refused_at_declaration(self):
        """The SDK accepted `{"name": str}` shorthand and converted it on the way to
        an MCP server. `tools_wire` has no such step and sends it verbatim."""
        with pytest.raises(TypeError, match="JSON Schema dict"):
            tool("greet", "greets", [("name", str)])  # type: ignore[arg-type]

    def test_a_sync_handler_is_refused_at_declaration(self):
        """`dispatch` awaits the handler inside a `try` that turns every exception
        into an error wire dict, so a sync handler ships broken and silent."""
        with pytest.raises(TypeError, match="async def"):

            @tool("greet", "greets", {"type": "object", "properties": {}})
            def _sync(args: dict) -> dict:  # type: ignore[misc]
                return _text_wire("hi")

    def test_the_refusal_names_the_tool(self):
        with pytest.raises(TypeError, match="'greet'"):
            tool("greet", "greets", "not a schema")  # type: ignore[arg-type]
