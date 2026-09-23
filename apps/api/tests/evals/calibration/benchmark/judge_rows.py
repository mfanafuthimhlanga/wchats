"""Score benchmark/rows.csv through the production faithfulness judge and keep its claims.

    cd apps/api
    OPENAI_API_KEY=... .venv/Scripts/python.exe tests/evals/calibration/benchmark/judge_rows.py \
        --benchmark tests/evals/calibration/benchmark

Writes `claims_<identity>.csv` (one row per claim the judge decided) and
`scores_<identity>.csv` (one row per answer) beside rows.csv, where `<identity>`
is the judge identity `eval_results` would store, `<model>-<effort>-<prompt_version>`.
The path is the deployed one: `FaithfulnessWithClaims` under `_score_samples`, the
same client and cap the nightly eval uses, billed to a local ledger that records
nothing. Two judge calls per row.

RESUMABLE. A row already scored by this identity is kept and not paid for again,
so a provider outage mid-run costs the outage's rows and not the set. Delete the
two files to score from scratch. `EVAL_SCORING_CONCURRENCY` bounds the samples in
flight; 2 finished a 40-row set that lost 33 rows to connection errors at the
default.

Spends money. Nothing under tests/unit imports this file.
"""

from __future__ import annotations

import asyncio
import csv
import os
import pathlib
import sys

os.environ.setdefault("ENVIRONMENT", "development")

from app.core.model_client import LedgerContext, route_for  # noqa: E402
from app.domain.judge_identity import JUDGE_PROMPT_VERSION, JudgeIdentity  # noqa: E402
from app.services.eval_service import (  # noqa: E402
    CLAIMS_COLUMN,
    _build_instructor_llm,
    _score_samples,
)
from app.services.faithfulness_metric import FaithfulnessWithClaims  # noqa: E402

CLAIM_COLUMNS = ["scenario_id", "position", "statement", "supported", "reason"]
SCORE_COLUMNS = ["scenario_id", "dimension", "verdict", "score", "claims"]
GATE = 0.80


class _Sample:
    """The four attributes `_score_samples` reads, off one rows.csv row."""

    def __init__(self, row: dict) -> None:
        self.user_input = row["resolved_question"] or row["question"]
        self.reference = row["reference"]
        self.response = row["response"]
        self.retrieved_contexts = [row["retrieved_contexts"]]


def _read(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write(path: pathlib.Path, columns: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main(argv: list[str]) -> int:
    opts = dict(zip(argv[::2], argv[1::2]))
    if "--benchmark" not in opts or not os.environ.get("OPENAI_API_KEY"):
        print(__doc__)
        return 2
    bench = pathlib.Path(opts["--benchmark"])
    # THE RAGAS JUDGE'S OWN IDENTITY, not `judge_identity_for("faithfulness")`,
    # which names the grounding rule since ADR 0015. This script still scores
    # with the judge so the benchmark keeps a judge column beside the rule's.
    route = route_for("judge_faithfulness")
    if route.reasoning_effort is None:
        raise SystemExit("the faithfulness judge route names no effort; nothing to name the files after")
    identity = JudgeIdentity(model=route.model, reasoning_effort=route.reasoning_effort, prompt_version=JUDGE_PROMPT_VERSION)
    slug = f"{identity.model}-{identity.reasoning_effort}-{identity.prompt_version}".replace("/", "_")
    claims_path, scores_path = bench / f"claims_{slug}.csv", bench / f"scores_{slug}.csv"

    all_rows = _read(bench / "rows.csv")
    kept_scores = [r for r in _read(scores_path) if r["score"] != ""]
    done = {r["scenario_id"] for r in kept_scores}
    kept_claims = [r for r in _read(claims_path) if r["scenario_id"] in done]
    todo = [r for r in all_rows if r["scenario_id"] not in done]
    print(f"judge {slug}: {len(done)} already scored, judging {len(todo)}")

    calls: list = []
    llm = _build_instructor_llm(
        "judge_faithfulness", LedgerContext(tenant_id="benchmark-local", recorder=calls.append)
    )
    scored = asyncio.run(
        _score_samples([("faithfulness", FaithfulnessWithClaims(llm=llm))], [_Sample(r) for r in todo])
    )

    claims_out, scores_out = list(kept_claims), list(kept_scores)
    for row, s in zip(todo, scored, strict=True):
        score = s.get("faithfulness")
        claims = s.get(CLAIMS_COLUMN) or []
        scores_out.append({
            "scenario_id": row["scenario_id"],
            "dimension": "faithfulness",
            "verdict": "" if score is None else ("pass" if score >= GATE else "fail"),
            "score": "" if score is None else score,
            "claims": len(claims),
        })
        for i, c in enumerate(claims):
            claims_out.append({
                "scenario_id": row["scenario_id"],
                "position": i,
                "statement": c["statement"],
                "supported": str(bool(c["supported"])).lower(),
                "reason": c["reason"],
            })
    _write(claims_path, CLAIM_COLUMNS, claims_out)
    _write(scores_path, SCORE_COLUMNS, scores_out)

    unscored = [o["scenario_id"][:8] for o in scores_out if o["score"] == ""]
    print(f"rows {len(all_rows)}, scored {len(all_rows) - len(unscored)}, unscored {unscored}, claims {len(claims_out)}")
    print(f"judge calls this run: {len(calls)}")
    return 0 if not unscored else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
