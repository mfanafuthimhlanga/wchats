"""JudgeRecord, what one Judge decided about one dimension (ticket 14, #51, slice 2).

WHAT THE ROW USED TO SAY, AND WHAT IT DID NOT
    `eval_results` holds one row per (scenario, metric). Since #47 its `detail`
    column has carried the WHOLE score row plus the Judge identity, so each of a
    scenario's four rows repeats all four of that scenario's scores. The row's
    own `metric` was the only thing telling a reader which of the four copies
    belonged to it, and the other three were there to be read by mistake.

    What no copy carried was the decision. Whether a score cleared its gate was
    rebuilt at READ time, in `api/v1/evals.py`, out of today's `settings`. So
    raising `EVAL_FAITHFULNESS_THRESHOLD` restated every verdict already
    written down, and a run scored under the old gate read as though it had been
    scored under the new one. A verdict that changes when nobody re-ran anything
    is not a verdict.

    This record is the row. One dimension, its score, the number it was compared
    against, the answer, the Judge that produced it, and the ledger bucket the
    calls billed to.

THE VERDICT IS DERIVED, AND CONSTRUCTION REFUSES A WRONG ONE
    `verdict_for` below is the only comparison in the system, and `__post_init__`
    refuses a `binary_verdict` that disagrees with it. The check earns its keep
    on the way OUT rather than on the way in: `from_payload` reads a stored row
    whose verdict was written by an older build under an older threshold, and a
    row whose three fields no longer agree with each other is refused rather
    than believed because it is already in the database.

NULL IS NOT FALSE, TWICE OVER
    A metric with no threshold gets no verdict. `context_precision` and
    `context_recall` have no threshold anywhere in this codebase, so their rows
    say so with None rather than borrowing one of the other two.

    A metric the judge did not score gets no verdict either, AND IT STILL GETS A
    ROW. An unscored metric is visible as a row carrying no score, never as an
    absent row, because absence is indistinguishable from a scenario that was
    never sent and a reader counting rows would silently lose the denominator.

    Neither case is False. "Nobody gated this" and "this failed its gate" are
    different claims, and a deploy gate cannot tell them apart once the second
    one is written down. That is the same rule `Measurement` enforces one grain
    up, where zero observations may not claim `measured=True`.

WHY THE LEDGER REFERENCE IS A PURPOSE
    `ledger_purpose` is half of a join key, not an id. The row's judge calls are
    the `model_calls` whose `job_id` is the run and whose `purpose` is this
    value, which is per metric within the run and NOT per scenario. The ledger
    cannot go finer: `record_model_call` mints each row's uuid inside itself and
    `Recorder` returns None, so no caller holds a call id, and the row is written
    from an httpx response hook firing under ragas' own scoring loop, which sees
    a purpose, a tenant, an agent and a job and never a scenario. Tenant
    migration 0023's column comment carries the same sentence, for a reader who
    has the catalogue and not this file.

Rung: `app.domain` imports the standard library, third-party packages and its
domain siblings. This module imports the standard library and
`app.domain.judge_identity`.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.domain.judge_identity import JudgeIdentity


class InvalidJudgeRecord(ValueError):
    """A judge row that would misreport what a Judge decided, refused on construction.

    A ValueError, so callers that already catch ValueError keep catching it, the
    same choice `InvalidModelCall`, `InvalidJudgeIdentity`, `InvalidRedTeamResult`
    and `InvalidEvalResult` made.
    """


def _require_text(name: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidJudgeRecord(f"JudgeRecord needs a {name}, got {value!r}")


def _require_optional_text(name: str, value: Any) -> None:
    """None says there is no such value. An empty string says there is one and hides it."""
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise InvalidJudgeRecord(
            f"JudgeRecord needs {name} as a non-empty string or None, got {value!r}"
        )


def _as_optional_float(name: str, value: Any) -> float | None:
    """A real number or None. bool is refused first, and so is NaN.

    NaN is the one that matters. `_score_samples` already converts a NaN cell to
    None because a NaN compares False against every threshold, so a NaN score
    would reach `verdict_for` and come back a quiet failure. Refusing it here
    means the conversion cannot be dropped upstream without this going red.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidJudgeRecord(
            f"JudgeRecord needs {name} as a number or None, got {value!r}"
        )
    number = float(value)
    if math.isnan(number):
        raise InvalidJudgeRecord(
            f"JudgeRecord refuses a NaN {name}. A NaN loses every comparison, so "
            "it would read as a failed verdict rather than as an absent one"
        )
    return number


