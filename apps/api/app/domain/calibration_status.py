"""CalibrationStatus, what the calibration harness measured about one Judge (ticket #53, slice 1).

WHY THE APP CANNOT READ THE HARNESS'S DICT
    `tests/evals/calibration/compute_correlation.py` already reaches a verdict on
    a Judge, and it reaches it well. It is also a test-tree script that runs by
    hand, costs a judge call per labelled row, and lives behind `.dockerignore`.
    Nothing on the deploy path can import it, call it, or wait for it. So the
    harness's answer has to travel as data, and this record is the shape it
    travels in. Slice 2 writes the artifact, slice 3 puts it in the deploy
    summary, and ticket 17 decides what to do about it.

THE FOUR STATUSES ARE THE HARNESS'S OWN, SPELLED ITS WAY
    `compute_correlation.py:262-276` names them and pins `TRUSTWORTHY_STATUS` to
    exactly one. Renaming any of them here would give this repo two vocabularies
    for one measurement, and the day they drifted the record would still parse.
    `calibrated` is true for `calibrated` and for nothing else, including
    `not_calibrated_yet`, which is an absence rather than a pass.

THE THREE-PART VERDICT IS CARRIED, NEVER RECOMPUTED
    `agreement.calibration_verdict` (line 415) answers three questions: does the
    judge beat chance, does the labeller's self-agreement beat chance, and does
    the judge reach that ceiling. Each answer is True, False, or None for a part
    nobody could evaluate, and the distinction between False and None is what
    separates "fix the judge" from "the measurement has not been made". This
    record holds all three as they were stated. Recomputing them from the
    intervals here would put a second arithmetic beside the harness's, free to
    disagree, and the one that disagreed would be whichever the deploy gate read.
    That is the defect `EvalResult` was built to end one module over.

    `.dev/reference/260818-llm-eval-fundamentals.md` section 9 (lines 119-148) is
    why the identity travels with the numbers. Humans label first, the judge is
    built to agree with them, and alignment decays as prompts and data move. A
    kappa with no Judge attached is a number about a judge nobody can name.
    Section 10 (149-165) is why kappa and Matthews both have fields and neither
    is a gate on its own. Kappa collapses on imbalanced labels even for a good
    judge, which is exactly when Matthews is the readable one.

ZERO IS A MEASUREMENT AND NONE IS AN ABSENCE
    Every interval bound, both coefficients and all three verdict parts are
    nullable, and None never defaults to a number. A judge interval of None means
    the bootstrap carried no information; a `low` of 0.0 means it carried
    information and the judge did not beat chance. A default would make those two
    indistinguishable one reader later, which is the measurement-honesty rule this
    project runs on.

`from_harness` MAPS AND NEVER COMPUTES
    Slice 2 added it beside the writer that calls it. It copies one harness key
    onto one field, reads the three verdict parts out of the `gate` dict, and
    does no arithmetic at all, because arithmetic here would be a second copy of
    `agreement.py` free to disagree with the first. A result dict missing a key
    the mapping reads is refused rather than defaulted. A zero standing in for an
    absent count reports a measurement nobody made.

    `status`, `judge_identity`, `labelled_at` and `harness_version` are passed
    in. None of the four is in the result dict, and the caller is the only thing
    that knows them.

`from_payload` REQUIRES EVERY KEY, AND THE VERSION IT WAS BUILT FOR
    A stored artifact carries all nineteen keys or it is refused. The reader used
    to default each count to 0 and each figure to None, so a four-key file saying
    `{status: calibrated, judge_identity, beats_chance, reaches_ceiling}` loaded
    as a calibrated Judge measured over zero rows with no kappa. Nothing in that
    file was a lie; the defaults were. `artifact_version` is compared against
    ARTIFACT_VERSION rather than merely stored, because a field that means
    something else under another build's rules is refused rather than read under
    these.

Rung: `app.domain` imports the standard library, third-party packages and its
domain siblings. This module imports the standard library and
`app.domain.judge_identity`.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.domain.judge_identity import JudgeIdentity

#: The harness's four statuses, spelled as `compute_correlation.py:262-276` spells
#: them. A fifth name here would be a status no exit code covers.
STATUS_CALIBRATED = "calibrated"
STATUS_NOT_CALIBRATED = "not_calibrated"
STATUS_SETUP_ERROR = "setup_error"
STATUS_NOT_CALIBRATED_YET = "not_calibrated_yet"

CALIBRATION_STATUSES = (
    STATUS_CALIBRATED,
    STATUS_NOT_CALIBRATED,
    STATUS_SETUP_ERROR,
    STATUS_NOT_CALIBRATED_YET,
)

#: The reasons the loader may stamp, and the closed set a block message can switch
#: on. `reason` itself is free text, because the harness writes sentences into its
#: own `reasons` list and slice 2 has to carry one of them.
#:
#:   no_artifact                nothing at the configured path. The state a
#:                              container is in, because `.dockerignore` excludes
#:                              `tests/`, and the correct answer until slice 2
#:                              ships an artifact somewhere a container can read.
#:   unreadable                 a file that is there and is not JSON this can
#:                              open, or one too large to read at all
#:   invalid                    JSON this record refuses
#:   no_single_judge_identity   the run had no one Judge to compare against
#:   artifact_names_no_judge    the artifact names no Judge, so it measures
#:                              nobody. Every artifact this harness writes today
#:                              is one of these (#58), and it used to report as
#:                              `identity_mismatch`. That says the opposite, that
#:                              somebody was measured and it was somebody else.
#:   identity_mismatch          the artifact measures a different Judge
ABSENT_REASONS = (
    "no_artifact",
    "unreadable",
    "invalid",
    "no_single_judge_identity",
    "artifact_names_no_judge",
    "identity_mismatch",
)

#: Which construction rules built the record. Bumped when a stored artifact from
#: an older build would be read wrongly rather than refused.
ARTIFACT_VERSION = 1


class InvalidCalibrationStatus(ValueError):
    """A calibration record that would misreport what was measured.

    A ValueError, so callers that already catch ValueError keep catching it, the
    same choice `InvalidJudgeIdentity` and `InvalidEvalResult` made.
    """


def _require_optional_text(name: str, value: Any) -> None:
    """None says there is no such value. An empty string says there is one and hides it."""
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise InvalidCalibrationStatus(
            f"CalibrationStatus needs {name} as a non-empty string or None, got {value!r}"
        )


def _required_str(payload: Mapping, key: str) -> str:
    """The stored text under `key`. An absent key is a refusal, not a None."""
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InvalidCalibrationStatus(f"CalibrationStatus needs a {key}, got {value!r}")
    return value


def _required_key(payload: Mapping, key: str) -> Any:
    """The stored value under `key`, null included. An absent key is a refusal.

    Null and absent are different facts and the reader used to collapse them. A
    stored `kappa: null` says the harness found the coefficient undefined; no
    `kappa` key at all says nobody wrote one down, and defaulting it to None
    turned the second into the first. Applied to the counts the swap was worse,
    because a defaulted `pairs: 0` reads as a run that scored nothing while a
    file that never mentioned pairs says nothing about them.
    """
    if key not in payload:
        raise InvalidCalibrationStatus(
            f"CalibrationStatus needs {key!r} in the stored artifact. A default in "
            "its place would report a figure nobody wrote down."
        )
    return payload[key]


def _require_count(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidCalibrationStatus(
            f"CalibrationStatus needs {name} as an int, got {type(value).__name__}"
        )
    if value < 0:
        raise InvalidCalibrationStatus(
            f"CalibrationStatus needs {name} at zero or above, got {value}"
        )


def _require_optional_number(name: str, value: Any) -> float | None:
    """None, or a finite real number held as a float.

    NaN is what the harness produces for an undefined coefficient and it converts
    every one of them to None before returning (`compute_correlation.py:1113`).
    An artifact carrying a NaN through means something bypassed that conversion,
    and NaN compares unequal to itself, so it would break the round-trip contract
    silently rather than loudly. Infinity is not a kappa at all.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidCalibrationStatus(
            f"CalibrationStatus needs {name} as a number or None, got {type(value).__name__}"
        )
    if not math.isfinite(value):
        raise InvalidCalibrationStatus(
            f"CalibrationStatus needs {name} finite, got {value!r}"
        )
    return float(value)


