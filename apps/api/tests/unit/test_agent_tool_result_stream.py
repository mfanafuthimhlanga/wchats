"""BACKLOG 5.9 — tool results arrive on UserMessage, and the turn loop must read them.

The defect these tests pin, stated as what it cost rather than as a type error:

    `_run_sdk_turn` collected `ToolResultBlock` only inside `AssistantMessage`.
    The Claude Code CLI emits tool results as `{"type":"user", "message":
    {"role":"user","content":[{"type":"tool_result",...}]}}`. The branch was
    therefore unreachable, and three separate downstream readers were reading a
    channel nothing ever wrote:

      * `agent.tool_result` job_events were never emitted, so
        `retrieval_eval._fetch_turn_context` always built `retrieve_contexts == []`.
      * `tc["result"]` was never set, so the Auditor — the GROUNDING judge —
        received `retrieved_context_json == "[]"` on every turn ever run.
      * `RETRIEVE_CHUNKS_KEY` was never set, so `eval.py` saw zero chunks and
        excluded every row as `no_retrieval`.

    Stacked underneath it: the handler read `getattr(block, "name", "unknown")`,
    but `ToolResultBlock` declares only `tool_use_id` / `content` / `is_error`.
    So even a reachable branch would have emitted `tool_name="unknown"`, which
    `retrieval_eval.py`'s `payload["tool_name"] == "retrieve"` filter never
    matches. Fixing the message type alone would have produced events that still
    joined to nothing — which is why `test_tool_name_is_resolved_not_unknown`
    exists as its own assertion.

Why no existing test caught either: every unit test of this path installs a fake
`claude_agent_sdk` of MagicMocks and hand-builds the message stream, so the
stream's SHAPE was whatever the test assumed — the same assumption the code made.
These tests use the REAL SDK dataclasses in the shape observed from 42,334
tool_result entries across 782 real CLI session transcripts (all `type:"user"`,
zero assistant-carried).

`_real_sdk_types` defends against BACKLOG 2.24: `test_agent_task.py` installs a
fake `claude_agent_sdk` into `sys.modules` and never removes it, so by collection
order this module would otherwise see MagicMocks instead of dataclasses.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from app.worker.tasks.runtime import agent as agent_module


def _real_sdk_types():
    """Import the REAL claude_agent_sdk, whatever fake sits in sys.modules.

    Returns the five block/message classes. Restores sys.modules exactly as it
    was found, so this module cannot itself become the next 2.24.
    """
    saved = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "claude_agent_sdk" or name.startswith("claude_agent_sdk.")
    }
    for name in saved:
        del sys.modules[name]
    try:
        real = importlib.import_module("claude_agent_sdk")
        types = (
            real.AssistantMessage,
            real.UserMessage,
            real.ToolUseBlock,
            real.ToolResultBlock,
            real.ResultMessage,
            real.TextBlock,
        )
        assert isinstance(real.ToolResultBlock, type), (
            "claude_agent_sdk.ToolResultBlock is not a class — a fake SDK survived "
            "the sys.modules swap and these tests would prove nothing"
        )
        return types
    finally:
        for name in list(sys.modules):
            if name == "claude_agent_sdk" or name.startswith("claude_agent_sdk."):
                del sys.modules[name]
        sys.modules.update(saved)


(
    AssistantMessage,
    UserMessage,
    ToolUseBlock,
    ToolResultBlock,
    ResultMessage,
    TextBlock,
) = _real_sdk_types()


def test_tool_result_block_has_no_name_attribute() -> None:
    """The stacked defect, pinned at its root.

    `getattr(block, "name", "unknown")` was not a defensive default — it was the
    ONLY outcome, because the dataclass has no such field. If a future SDK adds
    one, this test fails and the resolution-by-tool_use_id can be simplified
    deliberately rather than by accident.
    """
    block = ToolResultBlock(tool_use_id="toolu_1", content="x", is_error=False)
    assert not hasattr(block, "name"), (
        "ToolResultBlock now has a `name` field; revisit the tool_use_id join in "
        "_run_sdk_turn, which exists precisely because it did not."
    )
    assert {"tool_use_id", "content", "is_error"} <= set(vars(block))


# ---------------------------------------------------------------------------
# Harness: drive the real _run_sdk_turn over a scripted message stream.
# ---------------------------------------------------------------------------


def _framed_retrieve_payload() -> str:
    """The real wire payload, built by the REAL producer.

    `retrieve_tool` returns `_frame_retrieved_context(str(chunks))` — header,
    the repr of a list of chunk dicts, footer. Building it with the production
    framer rather than a hand-written literal means this fixture cannot drift
    away from what the tool actually emits, which is the failure mode that let
    the defect under test survive: every existing test hand-built the shape it
    was asserting about.
    """
    from app.services.agent_tools import _frame_retrieved_context

    chunks = [
        {"content": "Refunds are accepted within 30 days of delivery.", "score": 0.91},
        {"content": "Shipping is free above R500.", "score": 0.62},
    ]
    return _frame_retrieved_context(str(chunks))


_RETRIEVE_PAYLOAD = _framed_retrieve_payload()


class _FakeClient:
    def __init__(self, messages, **_kwargs):
        self._messages = messages

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def query(self, _message):
        return None

    async def receive_response(self):
        for msg in self._messages:
            yield msg


@contextmanager
def _driven(messages):
    """Run _run_sdk_turn over `messages`; yield (result, emitted_events)."""
    emitted: list[tuple] = []

    def _record_emit(job_id, event_type, payload, _db, _redis):
        emitted.append((event_type, payload))

    def _client_factory(**kwargs):
        return _FakeClient(messages, **kwargs)

    with (
        patch.object(agent_module, "ClaudeSDKClient", _client_factory),
        patch.object(agent_module, "emit", _record_emit),
        patch.object(agent_module, "AssistantMessage", AssistantMessage),
        patch.object(agent_module, "UserMessage", UserMessage),
        patch.object(agent_module, "ToolUseBlock", ToolUseBlock),
        patch.object(agent_module, "ToolResultBlock", ToolResultBlock),
        patch.object(agent_module, "ResultMessage", ResultMessage),
        patch.object(agent_module, "TextBlock", TextBlock),
    ):
        result = asyncio.run(
            agent_module._run_sdk_turn(
                message="do I get a refund?",
                options=object(),
                job_id="job-5-9",
                local_conversation_id="conv-5-9",
                conn_str="postgresql://unused",
                db=object(),
                redis=object(),
            )
        )
        yield result, emitted


def _retrieve_turn_messages(payload: str = _RETRIEVE_PAYLOAD):
    """The OBSERVED CLI shape: tool_use on assistant, tool_result on user."""
    return [
        AssistantMessage(
            content=[
                ToolUseBlock(
                    id="toolu_retrieve_1",
                    name="mcp__customer-tools__retrieve",
                    input={"query": "refund policy"},
                )
            ],
            model="claude-haiku-4-5-20251001",
        ),
        UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="toolu_retrieve_1",
                    content=[{"type": "text", "text": payload}],
                    is_error=False,
                )
            ]
        ),
        AssistantMessage(
            content=[TextBlock(text="Yes — within 30 days of delivery.")],
            model="claude-haiku-4-5-20251001",
        ),
    ]


# ---------------------------------------------------------------------------
# The three channels the dead branch silently emptied.
# ---------------------------------------------------------------------------


def test_a_tool_result_on_a_user_message_emits_the_sse_event() -> None:
    """Channel 1: `agent.tool_result` job_events. Read by retrieval_eval."""
    with _driven(_retrieve_turn_messages()) as (_result, emitted):
        results = [payload for kind, payload in emitted if kind == "agent.tool_result"]

    assert results, (
        "no agent.tool_result event was emitted for a tool result delivered on a "
        "UserMessage — retrieval_eval._fetch_turn_context selects exactly this "
        "event_type and would build an empty context for every turn"
    )
    assert len(results) == 1


def test_tool_name_is_resolved_not_unknown() -> None:
    """Channel 1, stacked defect: the name must join back through tool_use_id.

    `retrieval_eval.py:194` filters on `payload["tool_name"] == "retrieve"`.
    "unknown" — the only value the old code could produce — matches nothing, so
    this assertion is what makes the emitted event actually reachable.
    """
    with _driven(_retrieve_turn_messages()) as (_result, emitted):
        results = [payload for kind, payload in emitted if kind == "agent.tool_result"]

    assert results[0]["tool_name"] == "retrieve", (
        f"tool_name was {results[0]['tool_name']!r}; retrieval_eval joins on "
        "== 'retrieve' and would drop this event"
    )


def test_the_auditors_retrieve_result_is_captured() -> None:
    """Channel 2: tc["result"], which becomes the GROUNDING judge's context."""
    with _driven(_retrieve_turn_messages()) as (result, _emitted):
        retrieve_calls = [
            tc for tc in result["tool_calls_log"] if tc.get("tool_name") == "retrieve"
        ]

    assert retrieve_calls, "the retrieve tool call was not logged at all"
    assert "result" in retrieve_calls[0], (
        "tc['result'] was never set, so agent.py's retrieved_context_json is '[]' "
        "and the Auditor judges grounding against an empty context"
    )
    assert "30 days" in retrieve_calls[0]["result"]


