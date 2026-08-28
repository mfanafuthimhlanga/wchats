"""BACKLOG 7.34 — a stored tool call carries the chunks its answer was grounded in.

`grounding_fidelity`'s rubric asks whether a claim is traceable to a chunk
"provided in the tool_calls log". Nothing outside the worker could provide one:
the untruncated chunks live on the in-process log, `retrieved_context_json` is a
Celery task argument, and the customer SSE carries a 200-character repr. So the
rubric's PASS branch was unreachable and every grounding verdict had to FAIL
whatever the answer said, which is a confident number about nothing.

These tests are on `_persisted_chunks`, the function that decides what one row
stores, rather than on the INSERT: the SQL needs a PostgreSQL this machine does
not have, and a test that cannot run is unobserved rather than passing. What is
provable here is the decision, and the decision is where 5.16's failure lives.
"""

from __future__ import annotations

import json

# The capture keys belong to the loop that writes them (ADR 0008); the column
# writer belongs to the task that persists them. Two imports, one for each.
from app.services.agent_loop import (
    RETRIEVE_CHUNKS_KEY,
    RETRIEVE_CHUNKS_PARSED,
    RETRIEVE_CHUNKS_SOURCE_KEY,
    RETRIEVE_CHUNKS_UNPARSED,
    RETRIEVE_JUDGE_CHUNKS_KEY,
    RETRIEVE_RESULT_IS_ERROR_KEY,
)
from app.worker.tasks.runtime.agent import _persisted_chunks

JUDGE_CHUNK = (
    "[source: ACME-HANDBOOK.pdf | section: Returns | chunk: c-1 | score: 0.91]\n"
    "Unopened bags may be returned within 14 days of delivery."
)
CONTENT_ONLY = "Unopened bags may be returned within 14 days of delivery."


def _retrieve(**overrides) -> dict:
    call = {
        "tool_name": "retrieve",
        "input": {"query": "return policy"},
        "result": "<audit repr>",
        RETRIEVE_RESULT_IS_ERROR_KEY: False,
        RETRIEVE_CHUNKS_SOURCE_KEY: RETRIEVE_CHUNKS_PARSED,
        RETRIEVE_CHUNKS_KEY: [CONTENT_ONLY],
        RETRIEVE_JUDGE_CHUNKS_KEY: [JUDGE_CHUNK],
    }
    call.update(overrides)
    return call


def test_a_retrieve_stores_its_chunks():
    stored = _persisted_chunks(_retrieve())
    assert json.loads(stored) == [JUDGE_CHUNK]


def test_the_stored_rendering_carries_provenance():
    """BACKLOG 5.18: a claim naming a document is unsupported by a context without it."""
    stored = json.loads(_persisted_chunks(_retrieve()))
    assert "ACME-HANDBOOK.pdf" in stored[0]
    assert "section: Returns" in stored[0]
    assert stored != [CONTENT_ONLY], (
        "the content-only rendering is what Ragas reads; a judge asked whether a "
        "claim is SUPPORTED needs the provenance the agent saw"
    )


def test_a_corpus_miss_stores_an_empty_list_not_null():
    """A retrieve that matched nothing is an observation, and it is not absence."""
    stored = _persisted_chunks(_retrieve(**{RETRIEVE_JUDGE_CHUNKS_KEY: []}))
    assert stored is not None
    assert json.loads(stored) == []


def test_a_tool_that_does_not_retrieve_stores_null():
    call = {"tool_name": "escalate_to_human", "input": {"reason": "frustration"}}
    assert _persisted_chunks(call) is None


def test_an_undecodable_capture_stores_null_not_an_empty_list():
    """BACKLOG 5.16 one level down, and the reason this is not `or []`.

    An empty context makes every claim unsupported. Reporting a decode failure as
    "this retrieve found nothing" manufactures an ungrounded verdict that is
    about the decoder rather than about the answer.
    """
    call = _retrieve(**{
        RETRIEVE_CHUNKS_SOURCE_KEY: RETRIEVE_CHUNKS_UNPARSED,
        RETRIEVE_JUDGE_CHUNKS_KEY: [],
    })
    assert _persisted_chunks(call) is None


def test_an_errored_retrieve_stores_null():
    """`retrieve_tool` returns its DoS-guard refusal as ordinary text with is_error set."""
    call = _retrieve(**{RETRIEVE_RESULT_IS_ERROR_KEY: True})
    assert _persisted_chunks(call) is None


def test_a_pre_capture_log_entry_stores_null():
    """A hand-built or pre-7.34 entry has no judge-chunks key at all."""
    call = {"tool_name": "retrieve", "input": {"query": "q"}, "result": "<repr>"}
    assert _persisted_chunks(call) is None


def test_null_and_empty_are_distinguishable_by_the_reader():
    """The property the column exists to preserve, asserted as one statement."""
    miss = _persisted_chunks(_retrieve(**{RETRIEVE_JUDGE_CHUNKS_KEY: []}))
    undecodable = _persisted_chunks(
        _retrieve(**{RETRIEVE_CHUNKS_SOURCE_KEY: RETRIEVE_CHUNKS_UNPARSED})
    )
    assert miss == "[]" and undecodable is None, (
        "a corpus miss and a decode failure must not arrive at the judge as the "
        "same value; collapsing them is what BACKLOG 5.16 cost"
    )
