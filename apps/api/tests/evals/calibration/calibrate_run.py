"""
calibrate_run.py - Calibrate the platform's Judge against the owner's labels on
one eval run, reading verdicts off the run's stored rows instead of asking a
judge.

`compute_correlation.py` scores S-*.json responses with a judge of its own, over
a rubric the platform never runs, so no artifact it writes can name the Judge
`run_eval_suite` uses (#58). This script takes an eval run the platform already
scored: `eval_samples` (0027) holds the four strings Ragas was handed and
`eval_results` (0023) holds the verdict and the Judge identity per (scenario,
metric). The owner labels the text; the verdicts are looked up, never
recomputed; the statistics, the floors and the artifact are the ones
`compute_correlation.py` already has.

Usage:
    python apps/api/tests/evals/calibration/calibrate_run.py --sheet <run_id>
    python apps/api/tests/evals/calibration/calibrate_run.py --second-pass <run_id>
    python apps/api/tests/evals/calibration/calibrate_run.py --score <run_id>

    --sheet        writes runs/<run_id>/human_scores.csv: one row per (scenario,
                   gated metric) with the question, the agent's answer, the
                   contexts, the reference and an EMPTY human_verdict. No judge
                   verdict is on the sheet: the label has to be blind to it.
                   Refuses to overwrite a sheet that exists.
    --second-pass  writes runs/<run_id>/human_scores_pass2.csv from the finished
                   first pass, shuffled, verdict column empty. The same refusals
                   as `compute_correlation.py --emit-second-pass`.
    --score        reads both sheets, joins each label to the run's stored
                   verdict, and writes calibration.json where the deploy gate
                   reads it. Spends nothing.

Environment:
    CALIBRATION_TENANT_DSN   the tenant database the run lives in. Read for
                             --sheet and --score; --second-pass is local.

Exit codes are `compute_correlation.py`'s: 0 calibrated, 1 not calibrated, 2
setup error, 3 not calibrated yet, 5 a sheet was written.
"""

from __future__ import annotations

import csv
import json
import os
import pathlib
import sys
from collections.abc import Callable, Mapping, Sequence

if __name__ == "__main__":  # pragma: no cover - script entry; tests import
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from tests.evals.calibration import compute_correlation as cc  # noqa: E402

RUNS_DIR = cc.CALIBRATION_DIR / "runs"
SHEET_NAME = "human_scores.csv"
PASS2_NAME = "human_scores_pass2.csv"
TENANT_DSN_ENV = "CALIBRATION_TENANT_DSN"

#: The two metrics a deploy is gated on, and so the two a human labels. The
#: other two carry no verdict on their rows (`threshold_for` returns None), so
#: there is nothing to agree or disagree with.
GATED_METRICS: tuple[str, ...] = ("faithfulness", "answer_relevancy")

#: What the owner reads on the sheet. `human_verdict` and `human_score` are the
#: columns `read_human_score_rows` reads; the rest are for reading only.
SHEET_COLUMNS = (
    "scenario_id",
    "dimension",
    "human_verdict",
    "human_score",
    "notes",
    "dataset",
    "question",
    "response",
    "retrieved_contexts",
    "reference",
    # Added by #227 PR 2. Empty for the single-turn scenarios that are the whole
    # corpus before it, so an older run's sheet reads exactly as it did.
    "turns",
    "resolved_question",
)

#: The judge's score on the 1-5 scale `compute_correlation` reports Spearman
#: over. A stored verdict is binary, so the two ends of the scale are the honest
#: rendering; 0 is `compute_correlation`'s "no score", which an absent verdict is.
STORED_PASS_SCORE = 5
STORED_FAIL_SCORE = 1

_SAMPLES_SQL = """
    SELECT scenario_id, dataset, user_input, response, retrieved_contexts, reference,
           turns, resolved_question
    FROM eval_samples
    WHERE eval_run_id = %(run_id)s::uuid
    ORDER BY scenario_id
"""

