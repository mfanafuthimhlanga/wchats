"""BACKLOG 5.16 — the grounding judge must see exactly what the agent saw.

The defect, stated as what it cost rather than as a slice:

    `agent.py` built the Auditor's context as
    `json.dumps([str(r)[:600] for r in retrieve_results][:3])`, where `r` is
    `tc["result"]` — the AUDIT capture, a Python repr of the SDK content block
    already cut at RETRIEVE_RESULT_CAPTURE_CHARS because it reaches a jsonb
    column. Three losses in one line: 600 chars per call against the 10,000
    (MAX_CHUNKS x CHUNK_CONTENT_CHAR_LIMIT) the agent was shown, retrieve calls
    4+ dropped, and a repr in place of the chunk text.

    The first valid verdict in the platform's history (E2E-3, 2026-08-13) marked
    the agent's price claims unsupported and gave as its reason that the context
    "only confirms VAT exclusion" — it had not been shown the price rows the
    agent answered from.

WHY THIS MODULE IS SHAPED THE WAY IT IS. Its first version guarded the SHAPE OF
THE LINE with two AST checks, and an adversarial review then reintroduced the
whole defect five ways that stayed 10/10 green: a truncating helper, a
differently named variable, `itertools.islice`, a second assignment on the next
line, and rebuilding the old repr while still calling the helper. A text-shaped
guard bans one spelling, and the author picks the spelling.

So the load-bearing test here is `TestWhatTheAuditorIsActuallyHanded`: it drives
the real dispatch seam and asserts on **the argument `run_auditor.si` receives**.
Guard the value the consumer gets, never the syntax that produces it.
"""

from __future__ import annotations

import importlib
import json
import sys
from unittest.mock import patch

import pytest

from app.services.agent_tools import (
    CHUNK_CONTENT_CHAR_LIMIT,
    MAX_CHUNKS,
    _frame_retrieved_context,
    _RETRIEVE_CALLS_PER_TURN_MAX,
)
from app.worker.tasks.runtime import agent as agent_module

#: The two numbers the old line applied, named so each assertion can say which
#: half of the defect it stands on.
OLD_PER_CALL_CHAR_CAP = 600
OLD_RESULT_COUNT_CAP = 3


def _real_tool_result_block():
    """Import the REAL `ToolResultBlock`, whatever fake sits in `sys.modules`.

    Duplicated from `test_agent_tool_result_stream.py` rather than imported from
    it: importing another test module for a helper makes this module's meaning
    depend on that one keeping the name. The hazard is BACKLOG 2.24 —
    `test_agent_task.py` installs a fake `claude_agent_sdk` into `sys.modules`
    and never removes it, so by collection order this module would otherwise
    capture a MagicMock and prove nothing.
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
    drift away from what the tool emits — which is how 1.26 survived: a fixture
    that manufactured a contract the product had abandoned.
    """
    return _frame_retrieved_context(str([{"content": t, "score": 0.9} for t in chunk_texts]))