def test_the_evals_untruncated_chunks_are_captured() -> None:
    """Channel 3: RETRIEVE_CHUNKS_KEY, which eval.py hands to Ragas."""
    with _driven(_retrieve_turn_messages()) as (result, _emitted):
        call = next(
            tc for tc in result["tool_calls_log"] if tc.get("tool_name") == "retrieve"
        )

    chunks = call.get(agent_module.RETRIEVE_CHUNKS_KEY)
    assert chunks, (
        "RETRIEVE_CHUNKS_KEY is empty, so eval.py:495 sees zero contexts and "
        "excludes the row as `no_retrieval` — D1/P2's untruncated-chunk capture "
        "is inert"
    )
    assert len(chunks) == 2
    assert any("30 days" in c for c in chunks)
    assert call[agent_module.RETRIEVE_CHUNKS_SOURCE_KEY] == agent_module.RETRIEVE_CHUNKS_PARSED


# ---------------------------------------------------------------------------
# Shape and robustness.
# ---------------------------------------------------------------------------


def test_parallel_tool_calls_attribute_results_to_the_right_tool() -> None:
    """Two tool_use blocks in one assistant turn, results in the other order.

    A single `pending_skill`/last-write-wins variable mis-attributes here. The
    tool_use_id join is what makes the result-to-tool mapping correct rather
    than positional.
    """
    messages = [
        AssistantMessage(
            content=[
                ToolUseBlock(
                    id="toolu_a", name="mcp__customer-tools__retrieve", input={"q": "x"}
                ),
                ToolUseBlock(
                    id="toolu_b",
                    name="mcp__customer-tools__lookup_order",
                    input={"order_id": "1"},
                ),
            ],
            model="m",
        ),
        # Deliberately reversed relative to the tool_use order.
        UserMessage(
            content=[
                ToolResultBlock(tool_use_id="toolu_b", content="order found", is_error=False),
                ToolResultBlock(
                    tool_use_id="toolu_a",
                    content=[{"type": "text", "text": _RETRIEVE_PAYLOAD}],
                    is_error=False,
                ),
            ]
        ),
    ]
    with _driven(messages) as (result, emitted):
        names = [p["tool_name"] for k, p in emitted if k == "agent.tool_result"]
        summaries = {
            p["tool_name"]: p["summary"] for k, p in emitted if k == "agent.tool_result"
        }
        retrieve_call = next(
            tc for tc in result["tool_calls_log"] if tc["tool_name"] == "retrieve"
        )

    assert names == ["lookup_order", "retrieve"], f"results mis-attributed: {names}"
    assert "order found" in summaries["lookup_order"]
    # The retrieve capture must have taken the RETRIEVE result, not the order one.
    assert "30 days" in retrieve_call["result"]


