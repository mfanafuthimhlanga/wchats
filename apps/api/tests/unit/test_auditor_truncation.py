"""The grounding judge must be able to return a verdict about real evidence.

BACKLOG `5.14`. Observed 2026-08-13 in E2E-3 — the first customer turn ever run
against a real corpus, and therefore the first time the Auditor was handed a
non-empty `retrieved_context` (`5.11`: it received `"[]"` on every turn from
2026-05-16 until `dc67d37`).

    run_auditor.failed  error="2 validation errors for AuditorVerdict
      citation_spans  Field required  [input_value={'verdict': 'partial', 'confidence': 0.65}]
      reason          Field required"

Three attempts, all identical. `max_tokens` was **512**, and the Auditor's
verdict is the one judge output that must ECHO EVIDENCE: one
`{claim, source_chunk, supported}` object per audited claim. An empty
`citation_spans` costs nothing, which is why 512 sufficed for three months of
empty-context audits; a real multi-claim answer over 962 tokens of context
truncates the tool JSON mid-object, so the required fields never arrive.

**The failure mode this module exists to prevent is not the truncation itself —
it is the misdiagnosis.** A truncated tool call arrives as a partial dict, and
pydantic then reports "Field required", which reads exactly like a model
ignoring its schema. The two have different remedies (raise the ceiling vs.
change the prompt), so `call_auditor` now raises `AuditorVerdictTruncated`
before validating.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import validation_service
from app.services.validation_service import (
    AUDITOR_MAX_CITATION_SPANS,
    AUDITOR_MAX_TOKENS,
    AuditorVerdictTruncated,
    call_auditor,
)
from tests.model_doubles import factory, ledger


def _response(*, stop_reason: str, content: list):
    return SimpleNamespace(stop_reason=stop_reason, content=content)


def _client(create):
    """The double the factory hands the Auditor (ticket #47)."""
    return SimpleNamespace(messages=SimpleNamespace(create=create))


def _tool_use(payload: dict):
    return SimpleNamespace(type="tool_use", name="submit_verdict", input=payload)


_GOOD_VERDICT = {
    "verdict": "partial",
    "confidence": 0.65,
    "citation_spans": [
        {"claim": "R 480/kg", "source_chunk": "Yirgacheffe: R 480/kg", "supported": True}
    ],
    "reason": "The price claim is supported by the wholesale pricing section.",
}

#: Exactly what the API returned in E2E-3 — a tool call cut off mid-JSON.
_TRUNCATED_PAYLOAD = {"verdict": "partial", "confidence": 0.65}


def test_the_ceiling_is_big_enough_to_be_worth_calling_a_ceiling():
    """512 was the observed-insufficient value; guard against a silent revert."""
    assert AUDITOR_MAX_TOKENS >= 2048, (
        f"AUDITOR_MAX_TOKENS is {AUDITOR_MAX_TOKENS}. 512 was measured to truncate "
        "a real verdict in E2E-3. Lowering this re-opens BACKLOG 5.14."
    )


def test_the_span_cap_exists_so_the_verdict_cannot_grow_without_bound():
    """The ceiling alone is not a fix: output scales with the answer."""
    assert 1 <= AUDITOR_MAX_CITATION_SPANS <= 20


def test_the_span_cap_reaches_the_model():
    """A cap the prompt never states is a cap on nothing."""
    captured = {}

    def _create(**kwargs):
        captured.update(kwargs)
        return _response(stop_reason="tool_use", content=[_tool_use(_GOOD_VERDICT)])

    with factory(_client(_create)):
        call_auditor("q", "r", "ctx", ledger())

    assert captured["max_tokens"] == AUDITOR_MAX_TOKENS

    # BACKLOG 1.33. This was `str(AUDITOR_MAX_CITATION_SPANS) in system` -- a
    # single-digit substring search over a paragraph of prose. An adversarial
    # review set the cap to 2, DELETED the sentence stating it from the system
    # prompt, and observed 7 passed: the "2" in "under 25 words" satisfied it.
    # The test whose docstring reads "a cap the prompt never states is a cap on
    # nothing" was green with the prompt never stating the cap. It was
    # non-vacuous only by the accident that today's value is 8.
    #
    # Pinned as the whole phrase, so the number must appear in the sentence that
    # actually instructs the model.
    expected_phrase = f"at most {AUDITOR_MAX_CITATION_SPANS} citation spans"
    assert expected_phrase in captured["system"], (
        f"the system prompt does not contain {expected_phrase!r}, so the span "
        "cap is a decorative constant the model is never told about. "
        f"system={captured['system']!r}"
    )


def test_a_truncated_tool_call_raises_truncated_not_a_validation_error():
    """THE regression this module exists for.

    Before the fix this path raised pydantic's "citation_spans Field required",
    which is indistinguishable from a model that ignored its schema.
    """
    with factory(_client(
    lambda **kw: _response(
        stop_reason="max_tokens", content=[_tool_use(_TRUNCATED_PAYLOAD)]
    )
    )):
        with pytest.raises(AuditorVerdictTruncated) as exc_info:
            call_auditor("q", "r", "ctx", ledger())

    message = str(exc_info.value)
    assert "max_tokens" in message, message
    # The distinction that keeps the measurement layer honest.
    assert "ungrounded" in message.lower(), (
        "the error must say a truncated verdict is not an ungrounded verdict; "
        "that is the whole reason it is a separate exception type"
    )


def test_truncation_is_checked_before_validation():
    """Order matters: a truncated payload is ALSO schema-invalid.

    If validation ran first, the specific error would never be reachable and
    this module would be pinning a branch that cannot execute.
    """
    with factory(_client(
    lambda **kw: _response(
        stop_reason="max_tokens", content=[_tool_use(_TRUNCATED_PAYLOAD)]
    )
    )):
        with pytest.raises(AuditorVerdictTruncated):
            call_auditor("q", "r", "ctx", ledger())


def test_a_complete_verdict_still_validates_normally():
    """The guard must not cost the happy path."""
    with factory(_client(
    lambda **kw: _response(stop_reason="tool_use", content=[_tool_use(_GOOD_VERDICT)])
    )):
        verdict = call_auditor("q", "r", "ctx", ledger())

    assert verdict.verdict == "partial"
    assert verdict.confidence == 0.65
    assert len(verdict.citation_spans) == 1
    assert verdict.citation_spans[0].supported is True


def test_a_schema_violation_that_is_NOT_truncation_still_raises_validation_error():
    """The two failures stay distinguishable in both directions."""
    from pydantic import ValidationError

    with factory(_client(
    lambda **kw: _response(
        stop_reason="tool_use", content=[_tool_use({"verdict": "partial"})]
    )
    )):
        with pytest.raises(ValidationError):
            call_auditor("q", "r", "ctx", ledger())


# ---------------------------------------------------------------------------
# BACKLOG 5.19 — the retrieval frame reaches the judge
# ---------------------------------------------------------------------------


def test_the_retrieved_context_reaches_the_judge_framed():
    """The SEC-02/L6 boundary the AGENT gets must reach the judge too.

    `retrieve_tool` wraps retrieved chunks in a header saying everything inside
    is data and never instructions; `agent.py` strips it when decoding the
    payload back into chunks. So the Auditor used to receive tenant-ingested,
    attacker-influenceable text bare, and since `5.16` it receives up to 80,000
    chars of it rather than 1,800.

    Asserts the SAME string the agent gets, imported from the one place it is
    defined, so a second copy cannot drift away from the control it enforces.
    """
    from app.domain.context_frame import (
        RETRIEVED_CONTEXT_FOOTER,
        RETRIEVED_CONTEXT_HEADER,
    )

    seen = {}

    def _capture(**kw):
        seen.update(kw)
        return _response(stop_reason="tool_use", content=[_tool_use(_GOOD_VERDICT)])

    context = '["Tier A costs R450."]'
    with factory(_client(_capture)):
        call_auditor("q", "r", context, ledger())

    sent = seen["messages"][0]["content"]
    assert RETRIEVED_CONTEXT_HEADER in sent, (
        "the judge's retrieved context carries no data-not-instructions "
        "boundary, so a directive inside a tenant's own document is presented "
        "to the grounding judge as ordinary evidence"
    )
    assert RETRIEVED_CONTEXT_FOOTER in sent
    header_at = sent.index(RETRIEVED_CONTEXT_HEADER)
    assert header_at < sent.index(context) < sent.index(RETRIEVED_CONTEXT_FOOTER), (
        "the context is not enclosed by the frame; a boundary the evidence sits "
        "outside of bounds nothing"
    )


# ---------------------------------------------------------------------------
# BACKLOG 5.18 — the two output constants must stay solvent together
# ---------------------------------------------------------------------------


def test_the_span_cap_and_the_token_ceiling_are_solvent_together():
    """`5.14` needed BOTH numbers and nothing pinned their relationship.

    A verdict is `AUDITOR_MAX_CITATION_SPANS` spans, each carrying a `claim` and
    a `source_chunk` excerpt the system prompt caps at 25 words, plus a reason
    and the JSON scaffolding. If someone raises the span cap without raising the
    ceiling, `5.14` returns: every verdict truncates and the error names the
    schema rather than the budget.

    Deliberately arithmetic rather than a live call. It is the RELATIONSHIP that
    has to hold, and the cost of proving it should not be an API bill.
    """
    words_per_excerpt = 25
    tokens_per_word = 1.4          # conservative for English prose
    excerpts_per_span = 2          # claim + source_chunk
    json_overhead_per_span = 20    # keys, quotes, braces, the boolean

    span_tokens = (
        words_per_excerpt * tokens_per_word * excerpts_per_span
        + json_overhead_per_span
    )
    reason_and_scaffolding = 150

    worst_case = (
        validation_service.AUDITOR_MAX_CITATION_SPANS * span_tokens
        + reason_and_scaffolding
    )

    assert worst_case < validation_service.AUDITOR_MAX_TOKENS, (
        f"a full verdict needs about {worst_case:.0f} output tokens against a "
        f"ceiling of {validation_service.AUDITOR_MAX_TOKENS}. That is BACKLOG "
        "5.14 exactly: the tool call truncates mid-JSON and pydantic reports "
        "'Field required', which points at the prompt instead of the budget."
    )


# NOT ADDED HERE, deliberately: a second "is the cap in the prompt?" test.
# `test_the_span_cap_reaches_the_model` above already pins the whole phrase
# `at most {N} citation spans`, and the weaker `str(N) in system` form is the
# exact vacuity BACKLOG 1.33 B5 removed — the "2" in "under 25 words" satisfied
# it. Two guards on one claim, one of them weaker, is how the weaker one becomes
# the one someone edits.
