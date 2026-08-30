"""EvalResult, what one eval run measured (ticket 14, issue #51, slice 1).

WHY THE RUN HAD NO RECORD TYPE
    `run_eval_suite` hand-assembled a return dict at the end of its body and wrote
    nothing equivalent to the row. Every reader then rebuilt the numbers for
    itself: `api/v1/evals.py` runs its own `COUNT`/`AVG` over `eval_results`,
    `deployment_service._fetch_eval_summary_sync` runs a second one, and the task's
    own dict came from a third derivation in Python. Three arithmetics over one
    run, free to disagree, and the one that disagreed was whichever the deploy
    gate happened to read.

    This record is the one derivation. The task builds it once from the summaries
    it already holds, writes it to `eval_runs.result`, and derives its own return
    dict from `payload`. Slices 3 and 4 point the routes and the deployment
    service at `read_eval_result`, and the recomputation goes.

EVERY NUMBER TRAVELS WITH ITS DENOMINATOR
    `.dev/reference/260818-llm-eval-fundamentals.md` section 11, "Reporting a
    number honestly", says two things this record is shaped by. Never quote a
    success rate as a point estimate, so a `Measurement` carries the observation
    count that produced it and a reader can see that a 0.91 came off four rows.
    And never quote a pooled rate, so the metrics are per dataset and there is no
    run-level mean anywhere in this file to misread. Averaging the fixed golden
    set with the rotating exploratory draw would destroy the only property the
    split exists to create.

ZERO OBSERVATIONS IS UNKNOWN, NEVER A PASS
    `Measurement` refuses to be built claiming `measured=True` over nothing, and
    refuses to carry a value it did not measure. That is the criterion 4 rule of
    the ticket, enforced at construction rather than at whichever reader notices
    first, the same choice `ModelCall` and `RedTeamResult` made. A metric nobody
    reported is ABSENT from `payload` rather than present with a default: a
    default is a number, and a number is indistinguishable from a measurement one
    reader later.

THE PER-SCENARIO VERDICTS ARE COUNTED HERE TOO, AND THEY SUM TO `scored`
    A deploy gate needs to know how many scenarios failed and how many nobody
    decided, and it used to count both at read time over `eval_results`. That
    read answers a different question from the one the run answered, and it says
    nought failing over a run whose result rows were deleted. `DatasetOutcome`
    carries the three counts the run itself reached, and refuses a set that does
    not add up to the scenarios it says it scored.

WHY THE COST IS ON THE RUN AND WHY IT CAN BE UNKNOWN
    A run's judges and its agent turns bill to `model_calls WHERE job_id = run_id`.
    Reading them back is how a run says what its measurement cost, and `measured`
    is False when the ledger held no rows for it. A cost of zero would be a claim
    that the run was free, which is the claim `ModelCall` exists to stop anyone
    making. The dollars and the rand are separately nullable underneath that,
    because the price book can refuse a model whose tokens are perfectly well
    known, which is `usage_rollup`'s rule one table over.

WHY THE PROXY VERSION IS STAMPED
    Issue #84. The text this eval scores as "retrieved context" is a proxy for
    what the customer's retrieval returned, and the proxy's shape has changed
    under scores that then read as quality changes. CONTEXT_PROXY_VERSION below
    says which shape produced these numbers, so a reader comparing two runs can
    see that the instrument moved rather than the agent.

ADR 0007 is ticket 17's and is not written yet. This record cites it as pending.

Rung: `app.domain` imports the standard library, third-party packages and its
domain siblings. This module imports the standard library, `app.domain.judge_identity`,
`app.domain.model_call` and `app.domain.pricing`.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from app.domain.judge_identity import JudgeIdentity
from app.domain.model_call import ModelCall
from app.domain.pricing import UnknownFxRate, UnknownPrice, cost_usd, cost_zar

#: The two datasets a run reports, and the only two names `datasets` accepts.
#: `app.services.eval_service` spells the same two and its `EVAL_DATASETS` is
#: this tuple's twin, repeated there rather than imported; that pin is a test,
#: not an import. `app.services.scenario_service` DOES import these two, because
#: a producer holding its own copy of the vocabulary is how #27's column stayed
#: NULL on every generated row while three readers folded NULL to exploratory.
DATASET_GOLDEN = "golden"
DATASET_EXPLORATORY = "exploratory"
EVAL_DATASETS: tuple[str, ...] = (DATASET_GOLDEN, DATASET_EXPLORATORY)

#: The four dimensions a run scores, in the order the console reads them.
#: `eval_service.METRIC_KEYS` is the same tuple, for the same reason as above.
METRIC_KEYS: tuple[str, ...] = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
)

#: WHICH SHAPE OF "RETRIEVED CONTEXT" THESE SCORES WERE COMPUTED OVER (#84).
#:
#: Faithfulness, ContextPrecision and ContextRecall all score an answer against
#: the contexts a run says the agent retrieved. `run_eval_suite` builds those by
#: splitting each `retrieve` tool result back into chunks, which is the shape
#: `summarise_agent_invocation` reports as `retrieved_context_source`. Change how
#: that text is assembled and every score moves without the agent changing at
#: all, and #84 records exactly that happening once already, unstamped, in the
#: live-traffic Judge one task over.
#:
#: So the version travels on the run. Two runs carrying different values here are
#: not comparable, and a reader can see it instead of reading a format change as
#: a quality change. BUMP IT whenever what reaches the judge as a context changes
#: shape, not when the retrieval gets better at its job.
CONTEXT_PROXY_VERSION = "agent_retrieve_chunks/1"

#: Which construction rules produced this record. A stored payload that was
#: written under a different set of rules is readable but is not a like-for-like
#: comparison, and this is the field that says so. One, because this is the
#: first.
RULE_VERSION = 1

_COUNT_FIELDS_INVOCATION = (
    "valid",
    "attempted",
    "responded",
    "scorable",
    "failed",
    "empty",
    "responses_deflected",
    "scored_responses_deflected",
)


class InvalidEvalResult(ValueError):
    """A run record that would misreport what a run measured, refused on construction.

    A ValueError, so callers that already catch ValueError keep catching it, the
    same choice `InvalidModelCall`, `InvalidJudgeIdentity` and
    `InvalidRedTeamResult` made.
    """


class InvocationStatus(StrEnum):
    """Whether the invocation phase of a run constitutes a measurement.

    The two values `eval_service.summarise_agent_invocation` produces, spelled the
    way it spells them, because the string in the payload has to be the string in
    the run's config or two readers of one run disagree.

    MEASURED: turns were attempted, the response rate cleared its floor, and
              enough rows reached the scorer.
    UNKNOWN:  any of those failed, including the zero-attempt case. A rate over an
              empty denominator is unknown, never a pass.

    `eval_service.AGENT_INVOCATION_NOT_STARTED` is deliberately NOT here. It
    describes a run whose invocation phase never reported, and such a run never
    reaches this record: the absence is expressed by `eval_runs.result` being
    NULL, which `read_eval_result` returns as None. A third value would give a
    written record two ways to say the same nothing.
    """

    MEASURED = "measured"
    UNKNOWN = "unknown"


def _as_status(value: Any) -> InvocationStatus:
    """Coerce a stored string to the enum, refusing a status nobody defined."""
    if isinstance(value, InvocationStatus):
        return value
    try:
        return InvocationStatus(value)
    except ValueError:
        raise InvalidEvalResult(
            "EvalResult needs an invocation status from "
            f"{[s.value for s in InvocationStatus]}, got {value!r}"
        ) from None


def _require_count(name: str, value: Any) -> None:
    """A count is a non-negative int. bool is checked first: True counts as one."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidEvalResult(
            f"EvalResult needs {name} as an int, got {type(value).__name__}"
        )
    if value < 0:
        raise InvalidEvalResult(f"EvalResult needs {name} at zero or above, got {value}")


