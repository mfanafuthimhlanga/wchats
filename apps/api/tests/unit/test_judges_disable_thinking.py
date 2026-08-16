"""Every judge in `validation_service` must send `thinking` disabled.

Observed 2026-08-16 against DeepSeek's Anthropic-format endpoint, which is the
platform's default provider via `ANTHROPIC_BASE_URL`: a `messages.create`
carrying a forced `tool_choice` is rejected with

    HTTP 400 — Thinking mode does not support this tool_choice

Adding `thinking={"type": "disabled"}` clears it, and the same parameter is
accepted and inert on the real Anthropic API, so the fix is provider-neutral.
A judge is one forced-tool verdict call; there is nothing for it to think about.

The three judges live in one module and one of them (`call_auditor`) already has
a home in `test_auditor_truncation.py`. This module is scoped to the provider
contract rather than to a judge, so the Gatekeeper and Strategist — which have no
other unit test driving the real client — are covered in the same place as the
Auditor instead of being split across two files by accident of history.

Asserted on the **kwargs the client receives**, never on the source text: the
kwargs are what the endpoint validates, and a source-shaped guard bans one
spelling while the author picks the spelling.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services import validation_service

#: What every forced-tool-choice call site must send.
THINKING_DISABLED = {"type": "disabled"}


def _tool_use(payload: dict):
    return SimpleNamespace(type="tool_use", name="submit_verdict", input=payload)


_GATEKEEPER_VERDICT = {"verdict": "pass", "confidence": 0.9, "reason": "Addresses the question."}

_AUDITOR_VERDICT = {
    "verdict": "grounded",
    "confidence": 0.9,
    "citation_spans": [{"claim": "R 480/kg", "source_chunk": "Yirgacheffe: R 480/kg", "supported": True}],
    "reason": "The price claim is supported.",
}

_STRATEGIST_VERDICT = {"verdict": "ship", "confidence": 0.9, "issues": [], "reason": "On brand."}


#: (name, invoke, verdict payload the mocked tool_use block returns)
_JUDGES = [
    ("call_gatekeeper", lambda: validation_service.call_gatekeeper("q", "r"), _GATEKEEPER_VERDICT),
    ("call_auditor", lambda: validation_service.call_auditor("q", "r", "ctx"), _AUDITOR_VERDICT),
    (
        "call_strategist",
        lambda: validation_service.call_strategist("q", "r", "role", "voice", ["do"], ["do not"]),
        _STRATEGIST_VERDICT,
    ),
]


@pytest.mark.parametrize(
    ("name", "invoke", "payload"), _JUDGES, ids=[judge[0] for judge in _JUDGES]
)
def test_judge_sends_thinking_disabled_with_its_forced_tool_choice(name, invoke, payload):
    """The parameter the provider actually receives, per judge."""
    captured: dict = {}

    def _create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(stop_reason="tool_use", content=[_tool_use(payload)])

    with patch.object(validation_service.ANTHROPIC_CLIENT.messages, "create", _create):
        invoke()

    # The precondition: without a forced tool_choice there is nothing to disable
    # thinking for, and this assertion would be pinning an unrelated call.
    assert captured["tool_choice"]["type"] == "tool", (
        f"{name} no longer forces a tool_choice, so this guard proves nothing. "
        f"tool_choice={captured.get('tool_choice')!r}"
    )

    assert captured.get("thinking") == THINKING_DISABLED, (
        f"{name} sent thinking={captured.get('thinking')!r}. The default provider "
        "rejects a forced tool_choice with HTTP 400 'Thinking mode does not support "
        "this tool_choice' unless thinking is explicitly disabled, so this judge "
        "returns no verdict at all in production."
    )
