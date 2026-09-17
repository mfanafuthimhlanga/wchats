"""The faithfulness judge's completion cap, and the rows a short one lost (#279).

OBSERVED 2026-09-15 on eval run 735fb9fa: 17 of 49 faithfulness rows stored no
verdict, each logging `run_ragas_eval.metric_failed
error_type=IncompleteOutputException 'The output is incomplete due to a
max_tokens length limit.'`. One more row went the same way on the 0a99f7ab
rejudge. A third of the measurement was lost to a number.

Nothing here touches a socket. A MockTransport stands in for the provider and
ENFORCES THE CAP THE JUDGE ITSELF ASKED FOR, reading `max_completion_tokens` off
the request body it was sent. That is what makes these tests bind on
`judge_llm.JUDGE_MAX_COMPLETION_TOKENS` rather than on a number written here:
lower the constant and the fake provider starts cutting, exactly as the real one
did.

The fixture is a response this product could actually store, no longer than
`TURN_HISTORY_MAX_ROW_CHARS`, so the bug is reproduced at a real size.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import structlog

from app.core.model_client import LedgerContext
from app.services.eval_service import JUDGE_PURPOSES, _build_instructor_llm, _score_samples
from app.services.judge_llm import JUDGE_MAX_COMPLETION_TOKENS
from app.worker.tasks.runtime.agent import TURN_HISTORY_MAX_ROW_CHARS
from app.worker.tasks.runtime.retrieval_eval import JUDGE_PURPOSE as RETRIEVAL_JUDGE_PURPOSE

TENANT = "11111111-1111-1111-1111-111111111111"

# The fake provider counts characters where the real one counts tokens. Four
# characters per token is the usual English approximation, and this test only
# needs the fake to cut in the same region the provider cut in.
_CHARS_PER_TOKEN = 4

# ---------------------------------------------------------------------------
# The fixture: one long grounded answer, decomposed and verdicted.
#
# Faithfulness makes two calls. `StatementGeneratorOutput` returns the answer as
# atomic claims, which is about as long as the answer. `NLIStatementOutput`
# repeats every claim WORD BY WORD and adds a reason and a verdict to each, so it
# is roughly twice the claims again. The second call is the one that blew the
# cap, and the sizes below are chosen so only the second one does.
# ---------------------------------------------------------------------------
_CLAIM_CHARS = 190
_REASON_CHARS = 150
_CLAIM_COUNT = 18

_FILLER = "the returns policy covers this point in full and names the window. "


def _sized(prefix: str, n: int) -> str:
    return (prefix + _FILLER * 12)[:n]


_CLAIMS = [_sized(f"Claim {i:02d}. ", _CLAIM_CHARS) for i in range(_CLAIM_COUNT)]
_REASONS = [_sized(f"Reason {i:02d}. ", _REASON_CHARS) for i in range(_CLAIM_COUNT)]
_RESPONSE = " ".join(_CLAIMS)
_CONTEXTS = ("The returns policy grants 30 days from delivery on unopened goods.",)

_STATEMENT_ARGS = {"statements": _CLAIMS}
_VERDICT_ARGS = {
    "statements": [
        {"statement": claim, "reason": reason, "verdict": 1}
        for claim, reason in zip(_CLAIMS, _REASONS, strict=True)
    ]
}


class _Sample:
    """The two attributes `_score_samples` reads off a row, plus faithfulness' three."""

    def __init__(self) -> None:
        self.user_input = "How long do I have to return something?"
        self.reference = "Thirty days from delivery."
        self.response = _RESPONSE
        self.retrieved_contexts = list(_CONTEXTS)


def _body(tool: str, arguments: str, finish_reason: str) -> dict:
    return {
        "id": "chatcmpl-judge",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-5.6-luna",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": tool, "arguments": arguments},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 400, "completion_tokens": 400},
    }


def _capped_provider(monkeypatch: pytest.MonkeyPatch, seen: list[dict]) -> None:
    """Patch httpx so every judge call is answered under the cap it asked for.

    A response whose JSON does not fit the request's own `max_completion_tokens`
    comes back with `finish_reason: "length"` and its arguments cut, which is the
    shape instructor raises `IncompleteOutputException` on
    (`instructor/v2/core/function_calls.py:45`).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        tool = body["tools"][0]["function"]["name"]
        args = _STATEMENT_ARGS if tool == "StatementGeneratorOutput" else _VERDICT_ARGS
        text = json.dumps(args)
        budget = body["max_completion_tokens"] * _CHARS_PER_TOKEN
        if len(text) > budget:
            return httpx.Response(
                200,
                json=_body(tool, text[:budget], "length"),
                headers={"content-type": "application/json"},
            )
        return httpx.Response(
            200,
            json=_body(tool, text, "tool_calls"),
            headers={"content-type": "application/json"},
        )

    transport = httpx.MockTransport(handler)

    class _Pinned(httpx.AsyncClient):
        """A real AsyncClient subclass, because the OpenAI SDK isinstance-checks it."""

        def __init__(self, **kwargs):
            super().__init__(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _Pinned)


def _recording_provider(monkeypatch: pytest.MonkeyPatch, seen: list[dict]) -> None:
    """Patch httpx to record the request and always answer whole.

    Separate from `_capped_provider` so a test about the number on the wire fails
    on its own assertion rather than on a transport that cut the reply.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        tool = body["tools"][0]["function"]["name"]
        return httpx.Response(
            200,
            json=_body(tool, json.dumps({"score": 1}), "tool_calls"),
            headers={"content-type": "application/json"},
        )

    transport = httpx.MockTransport(handler)

    class _Pinned(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _Pinned)


