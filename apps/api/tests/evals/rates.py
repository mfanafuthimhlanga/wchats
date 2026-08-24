"""pass@k and reliable@k, and the diagnosis that only their difference gives.

BACKLOG 8.1. Two questions, two metrics, and they prescribe opposite work:

    pass@k       over k tries, did it EVER succeed?     capability
    reliable@k   over k tries, how OFTEN?               consistency

    pass@k near zero          the system CANNOT do the task. Prompt tuning is
                              wasted effort; change the model, the tools, or the
                              architecture.
    pass@k high, reliable low it CAN, and the remaining work is variance.
    the two equal             deterministic. The gap between them IS the AI
                              problem.

A rate quoted from one run per scenario is silent about which of those it is
looking at, which is why every number this module returns carries its own `k`.

WHAT THIS MODULE REFUSES TO DO, and both refusals came from adversarial review
on 2026-08-18:

**It will not aggregate pass@k across a ragged corpus.** reliable@k per scenario
estimates p and is unbiased at any k. pass@k is an indicator whose expectation is
`1 - (1-p)^k`, so it RISES WITH k on its own. Averaging it across scenarios
captured a different number of times produces a number decided by which
scenarios errored on the previous capture. Observed: two scenarios both plausibly
p = 1/8, one sampled once and one eight times, reported `pass@k 50%`. The earlier
version printed RAGGED beside that number instead of declining to compute it.

**It will not treat a non-boolean as an outcome.** `sum(1 for o in outcomes if o)`
counted the strings `"no"`, `"FAIL"` and `"0"` as passes, counted an unextracted
verdict dict as a pass, and counted `None` - what an errored or unparsed run
looks like - as an observed FAILURE, converting unknown into fail inside the
denominator.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def scenario_rates(outcomes: Sequence[bool]) -> dict:
    """pass@k and reliable@k over ONE scenario's k runs.

    Raises on zero runs rather than dividing by it: a metric computed over no
    observation is `unknown`, never `pass`. (The earlier docstring claimed the
    guard prevented "a rate of 1.0 over nothing". That was wrong - without the
    guard this is `0/0` and raises ZeroDivisionError - and an adversarial review
    caught the claim by removing the guard and watching what actually happened.)

    Every outcome must be a real `bool`. A judge verdict, a raw string or a None
    reaching here is a bug in the caller's extraction, and coercing it silently
    turns that bug into a number.
    """
    if isinstance(outcomes, (str, bytes)):
        raise TypeError("outcomes must be a sequence of bools, not a string")
    outcomes = list(outcomes)
    k = len(outcomes)
    if k == 0:
        raise ValueError("no runs to rate; a rate over zero observations is unknown, not a pass")
    for index, outcome in enumerate(outcomes):
        if not isinstance(outcome, bool):
            raise TypeError(
                f"outcome {index} is {type(outcome).__name__} ({outcome!r}), not a bool. "
                "A verdict string or a None here is an extraction bug in the caller, and "
                "counting it by truthiness would turn that bug into a rate."
            )

    passes = sum(outcomes)
    return {
        "k": k,
        "passes": passes,
        # `ever_passed`, not `pass_at_k`. The aggregate below carries a `pass_at_k`
        # that is a FRACTION, and one key meaning a bool in one dict and a float
        # in the other is a silent wrong answer to `if r["pass_at_k"]`.
        "ever_passed": passes > 0,
        "reliable_at_k": passes / k,
    }


def aggregate(per_scenario: Mapping[str, Sequence[bool]]) -> dict:
    """Roll one dimension's per-scenario outcomes into rates.

    `reliable_at_k` is the MEAN OF THE PER-SCENARIO RATES, not total successes
    over total runs. The two are equal only while every scenario has the same k,
    and they diverge exactly when it matters: a capture that errored partway
    leaves some scenarios at k=5 and some at k=1, and total-over-total then lets
    the scenarios that happened to get more runs decide the number. Weighting
    each scenario equally keeps the rate a statement about the scenario set.

    `pass_at_k` is **None when k is ragged**. Weighting cannot rescue it: the
    quantity itself grows with k, so the average is not a property of the agent.
    A well-formed capture is never ragged - `runs_to_capture` tops every scenario
    up to the same k - so this only fires on a partial capture, which is exactly
    when the number would be meaningless.

    `never_passed` and `flaky` are the two failure kinds kept apart, because a
    scenario in the first needs a different model or architecture and one in the
    second needs variance work. Collapsing them is what a k=1 corpus does.
    """
    if not per_scenario:
        return {
            "scenarios": 0, "k_min": None, "k_max": None, "ragged": False,
            "pass_at_k": None, "pass_at_k_unavailable": None,
            "reliable_at_k": None, "never_passed": [], "flaky": [], "per_scenario": {},
        }

    rated = {sid: scenario_rates(outcomes) for sid, outcomes in per_scenario.items()}
    ks = [r["k"] for r in rated.values()]
    ragged = min(ks) != max(ks)

    return {
        "scenarios": len(rated),
        "k_min": min(ks),
        "k_max": max(ks),
        "ragged": ragged,
        "pass_at_k": (
            None if ragged
            else sum(1 for r in rated.values() if r["ever_passed"]) / len(rated)
        ),
        "pass_at_k_unavailable": (
            "k is ragged, and pass@k grows with k on its own, so an average over "
            "scenarios captured a different number of times is decided by the capture "
            "rather than by the agent" if ragged else None
        ),
        "reliable_at_k": sum(r["reliable_at_k"] for r in rated.values()) / len(rated),
        "never_passed": sorted(sid for sid, r in rated.items() if not r["ever_passed"]),
        "flaky": sorted(sid for sid, r in rated.items() if 0 < r["reliable_at_k"] < 1),
        "per_scenario": rated,
    }


def describe(name: str, agg: dict) -> str:
    """One line a person can read a rate off without being misled by it.

    **Counts travel with every percentage.** `:.0%` renders 249 of 250 as `100%`,
    so a dimension with a known failure printed as perfect. The counts are what
    make a single failure visible, and they are the reason this line can be read
    at a glance without opening `never_passed`.

    `k` travels with the numbers rather than sitting in a header, because a
    pass@k at k=1 is not evidence of capability and a reliable@k at k=1 is not
    evidence of consistency, and both look identical to a rate at k=5 once the k
    is out of the sentence.
    """
    if not agg["scenarios"]:
        return f"{name}: no scenarios rated"

    total = agg["scenarios"]
    k = f"k={agg['k_min']}" if not agg["ragged"] else f"k={agg['k_min']}-{agg['k_max']} RAGGED"

    if agg["pass_at_k"] is None:
        passed = total - len(agg["never_passed"])
        pass_part = f"pass@k unavailable ({passed}/{total} ever passed)"
    else:
        passed = round(agg["pass_at_k"] * total)
        pass_part = f"pass@k {agg['pass_at_k']:.0%} ({passed}/{total})"

    consistent = total - len(agg["never_passed"]) - len(agg["flaky"])
    return (
        f"{name}: {pass_part}, reliable@k {agg['reliable_at_k']:.0%} "
        f"({consistent}/{total} scenarios passed every run, {len(agg['flaky'])} flaky, "
        f"{len(agg['never_passed'])} never), {k}"
    )