def _require_optional_bool(name: str, value: Any) -> None:
    """True, False, or None for a part nobody evaluated. Never a stand-in."""
    if value is None or isinstance(value, bool):
        return
    raise InvalidCalibrationStatus(
        f"CalibrationStatus needs {name} as a bool or None, got {type(value).__name__}"
    )


@dataclass(frozen=True)
class Interval:
    """One bootstrap interval, as `agreement._interval` hands it over.

    Four of that dict's seven keys. `undefined_fraction`,
    `coverage_of_total_mass` and `spans_the_whole_range` are the harness's
    working, and the conclusion it drew from them is already on the record as
    `usable` and as the three verdict parts. Carrying the working as well would
    invite a reader to re-derive the conclusion and get a different one.

    Args:
        low:    lower bound, or None when the interval is not a measurement.
        high:   upper bound, same.
        point:  the point estimate, None when the coefficient was undefined. It
                can be present while the bounds are not. The harness reports a
                point over a corpus whose resamples carried no information.
        usable: whether the bounds are a measurement. False is never "the judge
                scored badly"; it is "these rows say nothing about any judge".

    Raises:
        InvalidCalibrationStatus: a bound is not a finite number, `usable` is not
            a bool, or `low` exceeds `high`.
    """

    low: float | None
    high: float | None
    point: float | None
    usable: bool

    def __post_init__(self) -> None:
        for name in ("low", "high", "point"):
            # object.__setattr__ is how a frozen dataclass normalises a field.
            object.__setattr__(
                self, name, _require_optional_number(name, getattr(self, name))
            )
        if not isinstance(self.usable, bool):
            raise InvalidCalibrationStatus(
                f"Interval needs usable as a bool, got {type(self.usable).__name__}"
            )
        if self.low is not None and self.high is not None and self.low > self.high:
            raise InvalidCalibrationStatus(
                f"Interval runs from {self.low} down to {self.high}. An interval whose "
                "bounds are the wrong way round reads as a narrow one."
            )

    @property
    def payload(self) -> dict:
        """{"low", "high", "point", "usable"}, the harness's own key names."""
        return {
            "low": self.low,
            "high": self.high,
            "point": self.point,
            "usable": self.usable,
        }

    @classmethod
    def from_payload(cls, payload: Mapping) -> Interval:
        """Rebuild one interval from its stored form.

        Raises:
            InvalidCalibrationStatus: the stored shape is not a mapping, it says
                nothing about `usable`, or it breaks a construction rule above.
        """
        if not isinstance(payload, Mapping):
            raise InvalidCalibrationStatus(
                f"Interval needs a mapping, got {type(payload).__name__}"
            )
        # Named rather than passed inline. A stored artifact that says nothing
        # about `usable` reads as None here, `__post_init__` refuses it, and the
        # declared `bool` stays honest about what the record holds afterwards.
        usable: Any = payload.get("usable")
        return cls(
            low=payload.get("low"),
            high=payload.get("high"),
            point=payload.get("point"),
            usable=usable,
        )