def _score_one(monkeypatch: pytest.MonkeyPatch, cap: int | None = None) -> tuple[dict, list[dict]]:
    """Score one long sample for faithfulness. Returns (row, structlog events).

    `cap` overrides what the builder asked for, which is how a test drives a cap
    the fix did not choose.
    """
    from ragas.metrics.collections import Faithfulness

    seen: list[dict] = []
    _capped_provider(monkeypatch, seen)
    llm = _build_instructor_llm(
        "judge_faithfulness", LedgerContext(tenant_id=TENANT, recorder=lambda call: None)
    )
    if cap is not None:
        llm.model_args["max_tokens"] = cap
    with structlog.testing.capture_logs() as logs:
        rows = asyncio.run(_score_samples([Faithfulness(llm=llm)], [_Sample()]))
    return rows[0], logs


class TestTheFixtureIsARealSize:
    def test_the_answer_is_one_this_product_would_store_whole(self):
        """A bug reproduced on an absurd input proves nothing about the run."""
        assert len(_RESPONSE) <= TURN_HISTORY_MAX_ROW_CHARS, (
            f"the fixture answer is {len(_RESPONSE)} characters, longer than the "
            f"{TURN_HISTORY_MAX_ROW_CHARS} this product stores"
        )

    def test_only_the_verdict_call_exceeds_the_cap_that_shipped(self):
        """Decomposition fit 1024. Verdicting the same claims did not (#279)."""
        shipped_budget = 1024 * _CHARS_PER_TOKEN
        assert len(json.dumps(_STATEMENT_ARGS)) < shipped_budget
        assert len(json.dumps(_VERDICT_ARGS)) > shipped_budget


class TestALongAnswerScores:
    def test_a_long_answer_stores_a_verdict_rather_than_unknown(self, monkeypatch):
        """The repro. Red at the 1024 ragas ships, green at the cap #279 set."""
        row, logs = _score_one(monkeypatch)

        failures = [e for e in logs if e.get("event") == "run_ragas_eval.metric_failed"]
        assert not failures, f"the judge still lost the row: {failures!r}"
        assert row["faithfulness"] == 1.0, (
            f"faithfulness stored {row['faithfulness']!r} for an answer of "
            f"{len(_RESPONSE)} characters"
        )

    def test_both_judge_calls_went_out_under_the_raised_cap(self, monkeypatch):
        """Both calls, because the cap rides on the client and not on one prompt."""
        seen: list[dict] = []
        _capped_provider(monkeypatch, seen)
        from ragas.metrics.collections import Faithfulness

        llm = _build_instructor_llm(
            "judge_faithfulness", LedgerContext(tenant_id=TENANT, recorder=lambda call: None)
        )
        asyncio.run(_score_samples([Faithfulness(llm=llm)], [_Sample()]))

        assert [b["tools"][0]["function"]["name"] for b in seen] == [
            "StatementGeneratorOutput",
            "NLIStatementOutput",
        ]
        assert [b["max_completion_tokens"] for b in seen] == [
            JUDGE_MAX_COMPLETION_TOKENS,
            JUDGE_MAX_COMPLETION_TOKENS,
        ]


class TestAnOutputThatStillDoesNotFitIsUnknown:
    def test_a_truncated_verdict_stores_nothing_and_says_why(self, monkeypatch):
        """The cap fits the answers this product produces. It is not a proof.

        A cap is a number and an answer can always be longer, so the behaviour
        when one does not fit is pinned here: the cell is None, the event names
        the exception, and nothing turns a cut-off output into a score.
        """
        row, logs = _score_one(monkeypatch, cap=128)

        failures = [e for e in logs if e.get("event") == "run_ragas_eval.metric_failed"]
        assert len(failures) == 1, f"expected one metric_failed, got {failures!r}"
        assert failures[0]["metric"] == "faithfulness"
        assert failures[0]["error_type"] == "IncompleteOutputException"
        assert row["faithfulness"] is None, (
            f"a truncated output scored {row['faithfulness']!r} rather than unknown"
        )


class TestEveryJudgePurposeAsksForTheSameCap:
    @pytest.mark.parametrize(
        "purpose", [*JUDGE_PURPOSES, RETRIEVAL_JUDGE_PURPOSE]
    )
    def test_the_cap_reaches_the_wire_at_or_above_4096(self, purpose, monkeypatch):
        """4096 as a literal, so lowering the constant is red here.

        Read off the request body rather than off the builder. Ragas maps
        parameters on the way past and instructor fills in its own defaults, so
        the body is the only place the claim is true or false.
        """
        from pydantic import BaseModel

        class _Verdict(BaseModel):
            score: int

        seen: list[dict] = []
        _recording_provider(monkeypatch, seen)
        llm = _build_instructor_llm(
            purpose, LedgerContext(tenant_id=TENANT, recorder=lambda call: None)
        )
        asyncio.run(llm.agenerate("score this", _Verdict))

        assert "max_tokens" not in seen[0], (
            f"{purpose} sent max_tokens={seen[0].get('max_tokens')!r}, which this "
            "provider refuses (#198)"
        )
        assert seen[0]["max_completion_tokens"] >= 4096, (
            f"{purpose} asked for max_completion_tokens="
            f"{seen[0].get('max_completion_tokens')!r}; #279 lost 17 of 49 rows at 1024"
        )
