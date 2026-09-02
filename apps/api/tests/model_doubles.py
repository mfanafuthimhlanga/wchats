"""Doubles for the call sites that build their client through the factory.

Ticket #47 moved every direct-API construction into `app.core.model_client`. A
site now takes a `LedgerContext` and asks it for a client per call, so a test
that used to patch a module-level `ANTHROPIC_CLIENT` patches the factory
instead. One target covers every site, and the assertions stay where they were:
on the kwargs the provider receives.

`ledger()` builds a REAL `LedgerContext`, not a stand-in. Its recorder collects
rows in a list rather than opening a database, which is the only part a unit
test cannot afford.

The two app imports are LAZY, inside the helper that needs them. Importing
`app.core.model_client` at module level made every consumer of this file pay for
the provider SDKs at collection time, about 3.3s for openai and 1.9s for
anthropic on this machine, including the consumers that only call `factory()`
and never build a context. `patch()` takes its target as a string and imports
nothing here.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from app.core.model_client import LedgerContext
    from app.domain.model_call import ModelCall

#: Ids a unit test bills to. Real UUIDs, because `ModelCall` and the ledger
#: columns take UUID strings and a row built from "t1" would never insert.
TENANT_ID = "11111111-1111-1111-1111-111111111111"
AGENT_ID = "22222222-2222-2222-2222-222222222222"
JOB_ID = "33333333-3333-3333-3333-333333333333"


def ledger(rows: list[ModelCall] | None = None) -> LedgerContext:
    """A LedgerContext whose recorder appends to `rows` instead of writing."""
    from app.core.model_client import LedgerContext

    collected = [] if rows is None else rows
    return LedgerContext(
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        job_id=JOB_ID,
        recorder=collected.append,
    )


def factory(client):
    """Patch the factory so every site under test is handed `client`.

    A context manager and a decorator, the same as any `patch(...)`.
    """
    return patch("app.core.model_client.make_client", return_value=client)


def openai_client(create=None, parse=None):
    """The double the factory hands a site, shaped like `openai.OpenAI`.

    Issue #76 moved the eleven direct-API sites off `messages.create` and
    `messages.parse` onto `chat.completions.create` and `chat.completions.parse`,
    so this is where a test reaches the method the site actually calls. A double
    for one of the two leaves the other None, which raises rather than silently
    passing if a site calls the wrong one.
    """
    completions = SimpleNamespace(create=create, parse=parse)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def tool_call(name: str, arguments: dict, call_id: str = "call_1"):
    """One tool call, shaped the way the OpenAI SDK hands one back.

    `arguments` is a JSON STRING on the wire and in the SDK object, which is why
    `forced_tool_arguments` parses it. Passing a dict here and letting this
    function serialise it keeps every test honest about that.
    """
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def completion(*, content=None, tool_calls=None, finish_reason="stop", parsed=None):
    """One chat completion, shaped the way the OpenAI SDK hands one back.

    `parsed` is what `chat.completions.parse` fills in and `create` leaves at
    None. `finish_reason` is `"length"` when the model hit the token ceiling,
    which is the Auditor's truncation signal.
    """
    message = SimpleNamespace(
        role="assistant",
        content=content,
        tool_calls=tool_calls,
        parsed=parsed,
        refusal=None,
    )
    choice = SimpleNamespace(index=0, message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])