def _require_members(record: CalibrationStatus) -> None:
    """Every field is the type it says it is, and the coefficients are floats."""
    if record.status not in CALIBRATION_STATUSES:
        raise InvalidCalibrationStatus(
            f"CalibrationStatus takes one of {', '.join(CALIBRATION_STATUSES)}, "
            f"got {record.status!r}"
        )
    for name in ("reason", "labelled_at", "harness_version", "written_at"):
        _require_optional_text(name, getattr(record, name))
    if record.judge_identity is not None and not isinstance(
        record.judge_identity, JudgeIdentity
    ):
        raise InvalidCalibrationStatus(
            "CalibrationStatus needs judge_identity as a JudgeIdentity or None, got "
            f"{type(record.judge_identity).__name__}"
        )
    for name in ("judge_interval", "ceiling_interval", "difference_interval"):
        value = getattr(record, name)
        if value is not None and not isinstance(value, Interval):
            raise InvalidCalibrationStatus(
                f"CalibrationStatus needs {name} as an Interval or None, got "
                f"{type(value).__name__}"
            )
    for name in ("beats_chance", "ceiling_beats_chance", "reaches_ceiling"):
        _require_optional_bool(name, getattr(record, name))
    for name in ("kappa", "matthews"):
        # object.__setattr__ is how a frozen dataclass normalises a field.
        object.__setattr__(
            record, name, _require_optional_number(name, getattr(record, name))
        )