def verdict_for(score: float | None, threshold: float | None) -> bool | None:
    """Did this score clear this gate. None when there is no gate or no score.

    THE ONE COMPARISON. Both the writer and `JudgeRecord.__post_init__` call it,
    so a row's stored verdict and the rule that checks the row cannot drift apart
    into two answers.

    `>=` and not `>`: a score exactly on the threshold passes. A gate written as
    "faithfulness must be at least 0.90" is the >= reading, and it is the one
    `api/v1/evals.py` has always used.
    """
    if score is None or threshold is None:
        return None
    return score >= threshold


def scenario_verdict(verdicts: Sequence[bool | None]) -> bool | None:
    """Did this scenario pass, over the verdicts of its gated dimensions.

    THE ONE CONJUNCTION. The results route renders it per scenario and
    `eval_service.dataset_verdict_counts` counts scenarios by it, so the console
    and the deploy report cannot describe one scenario two ways.

    None beats False. A scenario carrying one NULL verdict and one False is
    undecided, not failed: "nobody decided" reported as "it failed" is what turns
    a judge outage into an apparent quality collapse and an owner-initiated
    rollback.

    An empty sequence is None rather than `all([])`, which is True. A caller with
    no gated verdict to offer has a scenario nobody gated, and reading that as a
    pass is how every legacy row would clear a gate it was never held to.

    Args:
        verdicts: one entry per gated metric, in GATED_METRIC_KEYS order. A
            metric with no row at all contributes None, the same as a row the
            judge scored nothing for, because both are the same absence.
    """
    if not verdicts or any(verdict is None for verdict in verdicts):
        return None
    return all(verdicts)