def _capture(calls: list[str | list[str]], *, is_error: bool = False) -> list[dict]:
    """Drive `_record_tool_result` once per retrieve call; return tool_calls_log.

    Each entry is either a list of chunk texts (framed by the real producer) or a
    raw string, which is how an undecodable or errored payload is expressed.
    """
    tool_calls_log: list[dict] = []
    tool_names_by_use_id: dict[str, str] = {}
    for i, call in enumerate(calls):
        use_id = f"toolu_{i}"
        tool_names_by_use_id[use_id] = "retrieve"
        tool_calls_log.append({"tool_name": "retrieve", "input": {"query": f"q{i}"}})
        payload = _framed(call) if isinstance(call, list) else call
        agent_module._record_tool_result(
            ToolResultBlock(
                tool_use_id=use_id,
                content=[{"type": "text", "text": payload}],
                is_error=is_error,
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
# The load-bearing guard: what run_auditor is actually handed.
# ---------------------------------------------------------------------------


class TestWhatTheAuditorIsActuallyHanded:
    """Drive `_dispatch_validation_chain` and read the argument off `run_auditor.si`.

    This is the guard the first version of this module lacked. Every one of the
    five defect reintroductions the adversary found changes this value, and none
    of them changed the shape of the line the old AST checks inspected.
    """

    @staticmethod
    def _dispatch(tool_calls_log: list[dict]) -> tuple[str, tuple]:
        with (
            patch.object(agent_module, "celery_chain") as chain,
            patch.object(agent_module, "run_gatekeeper"),
            patch.object(agent_module, "run_auditor") as auditor,
            patch.object(agent_module, "run_strategist"),
            patch.object(agent_module, "run_retrieval_faithfulness"),
        ):
            returned = agent_module._dispatch_validation_chain(
                agent_id="agent-1",
                job_id="job-5-16",
                response_text="Tier A costs R450.",
                message="what does tier A cost?",
                conversation_id="conv-1",
                tool_calls_log=tool_calls_log,
            )
            chain.return_value.apply_async.assert_called_once_with(queue="runtime")
            return returned, auditor.si.call_args.args

    def test_the_auditor_receives_every_chunk_untruncated(self) -> None:
        shown = [
            _chunk_text(f"chunk{i}", CHUNK_CONTENT_CHAR_LIMIT) for i in range(MAX_CHUNKS)
        ]
        _returned, args = self._dispatch(_capture([shown]))

        handed_to_judge = json.loads(args[4])
        assert handed_to_judge == shown, (
            "the string dispatched to run_auditor is not the chunks the agent was "
            f"shown. agent saw {len(shown)} chunks / {sum(len(c) for c in shown)} "
            f"chars; judge is handed {len(handed_to_judge)} / "
            f"{sum(len(c) for c in handed_to_judge)}"
        )

    def test_every_retrieve_call_reaches_the_auditor(self) -> None:
        """The old `[:3]` dropped calls 4+. The real per-turn cap is 8."""
        calls = [
            [_chunk_text(f"call{i}", 700)] for i in range(_RETRIEVE_CALLS_PER_TURN_MAX)
        ]
        _returned, args = self._dispatch(_capture(calls))

        handed_to_judge = json.loads(args[4])
        missing = [c[0][:20] for c in calls if c[0] not in handed_to_judge]
        assert not missing, (
            f"{len(missing)} of {len(calls)} retrieve calls contributed nothing to "
            f"what run_auditor was handed: {missing}"
        )

    def test_a_chunk_longer_than_the_old_cap_arrives_whole(self) -> None:
        long_chunk = _chunk_text("prices", 1500)
        assert len(long_chunk) > OLD_PER_CALL_CHAR_CAP, (
            "the fixture chunk is shorter than the cap under test, so this would "
            "pass with the old code — a tautology, not a proof"
        )
        _returned, args = self._dispatch(_capture([[long_chunk]]))

        assert long_chunk in json.loads(args[4]), (
            "the chunk the agent answered from did not reach run_auditor whole"
        )

    def test_the_returned_string_is_the_dispatched_string(self) -> None:
        """Closes the gap between what the seam reports and what it sends.

        Without this, a change could return the honest value and dispatch a
        truncated one, and every assertion above would still pass.
        """
        returned, args = self._dispatch(_capture([[_chunk_text("a", 900)]]))
        assert returned == args[4]

    def test_a_turn_that_retrieved_nothing_hands_over_an_empty_context(self) -> None:
        """The complement, and it must stay honest.

        Padding an empty context would make an ungrounded answer look judged.
        """
        _returned, args = self._dispatch([{"tool_name": "lookup_structured", "result": "x"}])
        assert json.loads(args[4]) == []


# ---------------------------------------------------------------------------
# The four states of a retrieve call, counted separately.
# ---------------------------------------------------------------------------


class TestTheFourStatesAreNotCollapsed:
    """The first version inferred "unparsed" from an empty chunk list.

    That made three different observations indistinguishable: a corpus miss, an
    undecodable payload, and a DoS-guard refusal. It also fed the repr of a
    framed empty list to the judge as evidence for the corpus-miss case, which is
    the exact thing 5.16 exists to stop.
    """

    def test_a_corpus_miss_contributes_nothing_and_is_not_called_a_decode_failure(self) -> None:
        contexts, counts = agent_module._judge_retrieved_context(_capture([[]]))

        assert contexts == [], (
            "a retrieve that found nothing put its own framed-empty-list repr in "
            "front of the judge as a retrieved passage"
        )
        assert counts["empty"] == 1
        assert counts["unparsed"] == 0, (
            "a corpus miss was counted as a decode failure, so the metric E2E-6 "
            "reads cannot tell 'found nothing' from 'could not read it'"
        )

    def test_an_undecodable_payload_falls_back_and_is_counted_as_unparsed(self) -> None:
        """Reachable only via the SOURCE key, which is why the product reads it."""
        log = _capture(["<<<RETRIEVED CONTEXT>>> not-a-python-literal <<<END>>>"])
        assert log[0][agent_module.RETRIEVE_CHUNKS_SOURCE_KEY] == (
            agent_module.RETRIEVE_CHUNKS_UNPARSED
        ), "the fixture did not actually produce an undecodable payload"

        contexts, counts = agent_module._judge_retrieved_context(log)

        assert contexts, (
            "an undecodable retrieve contributed nothing, so the judge is handed "
            "[] for a turn that retrieved — BACKLOG 5.11 in a new spelling"
        )
        assert counts["unparsed"] == 1
        assert counts["empty"] == 0

    def test_an_errored_retrieve_is_not_evidence(self) -> None:
        """The DoS guard's refusal is a control message the agent read as a failure.

        `retrieve_tool` returns "Retrieve quota exceeded for this turn" with
        is_error set. Feeding it to the judge puts a sentence about quotas into
        the RETRIEVED CONTEXT block of the turn least likely to be well grounded.
        """
        refusal = (
            f"Retrieve quota exceeded for this turn (max {_RETRIEVE_CALLS_PER_TURN_MAX} "
            "calls allowed). Please synthesize an answer from the results already retrieved."
        )
        contexts, counts = agent_module._judge_retrieved_context(
            _capture([refusal], is_error=True)
        )

        assert not any("quota exceeded" in c.lower() for c in contexts), (
            "a tool-error control message reached the grounding judge as a "
            "retrieved passage"
        )
        assert contexts == []
        assert counts["errored"] == 1
        assert counts["unparsed"] == 0

    def test_the_states_are_counted_independently_in_one_turn(self) -> None:
        """A turn mixing all four, so no count can be satisfied by another's value."""
        good = [_chunk_text("a", 700), _chunk_text("b", 700)]
        log = _capture([good, [], "<<<RETRIEVED CONTEXT>>> junk <<<END>>>"])
        log += _capture(["refused"], is_error=True)

        contexts, counts = agent_module._judge_retrieved_context(log)

        assert counts == {
            "calls": 4,
            "chunks": 2,
            "empty": 1,
            "unparsed": 1,
            "errored": 1,
        }, f"state tally is wrong: {counts}"
        assert contexts[:2] == good


# ---------------------------------------------------------------------------
# The bound, and that it belongs to the retrieval layer.
# ---------------------------------------------------------------------------


def test_the_helper_applies_no_cap_of_its_own() -> None:
    """No number is chosen here. The bound is MAX_CHUNKS x CHAR_LIMIT x CALL_CAP.

    Driven at the real per-turn ceiling, which is `_RETRIEVE_CALLS_PER_TURN_MAX`
    (8) and NOT `max_turns` (6): max_turns bounds assistant turns, and parallel
    tool use puts several retrieves in one. The first version of this test used 6
    and therefore did not exercise the bound it named.
    """
    calls = [
        [_chunk_text(f"c{call}-{i}", CHUNK_CONTENT_CHAR_LIMIT) for i in range(MAX_CHUNKS)]
        for call in range(_RETRIEVE_CALLS_PER_TURN_MAX)
    ]
    contexts, counts = agent_module._judge_retrieved_context(_capture(calls))

    expected = [text for call in calls for text in call]
    assert contexts == expected, (
        f"{len(expected) - len(contexts)} of {len(expected)} chunks were dropped "
        "at the judge boundary"
    )
    assert counts["chunks"] == MAX_CHUNKS * _RETRIEVE_CALLS_PER_TURN_MAX


def test_the_worst_case_is_the_documented_one() -> None:
    """Pins the number the cost estimate is derived from.

    If a retrieval constant moves, the cost of every Auditor call moves with it,
    and this is the test that says so out loud instead of leaving it to be
    rediscovered from a bill.
    """
    calls = [
        [_chunk_text(f"c{call}-{i}", CHUNK_CONTENT_CHAR_LIMIT) for i in range(MAX_CHUNKS)]
        for call in range(_RETRIEVE_CALLS_PER_TURN_MAX)
    ]
    contexts, _counts = agent_module._judge_retrieved_context(_capture(calls))

    ceiling = MAX_CHUNKS * CHUNK_CONTENT_CHAR_LIMIT * _RETRIEVE_CALLS_PER_TURN_MAX
    assert sum(len(c) for c in contexts) == ceiling == 80_000


def test_the_judge_gets_one_element_per_chunk_not_one_repr_per_call() -> None:
    """`tc["result"]` is a repr; the judge needs chunk text.

    A repr hands the judge dict syntax it cannot distinguish from evidence, in a
    single element, which is why eval.py:481 refuses to score `result`.
    """
    chunks = [_chunk_text("a", 700), _chunk_text("b", 700), _chunk_text("c", 700)]
    contexts, _counts = agent_module._judge_retrieved_context(_capture([chunks]))

    assert len(contexts) == len(chunks), (
        f"the judge got {len(contexts)} element(s) for {len(chunks)} chunks — "
        "chunk boundaries the agent saw were collapsed"
    )
    assert not any("'content':" in c or "'score':" in c for c in contexts), (
        "a Python repr of the chunk dicts reached the judge; the transport "
        "encoding is then most of its token budget and is scored as evidence"
    )