_VERDICTS_SQL = """
    SELECT scenario_id, metric, score, binary_verdict, judge_identity
    FROM eval_results
    WHERE eval_run_id = %(run_id)s::uuid
"""


def run_dir(run_id: str) -> pathlib.Path:
    return RUNS_DIR / run_id


def tenant_dsn() -> str:
    dsn = os.environ.get(TENANT_DSN_ENV, "").strip()
    if not dsn:
        raise SystemExit(
            f"{TENANT_DSN_ENV} is not in os.environ. It names the tenant database the "
            "run lives in; export it in this shell (.env is not read here)."
        )
    return dsn


# ---------------------------------------------------------------------------
# Reading the run
# ---------------------------------------------------------------------------


def fetch_samples(run_id: str, dsn: str) -> list[dict]:
    """The rows `write_eval_samples` left for this run, one per scored scenario."""
    import psycopg2  # noqa: PLC0415

    conn = psycopg2.connect(dsn, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(_SAMPLES_SQL, {"run_id": run_id})
            rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {
            "scenario_id": str(sid),
            "dataset": dataset,
            "question": user_input,
            "response": response,
            "retrieved_contexts": list(contexts or []),
            "reference": reference,
            # The conversation the question was asked in, and the question
            # rewritten to stand alone in it (tenant 0028, #227). A labeller shown
            # a follow-up with its binding stripped off is being asked to judge an
            # answer to a question nobody asked.
            "turns": list(turns or []),
            "resolved_question": resolved_question or "",
        }
        for sid, dataset, user_input, response, contexts, reference, turns, resolved_question in rows
    ]


def _rendered_turns(turns) -> str:
    """The conversation as one cell, oldest first, one message per line.

    Empty for a single-turn scenario, which is every row the eval scored before
    #227, so an older run's sheet reads exactly as it did.
    """
    if not isinstance(turns, list):
        return ""
    lines = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        lines.append(f"{str(turn.get('role', '')).upper()}: {turn.get('content', '')}")
    return "\n".join(lines)


