"""How much agreement is enough, answered by the data instead of by a constant.

BACKLOG 8.2c built this. BACKLOG 8.2d rewrote half of it, after four independent
adversarial reviews on 2026-08-19. Read the second paragraph before trusting any
memory of the first version.

The owner's instruction, 2026-08-18: "the kappa measurement must not be a choice
it must be derived from data." `KAPPA_THRESHOLD = 0.6` was deleted for that
reason. What replaced it, `judge_high >= ceiling_low`, contained a constant
nobody chose:

    against a self-consistent labeller, ceiling_low is 1.000, so the rule is
    judge_high >= 1.000, which needs 2.5% of resamples to contain NONE of the
    judge's error rows:

        (1 - e/n)^n  ~=  e^-e  >=  0.025      =>      e <= 3.68

    e is a COUNT. So the gate was "at most 3 disagreements" at every n: looser
    than kappa 0.6 below n=18, stricter above it, and it got harder the more
    rows the owner labelled. Measured: one fixed judge, 200 second passes drawn
    from a 95%-self-consistent labeller, CALIBRATED in 127 of them.

THE RULE NOW. Three parts, and all three are required.

    (a)  the judge beats chance          judge_ci_low   > 0
    (b1) the labeller set a scale        ceiling_ci_low > 0
    (b2) the judge is not distinguishably worse than the labeller
                                         d_ci_low <= 0, where d is measured
                                         WITHIN each resample as
                                         ceiling_kappa - judge_kappa

WHY (b1) EXISTS
    Two reviewers independently produced an exit-0 CALIBRATED from a ceiling
    whose own interval spanned zero. Nothing asked whether the ceiling was any
    good, so the WORSE the labeller, the lower the bar, and a coin-flip second
    pass waved every judge through. A labeller who cannot reproduce their own
    verdicts has not set a low ceiling; they have set none. (b1) is (a) applied
    to the human, so it is derived exactly as (a) is.

WHY (b2) IS PAIRED AND NOT AN OVERLAP TEST
    Comparing two marginal intervals is the overlapping-confidence-intervals
    fallacy: its effective alpha is unknown and moves with the ceiling's width.
    The two statistics are not independent either, because they share the
    human's first-pass label vector. Measuring the difference INSIDE each
    resample cancels that shared component, is correctly calibrated, and is
    strictly more powerful. It also removes the e <= 3.68 artefact, because it
    never asks an interval to reach exactly 1.000.

WHAT THIS MODULE WILL NOT DO
    Return a number when the corpus cannot support one. Kappa carries no
    information when EITHER rater used a single label: the arithmetic then
    forces observed == expected and kappa is identically 0 whatever the other
    rater did. Those cases are NaN here, not 0.0, and resamples that land in
    them are counted rather than averaged in.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from fractions import Fraction

#: Fixed so the gate does not flicker between runs on the same data. An unseeded
#: bootstrap is the same defect as an unset judge temperature, one level up.
#:
#: It freezes ONE draw from a distribution of verdicts; it does not make the
#: verdict correct. Measured 2026-08-19 at 10k iterations: the low bound moves
#: up to 0.033 across seeds at n=16.
BOOTSTRAP_SEED = 20260818

#: 10k resamples of a 10-to-30 row sheet is milliseconds.
#:
#: CORRECTED 2026-08-19: the previous comment claimed the percentile was "stable
#: to the third decimal". Measured false - the spread across seeds reaches 0.033
#: at n=16, which is the second decimal, and at n=12 a full verdict flip was
#: found. `agreement_precision()` reports the spread for a given corpus so the
#: claim can be checked rather than repeated.
BOOTSTRAP_ITERATIONS = 10_000

#: Above this fraction of resamples carrying no information, the interval is not
#: a measurement.
#:
#: WHAT IT ACTUALLY MEANS. A resample loses a label entirely with probability
#: about `e^-m`, where `m` is the count of MINORITY-label rows in the sample. So
#: this threshold is not a percentage anyone tuned, it is the line between
#: m = 1 (e^-1 = 0.368, refused) and m = 2 (e^-2 = 0.135, allowed). Read it as:
#: **at least two rows must carry the minority label.**
#:
#: CORRECTED 2026-08-19: the previous comment said "Nothing passes because of
#: it." That is false and was measured false - `usable=False` forces
#: `calibrated=False`, so raising this constant converts refusals into passes.
#: It gates in both directions.
MAX_UNDEFINED_FRACTION = 0.2

#: The stated coverage of every interval here. Note that the percentile is taken
#: over the INFORMATIVE resamples only, so at the MAX_UNDEFINED_FRACTION
#: boundary a "95%" interval covers about 76% of the total resample mass.
#: `coverage_of_total_mass()` reports the real figure for a run.
CONFIDENCE = 0.95


# ---------------------------------------------------------------------------
# The two primitives every number below is built from
# ---------------------------------------------------------------------------


def confusion(pairs: Sequence[tuple[bool, bool]]) -> dict[str, int]:
    """The 2x2 of (human_passed, judge_passed). Four cells, four actions.

    This is the report card, not a step towards one number. Each cell prescribes
    something different:

        both pass                nothing to do
        human pass, judge fail   the judge is too harsh; read its stated reasons
        human fail, judge pass   the judge is too LENIENT; bad answers are
                                 reaching customers, and this is the dangerous cell
        both fail                the AI SYSTEM is the problem, not the eval

    That last cell is the one that stops a team tuning a judge when the product
    is what is broken, and a single correlation coefficient cannot point at it.
    """
    cells = {"both_pass": 0, "judge_too_harsh": 0, "judge_too_lenient": 0, "both_fail": 0}
    for human_passed, judge_passed in pairs:
        if human_passed and judge_passed:
            cells["both_pass"] += 1
        elif human_passed and not judge_passed:
            cells["judge_too_harsh"] += 1
        elif not human_passed and judge_passed:
            cells["judge_too_lenient"] += 1
        else:
            cells["both_fail"] += 1
    return cells


def cohens_kappa(cells: dict[str, int]) -> float:
    """Chance-corrected agreement. NaN when the table carries no information.

    EXACT ARITHMETIC, and that is not a nicety. `observed` and `expected` reach
    the same value by different routes, so in float a mathematically exact zero
    came back as 2.66e-16. `calibration_verdict` tests `low > 0`, so half (a)
    passed on dust, and the shipped seed landed on the wrong side of it: the same
    corpus returned CALIBRATED at seed 20260818 and NOT CALIBRATED at seed 3000,
    both printing `[+0.000, +1.000]`. Fractions remove the class rather than
    papering over it with a tolerance nobody could derive.

    NaN, NOT 0.0, WHEN EITHER MARGINAL IS DEGENERATE. If one rater used a single
    label then `observed == expected` identically, so kappa is 0 no matter what
    the other rater did. 0.0 means "no better than chance", which is a finding
    about the judge; this is the absence of a finding, and printing it as the
    first is how a lopsided corpus gets reported as a bad judge. The previous
    version returned NaN only when BOTH marginals were degenerate, which
    contradicted this docstring and pinned bootstrap lower bounds at zero: on an
    18-pass/2-fail corpus, 24% of resamples were hard zeros and the harness
    exited 1, "fix the judge", over a judge whose point kappa was 0.643.
    """
    n11, n10 = cells["both_pass"], cells["judge_too_harsh"]
    n01, n00 = cells["judge_too_lenient"], cells["both_fail"]
    n = n11 + n10 + n01 + n00
    if n == 0:
        return float("nan")

    human_pass = Fraction(n11 + n10, n)
    judge_pass = Fraction(n11 + n01, n)
    if human_pass in (0, 1) or judge_pass in (0, 1):
        return float("nan")

    observed = Fraction(n11 + n00, n)
    expected = human_pass * judge_pass + (1 - human_pass) * (1 - judge_pass)
    return float((observed - expected) / (1 - expected))


# ---------------------------------------------------------------------------
# Intervals
# ---------------------------------------------------------------------------


def tails(confidence: float) -> tuple[float, float]:
    """The two percentile positions a two-sided interval at `confidence` reads.

    A named function because it was three characters inside `_interval` and
    nothing pinned it: changing `(1 - confidence) / 2` to `(1 - confidence)`
    silently turned every "95%" interval into an 90% one and left 87 tests green.
    The width is what BOTH halves of the gate compare, so it is not a detail.
    """
    excluded = 1 - confidence
    return excluded / 2, 1 - excluded / 2


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Percentile-bootstrap rank, the `ceil(alpha * (B + 1))` convention.

    The previous nearest-rank form sat one rank inside this at both ends, which
    narrows a "95%" interval on both sides. Immaterial against the ties kappa
    produces at n = 10 to 30, corrected because the label says 95%.
    """
    if not sorted_values:
        raise ValueError("no values")
    count = len(sorted_values)
    index = int(-(-fraction * (count + 1) // 1)) - 1
    return sorted_values[min(max(index, 0), count - 1)]


def _resample_indices(rng: random.Random, n: int) -> list[int]:
    return [rng.randrange(n) for _ in range(n)]


def _interval(
    point: float,
    values: list[float],
    undefined: int,
    iterations: int,
    confidence: float,
) -> dict:
    """Assemble one interval, refusing when too little of it carried information."""
    undefined_fraction = undefined / iterations
    if not values or undefined_fraction > MAX_UNDEFINED_FRACTION:
        return {
            "point": None if point != point else point,
            "low": None,
            "high": None,
            "undefined_fraction": undefined_fraction,
            "coverage_of_total_mass": 0.0,
            "spans_the_whole_range": False,
            "usable": False,
        }

    values.sort()
    low_fraction, high_fraction = tails(confidence)
    low, high = _percentile(values, low_fraction), _percentile(values, high_fraction)

    # An interval running from at-or-below chance to perfect agreement spans the
    # entire meaningful range of kappa, so it separates a coin from a flawless
    # judge by nothing. Both bounds are derived, not chosen: 0 is what chance
    # scores and 1 is kappa's maximum. Without this a 3-row corpus returned
    # [-0.000, +1.000], half (a) read that as a MEASURED failure, and the run
    # exited 1 - which the exit codes define as "fix the judge" - over three rows
    # that could not say anything about any judge.
    if low <= 0 and high >= 1.0:
        return {
            "point": None if point != point else point,
            "low": None,
            "high": None,
            "undefined_fraction": undefined_fraction,
            "coverage_of_total_mass": 0.0,
            "spans_the_whole_range": True,
            "usable": False,
        }

    return {
        "point": None if point != point else point,
        "low": low,
        "high": high,
        "spans_the_whole_range": False,
        "undefined_fraction": undefined_fraction,
        # What the stated confidence is worth once the uninformative resamples
        # are excluded. Reported rather than buried, because "95%" is not what
        # this interval covers when the corpus is lopsided.
        "coverage_of_total_mass": confidence * (1 - undefined_fraction),
        "usable": True,
    }


def bootstrap_kappa(
    pairs: Sequence[tuple[bool, bool]],
    *,
    iterations: int = BOOTSTRAP_ITERATIONS,
    confidence: float = CONFIDENCE,
    seed: int = BOOTSTRAP_SEED,
) -> dict:
    """Point estimate and confidence interval for Cohen's kappa over `pairs`.

    Returns `{"point", "low", "high", "undefined_fraction",
    "coverage_of_total_mass", "usable"}`. `point`, `low` and `high` are None
    whenever the interval is not a measurement, so a caller can never read a
    bound out of a run that did not make one.
    """
    pairs = list(pairs)
    if len(pairs) < 2:
        return {"point": None, "low": None, "high": None, "undefined_fraction": 1.0,
                "coverage_of_total_mass": 0.0, "spans_the_whole_range": False,
                "usable": False}

    point = cohens_kappa(confusion(pairs))
    rng = random.Random(seed)
    n = len(pairs)

    values: list[float] = []
    undefined = 0
    for _ in range(iterations):
        sample = [pairs[i] for i in _resample_indices(rng, n)]
        value = cohens_kappa(confusion(sample))
        if value != value:
            undefined += 1
        else:
            values.append(value)

    return _interval(point, values, undefined, iterations, confidence)


def human_ceiling(
    pairs: Sequence[tuple[bool, bool]],
    *,
    iterations: int = BOOTSTRAP_ITERATIONS,
    confidence: float = CONFIDENCE,
    seed: int = BOOTSTRAP_SEED,
) -> dict:
    """The same bootstrap, over one human's two passes at the same rows.

    Separate name rather than a flag, because the two are read differently: this
    one is a property of the LABELLER and the corpus, not of any judge, and it
    caps every judge measured against that sheet.

    Keyword arguments are named rather than forwarded through `**kwargs`. The
    previous version swallowed them, which meant a caller could not vary this
    bootstrap independently and made the accidental index-sharing with the judge
    invisible. `paired_difference` now shares indices ON PURPOSE.
    """
    return bootstrap_kappa(pairs, iterations=iterations, confidence=confidence, seed=seed)


def paired_difference(
    judge_pairs: Sequence[tuple[bool, bool]],
    ceiling_pairs: Sequence[tuple[bool, bool]],
    *,
    iterations: int = BOOTSTRAP_ITERATIONS,
    confidence: float = CONFIDENCE,
    seed: int = BOOTSTRAP_SEED,
) -> dict:
    """Interval on `ceiling_kappa - judge_kappa`, measured WITHIN each resample.

    Both sequences are indexed by the same rows in the same order: element `i`
    of each describes row `i`. One resample of row indices drives both, so the
    difference is paired and the human's first-pass label vector, which both
    statistics are built from, cancels instead of being compared across two
    marginals as though they were independent.

    A resample in which either kappa carries no information contributes nothing
    and is counted, exactly as in `bootstrap_kappa`.

    Read the result as: the judge is distinguishably worse than the labeller
    only when `low > 0`, that is, only when the whole interval says the human
    agrees with themself more than the judge agrees with the human.
    """
    judge_pairs, ceiling_pairs = list(judge_pairs), list(ceiling_pairs)
    if len(judge_pairs) != len(ceiling_pairs):
        raise ValueError(
            f"paired bootstrap needs one ceiling pair per judged row: "
            f"{len(judge_pairs)} judged, {len(ceiling_pairs)} ceiling"
        )
    if len(judge_pairs) < 2:
        return {"point": None, "low": None, "high": None, "undefined_fraction": 1.0,
                "coverage_of_total_mass": 0.0, "spans_the_whole_range": False,
                "usable": False}

    judge_point = cohens_kappa(confusion(judge_pairs))
    ceiling_point = cohens_kappa(confusion(ceiling_pairs))
    point = ceiling_point - judge_point  # NaN propagates, which is correct

    rng = random.Random(seed)
    n = len(judge_pairs)

    values: list[float] = []
    undefined = 0
    for _ in range(iterations):
        indices = _resample_indices(rng, n)
        judge_value = cohens_kappa(confusion([judge_pairs[i] for i in indices]))
        ceiling_value = cohens_kappa(confusion([ceiling_pairs[i] for i in indices]))
        if judge_value != judge_value or ceiling_value != ceiling_value:
            undefined += 1
        else:
            values.append(ceiling_value - judge_value)

    return _interval(point, values, undefined, iterations, confidence)


def agreement_precision(
    pairs: Sequence[tuple[bool, bool]],
    *,
    seeds: Sequence[int] = (20260818, 3000, 3001, 3002, 3003),
    iterations: int = BOOTSTRAP_ITERATIONS,
) -> dict:
    """How far the low bound moves across seeds on THIS corpus.

    Exists so nobody has to repeat the claim that the interval is "stable to the
    third decimal". It was not, and the honest number depends on n and on how
    lopsided the labels are. Call it and read the spread.
    """
    lows = [
        bootstrap_kappa(pairs, iterations=iterations, seed=s)["low"]
        for s in seeds
    ]
    measured = [value for value in lows if value is not None]
    if not measured:
        return {"lows": lows, "spread": None, "usable": False}
    return {"lows": lows, "spread": max(measured) - min(measured), "usable": True}


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------

#: Where the second labelling pass lives. Named here so the one string a stuck
#: owner reads cannot drift from the file that actually exists: the first
#: version of this module told them to fill a `human_verdict_2` column that was
#: deliberately never built.
SECOND_PASS_SHEET = "human_scores_pass2.csv"

_NO_SCALE = (
    "A judge cannot be expected to agree with a human more than that human "
    "agrees with themself, so without it there is no scale to read the judge's "
    "kappa against."
)


def calibration_verdict(
    judge: dict,
    ceiling: dict | None,
    difference: dict | None = None,
) -> dict:
    """Do all three parts hold?

    Returns `{"beats_chance", "ceiling_beats_chance", "reaches_ceiling",
    "calibrated", "reasons"}`. Every part is None when it could not be
    evaluated, and never False: a part that was not measured is an absence, and
    the caller maps absences and failures to different exit codes.
    """
    reasons: list[str] = []

    if judge.get("spans_the_whole_range"):
        reasons.append(
            "the judge's kappa interval runs from chance all the way to perfect "
            "agreement, so these rows separate a coin from a flawless judge by nothing. "
            "That is a statement about the SIZE of the labelled set, not about the "
            "judge. Label more rows."
        )
        return {"beats_chance": None, "ceiling_beats_chance": None,
                "reaches_ceiling": None, "calibrated": False, "reasons": reasons}

    if not judge.get("usable"):
        reasons.append(
            "the judge's kappa interval is not a measurement: "
            f"{judge.get('undefined_fraction', 1.0):.0%} of resamples carried no "
            "information, which is what a corpus labelled almost entirely one way "
            "produces. At least two rows must carry each label. Label a wider spread "
            "of rows (BACKLOG 8.4)."
        )
        return {"beats_chance": None, "ceiling_beats_chance": None,
                "reaches_ceiling": None, "calibrated": False, "reasons": reasons}

    beats_chance = judge["low"] > 0
    if not beats_chance:
        reasons.append(
            f"(a) FAILED. The judge's kappa interval [{judge['low']:.3f}, "
            f"{judge['high']:.3f}] includes 0, so this corpus does not show it doing "
            "better than chance."
        )

    if ceiling is None:
        reasons.append(
            f"(b) NOT MEASURED. There is no second labelling pass on file. Run "
            f"`--emit-second-pass`, then label {SECOND_PASS_SHEET} without opening the "
            f"first sheet. {_NO_SCALE}"
        )
        return {"beats_chance": beats_chance, "ceiling_beats_chance": None,
                "reaches_ceiling": None, "calibrated": False, "reasons": reasons}

    if not ceiling.get("usable"):
        reasons.append(
            "(b) NOT MEASURED. The second pass EXISTS and was read, but its interval "
            f"is not a measurement: {ceiling.get('undefined_fraction', 1.0):.0%} of "
            "resamples carried no information. That is a property of these rows, not "
            "of your labelling: at least two rows must carry each label, on both "
            "passes. This is not a request to label them again."
        )
        return {"beats_chance": beats_chance, "ceiling_beats_chance": None,
                "reaches_ceiling": None, "calibrated": False, "reasons": reasons}

    ceiling_beats_chance = ceiling["low"] > 0
    if not ceiling_beats_chance:
        reasons.append(
            f"(b1) FAILED. Your two labelling passes agree at kappa interval "
            f"[{ceiling['low']:.3f}, {ceiling['high']:.3f}], which includes 0: they are "
            "not distinguishable from labelling the same rows at random. That sets no "
            "ceiling at all, and a gate that read it would get EASIER the less "
            "consistent you were. Nothing can be concluded about the judge from these "
            "labels."
        )
        return {"beats_chance": beats_chance, "ceiling_beats_chance": False,
                "reaches_ceiling": None, "calibrated": False, "reasons": reasons}

    if difference is None or not difference.get("usable"):
        reasons.append(
            "(b2) NOT MEASURED. The paired difference between your self-agreement and "
            "the judge's agreement with you could not be computed over these rows."
        )
        return {"beats_chance": beats_chance, "ceiling_beats_chance": True,
                "reaches_ceiling": None, "calibrated": False, "reasons": reasons}

    reaches_ceiling = difference["low"] <= 0
    if not reaches_ceiling:
        reasons.append(
            f"(b2) FAILED. You agree with yourself more than the judge agrees with you, "
            f"by {difference['point']:.3f} kappa, and the whole interval "
            f"[{difference['low']:.3f}, {difference['high']:.3f}] is above 0. The judge "
            "is distinguishably worse than you are."
        )

    return {
        "beats_chance": beats_chance,
        "ceiling_beats_chance": True,
        "reaches_ceiling": reaches_ceiling,
        "calibrated": bool(beats_chance and reaches_ceiling),
        "reasons": reasons,
    }