def _require_counts(record: CalibrationStatus) -> None:
    """The four counts, and the one containment the harness guarantees.

    `scored_pairs` counts rows where the human also gave a 1-5 score, and every
    one of those rows first contributed a binary pair (`compute_correlation.py`
    around line 1004). So Spearman's denominator is a subset of the gate's, and a
    record claiming otherwise reports a rho over rows the gate never saw.
    """
    for name in ("scored_pairs", "pairs", "attempted", "valid", "artifact_version"):
        _require_count(name, getattr(record, name))
    if record.scored_pairs > record.pairs:
        raise InvalidCalibrationStatus(
            f"CalibrationStatus reports {record.scored_pairs} scored_pairs over "
            f"{record.pairs} pairs. scored_pairs is a subset of pairs."
        )


def _require_calibrated_is_earned(record: CalibrationStatus) -> None:
    """`calibrated` is the one status a reader may act on, so it costs the most.

    A record can only say `calibrated` if it names the Judge it measured and if
    all three parts of the verdict came back True. `None` is refused as hard as
    `False`. An unevaluated part is an absence, and a deploy that shipped on an
    absence would be shipping on a measurement nobody made.

    ALL THREE PARTS, WHICH IS THE HARNESS'S OWN RULE CARRIED RATHER THAN
    RECOMPUTED. `tests/evals/calibration/agreement.py:415-490` returns
    `ceiling_beats_chance: False` when the labeller's two passes are not
    distinguishable from labelling the same rows at random, and it stops there
    with `reaches_ceiling: None`. Reading two of the three let an artifact claim
    `calibrated` over a ceiling that set no ceiling at all.
    """
    if record.status != STATUS_CALIBRATED:
        return
    if record.judge_identity is None:
        raise InvalidCalibrationStatus(
            "CalibrationStatus cannot be calibrated with no judge_identity. A "
            "calibration figure with no Judge attached covers every Judge and none."
        )
    for name in ("beats_chance", "ceiling_beats_chance", "reaches_ceiling"):
        if getattr(record, name) is not True:
            raise InvalidCalibrationStatus(
                f"CalibrationStatus is calibrated with {name}={getattr(record, name)!r}. "
                "Only True earns it; None means that part was never evaluated."
            )


def _require_calibrated_shows_its_working(record: CalibrationStatus) -> None:
    """A `calibrated` record also carries the measurement the verdict came from.

    THE FOUR-KEY ARTIFACT IS WHY THIS EXISTS. A file holding `status`,
    `judge_identity`, `beats_chance` and `reaches_ceiling` and nothing else used
    to load as a calibrated Judge, because the reader defaulted every count to 0
    and every figure to None. Three verdict booleans are cheap to write by hand
    and the numbers behind them are not.

    `scored_pairs >= 1` closes the count half on its own. `_require_counts`
    already holds `scored_pairs <= pairs`, so one scored pair forces at least one
    pair, and a verdict reported over zero rows is refused whichever count a
    hand-written file left out.
    """
    if record.status != STATUS_CALIBRATED:
        return
    for name in ("judge_interval", "ceiling_interval"):
        interval = getattr(record, name)
        if interval is None or not interval.usable:
            raise InvalidCalibrationStatus(
                f"CalibrationStatus is calibrated with {name}={interval!r}. The verdict "
                "is read off two usable intervals, so a record claiming it carries both."
            )
    for name in ("kappa", "matthews"):
        if getattr(record, name) is None:
            raise InvalidCalibrationStatus(
                f"CalibrationStatus is calibrated with no {name}. The coefficient a "
                "verdict was reached from is not optional on the record reporting it."
            )
    if record.scored_pairs < 1:
        raise InvalidCalibrationStatus(
            "CalibrationStatus is calibrated over 0 scored_pairs. A judge that agreed "
            "with a human over no rows agreed about nothing."
        )