def fetch_verdicts(run_id: str, dsn: str) -> dict[tuple[str, str], dict]:
    """The run's stored decisions, keyed by (scenario_id, metric)."""
    import psycopg2  # noqa: PLC0415

    conn = psycopg2.connect(dsn, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(_VERDICTS_SQL, {"run_id": run_id})
            rows = cur.fetchall()
    finally:
        conn.close()
    return {
        (str(sid), str(metric)): {
            "score": None if score is None else float(score),
            "binary_verdict": verdict,
            "judge_identity": identity,
        }
        for sid, metric, score, verdict, identity in rows
    }


# ---------------------------------------------------------------------------
# The sheet
# ---------------------------------------------------------------------------


def write_sheet(samples: Sequence[Mapping], path: pathlib.Path) -> tuple[int, list[str]]:
    """One row per (scenario, gated metric), verdict empty. Returns (exit, messages).

    Refuses to overwrite: a sheet that exists may hold labels only the owner can
    produce. Refuses an empty run: nothing to label.
    """
    if path.exists():
        return cc.EXIT_SETUP_ERROR, [
            f"{path} already exists and was NOT overwritten. It may hold labels only "
            "you can produce; delete it by hand if you really mean to start over."
        ]
    if not samples:
        return cc.EXIT_SETUP_ERROR, [
            "the run has no eval_samples rows: either it predates migration 0027 or "
            "it scored nothing. Pick a run that finished after 0027 landed."
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=SHEET_COLUMNS)
        writer.writeheader()
        for sample in samples:
            for metric in GATED_METRICS:
                writer.writerow(
                    {
                        "scenario_id": sample["scenario_id"],
                        "dimension": metric,
                        "human_verdict": "",
                        "human_score": "",
                        "notes": "",
                        "dataset": sample.get("dataset") or "",
                        "question": sample["question"],
                        "response": sample["response"],
                        "retrieved_contexts": "\n\n".join(sample["retrieved_contexts"]),
                        "reference": sample["reference"],
                        "turns": _rendered_turns(sample.get("turns")),
                        "resolved_question": sample.get("resolved_question") or "",
                    }
                )
    rows = len(samples) * len(GATED_METRICS)
    return cc.EXIT_SECOND_PASS_EMITTED, [
        f"Wrote {path}: {rows} row(s), {len(samples)} scenario(s) x "
        f"{len(GATED_METRICS)} metric(s), verdict column empty.",
        "Fill human_verdict with pass or fail per row. faithfulness asks whether the",
        "response is supported by the retrieved contexts; answer_relevancy asks whether",
        "it answers the question. The Judge's own verdicts are not on this sheet.",
    ]


# ---------------------------------------------------------------------------
# The judge adapter: a lookup, not a call
# ---------------------------------------------------------------------------


def stored_judge(verdicts: Mapping[tuple[str, str], Mapping]) -> Callable[[str, str], dict]:
    """A `judge_fn` over the run's rows: (scenario_id, dimension) -> verdict dict.

    The dict is the shape `tests.evals.judge.judge` returns, so the row report
    and the artifact read it the same way. `judge_identity` is rebuilt from the
    row's JSON into the frozen type the artifact expects; a row whose identity is
    absent or malformed reports None, and `judge_identity_for_run` then refuses
    to name a Judge for the figure.
    """
    from app.domain.judge_identity import InvalidJudgeIdentity, JudgeIdentity  # noqa: PLC0415

    def _identity(raw) -> JudgeIdentity | None:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                return None
        if not isinstance(raw, Mapping):
            return None
        try:
            return JudgeIdentity(
                model=str(raw.get("model", "")),
                reasoning_effort=str(raw.get("reasoning_effort", "")),
                prompt_version=str(raw.get("prompt_version", "")),
            )
        except InvalidJudgeIdentity:
            return None

    def judge(scenario_id: str, dimension: str) -> dict:
        row = verdicts.get((scenario_id, dimension))
        if row is None:
            return {
                "dimension": dimension,
                "verdict": "ERROR",
                "score": 0,
                "reason": "no eval_results row for this scenario and metric",
                "judge_identity": None,
            }
        passed = row.get("binary_verdict")
        if passed is None:
            return {
                "dimension": dimension,
                "verdict": "ERROR",
                "score": 0,
                "reason": "the run stored no verdict on this row (judge outage or no threshold)",
                "judge_identity": _identity(row.get("judge_identity")),
            }
        score = row.get("score")
        return {
            "dimension": dimension,
            "verdict": "PASS" if passed else "FAIL",
            "score": STORED_PASS_SCORE if passed else STORED_FAIL_SCORE,
            "reason": f"stored score {score:.3f}" if score is not None else "stored verdict",
            "judge_identity": _identity(row.get("judge_identity")),
        }

    return judge


# ---------------------------------------------------------------------------
# Scoring: the loop, then compute_correlation's own statistics
# ---------------------------------------------------------------------------


def score_run(
    samples: Sequence[Mapping],
    verdicts: Mapping[tuple[str, str], Mapping],
    sheet: pathlib.Path,
    second_pass: pathlib.Path,
) -> dict:
    """Pair every labelled row with its stored verdict and measure agreement.

    Same shape as `compute_correlation`'s loop with three differences stated
    here: the judge is a lookup; a row whose scenario has no sample is an error
    rather than a missing file; and a deflected response is recognised on the
    sample's own text.
    """
    from app.domain.pii_firewall import PII_DEFLECTION  # noqa: PLC0415

    parsed = cc.read_human_score_rows(sheet)
    if parsed["missing_file"]:
        return {
            "status": cc.STATUS_SETUP_ERROR,
            "kappa": None, "judge_interval": None, "ceiling_interval": None,
            "difference_interval": None, "gate": None, "matthews": None,
            "cells": None, "rho": None, "scored_pairs": 0, "pairs": 0,
            "pair_rate": None, "attempted": 0, "valid": 0,
            "errors": [f"{sheet} not found. Write it with --sheet first."],
            "table": [],
        }
    if parsed["valid"] == 0:
        return {
            "status": cc.STATUS_NOT_CALIBRATED_YET,
            "kappa": None, "judge_interval": None, "ceiling_interval": None,
            "difference_interval": None, "gate": None, "matthews": None,
            "cells": None, "rho": None, "scored_pairs": 0, "pairs": 0,
            "pair_rate": None, "attempted": parsed["attempted"], "valid": 0,
            "errors": [], "table": [],
        }

    by_scenario = {s["scenario_id"]: s for s in samples}
    judge = stored_judge(verdicts)

    human_scores: list[float] = []
    judge_scores: list[float] = []
    binary_pairs: list[tuple[bool, bool]] = []
    judged_rows: list[tuple[str, str, bool]] = []
    errors: list[str] = []
    table: list[dict] = []

    for row in parsed["rows"]:
        sid, dim = row["scenario_id"], row["dimension"]
        h_score, h_passed = row["human_score"], row["human_passed"]
        entry = {
            "scenario_id": sid, "dimension": dim,
            "human_verdict": row["human_verdict"], "human": h_score,
            "judge_verdict": None, "judge": None, "judge_identity": None,
        }
        sample = by_scenario.get(sid)
        if sample is None:
            errors.append(f"{sid}/{dim}: no eval_samples row for this scenario in the run")
            table.append({**entry, "reason": "ERROR: no sample for this scenario"})
            continue
        if dim not in GATED_METRICS:
            errors.append(f"{sid}/{dim}: not a gated metric; the run stores no verdict for it")
            table.append({**entry, "reason": f"ERROR: {dim} carries no verdict"})
            continue

        verdict = judge(sid, dim)
        entry["judge_identity"] = verdict.get("judge_identity")
        if verdict["score"] == 0:
            errors.append(f"{sid}/{dim}: {verdict['reason']}")
            table.append({**entry, "reason": verdict["reason"]})
            continue
        if (sample.get("response") or "").strip() == PII_DEFLECTION:
            errors.append(
                f"{sid}/{dim}: the stored response is the PII firewall's deflection, so "
                "it is excluded from the agreement matrix."
            )
            table.append({**entry, "reason": "excluded: PII deflection"})
            continue

        binary_pairs.append((h_passed, verdict["verdict"] == "PASS"))
        judged_rows.append((sid, dim, h_passed))
        if h_score is not None:
            human_scores.append(float(h_score))
            judge_scores.append(float(verdict["score"]))
        table.append({
            **entry,
            "judge_verdict": verdict["verdict"], "judge": verdict["score"],
            "reason": verdict["reason"],
        })

    return cc.agreement_result(
        parsed, binary_pairs, judged_rows, human_scores, judge_scores, errors, table,
        second_pass_path=second_pass,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

USAGE = (
    "usage: calibrate_run.py (--sheet | --second-pass | --score) <run_id>",
    "  --sheet        write the labelling sheet for the run",
    "  --second-pass  write the blind second sheet from the finished first",
    "  --score        join the labels to the run's stored verdicts and write calibration.json",
)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] not in ("--sheet", "--second-pass", "--score"):
        for line in USAGE:
            print(line)
        return cc.EXIT_SETUP_ERROR
    flag, run_id = args
    sheet = run_dir(run_id) / SHEET_NAME
    pass2 = run_dir(run_id) / PASS2_NAME

    if flag == "--sheet":
        code, messages = write_sheet(fetch_samples(run_id, tenant_dsn()), sheet)
    elif flag == "--second-pass":
        code, messages = cc.emit_second_pass(pass2, first_pass=sheet)
    else:
        dsn = tenant_dsn()
        result = score_run(fetch_samples(run_id, dsn), fetch_verdicts(run_id, dsn), sheet, pass2)
        cc.write_calibration_artifact(result, cc.CALIBRATION_ARTIFACT_JSON, sheet=sheet)
        return cc.print_run_report(result)
    for message in messages:
        print(message)
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
