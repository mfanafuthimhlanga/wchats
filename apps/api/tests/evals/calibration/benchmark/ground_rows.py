"""Measure the grounding rule on the platform benchmark. No model call.

    python ground_rows.py [--floor CARRIED_FLOOR] [--threshold 0.80]

Recall: each planted sentence in `truth.csv` is scored ON ITS OWN against its answer's
retrieved text, and the rule must flag it. Scoring it inside the answer let a neighbouring
fragment on the same line earn the flag once (adversary pass, 2026-09-23). Precision has no
truth on the unplanted sentences,
so the second table reports what the rule does to the real answers at the gate: their scores,
how many pass the threshold, and how many sentences it flags, as numbers to read and not a
ratio to assert.
"""

from __future__ import annotations

import csv
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3]))

from app.domain.grounding import CARRIED_FLOOR, ground  # noqa: E402


def load(name: str) -> list[dict]:
    with (HERE / name).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def measure(floor: float, threshold: float) -> dict:
    rows = {r["scenario_id"]: r for r in load("rows.csv")}
    truth = load("truth.csv")
    planted_found = 0
    for t in truth:
        row = rows[t["scenario_id"]]
        g = ground(t["claim"], [row["retrieved_contexts"]], carried_floor=floor)
        if g.sentences and not all(s.supported for s in g.sentences):
            planted_found += 1
    real = [r for r in rows.values() if not r["scenario_id"].endswith("-seeded")]
    scores, flagged, total = [], 0, 0
    for r in real:
        g = ground(r["response"], [r["retrieved_contexts"]], carried_floor=floor)
        if g.score is not None:
            scores.append(g.score)
        flagged += sum(1 for s in g.sentences if not s.supported)
        total += len(g.sentences)
    scores.sort()
    return {
        "floor": floor,
        "threshold": threshold,
        "planted": len(truth),
        "planted_flagged": planted_found,
        "real_answers": len(real),
        "scored": len(scores),
        "pass_at_threshold": sum(1 for s in scores if s >= threshold),
        "median_score": scores[len(scores) // 2] if scores else None,
        "min_score": scores[0] if scores else None,
        "sentences": total,
        "sentences_flagged": flagged,
    }


def main(argv: list[str]) -> int:
    opts = dict(zip(argv[::2], argv[1::2]))
    m = measure(float(opts.get("--floor", CARRIED_FLOOR)), float(opts.get("--threshold", 0.80)))
    for k, v in m.items():
        print(f"{k:20} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