def _harness_key(source: Mapping, key: str, where: str) -> Any:
    """The value the harness stored under `key`. An absent key is a refusal.

    A default in place of a missing key is how a record comes to report a
    measurement nobody made. `kappa` defaulting to 0.0 reads as a judge that
    agreed at chance, and `pairs` defaulting to 0 reads as a run that scored
    nothing. Both are conclusions, and the harness stated neither.
    """
    if key not in source:
        raise InvalidCalibrationStatus(
            f"CalibrationStatus.from_harness needs {key!r} in the harness {where}. "
            "A default in its place would report a figure the harness never stated."
        )
    return source[key]


def _harness_gate(result: Mapping) -> Mapping:
    """`calibration_verdict`'s dict, or an empty one when the run never reached it.

    The four early returns in `compute_correlation` carry `gate: None`, which is
    a value rather than a missing key. Those runs stopped at a floor and no
    verdict was reached. An empty mapping gives all three parts as None, which is
    the absence the record already distinguishes from False.
    """
    gate = _harness_key(result, "gate", "result")
    if gate is None:
        return {}
    if not isinstance(gate, Mapping):
        raise InvalidCalibrationStatus(
            f"CalibrationStatus needs the harness gate as a mapping or None, got "
            f"{type(gate).__name__}"
        )
    return gate


def _gate_part(gate: Mapping, key: str) -> bool | None:
    """One of `calibration_verdict`'s three answers, as it stated it.

    None for a part nobody could evaluate, and the distinction between that and
    False is what separates "fix the judge" from "the measurement has not been
    made". A gate that reached a verdict names all three, so a present gate
    missing one is a refusal.
    """
    if not gate:
        return None
    return _harness_key(gate, key, "gate")


def _harness_interval(result: Mapping, key: str) -> Interval | None:
    """One of the harness's three interval dicts, or None where it left one undefined."""
    stored = _harness_key(result, key, "result")
    if stored is None:
        return None
    return Interval.from_payload(stored)


def _harness_reason(gate: Mapping) -> str | None:
    """The gate's own sentences, joined, or None when it wrote none.

    `calibration_verdict` writes a sentence per part it could not pass, addressed
    to the person who has to act on it. They are carried whole rather than
    summarised into a token, because the token set a reader can switch on is
    ABSENT_REASONS and the loader stamps those; these say which half moved.
    """
    if not gate:
        return None
    reasons = _harness_key(gate, "reasons", "gate")
    if not reasons:
        return None
    if not isinstance(reasons, (list, tuple)):
        raise InvalidCalibrationStatus(
            f"CalibrationStatus needs the gate's reasons as a sequence, got "
            f"{type(reasons).__name__}"
        )
    return " ".join(str(reason) for reason in reasons)


