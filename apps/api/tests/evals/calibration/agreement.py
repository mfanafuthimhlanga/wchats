"""How much agreement is enough, answered by the data instead of by a constant.

BACKLOG 8.2c. The gate used to contain `KAPPA_THRESHOLD = 0.6`, a number taken
from a band in a talk. The owner's instruction on 2026-08-18: "the kappa
measurement must not be a choice it must be derived from data."

Two halves, both derived, and BOTH are required:

    (a) beats chance      judge_ci_low  > 0
    (b) reaches ceiling   judge_ci_high >= human_ci_low

WHY (b) IS THE ONE THAT MATTERS
    A judge cannot be expected to agree with a human more than that human agrees
    with THEMSELF. The owner labels the same rows twice, blind and separated;
    their test-retest kappa is the ceiling, and it is the scale any judge kappa
    has to be read against. Without it, "kappa 0.62" is a number with no units.
    So with no ceiling measured this module refuses rather than inventing one.

WHY (b) IS AN OVERLAP TEST AND NOT A POINT COMPARISON
    The judge passes when it is NOT DISTINGUISHABLY WORSE than the human is with
    themself. Comparing point estimates would fail a judge for noise on ten rows,
    and comparing against a point ceiling would treat the human's own uncertainty
    as exact when it is measured from the same ten rows.

WHAT THIS MODULE WILL NOT DO
    Return a number when the corpus cannot support one. Kappa is undefined when
    one label dominates completely, and a bootstrap over a one-sided corpus
    inherits that: most resamples have no kappa at all. Those resamples are
    COUNTED, not dropped, and above `MAX_UNDEFINED_FRACTION` the interval is
    reported as undefined. A percentile taken over the handful of resamples that
    happened to contain a minority label is not a measurement of anything.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

from tests.evals.calibration.compute_correlation import cohens_kappa, confusion

#: Fixed so the gate does not flicker between runs on the same data. An unseeded
#: bootstrap is the same defect as an unset judge temperature, one level up.
BOOTSTRAP_SEED = 20260818

#: Enough resamples that the percentile is stable to the third decimal, and
#: cheap: 10k resamples of ten pairs is milliseconds.
BOOTSTRAP_ITERATIONS = 10_000

#: Above this fraction of resamples having no defined kappa, the interval is not
#: a measurement. A one-sided corpus lands here, and that is the honest answer.
MAX_UNDEFINED_FRACTION = 0.2

CONFIDENCE = 0.95


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank percentile. No numpy, matching the rest of this harness."""
    if not sorted_values:
        raise ValueError("no values")
    index = int(round(fraction * (len(sorted_values) - 1)))
    return sorted_values[min(max(index, 0), len(sorted_values) - 1)]


def bootstrap_kappa(
    pairs: Sequence[tuple[bool, bool]],
    *,
    iterations: int = BOOTSTRAP_ITERATIONS,
    confidence: float = CONFIDENCE,
    seed: int = BOOTSTRAP_SEED,
) -> dict:
    """Point estimate and confidence interval for Cohen's kappa over `pairs`.

    Returns `{"point", "low", "high", "undefined_fraction", "usable"}`. `point`,
    `low` and `high` are None whenever the interval is not a measurement, so a
    caller can never read a bound out of a run that did not produce one.

    `usable` is False when too many resamples had no defined kappa. That is the
    one-sided-corpus case, and it is reported rather than papered over: taking a
    percentile across only the resamples that happened to contain a minority
    label describes a corpus nobody has.
    """
    pairs = list(pairs)
    if len(pairs) < 2:
        return {"point": None, "low": None, "high": None,
                "undefined_fraction": 1.0, "usable": False}

    point = cohens_kappa(confusion(pairs))
    rng = random.Random(seed)
    n = len(pairs)

    defined: list[float] = []
    undefined = 0
    for _ in range(iterations):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        value = cohens_kappa(confusion(sample))
        if value != value:  # NaN: this resample carried one label only
            undefined += 1
        else:
            defined.append(value)

    undefined_fraction = undefined / iterations
    if undefined_fraction > MAX_UNDEFINED_FRACTION or not defined:
        return {"point": None if point != point else point,
                "low": None, "high": None,
                "undefined_fraction": undefined_fraction, "usable": False}

    defined.sort()
    tail = (1 - confidence) / 2
    return {
        "point": None if point != point else point,
        "low": _percentile(defined, tail),
        "high": _percentile(defined, 1 - tail),
        "undefined_fraction": undefined_fraction,
        "usable": True,
    }


def human_ceiling(pairs: Sequence[tuple[bool, bool]], **kwargs) -> dict:
    """The same bootstrap, over one human's two passes at the same rows.

    Separate name rather than a flag, because the two are read differently: this
    one is a property of the LABELLER and the corpus, not of any judge, and it
    caps every judge measured against that sheet.
    """
    return bootstrap_kappa(pairs, **kwargs)


def calibration_verdict(judge: dict, ceiling: dict | None) -> dict:
    """Does the judge beat chance, and does it reach the human ceiling?

    Returns `{"beats_chance", "reaches_ceiling", "calibrated", "reasons"}`.
    `reaches_ceiling` is None when no ceiling was measured, and `calibrated` is
    then False: an unmeasured ceiling is not a passed one.
    """
    reasons: list[str] = []

    if not judge.get("usable"):
        reasons.append(
            "the judge's kappa interval is not a measurement: "
            f"{judge.get('undefined_fraction', 1.0):.0%} of resamples had no defined kappa, "
            "which is what a corpus labelled almost entirely one way produces. Label a "
            "wider spread of rows (BACKLOG 8.4)."
        )
        return {"beats_chance": None, "reaches_ceiling": None,
                "calibrated": False, "reasons": reasons}

    beats_chance = judge["low"] > 0
    if not beats_chance:
        reasons.append(
            f"the judge's kappa interval [{judge['low']:.3f}, {judge['high']:.3f}] includes "
            "0, so this corpus does not show it doing better than chance."
        )

    if ceiling is None or not ceiling.get("usable"):
        reasons.append(
            "the HUMAN CEILING has never been measured. Label the same rows a second "
            "time, blind, and fill `human_verdict_2`. A judge cannot be expected to agree "
            "with a human more than that human agrees with themself, so without it there "
            "is no scale to read the judge's kappa against."
        )
        return {"beats_chance": beats_chance, "reaches_ceiling": None,
                "calibrated": False, "reasons": reasons}

    reaches_ceiling = judge["high"] >= ceiling["low"]
    if not reaches_ceiling:
        reasons.append(
            f"the judge's interval tops out at {judge['high']:.3f}, below the human's own "
            f"lower bound of {ceiling['low']:.3f}. It is distinguishably worse than the "
            "labeller is with themself."
        )

    return {
        "beats_chance": beats_chance,
        "reaches_ceiling": reaches_ceiling,
        "calibrated": bool(beats_chance and reaches_ceiling),
        "reasons": reasons,
    }
