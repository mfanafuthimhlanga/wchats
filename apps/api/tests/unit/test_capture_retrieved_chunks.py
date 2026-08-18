"""BACKLOG 7.34 — the capture records what the worker stored, chunks included.

The rule under test is the one that decides what a judge is shown, and it is the
same rule `_persisted_chunks` applies on the way in. Kept testable on a machine
with no PostgreSQL by shaping the row separately from fetching it.
"""

from __future__ import annotations

from tests.evals.capture_responses import shape_tool_call

CHUNK = "[source: HANDBOOK.pdf | section: Returns]\nUnopened bags, 14 days."


def test_a_retrieve_with_chunks_carries_them_into_the_corpus():
    call = shape_tool_call("retrieve", {"query": "returns"}, [CHUNK])
    assert call["tool_name"] == "retrieve"
    assert call["result"] == {"chunks": [CHUNK]}


def test_a_corpus_miss_is_present_and_empty():
    call = shape_tool_call("retrieve", {"query": "nothing matches"}, [])
    assert call["result"] == {"chunks": []}, (
        "a retrieve that ran and matched nothing is an observation the judge can use"
    )


def test_null_chunks_leave_the_result_absent():
    call = shape_tool_call("escalate_to_human", {"reason": "frustration"}, None)
    assert call["result"] == {}


def test_the_two_empty_shapes_are_distinguishable():
    """The property the column exists for, asserted where the judge will read it."""
    miss = shape_tool_call("retrieve", {"query": "q"}, [])
    absent = shape_tool_call("retrieve", {"query": "q"}, None)
    assert miss["result"] != absent["result"]
    assert bool(miss["result"]) is True and bool(absent["result"]) is False, (
        "validate_corpus keys BLIND on falsiness, so a corpus miss must not read "
        "as an absent chunk"
    )


def test_a_missing_tool_name_becomes_empty_string_not_none():
    """`run_evals.py` compares this to a literal; None would raise rather than miss."""
    call = shape_tool_call(None, None, None)
    assert call["tool_name"] == ""
    assert call["input"] == {}


def test_the_corpus_validator_agrees_with_this_shape():
    """The two halves of 7.34 must read the same rows the same way."""
    from tests.evals import validate_corpus as vc

    good = shape_tool_call("retrieve", {"query": "q"}, [CHUNK])
    blind = shape_tool_call("retrieve", {"query": "q"}, None)
    record = {"response_text": "x" * 200, "tool_calls_log": [good]}
    assert vc._blind_findings("S-101", record) == []
    record = {"response_text": "x" * 200, "tool_calls_log": [blind]}
    assert vc._blind_findings("S-101", record), (
        "an absent chunk must be reported, or the corpus passes validation and "
        "fails grounding silently"
    )
