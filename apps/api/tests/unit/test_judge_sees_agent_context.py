"""BACKLOG 5.16 — the grounding judge must see exactly what the agent saw.

The defect, stated as what it cost rather than as a slice:

    `agent.py` built the Auditor's context as
    `json.dumps([str(r)[:600] for r in retrieve_results][:3])`, where `r` is
    `tc["result"]`, the AUDIT capture, already cut at
    RETRIEVE_RESULT_CAPTURE_CHARS because it reaches a jsonb column. Three losses
    in one line: 600 chars per call against the 10,000
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

import json
from unittest.mock import patch

from app.domain.retrieved_context import RetrievedChunk, RetrievedContext
from app.services import agent_loop
from app.services.agent_tools import (
    _RETRIEVE_CALLS_PER_TURN_MAX,
    CHUNK_CONTENT_CHAR_LIMIT,
    MAX_CHUNKS,
    _frame_retrieved_context,
)
from app.worker.tasks.runtime import agent as agent_module

#: The two numbers the old line applied, named so each assertion can say which
#: half of the defect it stands on.
OLD_PER_CALL_CHAR_CAP = 600
OLD_RESULT_COUNT_CAP = 3


# ---------------------------------------------------------------------------
# Harness — the REAL capture path, one retrieve call at a time.
# ---------------------------------------------------------------------------


def _chunk_text(label: str, length: int) -> str:
    """A chunk long enough that the old 600-char cap provably bit."""
    head = f"{label}: "
    return head + ("x" * (length - len(head)))


#: Provenance the agent is shown alongside every chunk. Present in the fixture
#: because BACKLOG 5.18 is that the judge was NOT shown it, and a fixture without
#: it cannot see that defect — the first version of this module built chunks as
#: `{"content": t, "score": 0.9}` and was structurally blind to the finding.
DOC_ID = "PRICE-LIST.pdf"


def _chunk_dicts(chunk_texts: list[str]) -> list[dict]:
    """The chunk dicts a retrieve hands over, built by the type that emits them.

    Through `RetrievedContext.to_json` rather than by hand, so this fixture
    carries exactly the five keys the product carries. A hand-built dict here
    once carried a `section` the retrieval layer has never emitted, and the test
    below then asserted the judge was shown it: 1.26's shape, a fixture
    manufacturing a contract the product abandoned.
    """
    return RetrievedContext(
        query="what do you charge?",
        strategy="rerank",
        chunks=tuple(
            RetrievedChunk(
                chunk_id=f"chunk-{i}",
                document_id=DOC_ID,
                content=t,
                score=0.9,
                rank=i + 1,
            )
            for i, t in enumerate(chunk_texts)
        ),
    ).to_json()["chunks"]


def _wire(chunk_texts: list[str]) -> dict:
    """The wire dict `retrieve_tool` returns, built by the REAL framer.

    Two halves, and both are production's. `content` is the framed JSON the MODEL
    reads; `_retrieved_context` carries the same chunks structurally, and that is
    what the capture writes to `tool_calls_log`. Building the text with
    `_frame_retrieved_context` rather than a literal means this fixture cannot
    drift away from what the tool emits — which is how 1.26 survived: a fixture
    that manufactured a contract the product had abandoned.
    """
    chunks = _chunk_dicts(chunk_texts)
    return {
        "content": [{"type": "text", "text": _frame_retrieved_context(json.dumps(chunks))}],
        "_retrieved_context": {"chunks": chunks},
    }


def _carries(elements: list[str], text: str) -> bool:
    """Is this chunk's text present, whole, in some judge element?"""
    return any(text in e for e in elements)


