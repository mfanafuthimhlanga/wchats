"""
compute_correlation.py — Calibrate the LLM judge against human scores.

Reads human_scores.csv (scenario_id, dimension, human_score), loads the
corresponding recorded response from responses/, calls the judge, then
computes Spearman rank correlation between judge scores and human scores.

Target: Spearman ≥ 0.75 (AI-SPEC.md §5.2) before trusting automated
judge results at scale.

Usage:
    python apps/api/tests/evals/calibration/compute_correlation.py

Requirements:
    - responses/ populated via capture_responses.py
    - human_scores.csv has human_score column filled (1–5 integer scale)
    - ANTHROPIC_API_KEY set in environment

Exit codes:
    0 — correlation ≥ 0.75, or no scored rows yet (informational)
    1 — correlation < 0.75 (judge not calibrated; review rubrics)
    2 — missing responses/ files or other setup error
"""

from __future__ import annotations

import csv
import json
import pathlib
import sys

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CALIBRATION_DIR = pathlib.Path(__file__).parent
EVALS_DIR = CALIBRATION_DIR.parent
SCENARIOS_DIR = EVALS_DIR / "scenarios"
RESPONSES_DIR = EVALS_DIR / "responses"
HUMAN_SCORES_CSV = CALIBRATION_DIR / "human_scores.csv"

THRESHOLD = 0.75  # AI-SPEC.md §5.2


# ---------------------------------------------------------------------------
# Spearman rank correlation (stdlib only — no scipy dependency)
# ---------------------------------------------------------------------------

def _rank_with_ties(vals: list[float]) -> list[float]:
    """Return average ranks for each value, handling ties."""
    n = len(vals)
    sorted_pairs = sorted(enumerate(vals), key=lambda x: x[1])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n and sorted_pairs[j][1] == sorted_pairs[i][1]:
            j += 1
        avg_rank = (i + j + 1) / 2.0
        for k in range(i, j):
            ranks[sorted_pairs[k][0]] = avg_rank
        i = j
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float:
    """Spearman rank correlation coefficient with average-rank tie handling."""
    n = len(xs)
    if n < 3:
        return float("nan")
    rx = _rank_with_ties(xs)
    ry = _rank_with_ties(ys)
    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n
    num = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    den_x = sum((rx[i] - mean_rx) ** 2 for i in range(n)) ** 0.5
    den_y = sum((ry[i] - mean_ry) ** 2 for i in range(n)) ** 0.5
    if den_x == 0 or den_y == 0:
        return float("nan")
    return num / (den_x * den_y)


# ---------------------------------------------------------------------------
# CSV reader
# ---------------------------------------------------------------------------

def load_human_scores() -> list[dict]:
    """Read human_scores.csv; return rows where human_score is filled."""
    if not HUMAN_SCORES_CSV.exists():
        print(f"ERROR: {HUMAN_SCORES_CSV} not found.")
        sys.exit(2)

    rows = []
    with HUMAN_SCORES_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            score_str = row.get("human_score", "").strip()
            if not score_str:
                continue
            try:
                score = int(score_str)
            except ValueError:
                print(f"WARNING: non-integer human_score for {row['scenario_id']} — skipping")
                continue
            if not 1 <= score <= 5:
                print(f"WARNING: human_score {score} out of 1–5 range for {row['scenario_id']} — skipping")
                continue
            rows.append({
                "scenario_id": row["scenario_id"].strip(),
                "dimension": row["dimension"].strip(),
                "human_score": score,
                "notes": row.get("notes", "").strip(),
            })
    return rows


# ---------------------------------------------------------------------------
# Scenario + response loader
# ---------------------------------------------------------------------------

def load_scenario(scenario_id: str) -> dict:
    pattern = f"{scenario_id}_*.json"
    matches = list(SCENARIOS_DIR.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"No scenario file matching {pattern} in {SCENARIOS_DIR}")
    return json.loads(matches[0].read_text(encoding="utf-8"))


def load_response(scenario_id: str) -> dict:
    path = RESPONSES_DIR / f"{scenario_id}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No recorded response for {scenario_id}. "
            "Run capture_responses.py first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def build_transcript(scenario: dict, response: dict) -> str:
    parts = []
    for turn in scenario.get("turns", []):
        role = turn.get("role", "user").upper()
        parts.append(f"{role}: {turn.get('message', '')}")
    parts.append(f"AGENT: {response.get('response_text', '')}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    from tests.evals.judge import judge  # noqa: PLC0415

    scored_rows = load_human_scores()

    if not scored_rows:
        print(
            "No scored rows in human_scores.csv.\n"
            "Fill the human_score column (1–5) for the scenarios you reviewed,\n"
            "then re-run this script."
        )
        sys.exit(0)

    print(f"Calibration run — {len(scored_rows)} human-scored pairs\n")
    print(f"{'Scenario':<10}  {'Dimension':<30}  {'Human':>6}  {'Judge':>6}  {'Diff':>5}  Reason")
    print("-" * 100)

    human_scores: list[float] = []
    judge_scores: list[float] = []
    errors: list[str] = []

    for row in scored_rows:
        sid = row["scenario_id"]
        dim = row["dimension"]
        h_score = row["human_score"]

        try:
            scenario = load_scenario(sid)
            response = load_response(sid)
        except FileNotFoundError as exc:
            errors.append(str(exc))
            print(f"{sid:<10}  {dim:<30}  {h_score:>6}  {'N/A':>6}  {'—':>5}  ERROR: {exc}")
            continue

        transcript = build_transcript(scenario, response)
        tool_calls_log = response.get("tool_calls_log", [])

        verdict = judge(dim, transcript, tool_calls_log)
        j_score = verdict["score"]

        if j_score == 0:
            errors.append(f"{sid}/{dim}: judge returned ERROR — {verdict['reason']}")
            print(f"{sid:<10}  {dim:<30}  {h_score:>6}  {'ERR':>6}  {'—':>5}  {verdict['reason'][:60]}")
            continue

        diff = j_score - h_score
        human_scores.append(float(h_score))
        judge_scores.append(float(j_score))
        print(f"{sid:<10}  {dim:<30}  {h_score:>6}  {j_score:>6}  {diff:>+5}  {verdict['reason'][:60]}")

    print()

    if errors:
        print(f"Errors ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")
        print()

    n = len(human_scores)
    if n < 3:
        print(
            f"Only {n} valid pair(s) — need at least 3 to compute correlation.\n"
            "Fill more rows in human_scores.csv and re-run."
        )
        sys.exit(0)

    rho = spearman(human_scores, judge_scores)

    print(f"Spearman ρ = {rho:.3f}  (n={n}, threshold={THRESHOLD})")

    if rho >= THRESHOLD:
        print(f"PASS — judge is calibrated (ρ {rho:.3f} ≥ {THRESHOLD}). Safe to trust automated results.")
        sys.exit(0)
    else:
        print(
            f"FAIL — judge is NOT calibrated (ρ {rho:.3f} < {THRESHOLD}).\n"
            "Review rubrics in judge.py and adjust JUDGE_RUBRICS until ρ ≥ 0.75."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
