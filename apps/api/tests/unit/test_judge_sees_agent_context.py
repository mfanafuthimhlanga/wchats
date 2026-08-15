"""BACKLOG 5.16 — the grounding judge must see exactly what the agent saw.

The defect these tests pin, stated as what it cost rather than as a slice:

    `agent.py` built the Auditor's context as
    `json.dumps([str(r)[:600] for r in retrieve_results][:3])`, where `r` is
    `tc["result"]` — the AUDIT capture, a Python repr of the SDK content block
    already cut at RETRIEVE_RESULT_CAPTURE_CHARS because it reaches a jsonb
    column. Three losses in one line: 600 chars per call against the 10,000
    (MAX_CHUNKS x CHUNK_CONTENT_CHAR_LIMIT) the agent was shown, retrieve calls
    4+ dropped, and a repr in place of the chunk text.

    So the judge was asked "is this answer supported by its context?" while being
    shown roughly half of it. The first valid verdict in the platform's history
    (E2E-3, 2026-08-13) marked the agent's price claims unsupported and gave as
    its reason that the context "only confirms VAT exclusion" — it had not been
    shown the price rows the agent answered from. Every stored `auditor.complete`
    is biased toward partial/ungrounded by this, `verified_qa_candidates`
    (confidence >= 0.90) is starved, and 0.6's count(*) would read the artefact
    as signal.

Why no existing test caught it: `test_agent_tool_result_stream.py` proves the
capture REACHES `tool_calls_log` (5.9) and `test_validators.py` drives
`run_auditor` with a context string the test supplies. Nothing asserted anything
about the step BETWEEN them — the one place the evidence was cut. A boundary
with a test on each side and none across it.

These tests drive the REAL capture path (`_record_tool_result`, the real framer,
the real SDK dataclass) and then compare what the judge is handed against what
the agent was shown. The last one is a structural pin: the cap may not come back
to that line, checked by walking the AST rather than by reading the source as
text — 1.33 B4 is what happens when a check about code can be satisfied by prose.
"""

from __future__ import annotations

import ast
import importlib
import json
import sys
from pathlib import Path

import pytest

from app.services.agent_tools import (
    CHUNK_CONTENT_CHAR_LIMIT,
    MAX_CHUNKS,
    _frame_retrieved_context,
)
from app.worker.tasks.runtime import agent as agent_module

#: The two numbers the old line applied. Named so every assertion below can say
#: which half of the defect it is standing on, and so this module reads as a
#: statement about a specific historical cut rather than about "truncation".
OLD_PER_CALL_CHAR_CAP = 600
OLD_RESULT_COUNT_CAP = 3


def _real_tool_result_block():
    """Import the REAL `ToolResultBlock`, whatever fake sits in `sys.modules`.

    Duplicated from `test_agent_tool_result_stream.py` rather than imported from
    it: importing another test module for a helper makes this module's meaning
    depend on that one still existing under that name, and the thing being
    defended against here is BACKLOG 2.24 — `test_agent_task.py` installs a fake
    `claude_agent_sdk` into `sys.modules` and never removes it, so by collection
    order this module would otherwise capture a MagicMock and prove nothing.
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
        block_cls = real.ToolResultBlock
        assert isinstance(block_cls, type), (
            "claude_agent_sdk.ToolResultBlock is not a class — a fake SDK survived "
            "the sys.modules swap and these tests would prove nothing"
        )
        return block_cls
    finally:
        for name in list(sys.modules):
            if name == "claude_agent_sdk" or name.startswith("claude_agent_sdk."):
                del sys.modules[name]
        sys.modules.update(saved)


ToolResultBlock = _real_tool_result_block()


# ---------------------------------------------------------------------------
# Harness — the REAL capture path, one retrieve call at a time.
# ---------------------------------------------------------------------------


def _chunk_text(label: str, length: int) -> str:
    """A chunk long enough that the old 600-char cap provably bit."""
    head = f"{label}: "
    return head + ("x" * (length - len(head)))


def _framed(chunk_texts: list[str]) -> str:
    """Frame chunk texts with the REAL producer.

    `retrieve_tool` returns `_frame_retrieved_context(str(chunks))`. Building the
    fixture with the production framer rather than a literal means it cannot
    drift away from what the tool emits — which is precisely how 1.26 survived:
    a fixture that manufactured a contract the product had abandoned.
    """
    return _frame_retrieved_context(str([{"content": t, "score": 0.9} for t in chunk_texts]))


def _capture(calls: list[list[str]]) -> list[dict]:
    """Drive `_record_tool_result` once per retrieve call; return tool_calls_log.

    `calls` is one list of chunk texts per retrieve call, so the caller states the
    agent's evidence and this returns the log the dispatch site reads.
    """
    tool_calls_log: list[dict] = []
    tool_names_by_use_id: dict[str, str] = {}
    for i, chunk_texts in enumerate(calls):
        use_id = f"toolu_{i}"
        tool_names_by_use_id[use_id] = "retrieve"
        tool_calls_log.append({"tool_name": "retrieve", "input": {"query": f"q{i}"}})
        agent_module._record_tool_result(
            ToolResultBlock(
                tool_use_id=use_id,
                content=[{"type": "text", "text": _framed(chunk_texts)}],
                is_error=False,
            ),
            tool_names_by_use_id=tool_names_by_use_id,
            tool_calls_log=tool_calls_log,
            job_id="job-5-16",
            db=object(),
            redis=object(),
        )
    return tool_calls_log


@pytest.fixture(autouse=True)
def _emit_is_not_a_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_record_tool_result` emits an SSE event; this module is not testing that."""
    monkeypatch.setattr(agent_module, "emit", lambda *_a, **_k: None)


