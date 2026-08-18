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
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def scenario_rates(outcomes: Sequence[bool]) -> dict:
    """pass@k and reliable@k over ONE scenario's k runs.

    Raises on zero runs rather than returning a rate of 1.0 over nothing. A
    metric computed over no observation is `unknown` and never `pass`.
    """
    k = len(outcomes)
    if k == 0:
        raise ValueError("no runs to rate; a rate over zero observations is unknown, not a pass")
    passes = sum(1 for outcome in outcomes if outcome)
    return {
        "k": k,
        "passes": passes,
        "pass_at_k": passes > 0,
        "reliable_at_k": passes / k,
    }


def aggregate(per_scenario: Mapping[str, Sequence[bool]]) -> dict:
    """Roll one dimension's per-scenario outcomes into a pair of rates.

    `reliable_at_k` is the MEAN OF THE PER-SCENARIO RATES, not total successes
    over total runs. The two are equal only while every scenario has the same k,
    and they diverge exactly when it matters: a capture that errored partway
    leaves some scenarios at k=5 and some at k=1, and total-over-total then lets
    the scenarios that happened to get more runs decide the number. Weighting
    each scenario equally keeps the rate a statement about the scenario set.

    `never_passed` and `flaky` are the two failure kinds kept apart, because a
    scenario in the first list needs a different model or architecture and one in
    the second needs variance work. Collapsing them into a single failure count
    is what a k=1 corpus does.
    """
    if not per_scenario:
        return {
            "scenarios": 0, "k_min": None, "k_max": None,
            "pass_at_k": None, "reliable_at_k": None,
            "never_passed": [], "flaky": [], "per_scenario": {},
        }

    rated = {sid: scenario_rates(outcomes) for sid, outcomes in per_scenario.items()}
    ks = [r["k"] for r in rated.values()]

    return {
        "scenarios": len(rated),
        "k_min": min(ks),
        "k_max": max(ks),
        "pass_at_k": sum(1 for r in rated.values() if r["pass_at_k"]) / len(rated),
        "reliable_at_k": sum(r["reliable_at_k"] for r in rated.values()) / len(rated),
        "never_passed": sorted(sid for sid, r in rated.items() if not r["pass_at_k"]),
        "flaky": sorted(sid for sid, r in rated.items() if 0 < r["reliable_at_k"] < 1),
        "per_scenario": rated,
    }


def describe(name: str, agg: dict) -> str:
    """One line a person can read a rate off without being misled by it.

    `k` travels with the numbers rather than sitting in a header, because a
    pass@k at k=1 is not evidence of capability and a reliable@k at k=1 is not
    evidence of consistency, and both look identical to a rate at k=5 once the k
    is out of the sentence.
    """
    if not agg["scenarios"]:
        return f"{name}: no scenarios rated"
    k = f"k={agg['k_min']}" if agg["k_min"] == agg["k_max"] else f"k={agg['k_min']}-{agg['k_max']} RAGGED"
    return (
        f"{name}: pass@k {agg['pass_at_k']:.0%}, reliable@k {agg['reliable_at_k']:.0%} "
        f"over {agg['scenarios']} scenario(s), {k}"
    )