def test_a_non_retrieve_result_does_not_fill_the_retrieve_capture() -> None:
    """The capture is keyed on the resolved name, not on "the last retrieve"."""
    messages = [
        AssistantMessage(
            content=[
                ToolUseBlock(
                    id="toolu_r", name="mcp__customer-tools__retrieve", input={"q": "x"}
                ),
                ToolUseBlock(
                    id="toolu_o",
                    name="mcp__customer-tools__lookup_order",
                    input={"order_id": "1"},
                ),
            ],
            model="m",
        ),
        UserMessage(
            content=[
                ToolResultBlock(tool_use_id="toolu_o", content="order found", is_error=False)
            ]
        ),
    ]
    with _driven(messages) as (result, _emitted):
        retrieve_call = next(
            tc for tc in result["tool_calls_log"] if tc["tool_name"] == "retrieve"
        )

    assert "result" not in retrieve_call, (
        "a lookup_order result was written into the retrieve capture — the "
        "Auditor would then judge grounding against an order lookup"
    )


def test_an_assistant_carried_tool_result_is_still_tolerated() -> None:
    """message_parser.py:148 can build one; no observed CLI output does.

    Tolerance, not reliance — the UserMessage branch is what the fix rests on.
    """
    messages = [
        AssistantMessage(
            content=[
                ToolUseBlock(
                    id="toolu_1", name="mcp__customer-tools__retrieve", input={"q": "x"}
                ),
                ToolResultBlock(
                    tool_use_id="toolu_1",
                    content=[{"type": "text", "text": _RETRIEVE_PAYLOAD}],
                    is_error=False,
                ),
            ],
            model="m",
        ),
    ]
    with _driven(messages) as (result, emitted):
        results = [p for k, p in emitted if k == "agent.tool_result"]
        call = next(tc for tc in result["tool_calls_log"] if tc["tool_name"] == "retrieve")

    assert results and results[0]["tool_name"] == "retrieve"
    assert "result" in call