def _require_text(name: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidEvalResult(f"EvalResult needs a {name}, got {value!r}")


def _require_optional_text(name: str, value: Any) -> None:
    """None says there is no such value. An empty string says there is one and hides it."""
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise InvalidEvalResult(
            f"EvalResult needs {name} as a non-empty string or None, got {value!r}"
        )


def _at_most(smaller: str, larger: str, values: Mapping[str, int]) -> None:
    if values[smaller] > values[larger]:
        raise InvalidEvalResult(
            f"EvalResult reports {values[smaller]} {smaller} over {values[larger]} "
            f"{larger}. {smaller} is a subset of {larger}."
        )


@dataclass(frozen=True)
class Measurement:
    """One metric on one dataset: the number, what it was computed over, and whether it exists.

    Args:
        value:        the mean of the observations, or None when there is no
                      number. An int is accepted and held as a float.
        observations: how many real scores went into it. Zero is a normal
                      state and it means the metric is unknown.
        measured:     whether `value` is a reading. False is never "it scored
                      zero"; it is "nobody scored it".

    Raises:
        InvalidEvalResult: observations is negative or not an int, zero
            observations claim to be measured or carry a value, a measured
            metric carries no float, or an unmeasured one carries a value.
    """

    value: float | None
    observations: int
    measured: bool

    def __post_init__(self) -> None:
        _require_count("observations", self.observations)
        if not isinstance(self.measured, bool):
            raise InvalidEvalResult(
                f"Measurement needs measured as a bool, got {type(self.measured).__name__}"
            )
        if self.observations == 0 and self.measured:
            # The whole rule of the ticket's criterion 4, at the one place it can
            # be enforced. A mean over nothing is not a low score, and a deploy
            # gate cannot tell the two apart once the number is written down.
            raise InvalidEvalResult(
                "Measurement over zero observations cannot be measured=True. "
                "A metric nobody scored is unknown, never a pass."
            )
        if self.measured:
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
                raise InvalidEvalResult(
                    "Measurement needs a float value when measured=True, got "
                    f"{self.value!r}"
                )
            # object.__setattr__ is how a frozen dataclass normalises a field.
            object.__setattr__(self, "value", float(self.value))
        elif self.value is not None:
            raise InvalidEvalResult(
                f"Measurement is not measured and still carries {self.value!r}. "
                "A value nobody measured reads as one that somebody did."
            )

    @property
    def payload(self) -> dict:
        """{"value", "measured", "observations"} — the shape every reader already parses.

        Key for key what `summarise_run_validity` has emitted since the
        measurement-layer work, so a route reading a stored record and a route
        reading the live summary parse one thing.
        """
        return {
            "value": self.value,
            "measured": self.measured,
            "observations": self.observations,
        }

    @classmethod
    def from_payload(cls, payload: Mapping) -> Measurement:
        """Rebuild one measurement from its stored form.

        Raises:
            InvalidEvalResult: the stored shape is not a mapping, or it violates
                the construction rules above. A stored row that would be refused
                on the way in is refused on the way out too, rather than being
                trusted because it is already written down.
        """
        if not isinstance(payload, Mapping):
            raise InvalidEvalResult(
                f"Measurement needs a mapping, got {type(payload).__name__}"
            )
        return cls(
            value=payload.get("value"),
            observations=payload.get("observations", 0),
            measured=bool(payload.get("measured", False)),
        )


@dataclass(frozen=True)
class Invocation:
    """What the run's agent turns did, and whether they add up to a measurement.

    The counters are `summarise_agent_invocation`'s own, under its own names.
    Renaming any of them here would put a second vocabulary on one observation.

    Args:
        status:                     MEASURED or UNKNOWN, the summariser's verdict.
        valid:                      rows carrying a label, i.e. rows that could
                                    have been invoked. THE DENOMINATOR.
        attempted:                  rows the per-run ceiling actually let run.
        responded:                  turns that came back with text.
        scorable:                   turns that reached the scorer. Smaller than
                                    `responded` by the rows with no retrieved
                                    context, and it is what the metrics were
                                    computed over.
        failed:                     turns that raised.
        empty:                      turns that returned with no text and no
                                    exception, the max_turns / max_budget
                                    signature. Exactly attempted - responded -
                                    failed, and refused when it is not.
        responses_deflected:        answers the output firewall substituted.
        scored_responses_deflected: how many of those reached the scorer, which
                                    is the number that explains a Faithfulness
                                    that fell.
        deflection_detectors:       detector name to count. Carried whole because
                                    "three email deflections" and "three card
                                    deflections" are different findings.

    Raises:
        InvalidEvalResult: an unknown status, a count that is negative or not an
            int, a subset larger than its superset, or an `empty` that does not
            reconcile.
    """

    # The init input, not what the record holds. __post_init__ coerces a string
    # to the enum.
    status: InvocationStatus | str
    valid: int
    attempted: int
    responded: int
    scorable: int
    failed: int
    empty: int
    responses_deflected: int = 0
    scored_responses_deflected: int = 0
    deflection_detectors: Mapping[str, int] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in _COUNT_FIELDS_INVOCATION:
            _require_count(name, getattr(self, name))
        counts = {name: getattr(self, name) for name in _COUNT_FIELDS_INVOCATION}
        _at_most("attempted", "valid", counts)
        _at_most("responded", "attempted", counts)
        _at_most("scorable", "responded", counts)
        _at_most("failed", "attempted", counts)
        _at_most("responses_deflected", "attempted", counts)
        _at_most("scored_responses_deflected", "responses_deflected", counts)
        if self.empty != self.attempted - self.responded - self.failed:
            # A turn either answered, raised, or came back silent. Three buckets
            # over one denominator, and a record where they do not add up is
            # counting some turn twice or losing one.
            raise InvalidEvalResult(
                f"Invocation reports {self.attempted} attempted, {self.responded} "
                f"responded, {self.failed} failed and {self.empty} empty. The "
                "three outcomes partition the attempted turns; they must add up."
            )
        detectors = self.deflection_detectors
        if not isinstance(detectors, Mapping):
            raise InvalidEvalResult(
                "Invocation needs deflection_detectors as a mapping, got "
                f"{type(detectors).__name__}"
            )
        for name, count in detectors.items():
            _require_count(f"deflection_detectors[{name!r}]", count)
        # object.__setattr__ is how a frozen dataclass normalises a field.
        object.__setattr__(self, "status", _as_status(self.status))
        object.__setattr__(self, "deflection_detectors", dict(detectors))

    @property
    def payload(self) -> dict:
        """The whole observation as JSON.

        Returns:
            {"status", "valid", "attempted", "responded", "scorable", "failed",
             "empty", "responses_deflected", "scored_responses_deflected",
             "deflection_detectors"}.
        """
        return {
            "status": InvocationStatus(self.status).value,
            **{name: getattr(self, name) for name in _COUNT_FIELDS_INVOCATION},
            "deflection_detectors": dict(self.deflection_detectors),
        }

    @classmethod
    def from_payload(cls, payload: Mapping) -> Invocation:
        """Rebuild the observation from its stored form."""
        if not isinstance(payload, Mapping):
            raise InvalidEvalResult(
                f"Invocation needs a mapping, got {type(payload).__name__}"
            )
        return cls(
            status=payload.get("status"),
            **{name: payload.get(name, 0) for name in _COUNT_FIELDS_INVOCATION},
            deflection_detectors=payload.get("deflection_detectors") or {},
        )


@dataclass(frozen=True)
class ScenarioFailure:
    """One scenario whose agent turn raised, named by its class and a fixed phrase.

    WHY THE MESSAGE IS NOT THE EXCEPTION'S OWN TEXT (#25, #96).
        The run logged `error=str(exc)` and stored `type(exc).__name__`, and the
        90s per-turn bound raises `TimeoutError`, whose `str()` is the empty
        string. Eval run 29754ceb therefore recorded two failures that said
        `error= error_type=TimeoutError` and nothing about what ran out of what.
        The type alone cannot carry the budget, and the exception cannot either.

        So the writer composes the message instead of copying one. A timeout says
        the budget it exceeded; every other class says its own name and no more.
        `eval_runs.result` is jsonb the owner reads back, and #96 is the class of
        defect where a raw provider string reaches a row like that one. A phrase
        THIS BUILD chose cannot carry a customer's data, a connection string or a
        stack frame into an owner-visible column.

    Args:
        scenario_id: the `eval_scenarios` row the turn was for. The empty string
                     is the invoker's own reading of a row that carried no id,
                     carried through rather than replaced with an invented one.
        error_type:  the exception's class name, the same value that counts one
                     into `Invocation.failed` and into the run's error histogram.
        message:     what this build says about the failure. Never `str(exc)`.

    Raises:
        InvalidEvalResult: a scenario_id that is not a string, or an empty
            error_type or message.
    """

    scenario_id: str
    error_type: str
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_id, str):
            raise InvalidEvalResult(
                "ScenarioFailure needs scenario_id as a string, got "
                f"{type(self.scenario_id).__name__}"
            )
        _require_text("error_type", self.error_type)
        _require_text("message", self.message)

    @property
    def payload(self) -> dict:
        """{"scenario_id", "error_type", "message"}."""
        return {
            "scenario_id": self.scenario_id,
            "error_type": self.error_type,
            "message": self.message,
        }

    @classmethod
    def from_payload(cls, payload: Mapping) -> ScenarioFailure:
        """Rebuild one failure from its stored form."""
        if not isinstance(payload, Mapping):
            raise InvalidEvalResult(
                f"ScenarioFailure needs a mapping, got {type(payload).__name__}"
            )
        return cls(
            scenario_id=payload.get("scenario_id", ""),
            error_type=payload.get("error_type"),
            message=payload.get("message"),
        )


