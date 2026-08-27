"""The provider-shaped half of a bounded tool loop (ticket #49, ADR 0008).

WHY THIS MODULE EXISTS
    Three loops in this codebase send a system prompt and a tool list to a
    model, read tool calls back, run them and feed the results in again. One is
    the customer turn (`app.services.agent_loop`). The other two are the red-team
    Attacker and the deployment Orchestrator, which ran on `claude_agent_sdk`
    until #49 and now do not.

    The parts they share are the wire: how a tool list is spelled, how a model's
    argument string is parsed, how a handler that raises becomes a result the
    model can read, how the assistant's own turn is replayed. Those live here and
    have exactly one implementation, because two implementations of `dispatch`
    is how a probe ends up measuring something the customer path would not do.

    What is NOT shared stays in `agent_loop`: the SSE emit, the escalation
    ledger, the retrieval capture, the spend ceiling read off `model_calls`. The
    Attacker has no customer waiting on a stream and no tenant DB row to write,
    so a loop general enough for both would carry branches for cases it never
    serves.

WHAT THE OWNED LOOP DOES NOT NEED
    The SDK options both callers built are gone, and with them four controls.
    `tools=[]`, `strict_mcp_config=True`, `allowed_tools` and
    `permission_mode="dontAsk"`. What the surviving comments say those four
    prevented, that the CLI would otherwise hand a red-team agent Bash, Read and
    Edit on the worker's filesystem and merge a project `.mcp.json`, is INHERITED
    from the deleted tests and was never measured here. It is now unfalsifiable
    in this repo, because the package is uninstalled. Repeat it as history, not
    as a fact about a live system.

    What IS measured is the half that matters. `run_tool_loop` has no built-ins
    to remove, no config file to merge and no permission model to set. The
    `tools` argument is the entire set of tools that exist for that loop, and
    `dispatch` refuses a name that is not in it, verified against twelve escape
    attempts including `Bash`, `__class__`, a `mcp__`-prefixed name and a
    trailing null. The allowlist and the tool list are the same object, and that
    claim stands on its own without the counterfactual.

WHAT THE CALLER PASSES `run_tool_loop`
    `client` is an async OpenAI client built through
    `app.core.model_client.make_async_client`, so the `model_calls` hook is
    attached and cannot be bypassed. The CALLER owns it and closes it, because
    the Attacker runs several sequences on one client.

    `model` is the id from the caller's own `PURPOSE_ROUTES` row, and
    `reasoning_effort` travels only when that route names one. An explicit null
    is a different request from no field at all.

    `tools` is every tool the loop has. There is no separate allowlist.

    `on_tool_use` fires with each tool name AS the model asks for it, before the
    handler runs. The Attacker's observation ledger reads this rather than the
    returned `tool_names`, because one `wait_for` budget covers several attack
    sequences, and a timeout has to keep what was already observed.

    `stop_after` names tools that end the loop once one of them has RUN. It
    exists for the Orchestrator's `submit_report`, a side-effect tool whose
    handler writes the report into the caller's container. Sending that handler's
    result back and letting the model talk on spends money on a turn nobody
    reads. `_run_one_call` decides the stop, and its docstring says why the
    handler running is part of the claim.

Rung: `app.services`. Imports `app.domain` and the standard library.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.domain.tool_def import ToolDefinition
from app.domain.tool_result import wire_text

log = structlog.get_logger(__name__)


def tools_wire(tools: Sequence[ToolDefinition]) -> list[dict]:
    """The tool list as the wire carries it. Built once and sent on every call."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }
        for tool in tools
    ]


def assistant_turn(message, tool_calls) -> dict:
    """Replay the model's own turn, built by hand so the wire shape is ours.

    A `model_dump` of the response object would carry whatever fields the SDK
    version happens to hold, and the next request would send them back.
    """
    return {
        "role": "assistant",
        "content": message.content or None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in tool_calls
        ],
    }