def _capture(calls: list[str | list[str]], *, is_error: bool = False) -> list[dict]:
    """Drive `agent_loop._log_entry` once per retrieve call; return tool_calls_log.

    Each entry is either a list of chunk texts (framed by the real producer, with
    the ride-along the tool hands over) or a raw string, which is a result the
    tool returned as TEXT ONLY. That is an errored result, or one whose retrieval
    never reached the capture.
    """
    tool_calls_log: list[dict] = []
    for i, call in enumerate(calls):
        if isinstance(call, list):
            wire = _wire(call)
        else:
            wire = {"content": [{"type": "text", "text": call}]}
        if is_error:
            wire["is_error"] = True
        tool_calls_log.append(
            agent_loop._log_entry("retrieve", {"query": f"q{i}"}, f"toolu_{i}", wire)
        )
    return tool_calls_log


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
        assert len(handed_to_judge) == len(shown), (
            f"the judge got {len(handed_to_judge)} elements for {len(shown)} "
            "chunks the agent was shown"
        )
        missing = [t[:20] for t in shown if not _carries(handed_to_judge, t)]
        assert not missing, (
            "chunk text the agent answered from did not reach run_auditor whole: "
            f"{missing}"
        )

    def test_every_retrieve_call_reaches_the_auditor(self) -> None:
        """The old `[:3]` dropped calls 4+. The real per-turn cap is 8."""
        calls = [
            [_chunk_text(f"call{i}", 700)] for i in range(_RETRIEVE_CALLS_PER_TURN_MAX)
        ]
        _returned, args = self._dispatch(_capture(calls))

        handed_to_judge = json.loads(args[4])
        missing = [c[0][:20] for c in calls if not _carries(handed_to_judge, c[0])]
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

        assert _carries(json.loads(args[4]), long_chunk), (
            "the chunk the agent answered from did not reach run_auditor whole"
        )

    def test_the_provenance_the_agent_saw_reaches_the_judge(self) -> None:
        """BACKLOG 5.18. A claim naming a document cannot be supported without it.

        `retrieve_tool` hands the agent the JSON of full chunk dicts, so the agent
        sees `document_id`, `chunk_id` and `score`. Sending the judge content
        alone reproduces 5.16's own failure mode one level down: the judge marks
        a claim unsupported because it was not shown the evidence.
        """
        _returned, args = self._dispatch(_capture([[_chunk_text("prices", 400)]]))
        handed_to_judge = json.loads(args[4])

        assert any(DOC_ID in e for e in handed_to_judge), (
            f"the source document {DOC_ID!r} is absent from the judge's context, "
            "so an answer citing it by name has nothing to be grounded against"
        )
        assert any("chunk-0" in e for e in handed_to_judge), (
            "the chunk id the agent saw is absent from the judge's context"
        )
        assert not any("'content':" in e for e in handed_to_judge), (
            "the raw dict repr reached the judge; provenance must be rendered, "
            "not pasted, or the transport encoding is scored as evidence"
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

    def test_a_result_with_no_ride_along_falls_back_and_is_counted_as_unparsed(self) -> None:
        """A hand-built capture, driven against a branch production cannot reach.

        `retrieve_tool` attaches its ride-along on its one success path and every
        other producer sets `is_error`, and the error check runs first here, so
        `counts["unparsed"]` reads 0 in production. The fixture builds the wire
        by hand so the degraded fallback is still proven working on the day a new
        producer returns a success carrying no ride-along.
        """
        log = _capture(["<<<RETRIEVED CONTEXT>>> text with no ride-along <<<END>>>"])
        assert log[0][agent_loop.RETRIEVE_CHUNKS_SOURCE_KEY] == (
            agent_loop.RETRIEVE_CHUNKS_UNPARSED
        ), "the fixture did not actually produce a result without its retrieval"

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
        assert all(_carries(contexts[:2], t) for t in good)


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
    dropped = [t[:20] for t in expected if not _carries(contexts, t)]
    assert not dropped, (
        f"{len(dropped)} of {len(expected)} chunks were dropped at the judge "
        "boundary"
    )
    assert counts["chunks"] == MAX_CHUNKS * _RETRIEVE_CALLS_PER_TURN_MAX


def test_the_worst_case_is_bounded_by_the_retrieval_contract() -> None:
    """Pins the number the cost estimate and BACKLOG 5.20 are derived from.

    Two claims, because they fail differently. The CONTENT total is exactly the
    retrieval ceiling, so if a retrieval constant moves, the cost of every
    Auditor call moves with it and this test says so instead of leaving it to be
    found in a bill. The TOTAL adds one provenance header per chunk (5.18), which
    is bounded per chunk and must stay small relative to the content.
    """
    calls = [
        [_chunk_text(f"c{call}-{i}", CHUNK_CONTENT_CHAR_LIMIT) for i in range(MAX_CHUNKS)]
        for call in range(_RETRIEVE_CALLS_PER_TURN_MAX)
    ]
    contexts, _counts = agent_module._judge_retrieved_context(_capture(calls))

    ceiling = MAX_CHUNKS * CHUNK_CONTENT_CHAR_LIMIT * _RETRIEVE_CALLS_PER_TURN_MAX
    assert ceiling == 80_000
    content_total = sum(len(t) for call in calls for t in call)
    assert content_total == ceiling

    total = sum(len(c) for c in contexts)
    overhead = total - content_total
    assert 0 < overhead <= 200 * len(contexts), (
        f"provenance overhead is {overhead} chars over {len(contexts)} chunks, "
        "which is no longer a header per chunk"
    )
    assert total < 1.2 * ceiling, (
        f"the judge's context is {total} chars against a {ceiling}-char retrieval "
        "ceiling; BACKLOG 5.20's measured Celery message size no longer holds"
    )


# ---------------------------------------------------------------------------
# GONE WITH THE STREAM: TestResultsAttachToTheCallThatProducedThem (BACKLOG 5.21)
#
# Its subject was attribution under parallel tool use. `_record_tool_result`
# matched "the most recent retrieve entry without a result", walking in reverse,
# so the first result to arrive landed on the LAST call issued and the chunks
# were attributed to the wrong query. ADR 0008's loop makes that shape
# unreachable: `_run_tool_call` awaits ONE tool and builds ITS log entry from ITS
# own result, so there is no arrival order to get wrong and no entry to search
# for. What survives of the pin is `tool_use_id` on every entry, which
# `tests/unit/test_agent_loop.py` asserts against the call ids the model sent.
# ---------------------------------------------------------------------------


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