#: The three ways a scored scenario can end, and every scored scenario ends in
#: exactly one of them. `__post_init__` holds their sum to `scored`.
_VERDICT_COUNTS = ("scenarios_passed", "scenarios_failed", "scenarios_unmeasured")


@dataclass(frozen=True)
class DatasetOutcome:
    """One dataset's counts, its per-scenario verdicts and whichever metrics it reported.

    (attempted, valid, scored) are three different claims and collapsing any two
    is how a run comes to report a rate it never measured. They are stored rather
    than derived from `metrics` because none of the three is recoverable from a
    set of means: a dataset can attempt forty rows, hold labels on twelve, and
    have Ragas return a number for five.

    THE VERDICT COUNTS ARE STORED FOR THE SAME REASON, AND THEY SUM TO `scored`.
    The deploy gate used to count them at read time with a `COUNT(*) FILTER` over
    `eval_results`, which answers a different question from the one the run
    answered: delete every result row and that query says nought failing and
    nought undecided over a run the record still says scored thirty, and a gate
    reading "nothing failed" ships it. The run counts its own verdicts once, from
    the judge records it built, and the sum check below is what stops the three
    from describing a different set of scenarios from the one `scored` counts.

    Args:
        attempted:            rows the selector returned for this dataset.
        valid:                rows carrying a label. THE DENOMINATOR.
        scored:               rows for which at least one metric came back a real
                              number.
        metrics:              metric name to Measurement, over a subset of
                              METRIC_KEYS. A metric absent from this mapping is
                              absent from `payload` — it was not reported, which
                              is a different claim from reported and unmeasured,
                              and a default would erase the difference.
        scenarios_passed:     scored scenarios whose every gated verdict is True.
        scenarios_failed:     scored scenarios carrying a False gated verdict and
                              no NULL one.
        scenarios_unmeasured: scored scenarios missing a gated verdict. Unmeasured
                              beats failed, so a scenario with one NULL and one
                              False is counted here (`judge_record.scenario_verdict`).

    Raises:
        InvalidEvalResult: a count is negative or not an int, scored exceeds
            valid, valid exceeds attempted, the three verdict counts do not sum
            to scored, a metric name is not one of METRIC_KEYS, or a value is not
            a Measurement.
    """

    attempted: int
    valid: int
    scored: int
    # The init input, not what the record holds. __post_init__ copies it.
    metrics: Mapping[str, Measurement] = dataclasses.field(default_factory=dict)
    scenarios_passed: int = 0
    scenarios_failed: int = 0
    scenarios_unmeasured: int = 0

    def __post_init__(self) -> None:
        counts = {
            name: getattr(self, name)
            for name in ("attempted", "valid", "scored", *_VERDICT_COUNTS)
        }
        for name, value in counts.items():
            _require_count(name, value)
        _at_most("valid", "attempted", counts)
        _at_most("scored", "valid", counts)
        decided = sum(counts[name] for name in _VERDICT_COUNTS)
        if decided != self.scored:
            raise InvalidEvalResult(
                f"DatasetOutcome puts {decided} scenario(s) into a verdict over "
                f"{self.scored} scored. Every scored scenario passed, failed or "
                "went undecided, so a pair that does not add up is two walks "
                "over one run disagreeing about which scenarios they counted"
            )
        if not isinstance(self.metrics, Mapping):
            raise InvalidEvalResult(
                f"DatasetOutcome needs metrics as a mapping, got {type(self.metrics).__name__}"
            )
        unknown = sorted(set(self.metrics) - set(METRIC_KEYS))
        if unknown:
            # A metric name nobody scores is a key no reader will look for, so it
            # would be written and never read. The four are the run's whole
            # vocabulary.
            raise InvalidEvalResult(
                "DatasetOutcome takes the metrics a run scores "
                f"({', '.join(METRIC_KEYS)}), got " + ", ".join(unknown)
            )
        wrong = sorted(
            f"{name}={type(value).__name__}"
            for name, value in self.metrics.items()
            if not isinstance(value, Measurement)
        )
        if wrong:
            raise InvalidEvalResult(
                "DatasetOutcome needs every metric to be a Measurement, got "
                + ", ".join(wrong)
            )
        # object.__setattr__ is how a frozen dataclass normalises a field.
        object.__setattr__(self, "metrics", dict(self.metrics))

    @property
    def payload(self) -> dict:
        """The three counts, the three verdict counts and the reported metrics.

        {"attempted", "valid", "scored", "scenarios_passed", "scenarios_failed",
         "scenarios_unmeasured", "metrics"}.

        An unreported metric has no key under `metrics`. A reader that finds none
        learns the run did not report it, rather than reading a zero somebody
        wrote for it.
        """
        return {
            "attempted": self.attempted,
            "valid": self.valid,
            "scored": self.scored,
            **{name: getattr(self, name) for name in _VERDICT_COUNTS},
            "metrics": {
                metric: self.metrics[metric].payload
                for metric in METRIC_KEYS
                if metric in self.metrics
            },
        }

    @classmethod
    def from_payload(cls, payload: Mapping) -> DatasetOutcome:
        """Rebuild one dataset's outcome from its stored form.

        A payload with no verdict counts reads as three zeros, so a dataset that
        scored anything is refused by the sum check. That is a record written
        before the run counted its own verdicts, and a reader that filled the
        three in from the row would be doing the derivation the counts exist to
        replace.
        """
        if not isinstance(payload, Mapping):
            raise InvalidEvalResult(
                f"DatasetOutcome needs a mapping, got {type(payload).__name__}"
            )
        metrics = payload.get("metrics") or {}
        if not isinstance(metrics, Mapping):
            raise InvalidEvalResult(
                f"DatasetOutcome needs metrics as a mapping, got {type(metrics).__name__}"
            )
        return cls(
            attempted=payload.get("attempted", 0),
            valid=payload.get("valid", 0),
            scored=payload.get("scored", 0),
            metrics={
                metric: Measurement.from_payload(value)
                for metric, value in metrics.items()
            },
            **{name: payload.get(name, 0) for name in _VERDICT_COUNTS},
        )