@dataclass(frozen=True)
class CalibrationStatus:
    """One calibration run's answer about one Judge, as the app reads it.

    Frozen, so a consumer cannot edit a status into a pass on its way to a gate.

    Args:
        status:               one of CALIBRATION_STATUSES, the harness's own four.
        reason:               why it is not calibrated, as a short token from
                              ABSENT_REASONS or one of the harness's own reasons.
                              None when there is nothing to explain.
        judge_identity:       the Judge this figure was measured against. None
                              when no artifact was read.
        judge_interval:       bootstrap interval on the judge's kappa.
        ceiling_interval:     the same bootstrap over the labeller's two passes.
                              It caps every judge measured against that sheet.
        difference_interval:  paired interval on ceiling minus judge.
        beats_chance:         `calibration_verdict`'s part (a), carried as stated.
        ceiling_beats_chance: part (b1), carried as stated.
        reaches_ceiling:      part (b2), carried as stated.
        kappa:                Cohen's kappa point estimate, None when undefined.
        matthews:             Matthews correlation, the readable one when the
                              labels are lopsided. None when undefined.
        scored_pairs:         rows carrying both a human 1-5 and a judge score.
        pairs:                rows carrying both binary verdicts. The gate's
                              denominator, and never smaller than scored_pairs.
        attempted:            rows the sheet held.
        valid:                rows that parsed.
        labelled_at:          ISO timestamp of the labelling the figure covers,
                              or None. Section 9's alignment decay is read off
                              this, so a stale figure can be seen to be stale.
        harness_version:      which build of the harness produced it. None when
                              no harness produced this record, which is every
                              record `absent` builds.
        written_at:           when the writer stamped this file, ISO 8601 UTC.
                              The writer sets it and nothing else does, so two
                              runs are distinguishable even when they measured
                              the same rows to the same figures. None on a record
                              that was never written to disk. `labelled_at` dates
                              the labels and this one dates the reading of them.
        artifact_version:     which construction rules built it.

    Raises:
        InvalidCalibrationStatus: an unknown status, a member that is not the type
            it should be, a negative count, more scored_pairs than pairs, or a
            `calibrated` record that has not earned it.
    """

    status: str
    reason: str | None = None
    judge_identity: JudgeIdentity | None = None
    judge_interval: Interval | None = None
    ceiling_interval: Interval | None = None
    difference_interval: Interval | None = None
    beats_chance: bool | None = None
    ceiling_beats_chance: bool | None = None
    reaches_ceiling: bool | None = None
    kappa: float | None = None
    matthews: float | None = None
    scored_pairs: int = 0
    pairs: int = 0
    attempted: int = 0
    valid: int = 0
    labelled_at: str | None = None
    harness_version: str | None = None
    written_at: str | None = None
    artifact_version: int = ARTIFACT_VERSION

    def __post_init__(self) -> None:
        _require_members(self)
        _require_counts(self)
        _require_calibrated_is_earned(self)
        _require_calibrated_shows_its_working(self)

    @property
    def calibrated(self) -> bool:
        """True for exactly one of the four statuses.

        `TRUSTWORTHY_STATUS` in the harness is the same single name. Everything
        else, `not_calibrated_yet` included, means this Judge may not be trusted
        at scale yet.
        """
        return self.status == STATUS_CALIBRATED

    @classmethod
    def absent(cls, reason: str) -> CalibrationStatus:
        """No usable figure, and why.

        `not_calibrated_yet` rather than `not_calibrated`, because every reason in
        ABSENT_REASONS is an absence. Nobody measured this Judge. Reporting a
        measured failure over a file that was never written would send an owner to
        fix a judge that may be fine.

        Raises:
            InvalidCalibrationStatus: `reason` is not one of ABSENT_REASONS.
        """
        if reason not in ABSENT_REASONS:
            raise InvalidCalibrationStatus(
                f"CalibrationStatus.absent takes one of {', '.join(ABSENT_REASONS)}, "
                f"got {reason!r}"
            )
        return cls(status=STATUS_NOT_CALIBRATED_YET, reason=reason)

    @classmethod
    def from_harness(
        cls,
        result: Mapping,
        *,
        status: str,
        judge_identity: JudgeIdentity | None,
        labelled_at: str | None,
        harness_version: str | None,
    ) -> CalibrationStatus:
        """One harness result dict, mapped onto this record a key at a time.

        NO ARITHMETIC HAPPENS HERE. Every number and every verdict part is copied
        from where `compute_correlation` put it. The three parts come out of the
        `gate` dict `agreement.calibration_verdict` built, so a `beats_chance` the
        harness left as None arrives as None and never as False.

        Args:
            result:          `compute_correlation`'s result dict. Every key the
                             mapping reads must be present; a missing one is
                             refused rather than defaulted.
            status:          the status to record. The caller's, not the result
                             dict's, because a run the harness called `calibrated`
                             over a Judge nobody can name is not a calibrated
                             record and the writer is what knows which case it is.
            judge_identity:  the one Judge the run scored with, or None.
            labelled_at:     when the sheet the figure covers was last labelled.
            harness_version: which build of the harness produced it.

        Raises:
            InvalidCalibrationStatus: `result` is not a mapping, it is missing a
                key the mapping reads, or the record it builds breaks one of the
                construction rules above.
        """
        if not isinstance(result, Mapping):
            raise InvalidCalibrationStatus(
                f"CalibrationStatus.from_harness needs a mapping, got "
                f"{type(result).__name__}"
            )
        gate = _harness_gate(result)
        return cls(
            status=status,
            reason=_harness_reason(gate),
            judge_identity=judge_identity,
            judge_interval=_harness_interval(result, "judge_interval"),
            ceiling_interval=_harness_interval(result, "ceiling_interval"),
            difference_interval=_harness_interval(result, "difference_interval"),
            beats_chance=_gate_part(gate, "beats_chance"),
            ceiling_beats_chance=_gate_part(gate, "ceiling_beats_chance"),
            reaches_ceiling=_gate_part(gate, "reaches_ceiling"),
            kappa=_harness_key(result, "kappa", "result"),
            matthews=_harness_key(result, "matthews", "result"),
            scored_pairs=_harness_key(result, "scored_pairs", "result"),
            pairs=_harness_key(result, "pairs", "result"),
            attempted=_harness_key(result, "attempted", "result"),
            valid=_harness_key(result, "valid", "result"),
            labelled_at=labelled_at,
            harness_version=harness_version,
        )

    @property
    def payload(self) -> dict:
        """The whole record as JSON, which is how the artifact file holds it.

        One field per key, in declaration order, so slice 2's writer maps the
        harness's result dict onto it a field at a time and slice 3's reader parses
        the same names. `tests/unit/test_calibration_status_type.py` pins this key
        set, so adding or renaming a field goes red on the commit that does it.

        Returns:
            {"status", "reason", "judge_identity", "judge_interval",
             "ceiling_interval", "difference_interval", "beats_chance",
             "ceiling_beats_chance", "reaches_ceiling", "kappa", "matthews",
             "scored_pairs", "pairs", "attempted", "valid", "labelled_at",
             "harness_version", "written_at", "artifact_version"} where
            `judge_identity` is {"model", "reasoning_effort", "prompt_version"}
            or None and each interval is {"low", "high", "point", "usable"} or
            None.
        """
        return {
            "status": self.status,
            "reason": self.reason,
            "judge_identity": (
                dataclasses.asdict(self.judge_identity) if self.judge_identity else None
            ),
            "judge_interval": self.judge_interval.payload if self.judge_interval else None,
            "ceiling_interval": (
                self.ceiling_interval.payload if self.ceiling_interval else None
            ),
            "difference_interval": (
                self.difference_interval.payload if self.difference_interval else None
            ),
            "beats_chance": self.beats_chance,
            "ceiling_beats_chance": self.ceiling_beats_chance,
            "reaches_ceiling": self.reaches_ceiling,
            "kappa": self.kappa,
            "matthews": self.matthews,
            "scored_pairs": self.scored_pairs,
            "pairs": self.pairs,
            "attempted": self.attempted,
            "valid": self.valid,
            "labelled_at": self.labelled_at,
            "harness_version": self.harness_version,
            "written_at": self.written_at,
            "artifact_version": self.artifact_version,
        }

    @classmethod
    def from_payload(cls, payload: Mapping) -> CalibrationStatus:
        """Rebuild the record from a stored artifact, re-checking it on the way out.

        The round trip is the contract:
        `CalibrationStatus.from_payload(r.payload) == r`. A stored artifact is
        validated on the way out as it was on the way in, because already being
        written down is not evidence that a shape is honest.

        EVERY KEY IS REQUIRED AND NOTHING IS DEFAULTED. See `_required_key`. A
        key may hold null only where the field is optional by design, which is
        every field except `status`, the four counts and `artifact_version`.

        EVERY WAY A STORED SHAPE CAN BE WRONG LEAVES HERE AS
        InvalidCalibrationStatus. `JudgeIdentity(**identity)` over an extra key, a
        missing key or a string raises TypeError, and the loader catches this
        module's refusal alone, so an unwrapped TypeError would escape a function
        documented never to raise.

        Raises:
            InvalidCalibrationStatus: the stored shape is not a mapping, is
                missing a key, was built under different construction rules,
                breaks a rule, or cannot be read as this record at all.
        """
        if not isinstance(payload, Mapping):
            raise InvalidCalibrationStatus(
                f"CalibrationStatus needs a mapping, got {type(payload).__name__}"
            )
        try:
            return cls(**_stored_fields(payload))
        except InvalidCalibrationStatus:
            # Already this module's refusal, carrying which rule it broke.
            raise
        except (TypeError, KeyError, ValueError, AttributeError) as exc:
            raise InvalidCalibrationStatus(
                "CalibrationStatus cannot be rebuilt from this stored shape "
                f"({type(exc).__name__}: {exc})"
            ) from exc


