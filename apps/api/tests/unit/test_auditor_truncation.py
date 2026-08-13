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
from unittest.mock import patch

import pytest

from app.services import validation_service
from app.services.validation_service import (
    AUDITOR_MAX_CITATION_SPANS,
    AUDITOR_MAX_TOKENS,
    AuditorVerdictTruncated,
    call_auditor,
)


def _response(*, stop_reason: str, content: list):
    return SimpleNamespace(stop_reason=stop_reason, content=content)


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

    with patch.object(validation_service.ANTHROPIC_CLIENT.messages, "create", _create):
        call_auditor("q", "r", "ctx")

    assert captured["max_tokens"] == AUDITOR_MAX_TOKENS
    assert str(AUDITOR_MAX_CITATION_SPANS) in captured["system"], (
        "the span cap is defined but never told to the model; the constant would "
        f"be decorative. system={captured['system']!r}"
    )


def test_a_truncated_tool_call_raises_truncated_not_a_validation_error():
    """THE regression this module exists for.

    Before the fix this path raised pydantic's "citation_spans Field required",
    which is indistinguishable from a model that ignored its schema.
    """
    with patch.object(
        validation_service.ANTHROPIC_CLIENT.messages,
        "create",
        lambda **kw: _response(
            stop_reason="max_tokens", content=[_tool_use(_TRUNCATED_PAYLOAD)]
        ),
    ):
        with pytest.raises(AuditorVerdictTruncated) as exc_info:
            call_auditor("q", "r", "ctx")

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
    with patch.object(
        validation_service.ANTHROPIC_CLIENT.messages,
        "create",
        lambda **kw: _response(
            stop_reason="max_tokens", content=[_tool_use(_TRUNCATED_PAYLOAD)]
        ),
    ):
        with pytest.raises(AuditorVerdictTruncated):
            call_auditor("q", "r", "ctx")


def test_a_complete_verdict_still_validates_normally():
    """The guard must not cost the happy path."""
    with patch.object(
        validation_service.ANTHROPIC_CLIENT.messages,
        "create",
        lambda **kw: _response(stop_reason="tool_use", content=[_tool_use(_GOOD_VERDICT)]),
    ):
        verdict = call_auditor("q", "r", "ctx")

    assert verdict.verdict == "partial"
    assert verdict.confidence == 0.65
    assert len(verdict.citation_spans) == 1
    assert verdict.citation_spans[0].supported is True


def test_a_schema_violation_that_is_NOT_truncation_still_raises_validation_error():
    """The two failures stay distinguishable in both directions."""
    from pydantic import ValidationError

    with patch.object(
        validation_service.ANTHROPIC_CLIENT.messages,
        "create",
        lambda **kw: _response(
            stop_reason="tool_use", content=[_tool_use({"verdict": "partial"})]
        ),
    ):
        with pytest.raises(ValidationError):
            call_auditor("q", "r", "ctx")