@dataclass(frozen=True)
class Cost:
    """What one run's model calls spent, from its own ledger rows.

    Args:
        input_tokens:  fresh input tokens across the run's `model_calls` rows.
        output_tokens: tokens the models produced.
        usd:           the run priced against the versioned book, or None when
                       the book refuses one of its models. The tokens stay known
                       when the money does not, which is `usage_rollup`'s rule.
        zar:           the same figure converted, or None when no fx rate covers
                       one of the calls. The dollars and the rand fail separately.
        measured:      whether the ledger held rows for this run at all. False
                       means the cost is UNKNOWN. Zero would be a claim that the
                       run was free.

    Raises:
        InvalidEvalResult: a token count is negative or not an int, money is
            negative or not a number, or an unmeasured cost carries tokens or
            money.
    """

    input_tokens: int
    output_tokens: int
    usd: float | None
    zar: float | None
    measured: bool

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens"):
            _require_count(name, getattr(self, name))
        for name in ("usd", "zar"):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise InvalidEvalResult(
                    f"Cost needs {name} as a number or None, got {value!r}"
                )
            if value < 0:
                raise InvalidEvalResult(f"Cost needs {name} at zero or above, got {value}")
            # object.__setattr__ is how a frozen dataclass normalises a field.
            object.__setattr__(self, name, float(value))
        if not isinstance(self.measured, bool):
            raise InvalidEvalResult(
                f"Cost needs measured as a bool, got {type(self.measured).__name__}"
            )
        if not self.measured and (self.input_tokens or self.output_tokens
                                  or self.usd is not None or self.zar is not None):
            # No ledger rows and a figure anyway is a figure with no source. The
            # run either found its calls or it did not.
            raise InvalidEvalResult(
                "Cost is not measured and still carries tokens or money. An "
                "unmeasured cost is unknown, and unknown carries nothing."
            )

    @property
    def payload(self) -> dict:
        """{"input_tokens", "output_tokens", "usd", "zar", "measured"}."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "usd": self.usd,
            "zar": self.zar,
            "measured": self.measured,
        }

    @classmethod
    def from_payload(cls, payload: Mapping) -> Cost:
        """Rebuild the cost from its stored form."""
        if not isinstance(payload, Mapping):
            raise InvalidEvalResult(f"Cost needs a mapping, got {type(payload).__name__}")
        return cls(
            input_tokens=payload.get("input_tokens", 0),
            output_tokens=payload.get("output_tokens", 0),
            usd=payload.get("usd"),
            zar=payload.get("zar"),
            measured=bool(payload.get("measured", False)),
        )


#: The one unmeasured cost, so a run with no ledger rows names the same object
#: every time instead of five call sites each spelling five zeros.
COST_UNKNOWN = Cost(input_tokens=0, output_tokens=0, usd=None, zar=None, measured=False)


def cost_of_run(calls: Sequence[ModelCall]) -> Cost:
    """What one run's `model_calls` rows spent, priced against the versioned book.

    No rows is COST_UNKNOWN, not zero. The ledger hook fails open, so a run whose
    every call went unrecorded costs exactly as much as one that recorded them
    all, and only the log can tell the two apart. `_turn_cost_usd` reports None
    rather than zero for the same reason one grain down.

    THE RUN LOSES ITS MONEY ENTIRELY when the book refuses one of its models,
    rather than reporting the sum of the calls that did price. A partial sum
    understates the run while reading as a whole figure, and no field here would
    say how much was missing. `usage_rollup` applies the same rule per purpose per
    day. The rand fails separately from the dollars, because a call whose CAT date
    predates the fx table still has a known price in dollars.

    Decimal in, float out: `eval_runs.result` is jsonb, and a Decimal has no
    representation there. The exact arithmetic happens first and the rounding
    happens once, at the boundary.

    Args:
        calls: the run's ledger rows, i.e. `model_calls WHERE job_id = run_id`.
    """
    if not calls:
        return COST_UNKNOWN
    usd = Decimal(0)
    zar = Decimal(0)
    priced = True
    rated = True
    for call in calls:
        try:
            usd += cost_usd(call)[0]
        except UnknownPrice:
            priced = False
        try:
            zar += cost_zar(call)[0]
        except (UnknownPrice, UnknownFxRate):
            rated = False
    return Cost(
        input_tokens=sum(call.input_tokens for call in calls),
        output_tokens=sum(call.output_tokens for call in calls),
        usd=float(usd) if priced else None,
        zar=float(zar) if priced and rated else None,
        measured=True,
    )


#: The three run-level counts `payload` writes beside the datasets they came from.
_RUN_TOTALS = ("attempted", "valid", "scored")


def _require_totals_agree(payload: Mapping, record: EvalResult) -> None:
    """The stored run-level totals say what the stored datasets add up to.

    `payload` writes attempted, valid and scored at the top level AND inside each
    dataset. `from_payload` re-sums the datasets, so a payload claiming
    `attempted: 999` over two datasets attempting thirty read back as thirty and
    the row held two answers with nothing choosing between them. Writing a number
    down and then ignoring it is how a reader comes to trust the one that is
    wrong.

    A payload missing a total is not refused. The datasets are the source and a
    total is a convenience for a reader that has them; absence is silence, while
    a different number is a contradiction.

    Raises:
        InvalidEvalResult: a stored total disagrees with the datasets under it.
    """
    for name in _RUN_TOTALS:
        stored = payload.get(name)
        if stored is not None and stored != getattr(record, name):
            raise InvalidEvalResult(
                f"EvalResult stores {name}={stored!r} over datasets that add up "
                f"to {getattr(record, name)}. One run cannot have two answers to "
                "one count, and the datasets are where the numbers were measured"
            )


def _require_failures(failures: Sequence[ScenarioFailure], failed: int) -> None:
    """Every entry is a ScenarioFailure, and there are no more of them than `failed`.

    A record holding more failures than failed turns is describing turns that
    did not happen. The two come off one walk in `_failure_report`, and `failed`
    is the number every rate on this record is built from, so the pair
    disagreeing means one of them is about a different set of records.

    Raises:
        InvalidEvalResult: an entry of the wrong type, or more entries than the
            invocation counted failures.
    """
    wrong = sorted(
        type(failure).__name__
        for failure in failures
        if not isinstance(failure, ScenarioFailure)
    )
    if wrong:
        raise InvalidEvalResult(
            "EvalResult needs every failure to be a ScenarioFailure, got "
            + ", ".join(wrong)
        )
    if len(failures) > failed:
        raise InvalidEvalResult(
            f"EvalResult reports {len(failures)} failure(s) over {failed} failed "
            "turn(s). A failure names a turn that raised, and every turn that "
            "raised is counted."
        )


@dataclass(frozen=True)
class EvalResult:
    """One eval run's measurement: who ran, what answered, what scored, what it cost.

    Args:
        run_id:                UUID string of the `eval_runs` row this is about.
        agent_id:              UUID string of the agent that was evaluated.
        invocation:            the run's agent turns, as an Invocation.
        datasets:              dataset name to DatasetOutcome, over a subset of
                               EVAL_DATASETS. An absent dataset was not reported.
        requested_model:       the model the run's agent turns asked for, the one
                               the routing table names.
        cost:                  what the run's ledger rows spent, or COST_UNKNOWN.
        served_model:          the model the provider actually ran, when the
                               run's ledger rows agree on exactly one. None when
                               no row named one and None when two disagreed,
                               because a run served by two models has no single
                               served model and picking one would invent it.
        prompt_version_id:     the production prompt version this run is
                               attributed to. None is a real state: an agent with
                               no production version still runs, off its live
                               soul columns.
        judge_identity:        the Judge behind all four dimensions, when the four
                               routes name one. None when they differ or when a
                               route named no reasoning effort, and the per-call
                               identity on the `eval_results` rows is finer
                               grained than this either way (slice 2).
        failures:              one ScenarioFailure per turn that raised, in the
                               order the run attempted them. `invocation.failed`
                               already counts them. This says WHICH rows and WHAT
                               happened, which is what tells a run that lost two
                               turns to a provider from one that lost two to its
                               own 90s bound. Never longer than `failed`.
        context_proxy_version: which shape of retrieved context these scores were
                               computed over. Defaults to CONTEXT_PROXY_VERSION,
                               the shape this build produces (#84).
        rule_version:          which construction rules built the record.

    Raises:
        InvalidEvalResult: an empty id, an unknown dataset name, a member that is
            not the type it should be, or any rule the members enforce.
    """

    run_id: str
    agent_id: str
    invocation: Invocation
    # The init input, not what the record holds. __post_init__ copies it.
    datasets: Mapping[str, DatasetOutcome]
    requested_model: str
    cost: Cost = COST_UNKNOWN
    served_model: str | None = None
    prompt_version_id: str | None = None
    judge_identity: JudgeIdentity | None = None
    # The init input, not what the record holds. __post_init__ freezes it.
    failures: Sequence[ScenarioFailure] = ()
    context_proxy_version: str = CONTEXT_PROXY_VERSION
    rule_version: int = RULE_VERSION

    def __post_init__(self) -> None:
        for name in ("run_id", "agent_id", "requested_model", "context_proxy_version"):
            _require_text(name, getattr(self, name))
        for name in ("served_model", "prompt_version_id"):
            _require_optional_text(name, getattr(self, name))
        _require_count("rule_version", self.rule_version)
        if not isinstance(self.invocation, Invocation):
            raise InvalidEvalResult(
                f"EvalResult needs an Invocation, got {type(self.invocation).__name__}"
            )
        if not isinstance(self.cost, Cost):
            raise InvalidEvalResult(
                f"EvalResult needs a Cost, got {type(self.cost).__name__}"
            )
        if self.judge_identity is not None and not isinstance(
            self.judge_identity, JudgeIdentity
        ):
            raise InvalidEvalResult(
                "EvalResult needs judge_identity as a JudgeIdentity or None, got "
                f"{type(self.judge_identity).__name__}"
            )
        if not isinstance(self.datasets, Mapping):
            raise InvalidEvalResult(
                f"EvalResult needs datasets as a mapping, got {type(self.datasets).__name__}"
            )
        unknown = sorted(set(self.datasets) - set(EVAL_DATASETS))
        if unknown:
            # A third dataset name is a bucket no comparison covers. The golden
            # half is fixed and the exploratory half rotates; a row belonging to
            # neither cannot be compared against the next run either way.
            raise InvalidEvalResult(
                f"EvalResult takes the datasets a run reports ({', '.join(EVAL_DATASETS)}), "
                "got " + ", ".join(unknown)
            )
        wrong = sorted(
            f"{name}={type(value).__name__}"
            for name, value in self.datasets.items()
            if not isinstance(value, DatasetOutcome)
        )
        if wrong:
            raise InvalidEvalResult(
                "EvalResult needs every dataset to be a DatasetOutcome, got "
                + ", ".join(wrong)
            )
        _require_failures(self.failures, self.invocation.failed)
        # object.__setattr__ is how a frozen dataclass normalises a field.
        object.__setattr__(self, "datasets", dict(self.datasets))
        object.__setattr__(self, "failures", tuple(self.failures))

    @property
    def attempted(self) -> int:
        """Rows the selector returned, across every reported dataset."""
        return sum(outcome.attempted for outcome in self.datasets.values())

    @property
    def valid(self) -> int:
        """Rows carrying a label. The run-level denominator."""
        return sum(outcome.valid for outcome in self.datasets.values())

    @property
    def scored(self) -> int:
        """Rows at least one metric came back a real number for."""
        return sum(outcome.scored for outcome in self.datasets.values())

    @property
    def scenarios_passed(self) -> int:
        """Scored scenarios that cleared every gate, across every reported dataset."""
        return sum(outcome.scenarios_passed for outcome in self.datasets.values())

    @property
    def scenarios_failed(self) -> int:
        """Scored scenarios a gated verdict went against.

        Adding the two datasets is arithmetic on counts, not on rates, so it says
        exactly what it says: this many scenarios failed. The MEANS are what may
        never be pooled, because the golden half is fixed and the exploratory
        half rotates and one mean over both moves with the draw.
        """
        return sum(outcome.scenarios_failed for outcome in self.datasets.values())

    @property
    def scenarios_unmeasured(self) -> int:
        """Scored scenarios no gated verdict decided. Counted apart from the failures."""
        return sum(outcome.scenarios_unmeasured for outcome in self.datasets.values())

    @property
    def payload(self) -> dict:
        """The whole record as JSON, which is how `eval_runs.result` holds it.

        One place decides the stored shape, so the task that writes the column,
        `from_payload`, and the routes that grow a parser for it in slices 3 and
        4 are all looking at the same keys. The three run-level counts are
        written out rather than left to be derived again by whoever reads the
        row, the same choice `RedTeamResult.payload` made for its two totals.

        Returns:
            {"run_id", "agent_id", "prompt_version_id", "judge_identity",
             "requested_model", "served_model", "invocation", "datasets",
             "attempted", "valid", "scored", "cost", "failures",
             "context_proxy_version", "rule_version"} where `datasets` maps each
            reported dataset to {"attempted", "valid", "scored",
            "scenarios_passed", "scenarios_failed", "scenarios_unmeasured",
            "metrics"}, each reported metric to {"value", "measured",
            "observations"}, and `failures` is a list of {"scenario_id",
            "error_type", "message"}.
        """
        return {
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "prompt_version_id": self.prompt_version_id,
            "judge_identity": (
                dataclasses.asdict(self.judge_identity) if self.judge_identity else None
            ),
            "requested_model": self.requested_model,
            "served_model": self.served_model,
            "invocation": self.invocation.payload,
            "datasets": {
                name: self.datasets[name].payload
                for name in EVAL_DATASETS
                if name in self.datasets
            },
            "attempted": self.attempted,
            "valid": self.valid,
            "scored": self.scored,
            "cost": self.cost.payload,
            "failures": [failure.payload for failure in self.failures],
            "context_proxy_version": self.context_proxy_version,
            "rule_version": self.rule_version,
        }

    @classmethod
    def from_payload(cls, payload: Mapping) -> EvalResult:
        """Rebuild the record from a stored `eval_runs.result`.

        The round trip is the contract: `EvalResult.from_payload(r.payload) == r`.
        A stored row is validated on the way out exactly as it was on the way in.

        EVERY WAY A STORED SHAPE CAN BE WRONG LEAVES HERE AS InvalidEvalResult.
        `JudgeIdentity(**identity)` over an identity carrying an extra key, a
        missing key or a string raises TypeError, and the routes catch
        InvalidEvalResult alone, so one malformed row returned 500 for the whole
        of `GET /eval-runs`. A reader of one bad record loses that record only.

        Raises:
            InvalidEvalResult: the stored shape is not a mapping, breaks a
                construction rule, or cannot be read as this record at all.
        """
        if not isinstance(payload, Mapping):
            raise InvalidEvalResult(
                f"EvalResult needs a mapping, got {type(payload).__name__}"
            )
        identity = payload.get("judge_identity")
        datasets = payload.get("datasets") or {}
        if not isinstance(datasets, Mapping):
            raise InvalidEvalResult(
                f"EvalResult needs datasets as a mapping, got {type(datasets).__name__}"
            )
        try:
            record = cls(
                run_id=payload.get("run_id"),
                agent_id=payload.get("agent_id"),
                invocation=Invocation.from_payload(payload.get("invocation") or {}),
                datasets={
                    name: DatasetOutcome.from_payload(value)
                    for name, value in datasets.items()
                },
                requested_model=payload.get("requested_model"),
                cost=Cost.from_payload(payload.get("cost") or {}),
                served_model=payload.get("served_model"),
                prompt_version_id=payload.get("prompt_version_id"),
                judge_identity=JudgeIdentity(**identity) if identity else None,
                # No failures key reads as no failure DETAIL, never as none.
                failures=[
                    ScenarioFailure.from_payload(failure)
                    for failure in payload.get("failures") or []
                ],
                context_proxy_version=payload.get(
                    "context_proxy_version", CONTEXT_PROXY_VERSION
                ),
                rule_version=payload.get("rule_version", RULE_VERSION),
            )
        except InvalidEvalResult:
            # Already this module's refusal, carrying which rule it broke.
            raise
        except (TypeError, KeyError, ValueError, AttributeError) as exc:
            raise InvalidEvalResult(
                "EvalResult cannot be rebuilt from this stored shape "
                f"({type(exc).__name__}: {exc})"
            ) from exc
        _require_totals_agree(payload, record)
        return record


#: A metric nothing reported. One shared object, because Measurement is frozen
#: and every unreported metric is the same absence.
UNMEASURED = Measurement(value=None, observations=0, measured=False)


def unmeasured_metrics() -> dict[str, Measurement]:
    """The four metrics, none of them read."""
    return {metric: UNMEASURED for metric in METRIC_KEYS}


def metrics_of(outcome: DatasetOutcome) -> dict[str, Measurement]:
    """One dataset's four metrics, lifted out of the record unchanged.

    A metric the record does not report reads unmeasured, which is the same
    thing it read before the run had a record at all. Nothing is averaged and
    nothing is defaulted to a number.
    """
    return {metric: outcome.metrics.get(metric, UNMEASURED) for metric in METRIC_KEYS}


def scoring_datasets(record: EvalResult | None) -> list[str]:
    """Which reported datasets scored a row, in EVAL_DATASETS order."""
    if record is None:
        return []
    return [
        name
        for name in EVAL_DATASETS
        if name in record.datasets and record.datasets[name].scored > 0
    ]


def run_level_metrics(record: EvalResult | None) -> tuple[dict[str, Measurement], str | None]:
    """The run's four metrics and the dataset they were lifted from. THE ONE RULE.

    THIS RECORD HOLDS NO RUN-LEVEL MEAN and that is the point of it. A golden
    mean and an exploratory mean answer different questions: the golden rows are
    fixed and run in full every night, so consecutive runs are a paired per-item
    comparison, while the exploratory sample rotates and its mean moves whenever
    the draw moves. One number over both moves with the draw while looking like a
    quality change, which is why
    `.dev/reference/260818-llm-eval-fundamentals.md` section 11 forbids a pooled
    rate outright.

    So a run-level reading exists only when there is nothing to pool: exactly one
    dataset scored a row. Then its measurements ARE the run's, copied over
    verbatim, and the returned name says which. When both scored, this returns
    four unmeasured metrics and no name, and the numbers stay per dataset where
    the record keeps them apart. Unknown, never an average nobody computed.

    It lives here rather than in a reader because the console route and the
    deploy gate both ask it. Two copies of a selection rule is how a deploy gate
    and the screen the owner is looking at come to name different datasets.

    Returns:
        (metrics, dataset_name), dataset_name None when no single dataset
        produced the reading.
    """
    scoring = scoring_datasets(record)
    if record is None or len(scoring) != 1:
        return unmeasured_metrics(), None
    return metrics_of(record.datasets[scoring[0]]), scoring[0]