def _stored_fields(payload: Mapping) -> dict:
    """Every field of a stored artifact, read by name, none of them defaulted.

    Written out one line per field rather than looped over a key list, so the
    constructor keyword and the stored key are the same word in the same place
    and a field added to the record with no line here fails loudly.
    """
    return {
        "status": _required_str(payload, "status"),
        "reason": _required_key(payload, "reason"),
        "judge_identity": _stored_identity(payload),
        "judge_interval": _stored_interval(payload, "judge_interval"),
        "ceiling_interval": _stored_interval(payload, "ceiling_interval"),
        "difference_interval": _stored_interval(payload, "difference_interval"),
        "beats_chance": _required_key(payload, "beats_chance"),
        "ceiling_beats_chance": _required_key(payload, "ceiling_beats_chance"),
        "reaches_ceiling": _required_key(payload, "reaches_ceiling"),
        "kappa": _required_key(payload, "kappa"),
        "matthews": _required_key(payload, "matthews"),
        "scored_pairs": _required_key(payload, "scored_pairs"),
        "pairs": _required_key(payload, "pairs"),
        "attempted": _required_key(payload, "attempted"),
        "valid": _required_key(payload, "valid"),
        "labelled_at": _required_key(payload, "labelled_at"),
        "harness_version": _required_key(payload, "harness_version"),
        "written_at": _required_key(payload, "written_at"),
        "artifact_version": _stored_artifact_version(payload),
    }