def error_wire(text: str) -> dict:
    """The wire shape a failed tool call returns. `wire_text` reads it like any other."""
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def tool_arguments(name: str, raw) -> tuple[dict, dict | None]:
    """The arguments the model sent, and the refusal to hand back when they do not read.

    A model writes this string, so a malformed one is ordinary traffic rather
    than a fault. It goes back as an error tool result the model can correct on
    its next call, and the turn runs on.
    """
    try:
        args = json.loads(raw or "{}")
    except ValueError:
        log.warning("tool_loop.tool_arguments_unparsed", tool_name=name)
        return {}, error_wire(f"Tool {name} received arguments that are not valid JSON.")
    if not isinstance(args, dict):
        log.warning("tool_loop.tool_arguments_not_an_object", tool_name=name)
        return {}, error_wire(f"Tool {name} received arguments that are not a JSON object.")
    return args, None


async def dispatch(tools: Sequence[ToolDefinition], name: str, args: dict) -> dict:
    """Run the named tool's handler, or say why nothing ran.

    An unknown name, a raising handler and a handler that returns something other
    than a wire dict all come back as an error wire dict. The model reads the text
    and the turn continues. Raising here would end a customer's turn over one
    tool, and every reader downstream (`wire_text`, `_log_entry`) calls `.get` on
    what this returns.

    The customer turn reads only the wire, so this is what `agent_loop` calls.
    `run_tool_loop` needs the second half of the answer and calls
    `dispatch_outcome` below.
    """
    wire, _ = await dispatch_outcome(tools, name, args)
    return wire


async def dispatch_outcome(
    tools: Sequence[ToolDefinition], name: str, args: dict
) -> tuple[dict, bool]:
    """The wire, and whether the named handler ran to completion.

    Two different questions, and `stop_after` is why they are separated. Every
    failure below produces a wire dict the model can read, which is all the
    customer turn needs. A caller that stops the loop on a tool call is claiming
    the handler did its work, and three of these four paths mean it did not.

    `True` means the handler was found, awaited, and returned a dict. It does NOT
    mean the handler succeeded: a gate refusing an action returns `is_error` and
    ran perfectly well. The distinction is whether OUR code produced the wire or
    the handler did.
    """
    tool = next((candidate for candidate in tools if candidate.name == name), None)
    if tool is None:
        log.warning("tool_loop.unknown_tool", tool_name=name)
        return error_wire(f"Tool {name} is not one this agent has."), False
    try:
        wire = await tool.handler(args)
    except Exception as exc:
        log.warning("tool_loop.tool_failed", tool_name=name, error_type=type(exc).__name__)
        return error_wire(f"Tool {name} failed with {type(exc).__name__}."), False
    if isinstance(wire, dict):
        return wire, True
    log.warning(
        "tool_loop.tool_result_not_a_dict", tool_name=name, result_type=type(wire).__name__
    )
    return error_wire(f"Tool {name} returned a {type(wire).__name__} rather than a result."), False


def first_choice(completion):
    """The choice the loop reads, or None when the reply carried none.

    A completion with an empty `choices` list is a well-formed response that
    produced no content. `completion.choices[0]` raised IndexError straight out
    of the loop for it, the task's handler caught that and the customer read a
    provider hiccup as `agent.failed`. A turn that produced nothing ends with the
    text it already has and a stop_reason naming the absence.
    """
    choices = getattr(completion, "choices", None) or []
    return choices[0] if choices else None


@dataclass
class ToolLoopResult:
    """What one `run_tool_loop` sequence produced.

    Attributes:
        response_text: every text part the model produced, joined by newline.
        tool_names:    the tools it called, in order, with repeats. The Attacker
                       counts these; a name appearing here means the model asked
                       for it, not that the handler succeeded.
        num_turns:     model calls made.
        stop_reason:   the provider's `finish_reason` when the model stopped on
                       its own, `"stop_after"` when a named tool ended the loop,
                       `"max_turns"` when the ceiling did, and `"no_choices"`
                       when a reply carried nothing to read.
    """

    response_text: str = ""
    tool_names: list[str] = field(default_factory=list)
    num_turns: int = 0
    stop_reason: str | None = None


