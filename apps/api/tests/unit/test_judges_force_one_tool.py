"""Every judge in `validation_service` forces one tool call and samples at 0.

Issue #76 moved these three off DeepSeek's Anthropic-format endpoint onto OpenAI
`chat.completions`, and the parameters changed with the wire. `thinking={"type":
"disabled"}` went with the endpoint that needed it: it cleared an HTTP 400
("Thinking mode does not support this tool_choice") observed 2026-08-16, and
OpenAI rejects a body field it does not declare, so keeping it would break the
call it used to fix. What survives that move is the reason the guard existed at
all. A judge that does not force its tool returns prose, and every reader
downstream then gets `ValueError` instead of a verdict.

So this module pins the three things a judge's request has to carry:
  - a forced `tool_choice` naming `submit_verdict`, in the OpenAI function shape;
  - `temperature=0`, because a sampling judge moves rho, pass@k and a deploy gate
    for reasons that have nothing to do with the response being judged;
  - no Anthropic-only parameter, which the OpenAI endpoint would reject outright.

The three judges live in one module and one of them (`call_auditor`) already has
a home in `test_auditor_truncation.py`. This module is scoped to the provider
contract rather than to a judge, so the Gatekeeper and Strategist — which have no
other unit test driving the real client — are covered in the same place as the
Auditor instead of being split across two files by accident of history.

Asserted on the **kwargs the client receives**, never on the source text: the
kwargs are what the endpoint validates, and a source-shaped guard bans one
spelling while the author picks the spelling. The client itself comes from the
factory since ticket #47, so the patch target is `make_client` and the three
judges are handed a double.
"""

from __future__ import annotations

import pytest

from app.services import validation_service
from tests.model_doubles import completion, factory, ledger, openai_client, tool_call

#: The parameters that belonged to the Anthropic-format endpoint. Any one of them
#: on an OpenAI request body is an unrecognised field, not a harmless extra.
ANTHROPIC_ONLY = ("thinking", "system", "max_tokens")

#: BACKLOG 8.2a. Judgement is the one task that wants no creativity, and until
#: 8.2a `grep -rn "temperature" app tests/evals` returned NOTHING: every judge in
#: the platform sampled at the provider default, including the Actor gate that
#: runs before money moves. Some verdict variance survives temperature 0
#: anyway, from batching and hardware nondeterminism, which is why a
#: high-stakes verdict wants more than one sample. Not a reason to leave it
#: unset. An earlier version put that at 3 to 8 percent, which is quoted from a talk and has never been measured in this system (BACKLOG 8.11).
JUDGEMENT_TEMPERATURE = 0


_GATEKEEPER_VERDICT = {"verdict": "pass", "confidence": 0.9, "reason": "Addresses the question."}

_AUDITOR_VERDICT = {
    "verdict": "grounded",
    "confidence": 0.9,
    "citation_spans": [{"claim": "R 480/kg", "source_chunk": "Yirgacheffe: R 480/kg", "supported": True}],
    "reason": "The price claim is supported.",
}

_STRATEGIST_VERDICT = {"verdict": "ship", "confidence": 0.9, "issues": [], "reason": "On brand."}


#: (name, invoke, verdict payload the mocked tool call returns)
_JUDGES = [
    (
        "call_gatekeeper",
        lambda: validation_service.call_gatekeeper("q", "r", ledger()),
        _GATEKEEPER_VERDICT,
    ),
    (
        "call_auditor",
        lambda: validation_service.call_auditor("q", "r", "ctx", ledger()),
        _AUDITOR_VERDICT,
    ),
    (
        "call_strategist",
        lambda: validation_service.call_strategist(
            "q", "r", "role", "voice", ["do"], ["do not"], ledger()
        ),
        _STRATEGIST_VERDICT,
    ),
]


def _drive(payload: dict, invoke) -> dict:
    """Run one judge against a double and hand back the kwargs it sent."""
    captured: dict = {}

    def _create(**kwargs):
        captured.update(kwargs)
        return completion(
            tool_calls=[tool_call("submit_verdict", payload)], finish_reason="tool_calls"
        )

    with factory(openai_client(create=_create)):
        invoke()
    return captured


@pytest.mark.parametrize(
    ("name", "invoke", "payload"), _JUDGES, ids=[judge[0] for judge in _JUDGES]
)
def test_judge_forces_its_submit_verdict_tool(name, invoke, payload):
    """The parameter the provider actually receives, per judge."""
    captured = _drive(payload, invoke)

    assert captured["tool_choice"] == {
        "type": "function",
        "function": {"name": "submit_verdict"},
    }, (
        f"{name} no longer forces submit_verdict, so it can answer in prose and every "
        f"reader downstream gets a ValueError. tool_choice={captured.get('tool_choice')!r}"
    )


@pytest.mark.parametrize(
    ("name", "invoke", "payload"), _JUDGES, ids=[judge[0] for judge in _JUDGES]
)
def test_judge_declares_that_tool_as_an_openai_function(name, invoke, payload):
    """A forced name the tool list does not declare is a 400, not a fallback."""
    captured = _drive(payload, invoke)

    declared = [tool["function"]["name"] for tool in captured["tools"]]
    assert declared == ["submit_verdict"], f"{name} declared {declared!r}"
    assert all(tool["type"] == "function" for tool in captured["tools"]), (
        f"{name} sent a tool that is not an OpenAI function: {captured['tools']!r}"
    )


@pytest.mark.parametrize(
    ("name", "invoke", "payload"), _JUDGES, ids=[judge[0] for judge in _JUDGES]
)
def test_judge_samples_at_zero(name, invoke, payload):
    captured = _drive(payload, invoke)

    assert captured.get("temperature") == JUDGEMENT_TEMPERATURE, (
        f"{name} sent temperature={captured.get('temperature')!r}. A judge that samples "
        "returns a different verdict on the same input, which makes every number "
        "downstream of it - rho, pass@k, a deploy gate - move for reasons that have "
        "nothing to do with the response being judged."
    )


@pytest.mark.parametrize(
    ("name", "invoke", "payload"), _JUDGES, ids=[judge[0] for judge in _JUDGES]
)
def test_judge_sends_no_anthropic_only_parameter(name, invoke, payload):
    """`system` and `max_tokens` are the two a rewrite leaves behind by habit."""
    captured = _drive(payload, invoke)

    leftovers = [field for field in ANTHROPIC_ONLY if field in captured]
    assert leftovers == [], (
        f"{name} sent {leftovers!r}, which OpenAI rejects as unrecognised body fields. "
        "The system prompt is the first entry of `messages` and the ceiling is "
        "`max_completion_tokens`."
    )
