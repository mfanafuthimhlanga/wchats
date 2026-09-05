"""ToolDefinition, one tool as this codebase declares it (ticket #49, ADR 0008).

WHAT IT REPLACES
    `claude_agent_sdk.tool` and `claude_agent_sdk.SdkMcpTool`. The SDK's
    decorator is a six-line constructor over a dataclass, and the four fields it
    sets are the four `app.services.tool_loop.tools_wire` reads: `name`,
    `description`, `input_schema` and `handler`. Ticket #48 put the customer turn
    on the owned loop and left the tool declarations on the SDK, so the whole
    dependency was still installed for those six lines. This module is them.

WHY THE SCHEMA MUST BE A JSON SCHEMA DICT
    The SDK also accepted `{"name": str}` shorthand and TypedDict classes and
    converted them on its way to an MCP server. The owned loop has no such step:
    `_tools_wire` puts `input_schema` straight into the request body as the
    OpenAI `function.parameters` object. Shorthand there reaches the provider as
    a malformed schema, and the failure surfaces as a model that calls the tool
    with the wrong arguments rather than as an error naming the tool.

    Every one of the eighteen declarations in this repo already passes a full
    JSON Schema dict, so requiring one costs nothing and moves the failure from
    a live turn to import time.

WHY THE HANDLER MUST BE A COROUTINE FUNCTION
    `tool_loop.dispatch` awaits it. A `def` handler raises `TypeError: object
    dict can't be used in 'await' expression` inside the loop, where `_dispatch`
    catches every exception and returns an error wire dict, because ending a
    customer's turn over one tool is worse. So a sync handler would ship as a
    tool that is permanently broken and silent about it. `inspect` settles it at
    decoration time instead.

WHY FROZEN
    The eleven customer tools are module-level singletons shared by every turn
    in a worker process. A mutable definition is a per-process side channel
    between tenants.

Rung: `app.domain` imports the standard library, third-party packages and its
domain siblings. This module imports the standard library only.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypedDict

#: One tool call's arguments in, one wire result dict out. The wire dict is the
#: MCP content shape (`{"content": [{"type": "text", "text": ...}], "is_error":
#: bool}`), unchanged from the SDK because the model reads it and
#: `app.domain.tool_result.ToolResult` parses it.
ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class ToolSchema(TypedDict):
    """The three declaration arguments of `tool`, held as one dict.

    Several services hoist a tool's name, description and JSON Schema to a module
    constant and read the three keys back at the decorator (`@tool(S["name"],
    S["description"], S["input_schema"])`). An unannotated dict literal of those
    three keys infers `dict[str, Collection[str]]`, the join of `str` and
    `dict[str, Any]`, so each read comes back as `Collection[str]` and none of the
    three arguments type-checks. Declaring the constant as this TypedDict gives each
    key back its own type, and a schema that is spelled with a wrong key or a
    missing one is refused where it is written rather than at the decorator.
    """

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolDefinition:
    """One tool the model may call.

    Attributes:
        name:         what the model names in a tool call. Bare, with no
                      `mcp__{server}__` prefix, because there is no MCP server
                      any more. See the module note below.
        description:  what the model reads to decide whether to call it.
        input_schema: the JSON Schema object for the arguments, sent verbatim as
                      the OpenAI `function.parameters`.
        handler:      the coroutine that runs it.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler


def tool(
    name: str, description: str, input_schema: dict[str, Any]
) -> Callable[[ToolHandler], ToolDefinition]:
    """Declare a tool. Succeeds `claude_agent_sdk.tool`, same call shape.

    Args:
        name:         the tool name the model calls.
        description:  what it does, for the model.
        input_schema: a JSON Schema object describing the arguments.

    Returns:
        A decorator that turns the handler into a `ToolDefinition`.

    Raises:
        TypeError: if the schema is not a dict, or the handler is not a
                   coroutine function. Both fire at import time, because every
                   declaration in this repo is at module scope or inside a
                   builder that runs before the loop does.
    """
    if not isinstance(input_schema, dict):
        raise TypeError(
            f"Tool {name!r} was declared with a {type(input_schema).__name__} schema. "
            "The wire sends this object as `function.parameters`, so it must be a "
            "JSON Schema dict."
        )

    def decorator(handler: ToolHandler) -> ToolDefinition:
        if not inspect.iscoroutinefunction(handler):
            raise TypeError(
                f"Tool {name!r} was declared with a handler the loop cannot await. "
                f"{getattr(handler, '__name__', handler)!r} must be `async def`."
            )
        return ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
        )

    return decorator