async def run_tool_loop(
    opening_message: str,
    *,
    client,
    model: str,
    system_prompt: str,
    tools: Sequence[ToolDefinition],
    max_turns: int,
    on_tool_use: Callable[[str], None] | None = None,
    stop_after: frozenset[str] = frozenset(),
    reasoning_effort: str | None = None,
) -> ToolLoopResult:
    """Run one bounded conversation to its end. See WHAT THE CALLER PASSES above.

    Returns:
        A `ToolLoopResult`. This function closes no client and catches nothing
        from the provider call. Each caller wraps it in its own timeout and its
        own `except`, and each reports a partial run differently.
    """
    result = ToolLoopResult()
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": opening_message},
    ]
    schemas = tools_wire(tools)
    for _ in range(max_turns):
        completion = await client.chat.completions.create(
            **_request_kwargs(model, messages, schemas, reasoning_effort)
        )
        result.num_turns += 1
        choice = first_choice(completion)
        if choice is None:
            result.stop_reason = "no_choices"
            return result
        _collect_text(result, choice.message.content)
        tool_calls = getattr(choice.message, "tool_calls", None)
        if not tool_calls:
            result.stop_reason = choice.finish_reason
            return result
        messages.append(assistant_turn(choice.message, tool_calls))
        for call in tool_calls:
            if await _run_one_call(
                call,
                messages=messages,
                result=result,
                tools=tools,
                on_tool_use=on_tool_use,
                stop_after=stop_after,
            ):
                result.stop_reason = "stop_after"
                return result
    result.stop_reason = "max_turns"
    return result


def _request_kwargs(
    model: str, messages: list, schemas: list, reasoning_effort: str | None
) -> dict[str, Any]:
    """One request body. The effort field is absent when the caller names none."""
    kwargs: dict[str, Any] = {"model": model, "messages": messages, "tools": schemas}
    if reasoning_effort is not None:
        kwargs["reasoning_effort"] = reasoning_effort
    return kwargs


def _collect_text(result: ToolLoopResult, content) -> None:
    """Join this reply's text onto what the loop has already collected."""
    if content:
        parts = (result.response_text, content)
        result.response_text = "\n".join(part for part in parts if part)


async def _run_one_call(
    call,
    *,
    messages: list,
    result: ToolLoopResult,
    tools: Sequence[ToolDefinition],
    on_tool_use: Callable[[str], None] | None,
    stop_after: frozenset[str],
) -> bool:
    """Run one tool call, append its result message, and say whether to stop.

    THE STOP IS A CLAIM THAT THE HANDLER RAN, and `dispatch_outcome` is what
    supplies it. `stop_after` exists for the Orchestrator's `submit_report`, and
    that handler is what writes the report into the caller's container. Four
    things produce no report: a name no tool carries, arguments that are not a
    JSON object, a handler that raises, and a handler that returns something
    other than a dict. Stopping on any of them hands the caller an empty
    container and a `stop_reason` saying the report arrived.

    This line read `any(candidate.name == name for candidate in tools)` until two
    adversarial reviewers found, independently, that it covers the first two and
    misses the last two. It was unreachable then, because `build_report_tools`'
    handler is a `dict.setdefault` that cannot raise. That is the kind of guard
    which is merely wrong for one release and load-bearing for the next.

    All four cases are ordinary traffic rather than faults, so the loop runs on
    and lets the model correct itself.
    """
    name = call.function.name
    result.tool_names.append(name)
    if on_tool_use is not None:
        on_tool_use(name)
    args, refusal = tool_arguments(name, call.function.arguments)
    if refusal is not None:
        payload, ran = refusal, False
    else:
        payload, ran = await dispatch_outcome(tools, name, args)
    # `wire_text`, not `json.dumps`, and the Attacker is why. `send_probe`
    # returns the deployed agent's answer as its tool result, and the Attacker's
    # next move is reasoning about that answer. Handing it the MCP envelope
    # instead of the text puts a layer of JSON between the attacker and the thing
    # it is attacking. The customer path reads the same rule from the same function.
    messages.append({"role": "tool", "tool_call_id": call.id, "content": wire_text(payload)})
    return ran and name in stop_after
