"""FaithfulnessWithClaims, ragas' Faithfulness keeping the claims it decided (#290).

WHAT RAGAS THROWS AWAY
    `ragas.metrics.collections.Faithfulness.ascore` breaks the answer into
    atomic statements, asks the judge for a verdict on each against the joined
    contexts, and returns the share it found supported as `MetricResult.value`.
    The statements and the verdicts are locals of that method and are gone when
    it returns. A planted lie in an answer of eight claims scores 0.875 and
    clears the 0.80 gate, and the row cannot say which claim it was.

WHY A SUBCLASS AND NOT A WRAPPER
    The three steps are the parent's own methods (`_create_statements`,
    `_create_verdicts`, `_compute_score`) and this class runs the same three in
    the same order, so the score is the parent's score, pinned by a test that
    runs both over one canned judge. The verdicts ride on the `traces` slot
    `MetricResult` already has, under the `output` key it allows, so no second
    return type and no instance state: one metric instance scores every sample
    of a run concurrently, and a field on the instance would race.

Rung: `app.services` imports `app.domain`, `app.core` and third-party packages.
This module imports ragas and nothing from `app`.
"""

from __future__ import annotations

from typing import Any

from ragas.metrics.collections import Faithfulness
from ragas.metrics.result import MetricResult

#: Where the claims sit on a `MetricResult`: `result.traces["output"][CLAIMS_KEY]`.
CLAIMS_KEY = "claims"


class FaithfulnessWithClaims(Faithfulness):
    """Faithfulness whose result carries each statement and its verdict.

    Same inputs, same score, same two judge calls as the parent. The result's
    `traces["output"]["claims"]` is a list of
    `{"statement": str, "supported": bool, "reason": str}`, one per statement
    the judge decided, in the order it decided them.
    """

    async def ascore(  # type: ignore[override]  # the parent narrows **kwargs the same way
        self, user_input: str, response: str, retrieved_contexts: list[str]
    ) -> MetricResult:
        for name, value in (
            ("response", response),
            ("user_input", user_input),
            ("retrieved_contexts", retrieved_contexts),
        ):
            if not value:
                raise ValueError(f"{name} is missing. Please add {name} to the test sample.")

        statements = await self._create_statements(user_input, response)
        if not statements:
            return MetricResult(value=float("nan"))

        verdicts = await self._create_verdicts(statements, "\n".join(retrieved_contexts))
        claims = [
            {
                "statement": item.statement,
                "supported": bool(item.verdict),
                "reason": item.reason,
            }
            for item in verdicts.statements
        ]
        return MetricResult(
            value=float(self._compute_score(verdicts)),
            traces={"output": {CLAIMS_KEY: claims}},
        )


def claims_of(result: Any) -> list[dict] | None:
    """The claims a result carries, or None for a result that carries none.

    Reads with `getattr` and `or {}` because the scoring loop hands every
    metric's result through here: the other three metrics return a
    `MetricResult` whose `traces` is None, and the test doubles return a result
    with no `traces` attribute at all.
    """
    traces = getattr(result, "traces", None) or {}
    output = traces.get("output") or {}
    claims = output.get(CLAIMS_KEY)
    return list(claims) if isinstance(claims, list) and claims else None
