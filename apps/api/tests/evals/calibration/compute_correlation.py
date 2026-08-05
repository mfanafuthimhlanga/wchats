"""
compute_correlation.py — Calibrate the LLM judge against human scores.

Reads human_scores.csv (scenario_id, dimension, human_score), loads the
corresponding recorded response from responses/, calls the judge, then
computes Spearman rank correlation between judge scores and human scores.

Target: Spearman >= 0.75 (AI-SPEC.md §5.2) before trusting automated
judge results at scale.

Usage:
    python apps/api/tests/evals/calibration/compute_correlation.py
    python apps/api/tests/evals/calibration/compute_correlation.py --check

    --check reports readiness only. It touches no network and no API key: it
    says which inputs the harness has and which it is missing, which is the
    question worth asking before spending a judge call per row.

WHOSE COLUMN human_score IS
    The owner's, and no one else's. Every cell ships empty by design. An
    agent-filled calibration set would silently destroy the only instrument
    that can say whether ANY judge in this system is trustworthy — the
    Gatekeeper, the Auditor, the Strategist, classify_severity, and the Actor
    gate that runs synchronously before money moves. Nothing in this file
    writes to human_scores.csv; it is opened for reading only, and
    tests/unit/test_calibration_harness.py pins both halves of that.

WHY THE EXIT CODES CHANGED (audit D7)
    The shipped script exited 0 both when the judge was calibrated and when
    nobody had scored anything yet, calling the second case "informational".
    In CI, in a checklist, or in a summary, exit 0 reads as success — so an
    instrument that had never been given a single label reported the same
    thing as one that had passed. Missing data is never passing data. An
    unscored file now exits EXIT_NOT_CALIBRATED_YET, which is neither pass nor
    fail but is distinguishable from both.

Requirements:
    - responses/ populated via capture_responses.py (needs a live, ingested
      agent and AGENT_E2E_ENABLED=1 — see --check for what is missing)
    - human_scores.csv has the human_score column filled by a human (1-5)
    - ANTHROPIC_API_KEY set in environment

Exit codes:
    0 (EXIT_CALIBRATED)         — correlation >= 0.75 over >= MIN_PAIRS pairs
    1 (EXIT_NOT_CALIBRATED)     — correlation < 0.75; judge not calibrated
    2 (EXIT_SETUP_ERROR)        — missing files, unusable rows, other setup error
    3 (EXIT_NOT_CALIBRATED_YET) — no human scores, or fewer than MIN_PAIRS
                                  usable pairs. NOT a pass and NOT a failure of
                                  the judge: the measurement has not been made.
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

# Spearman over two points is not a correlation, it is a line through two
# points. spearman() already returns nan below three; this names the same floor
# so the status machinery can say "not calibrated yet" rather than "nan".
MIN_PAIRS = 3

# ---------------------------------------------------------------------------
# Exit codes and the statuses they encode
# ---------------------------------------------------------------------------
# Four outcomes, four codes, because three of them used to be 0. The status
# strings are what a caller (a test, a checklist, a future CI job) should
# branch on; the exit codes are the same information for a shell.

EXIT_CALIBRATED = 0
EXIT_NOT_CALIBRATED = 1
EXIT_SETUP_ERROR = 2
EXIT_NOT_CALIBRATED_YET = 3

STATUS_CALIBRATED = "calibrated"
STATUS_NOT_CALIBRATED = "not_calibrated"
STATUS_SETUP_ERROR = "setup_error"
STATUS_NOT_CALIBRATED_YET = "not_calibrated_yet"

EXIT_CODE_FOR_STATUS: dict[str, int] = {
    STATUS_CALIBRATED: EXIT_CALIBRATED,
    STATUS_NOT_CALIBRATED: EXIT_NOT_CALIBRATED,
    STATUS_SETUP_ERROR: EXIT_SETUP_ERROR,
    STATUS_NOT_CALIBRATED_YET: EXIT_NOT_CALIBRATED_YET,
}

# The one status a reader may treat as "this judge may be trusted at scale".
# Everything else, including STATUS_NOT_CALIBRATED_YET, means it may not.
TRUSTWORTHY_STATUS = STATUS_CALIBRATED


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
    if n < MIN_PAIRS:
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

def read_human_score_rows(path: pathlib.Path | None = None) -> dict:
    """Read human_scores.csv and report (attempted, valid) over its rows.

    Opened read-only. This function is the only thing in the harness that
    touches the file at all, and it never writes it — see the module docstring.

    `attempted` is every data row present. `valid` is the subset carrying a
    human score this harness can use, and it is the denominator: a correlation
    computed over `attempted` while nine of ten cells are empty would describe
    a calibration nobody performed.

    Args:
        path: Override for tests. Defaults to HUMAN_SCORES_CSV.

    Returns:
        {"attempted", "valid", "rows", "unusable", "missing_file"} where
        `rows` are the usable rows and `unusable` names each rejected row with
        its reason (never silently dropped).
    """
    csv_path = path or HUMAN_SCORES_CSV
    if not csv_path.exists():
        return {
            "attempted": 0,
            "valid": 0,
            "rows": [],
            "unusable": [],
            "missing_file": True,
        }

    rows: list[dict] = []
    unusable: list[str] = []
    attempted = 0

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            attempted += 1
            scenario_id = (row.get("scenario_id") or "").strip()
            dimension = (row.get("dimension") or "").strip()
            score_str = (row.get("human_score") or "").strip()

            if not score_str:
                unusable.append(f"{scenario_id}/{dimension}: human_score not filled in yet")
                continue
            try:
                score = int(score_str)
            except ValueError:
                unusable.append(f"{scenario_id}/{dimension}: non-integer human_score {score_str!r}")
                continue
            if not 1 <= score <= 5:
                unusable.append(f"{scenario_id}/{dimension}: human_score {score} outside 1-5")
                continue

            rows.append({
                "scenario_id": scenario_id,
                "dimension": dimension,
                "human_score": score,
                "notes": (row.get("notes") or "").strip(),
            })

    return {
        "attempted": attempted,
        "valid": len(rows),
        "rows": rows,
        "unusable": unusable,
        "missing_file": False,
    }


def load_human_scores() -> list[dict]:
    """Back-compatible accessor: the usable rows only.

    Kept because it was the shipped public name. New code should call
    read_human_score_rows() and read the denominator alongside the rows.
    """
    return read_human_score_rows()["rows"]


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
# Readiness — every input except the one that is the owner's
# ---------------------------------------------------------------------------

def readiness() -> dict:
    """Report which calibration inputs exist and which are missing.

    Answers the question that is actually blocking: not "is the judge
    calibrated" (it cannot be, yet) but "what is stopping anyone from finding
    out". Every check here is local — no network, no ANTHROPIC_API_KEY, no
    judge call — so it is safe to run anywhere, including a machine with none
    of the runtime services.

    `human_scores_valid` is reported beside `human_scores_attempted` rather than
    as a bare count, and `blocking` never says "the human scores are missing" as
    though that were a defect to fix in code: filling them is the owner's step
    and an agent must never perform it.

    Returns:
        A dict with per-input counts, the list of missing response files, any
        row referencing a scenario or judge dimension that does not exist, and
        `ready_to_calibrate` — True only when a judge run could actually produce
        MIN_PAIRS usable pairs.
    """
    from tests.evals.judge import JUDGE_RUBRICS  # noqa: PLC0415  (avoid import at CLI parse time)

    scenario_files = sorted(SCENARIOS_DIR.glob("S-*.json"))
    scenario_ids = {p.name.split("_")[0] for p in scenario_files}

    parsed = read_human_score_rows()
    referenced = [(r["scenario_id"], r["dimension"]) for r in parsed["rows"]]

    # Every row in the file, scored or not — a typo in an unscored row is worth
    # catching before the owner spends an evening grading against it.
    all_referenced_ids: list[str] = []
    all_referenced_dims: list[str] = []
    if not parsed["missing_file"]:
        with HUMAN_SCORES_CSV.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                all_referenced_ids.append((row.get("scenario_id") or "").strip())
                all_referenced_dims.append((row.get("dimension") or "").strip())

    unknown_scenarios = sorted({s for s in all_referenced_ids if s and s not in scenario_ids})
    unknown_dimensions = sorted({d for d in all_referenced_dims if d and d not in JUDGE_RUBRICS})

    responses_present = sorted(p.stem for p in RESPONSES_DIR.glob("S-*.json")) if RESPONSES_DIR.exists() else []
    responses_missing = sorted({s for s in all_referenced_ids if s and s not in set(responses_present)})

    blocking: list[str] = []
    if parsed["missing_file"]:
        blocking.append(f"{HUMAN_SCORES_CSV.name} does not exist")
    if not RESPONSES_DIR.exists():
        blocking.append(
            f"{RESPONSES_DIR.name}/ has never been captured - run capture_responses.py "
            "against a live, ingested agent (AGENT_E2E_ENABLED=1, AGENT_ID, API_KEY)"
        )
    elif responses_missing:
        blocking.append(
            f"{len(responses_missing)} recorded response(s) missing: {', '.join(responses_missing)}"
        )
    if unknown_scenarios:
        blocking.append(f"human_scores.csv references unknown scenario(s): {', '.join(unknown_scenarios)}")
    if unknown_dimensions:
        blocking.append(f"human_scores.csv references unknown judge dimension(s): {', '.join(unknown_dimensions)}")

    awaiting_owner = parsed["valid"] < MIN_PAIRS

    return {
        "scenarios_present": len(scenario_files),
        "human_scores_attempted": parsed["attempted"],
        "human_scores_valid": parsed["valid"],
        "human_scores_unusable": parsed["unusable"],
        "responses_present": len(responses_present),
        "responses_missing": responses_missing,
        "unknown_scenarios": unknown_scenarios,
        "unknown_dimensions": unknown_dimensions,
        "referenced_pairs": referenced,
        "blocking": blocking,
        "awaiting_owner_scores": awaiting_owner,
        "ready_to_calibrate": not blocking and not awaiting_owner,
    }


def print_readiness(report: dict) -> int:
    """Render a readiness report and return the exit code it implies."""
    print("Calibration readiness (no judge calls, no network)\n")
    print(f"  scenarios on disk          : {report['scenarios_present']}")
    print(
        f"  human_scores.csv rows      : {report['human_scores_valid']} scored "
        f"/ {report['human_scores_attempted']} present"
    )
    print(f"  recorded responses on disk : {report['responses_present']}")

    if report["unknown_scenarios"]:
        print(f"  unknown scenario ids       : {', '.join(report['unknown_scenarios'])}")
    if report["unknown_dimensions"]:
        print(f"  unknown judge dimensions   : {', '.join(report['unknown_dimensions'])}")

    print()
    if report["blocking"]:
        print("Blocking (machine-fixable):")
        for item in report["blocking"]:
            print(f"  - {item}")
        print()
    if report["awaiting_owner_scores"]:
        print(
            f"Awaiting the owner: {report['human_scores_valid']} of the {MIN_PAIRS} human "
            "scores needed are filled in.\n"
            "  That column is yours. Do not let anything - or anyone - fill it for you:\n"
            "  a judge calibrated against model-written labels measures agreement with\n"
            "  itself, which is exactly the tautology this file exists to detect.\n"
        )

    if report["ready_to_calibrate"]:
        print("READY - every input is present; run without --check to compute the correlation.")
        return EXIT_CALIBRATED

    print("NOT READY - the correlation cannot be computed yet, so the judge is UNCALIBRATED.")
    print("Uncalibrated is not 'passing'. No automated verdict in this system has been")
    print("validated against a human until this reports READY and the run reports PASS.")
    return EXIT_NOT_CALIBRATED_YET


# ---------------------------------------------------------------------------
# The correlation run
# ---------------------------------------------------------------------------

def compute_correlation(judge_fn) -> dict:
    """Score every human-labelled row with `judge_fn` and correlate.

    Pure of sys.exit and of print formatting decisions so the status can be
    asserted by a test without driving a CLI. `judge_fn` is injected for the
    same reason — the shipped code reached into tests.evals.judge inside main().

    Returns:
        {"status", "rho", "pairs", "attempted", "valid", "errors", "table"}.
        `rho` is None whenever status is anything but calibrated/not_calibrated,
        so a caller can never read a number out of a run that did not make the
        measurement.
    """
    parsed = read_human_score_rows()

    if parsed["missing_file"]:
        return {
            "status": STATUS_SETUP_ERROR,
            "rho": None,
            "pairs": 0,
            "attempted": 0,
            "valid": 0,
            "errors": [f"{HUMAN_SCORES_CSV} not found."],
            "table": [],
        }

    if parsed["valid"] == 0:
        return {
            "status": STATUS_NOT_CALIBRATED_YET,
            "rho": None,
            "pairs": 0,
            "attempted": parsed["attempted"],
            "valid": 0,
            "errors": [],
            "table": [],
        }

    human_scores: list[float] = []
    judge_scores: list[float] = []
    errors: list[str] = []
    table: list[dict] = []

    for row in parsed["rows"]:
        sid = row["scenario_id"]
        dim = row["dimension"]
        h_score = row["human_score"]

        try:
            scenario = load_scenario(sid)
            response = load_response(sid)
        except FileNotFoundError as exc:
            errors.append(str(exc))
            table.append({"scenario_id": sid, "dimension": dim, "human": h_score,
                          "judge": None, "reason": f"ERROR: {exc}"})
            continue

        verdict = judge_fn(dim, build_transcript(scenario, response),
                           response.get("tool_calls_log", []))
        j_score = verdict["score"]

        # The judge wrapper returns score=0 with verdict="ERROR" on any
        # exception. Zero is not a low score, it is the absence of one, and
        # correlating it would move rho with the failure rate of the API.
        if j_score == 0:
            errors.append(f"{sid}/{dim}: judge returned ERROR — {verdict['reason']}")
            table.append({"scenario_id": sid, "dimension": dim, "human": h_score,
                          "judge": None, "reason": verdict["reason"]})
            continue

        human_scores.append(float(h_score))
        judge_scores.append(float(j_score))
        table.append({"scenario_id": sid, "dimension": dim, "human": h_score,
                      "judge": j_score, "reason": verdict["reason"]})

    pairs = len(human_scores)
    if pairs < MIN_PAIRS:
        return {
            "status": STATUS_NOT_CALIBRATED_YET,
            "rho": None,
            "pairs": pairs,
            "attempted": parsed["attempted"],
            "valid": parsed["valid"],
            "errors": errors,
            "table": table,
        }

    rho = spearman(human_scores, judge_scores)
    if rho != rho:  # NaN — zero variance on one side; no ranking to correlate
        return {
            "status": STATUS_NOT_CALIBRATED_YET,
            "rho": None,
            "pairs": pairs,
            "attempted": parsed["attempted"],
            "valid": parsed["valid"],
            "errors": errors + [
                "Spearman is undefined (no variance in one of the two score sets) - "
                "score a wider spread of scenarios."
            ],
            "table": table,
        }

    return {
        "status": STATUS_CALIBRATED if rho >= THRESHOLD else STATUS_NOT_CALIBRATED,
        "rho": rho,
        "pairs": pairs,
        "attempted": parsed["attempted"],
        "valid": parsed["valid"],
        "errors": errors,
        "table": table,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the exit code rather than calling sys.exit."""
    args = sys.argv[1:] if argv is None else argv

    if "--check" in args:
        return print_readiness(readiness())

    from tests.evals.judge import judge  # noqa: PLC0415

    result = compute_correlation(judge)

    print(
        f"Calibration run - {result['valid']} scored / {result['attempted']} rows present\n"
    )
    if result["table"]:
        print(f"{'Scenario':<10}  {'Dimension':<30}  {'Human':>6}  {'Judge':>6}  {'Diff':>5}  Reason")
        print("-" * 100)
        for entry in result["table"]:
            j = entry["judge"]
            diff = f"{j - entry['human']:+d}" if j is not None else "—"
            j_txt = str(j) if j is not None else "ERR"
            print(
                f"{entry['scenario_id']:<10}  {entry['dimension']:<30}  "
                f"{entry['human']:>6}  {j_txt:>6}  {diff:>5}  {entry['reason'][:60]}"
            )
        print()

    if result["errors"]:
        print(f"Errors ({len(result['errors'])}):")
        for e in result["errors"]:
            print(f"  - {e}")
        print()

    status = result["status"]

    if status == STATUS_SETUP_ERROR:
        print("SETUP ERROR - the harness could not read its own inputs.")
    elif status == STATUS_NOT_CALIBRATED_YET:
        print(
            f"NOT CALIBRATED YET - {result['pairs']} usable pair(s), {MIN_PAIRS} needed.\n"
            "This is neither a pass nor a judge failure: the measurement has not been made.\n"
            "Run with --check to see exactly which input is missing. If it is the\n"
            "human_score column, that one is the owner's and must be filled by a human."
        )
    elif status == STATUS_CALIBRATED:
        print(f"Spearman rho = {result['rho']:.3f}  (n={result['pairs']}, threshold={THRESHOLD})")
        print(
            f"PASS - judge is calibrated (rho {result['rho']:.3f} >= {THRESHOLD}). "
            "Safe to trust automated results."
        )
    else:
        print(f"Spearman rho = {result['rho']:.3f}  (n={result['pairs']}, threshold={THRESHOLD})")
        print(
            f"FAIL - judge is NOT calibrated (rho {result['rho']:.3f} < {THRESHOLD}).\n"
            "Review rubrics in judge.py and adjust JUDGE_RUBRICS until rho >= 0.75."
        )

    return EXIT_CODE_FOR_STATUS[status]


if __name__ == "__main__":
    sys.exit(main())