# ---------------------------------------------------------------------------
# The three cuts, one test each.
# ---------------------------------------------------------------------------


def test_a_chunk_longer_than_the_old_cap_reaches_the_judge_whole() -> None:
    """`[:600]`. A chunk the agent was shown in full must reach the judge in full."""
    long_chunk = _chunk_text("prices", 1500)
    contexts, _unparsed = agent_module._judge_retrieved_context(_capture([[long_chunk]]))

    assert long_chunk in contexts, (
        "the chunk the agent answered from does not appear in the judge's context "
        f"as a whole string. Longest element is {max((len(c) for c in contexts), default=0)} "
        f"chars against the chunk's {len(long_chunk)} — the judge is being asked "
        "whether claims are supported by evidence it was not shown, which is what "
        "made the first real verdict `partial`"
    )
    assert len(long_chunk) > OLD_PER_CALL_CHAR_CAP, (
        "the fixture chunk is shorter than the cap under test, so this test would "
        "pass with the old code — it would be a tautology, not a proof"
    )


def test_every_retrieve_call_reaches_the_judge() -> None:
    """`[:3]`. A turn may retrieve up to `max_turns` times; none of it is optional."""
    per_call = [[_chunk_text(f"call{i}", 700)] for i in range(OLD_RESULT_COUNT_CAP + 2)]
    contexts, _unparsed = agent_module._judge_retrieved_context(_capture(per_call))

    missing = [
        chunks[0][:20]
        for chunks in per_call
        if not any(chunks[0] in c for c in contexts)
    ]
    assert not missing, (
        f"{len(missing)} retrieve call(s) contributed nothing to the judge's "
        f"context: {missing}. The old `[:3]` dropped every call past the third, so "
        "an agent that searched again to answer well was judged as if it had not"
    )


def test_the_judge_gets_one_element_per_chunk_not_one_repr_per_call() -> None:
    """`tc["result"]`. The audit capture is a repr; the judge needs chunk text.

    Feeding a repr hands the judge dict syntax it cannot distinguish from
    evidence, in a single element, which is the same reason eval.py:481 refuses
    to score `result`.
    """
    chunks = [_chunk_text("a", 700), _chunk_text("b", 700), _chunk_text("c", 700)]
    contexts, _unparsed = agent_module._judge_retrieved_context(_capture([chunks]))

    assert len(contexts) == len(chunks), (
        f"the judge got {len(contexts)} context element(s) for {len(chunks)} "
        "retrieved chunks — chunk boundaries the agent saw were collapsed"
    )
    assert not any("'content':" in c or "'score':" in c for c in contexts), (
        "a Python repr of the chunk dicts reached the judge; the transport "
        "encoding is then most of its token budget and is scored as evidence"
    )


# ---------------------------------------------------------------------------
# The rule itself: judge-context == agent-context.
# ---------------------------------------------------------------------------


def test_judge_context_is_exactly_what_the_agent_was_shown() -> None:
    """The whole of 5.16 in one assertion, over a realistic turn.

    `MAX_CHUNKS` chunks at `CHUNK_CONTENT_CHAR_LIMIT` is what `retrieve_tool`
    hands the agent at its own ceiling. Equality — not "contains", not "at least"
    — because both directions are defects: less means the judge marks supported
    claims unsupported, more means it is judging against evidence the agent never
    had, which would make a `grounded` verdict unearned.
    """
    shown = [_chunk_text(f"chunk{i}", CHUNK_CONTENT_CHAR_LIMIT) for i in range(MAX_CHUNKS)]
    contexts, unparsed = agent_module._judge_retrieved_context(_capture([shown]))

    assert contexts == shown, (
        "the judge's context is not the agent's context. "
        f"agent saw {len(shown)} chunks / {sum(len(c) for c in shown)} chars; "
        f"judge got {len(contexts)} / {sum(len(c) for c in contexts)}"
    )
    assert unparsed == 0


def test_the_helper_applies_no_cap_of_its_own() -> None:
    """No number is chosen here — the bound is the retrieval layer's.

    Six calls (the `max_turns=6` worst case) at the retrieval ceiling. If a future
    change adds a ceiling at this call site it will be an arbitrary one, it will
    drift from `MAX_CHUNKS` / `CHUNK_CONTENT_CHAR_LIMIT` the first time either
    moves, and this test is what makes that a decision rather than an accident.
    """
    calls = [
        [_chunk_text(f"c{call}-{i}", CHUNK_CONTENT_CHAR_LIMIT) for i in range(MAX_CHUNKS)]
        for call in range(6)
    ]
    contexts, _unparsed = agent_module._judge_retrieved_context(_capture(calls))

    expected = [text for call in calls for text in call]
    assert contexts == expected, (
        f"{len(expected) - len(contexts)} of {len(expected)} chunks were dropped "
        "at the judge boundary"
    )