@dataclass(frozen=True)
class JudgeRecord:
    """One Judge's decision about one dimension of one scenario.

    Frozen, so a decision cannot be edited after the run that made it, and so a
    set of records is a natural grouping.

    Args:
        scenario_id:    which scenario this dimension was scored on.
        metric:         the dimension. One of `eval_service.METRIC_KEYS`, which
                        is also the `eval_results.metric` value.
        score:          the number the Judge returned, or None when it returned
                        nothing for this metric. None is a normal state and the
                        row still exists.
        threshold:      the number `score` was compared against, as it stood at
                        scoring time, or None when this metric has no gate.
        binary_verdict: whether the score cleared the gate. None exactly when
                        `verdict_for` says None, and equal to it otherwise.
        judge_identity: the Judge behind this dimension's score, or None when the
                        route could not name a complete one. A verdict whose
                        Judge is unknown is unknown, never filed under the Judge
                        that happened to run last.
        ledger_purpose: the routing purpose this dimension's judge calls billed
                        under, which with the run id locates them in
                        `model_calls`. None when no bucket was recorded.

    Raises:
        InvalidJudgeRecord: scenario_id or metric is blank, score or threshold is
            not a real number, `judge_identity` is not a JudgeIdentity, or
            `binary_verdict` disagrees with `verdict_for(score, threshold)`.
    """

    scenario_id: str
    metric: str
    score: float | None
    threshold: float | None
    binary_verdict: bool | None
    judge_identity: JudgeIdentity | None = None
    ledger_purpose: str | None = None

    def __post_init__(self) -> None:
        _require_text("scenario_id", self.scenario_id)
        _require_text("metric", self.metric)
        # object.__setattr__ is how a frozen dataclass normalises a field.
        object.__setattr__(self, "score", _as_optional_float("score", self.score))
        object.__setattr__(
            self, "threshold", _as_optional_float("threshold", self.threshold)
        )
        _require_optional_text("ledger_purpose", self.ledger_purpose)

        if self.judge_identity is not None and not isinstance(
            self.judge_identity, JudgeIdentity
        ):
            raise InvalidJudgeRecord(
                "JudgeRecord needs judge_identity as a JudgeIdentity or None, got "
                f"{type(self.judge_identity).__name__}"
            )

        expected = verdict_for(self.score, self.threshold)
        if self.binary_verdict is not expected:
            raise InvalidJudgeRecord(
                f"JudgeRecord for {self.metric} carries verdict "
                f"{self.binary_verdict!r} over score {self.score!r} and threshold "
                f"{self.threshold!r}, which decide {expected!r}. A row whose "
                "verdict disagrees with its own numbers cannot be read as either"
            )

    @classmethod
    def scored(
        cls,
        scenario_id: str,
        metric: str,
        score: float | None,
        threshold: float | None,
        judge_identity: JudgeIdentity | None = None,
        ledger_purpose: str | None = None,
    ) -> JudgeRecord:
        """The record with its verdict derived rather than supplied.

        Every writer builds records this way. The bare constructor exists for the
        reader, where a stored verdict arrives already decided and has to be
        checked against the numbers stored beside it.

        The two numbers are coerced HERE as well as in `__post_init__`, through
        the same function, because `verdict_for` runs before the constructor
        does. Handing it a string score raised a TypeError out of the `>=` rather
        than this module's own refusal, which put a caller's bad input behind an
        error message about comparison operators.
        """
        score = _as_optional_float("score", score)
        threshold = _as_optional_float("threshold", threshold)
        return cls(
            scenario_id=scenario_id,
            metric=metric,
            score=score,
            threshold=threshold,
            binary_verdict=verdict_for(score, threshold),
            judge_identity=judge_identity,
            ledger_purpose=ledger_purpose,
        )

    @property
    def payload(self) -> dict:
        """The whole decision as JSON.

        {"scenario_id", "metric", "score", "threshold", "binary_verdict",
         "judge_identity", "ledger_purpose"}.

        `write_eval_results` spreads these across the columns tenant migration
        0023 added rather than storing this dict whole, so a reader groups on a
        verdict and a Judge in SQL. This shape is what a round trip through
        `from_payload` preserves, and it is what a test compares.
        """
        return {
            "scenario_id": self.scenario_id,
            "metric": self.metric,
            "score": self.score,
            "threshold": self.threshold,
            "binary_verdict": self.binary_verdict,
            "judge_identity": (
                dataclasses.asdict(self.judge_identity) if self.judge_identity else None
            ),
            "ledger_purpose": self.ledger_purpose,
        }

    @classmethod
    def from_payload(cls, payload: Mapping) -> JudgeRecord:
        """Rebuild one decision from its stored form, re-checking it on the way out.

        The verdict is taken from the payload rather than re-derived, which is the
        whole point: a stored row written under a threshold that has since moved,
        or by a build whose comparison was wrong, disagrees with its own numbers
        and is refused here instead of being trusted because it is already
        written down.

        Raises:
            InvalidJudgeRecord: the stored shape is not a mapping, or it violates
                any construction rule above.
        """
        if not isinstance(payload, Mapping):
            raise InvalidJudgeRecord(
                f"JudgeRecord needs a mapping, got {type(payload).__name__}"
            )
        identity = payload.get("judge_identity")
        if identity is not None and not isinstance(identity, Mapping):
            raise InvalidJudgeRecord(
                f"JudgeRecord needs judge_identity as a mapping or None, got "
                f"{type(identity).__name__}"
            )
        return cls(
            scenario_id=payload.get("scenario_id"),
            metric=payload.get("metric"),
            score=payload.get("score"),
            threshold=payload.get("threshold"),
            binary_verdict=payload.get("binary_verdict"),
            judge_identity=JudgeIdentity(**identity) if identity else None,
            ledger_purpose=payload.get("ledger_purpose"),
        )