def test_text_and_escalation_still_work_unchanged() -> None:
    """The refactor must not disturb the two things this loop already did."""
    messages = [
        AssistantMessage(
            content=[
                TextBlock(text="Let me get a human. "),
                ToolUseBlock(
                    id="toolu_e",
                    name="mcp__customer-tools__escalate_to_human",
                    input={"reason": "angry", "context": "refund dispute"},
                ),
            ],
            model="m",
        ),
        UserMessage(
            content=[ToolResultBlock(tool_use_id="toolu_e", content="ok", is_error=False)]
        ),
    ]
    with _driven(messages) as (result, emitted):
        pass

    assert result["response_text"] == "Let me get a human. "
    assert result["escalated"] is True
    assert result["escalation_reason"] == "angry"
    assert result["escalation_context"] == "refund dispute"
    assert any(k == "agent.tool_call" for k, _ in emitted)


def test_a_plain_string_user_message_is_ignored_safely() -> None:
    """UserMessage.content may be a bare str (message_parser.py:115)."""
    messages = [
        UserMessage(content="just text, no blocks"),
        AssistantMessage(content=[TextBlock(text="hello")], model="m"),
    ]
    with _driven(messages) as (result, emitted):
        pass

    assert result["response_text"] == "hello"
    assert not [k for k, _ in emitted if k == "agent.tool_result"]


@pytest.mark.parametrize("is_error", [True, False])
def test_the_error_flag_does_not_suppress_the_capture(is_error: bool) -> None:
    """A failed retrieve is still evidence about the turn, and must be recorded."""
    messages = [
        AssistantMessage(
            content=[
                ToolUseBlock(
                    id="toolu_1", name="mcp__customer-tools__retrieve", input={"q": "x"}
                )
            ],
            model="m",
        ),
        UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="toolu_1",
                    content=[{"type": "text", "text": _RETRIEVE_PAYLOAD}],
                    is_error=is_error,
                )
            ]
        ),
    ]
    with _driven(messages) as (_result, emitted):
        assert [p for k, p in emitted if k == "agent.tool_result"]