def test_an_undecodable_payload_falls_back_and_is_counted() -> None:
    """A degraded context is an observation, never a quiet reduction in evidence.

    `_retrieved_chunk_texts` returns None (never []) when the framed payload
    cannot be read. Contributing nothing for such a call would hand the judge
    `[]` for a turn that DID retrieve — which is 5.11, the empty-context era,
    in a new spelling.
    """
    tool_calls_log = [
        {
            "tool_name": "retrieve",
            "result": "<<<RETRIEVED CONTEXT>>> not-a-python-literal <<<END>>>",
            agent_module.RETRIEVE_CHUNKS_KEY: [],
            agent_module.RETRIEVE_CHUNKS_SOURCE_KEY: agent_module.RETRIEVE_CHUNKS_UNPARSED,
        }
    ]
    contexts, unparsed = agent_module._judge_retrieved_context(tool_calls_log)

    assert contexts, (
        "a retrieve call whose payload could not be decoded contributed nothing, "
        "so the grounding judge is handed an empty context for a turn that "
        "retrieved — BACKLOG 5.11 in a new spelling"
    )
    assert unparsed == 1, (
        "the degraded call was not counted, so nothing downstream can tell a "
        "short context apart from a decode failure"
    )


def test_a_turn_that_retrieved_nothing_reports_nothing() -> None:
    """The complement, and it must stay honest: no retrieval means no context.

    Padding an empty context would make an ungrounded answer look judged.
    """
    contexts, unparsed = agent_module._judge_retrieved_context(
        [{"tool_name": "lookup_structured", "result": "irrelevant"}]
    )
    assert contexts == []
    assert unparsed == 0


# ---------------------------------------------------------------------------
# Structural pin — the cap may not come back to the dispatch line.
# ---------------------------------------------------------------------------


def _retrieved_context_json_assignment() -> ast.Assign:
    """The AST node that builds the judge's context, found by walking the tree.

    AST rather than a substring search over the source: 1.33 B4 is the record of
    a check about code that a docstring sentence satisfied. Comments and prose
    are invisible here by construction.
    """
    tree = ast.parse(Path(agent_module.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "retrieved_context_json"
            for t in node.targets
        ):
            return node
    raise AssertionError(
        "no `retrieved_context_json = ...` assignment found in agent.py — this "
        "guard has been renamed out from under itself and is proving nothing"
    )


def test_the_dispatch_site_slices_nothing() -> None:
    """No slice, no integer literal, on the line that builds the judge's context."""
    node = _retrieved_context_json_assignment()
    offenders = [
        n for n in ast.walk(node.value)
        if isinstance(n, ast.Slice)
        or (isinstance(n, ast.Constant) and isinstance(n.value, int))
    ]
    assert not offenders, (
        "a slice or a numeric literal has reappeared where the judge's context is "
        f"built (agent.py:{node.lineno}). That line held BACKLOG 5.16 — 600 chars "
        "and 3 results — and the bound belongs to the retrieval layer "
        "(MAX_CHUNKS x CHUNK_CONTENT_CHAR_LIMIT), not to this call site"
    )


def test_the_dispatch_site_calls_the_helper() -> None:
    """`_judge_retrieved_context` is used, not merely defined.

    A helper defined at module scope and never wired is invisible to every test
    that drives it directly — which is 1.32 exactly, a tool schema defined and
    never registered, green in 2,200 tests.
    """
    tree = ast.parse(Path(agent_module.__file__).read_text(encoding="utf-8"))
    definitions = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_judge_retrieved_context"
    ]
    assert len(definitions) == 1, "expected exactly one _judge_retrieved_context def"

    def_lines = range(definitions[0].lineno, (definitions[0].end_lineno or 0) + 1)
    call_sites = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_judge_retrieved_context"
        and n.lineno not in def_lines
    ]
    assert call_sites, (
        "_judge_retrieved_context is defined and never called from agent.py. The "
        "judge's context is being built somewhere else and none of the tests "
        "above describe what the Auditor actually receives"
    )


def test_the_judges_context_is_json_the_auditor_can_parse() -> None:
    """`run_auditor` does `json.loads` for its chunk count and passes the string on.

    The count reaches Langfuse as `context_chunks`; it must be the real chunk
    count, not the number of tool calls.
    """
    shown = [_chunk_text("a", 800), _chunk_text("b", 800)]
    contexts, _unparsed = agent_module._judge_retrieved_context(_capture([shown]))
    parsed = json.loads(json.dumps(contexts))

    assert parsed == shown
    assert len(parsed) == 2, "context_chunks would report the tool-call count"