def _stored_identity(payload: Mapping) -> JudgeIdentity | None:
    """The stored Judge, rebuilt. Null is a real answer and an absent key is not.

    Most artifacts this repo writes today carry null here, because the harness
    cannot name the Judge it called. That is a fact about the run. A file with no
    `judge_identity` key at all is a file this reader cannot vouch for.
    """
    identity = _required_key(payload, "judge_identity")
    if identity is None:
        return None
    if not isinstance(identity, Mapping):
        raise InvalidCalibrationStatus(
            "CalibrationStatus needs judge_identity as a mapping or None, got "
            f"{type(identity).__name__}"
        )
    return JudgeIdentity(**identity)


def _stored_artifact_version(payload: Mapping) -> int:
    """The stored version, refused unless it is the one these rules were written for.

    It was stored and never compared, which made it decoration. The constant is
    bumped when an artifact from an older build would be READ WRONGLY rather than
    refused, so meeting one and reading it anyway is precisely the accident the
    field exists to stop. The refusal names both versions, and the loader puts
    them on its log line.

    A bool is refused before the comparison, because `True == 1` in Python and an
    `artifact_version: true` would otherwise pass for version 1.
    """
    stored = _required_key(payload, "artifact_version")
    if isinstance(stored, bool) or stored != ARTIFACT_VERSION:
        raise InvalidCalibrationStatus(
            f"CalibrationStatus reads artifact_version {ARTIFACT_VERSION} and this "
            f"artifact is version {stored!r}. A field can mean something else under "
            "another build's construction rules, so it is refused, not read."
        )
    return ARTIFACT_VERSION


def _stored_interval(payload: Mapping, key: str) -> Interval | None:
    """One stored interval, or None where the harness left that half undefined."""
    stored = _required_key(payload, key)
    if stored is None:
        return None
    return Interval.from_payload(stored)
