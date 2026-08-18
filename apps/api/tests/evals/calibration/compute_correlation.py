"""
compute_correlation.py — Calibrate the LLM judge against human labels.

Reads the calibration sheet (scenario_id, dimension, human_verdict,
human_score, notes), loads run 0 of each recorded response, calls the judge,
and measures agreement.

THE GATE IS COHEN'S KAPPA ON BINARY VERDICTS (BACKLOG 8.2b, owner decision
2026-08-18). Spearman is still computed over whichever rows carry an optional
1-5 score, and still reported, but it no longer decides anything.

    gate       Cohen's kappa >= 0.6 on (human_verdict, judge verdict)
    reported   Matthews correlation, the 2x2 confusion matrix, Spearman rho

WHY THE GATE MOVED, and it supersedes AI-SPEC.md §5.2
    Two defects in one number. The human column was a 1-5 SCALE, and a human
    cannot hold a scale steady: the same quality gets a 3 one hour and a 4 the
    next, so every label carried avoidable noise. And Spearman is NOT
    chance-corrected, so on a mostly-good corpus most of the agreement it
    measures is luck.

    The concrete failure: a judge that returns PASS to every input ranks in
    perfect agreement with any human whose scores happen to rise, so the shipped
    harness reported rho = 1.000 and "safe to trust automated results" over a
    judge that was not reading the response. Kappa subtracts the chance rate and
    refuses it. `test_a_judge_that_passes_everything_is_refused` pins that.

    The confusion matrix is the report card, because each cell prescribes
    something different, and the both-fail cell is the one that stops a team
    tuning a judge when the product is what is broken.

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
    - the calibration sheet has `human_verdict` filled by a human (pass/fail).
      `human_score` (1-5) is optional and feeds the reported Spearman only
    - ANTHROPIC_API_KEY set in environment

Exit codes:
    0 (EXIT_CALIBRATED)         — kappa >= 0.6 over >= MIN_PAIRS pairs
    1 (EXIT_NOT_CALIBRATED)     — kappa < 0.6; the judge is not calibrated
    2 (EXIT_SETUP_ERROR)        — missing files, unusable rows, other setup error
    3 (EXIT_NOT_CALIBRATED_YET) — no human scores, fewer than MIN_PAIRS usable
                                  pairs, or the judge failed too many of its own
                                  calls to have measured the set. NOT a pass and
                                  NOT a failure of the judge: the measurement has
                                  not been made.
    4 (EXIT_READY_TO_CALIBRATE) — `--check` only: every input is present and the
                                  correlation has NOT been computed.

    --check NEVER RETURNS 0. It makes no judge call by design, so it cannot
    establish the one thing exit 0 means. The first version of this fix returned
    EXIT_CALIBRATED from --check on a merely-ready tree, which is audit D7's own
    defect ("exit 0 is success to every reader") reintroduced on the new code
    path: a checklist step keyed on the documented exit code would have recorded
    the judge as calibrated while rho had never been computed and could have
    been -1.0.
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

THRESHOLD = 0.75  # AI-SPEC.md §5.2, Spearman. Now a REPORTED number, not the gate.

# BACKLOG 8.2b, and it supersedes the Spearman gate above (owner decision,
# 2026-08-18: "the spec changes because i pushed back on it").
#
# WHY THE GATE MOVED. Two defects in one number. (a) The human column was a 1-5
# SCALE, and the practice is explicit that a human cannot hold a scale steady:
# the same quality gets a 3 one hour and a 4 the next, so every label carried
# avoidable noise. (b) Spearman is NOT CHANCE-CORRECTED. Two raters agree by
# luck, and on a mostly-good corpus like ours that luck is most of the
# agreement, so a high rho can be produced by a judge that is not tracking the
# human at all.
#
# Cohen's kappa fixes both: it needs only a binary label, and it subtracts the
# rate at which the two would agree by chance.
#
#   below 0.4   the judge is not tracking the human
#   0.6 to 0.8  substantial
#   above 0.8   strong
#
# 0.6 is the floor those bands put on "substantial", and it is a CHOICE rather
# than a measurement: nothing in this repo has ever produced a kappa, so there is
# no observed distribution to set it against. Move it when there is one.
KAPPA_THRESHOLD = 0.6

# Kappa COLLAPSES on imbalanced data: when 95% of responses are good, chance
# agreement is already near certain, so a good judge scores badly. Matthews
# correlation does not have that failure mode, so it is computed alongside and
# reported whenever kappa is undefined or the corpus is lopsided. Reported, never
# gated: swapping the gate to whichever statistic looks better is how a gate
# stops meaning anything.
MATTHEWS_FLOOR = 0.5

#: The two spellings a human may write in `human_verdict`. Anything else is
#: rejected by name rather than coerced, because "ok", "y" and "1" are three
#: different guesses about what the labeller meant.
VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"

# Spearman over two points is not a correlation, it is a line through two
# points. spearman() already returns nan below three; this names the same floor
# so the status machinery can say "not calibrated yet" rather than "nan".
MIN_PAIRS = 3

# The second floor, and the P4 review is why it exists (MIN_PAIRS alone is not
# enough). A judge that returns verdict='ERROR' on seven of ten labelled rows —
# Anthropic 529s, a JSON parse failure, judge.py:170-177's score=0 on any
# exception — still leaves three pairs, and if those three happen to rank in
# agreement the harness reported rho=1.000 and "PASS - judge is calibrated. Safe
# to trust automated results." over a judge that failed 70% of its calls. The
# three survivors are not a random sample of the set either: whatever made the
# other seven fail (long transcripts, one dimension's prompt) selected them.
# A correlation over a fifth of the labelled rows is not a measurement of the
# set the human scored, so it reports NOT CALIBRATED YET rather than a verdict.
MIN_PAIR_RATE = 0.8

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

# Not a status — a readiness answer. `--check` computes no correlation, so no
# --check outcome may ever share an exit code with one that did. See the
# module docstring.
EXIT_READY_TO_CALIBRATE = 4

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
# Agreement statistics on binary labels (stdlib only)
# ---------------------------------------------------------------------------

def confusion(pairs: list[tuple[bool, bool]]) -> dict[str, int]:
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
    """Chance-corrected agreement. NaN when chance agreement is already certain.

    Returns NaN rather than 0.0 for the degenerate case, because 0.0 means "no
    better than chance" and NaN means "this set cannot distinguish the two". A
    corpus where both raters passed everything is the second, and reporting it as
    the first would read as a judge failure.
    """
    n11, n10 = cells["both_pass"], cells["judge_too_harsh"]
    n01, n00 = cells["judge_too_lenient"], cells["both_fail"]
    n = n11 + n10 + n01 + n00
    if n == 0:
        return float("nan")

    observed = (n11 + n00) / n
    human_pass, judge_pass = (n11 + n10) / n, (n11 + n01) / n
    expected = human_pass * judge_pass + (1 - human_pass) * (1 - judge_pass)
    if expected >= 1.0:
        return float("nan")
    return (observed - expected) / (1 - expected)


def matthews(cells: dict[str, int]) -> float:
    """Matthews correlation. NaN when a whole row or column of the 2x2 is empty.

    The statistic to read when kappa collapses on an imbalanced corpus, which is
    the corpus we have: mostly-good responses.
    """
    n11, n10 = cells["both_pass"], cells["judge_too_harsh"]
    n01, n00 = cells["judge_too_lenient"], cells["both_fail"]
    denominator = (n11 + n01) * (n11 + n10) * (n00 + n01) * (n00 + n10)
    if denominator == 0:
        return float("nan")
    return (n11 * n00 - n01 * n10) / (denominator ** 0.5)


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
            verdict_str = (row.get("human_verdict") or "").strip().lower()
            score_str = (row.get("human_score") or "").strip()

            # BACKLOG 8.2b: `human_verdict` is the gate column now. `human_score`
            # is optional and feeds the reported Spearman only.
            if not verdict_str:
                unusable.append(f"{scenario_id}/{dimension}: human_verdict not filled in yet")
                continue
            if verdict_str not in (VERDICT_PASS, VERDICT_FAIL):
                unusable.append(
                    f"{scenario_id}/{dimension}: human_verdict {verdict_str!r} is neither "
                    f"{VERDICT_PASS!r} nor {VERDICT_FAIL!r}"
                )
                continue

            score = None
            if score_str:
                try:
                    score = int(score_str)
                except ValueError:
                    unusable.append(
                        f"{scenario_id}/{dimension}: non-integer human_score {score_str!r}"
                    )
                    continue
                if not 1 <= score <= 5:
                    unusable.append(
                        f"{scenario_id}/{dimension}: human_score {score} outside 1-5"
                    )
                    continue

            rows.append({
                "scenario_id": scenario_id,
                "dimension": dimension,
                "human_verdict": verdict_str,
                "human_passed": verdict_str == VERDICT_PASS,
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
    """Run 0 of this scenario, and only run 0.

    BACKLOG 8.1. A record now holds k runs. `human_scores.csv` is one row per
    (scenario, dimension) with no concept of a run, so scoring every run would
    multiply the only human step in the system by k. The sequence that avoids
    it: capture once at k > 1, the human scores run 0, the judge is calibrated
    against those labels here, and the CALIBRATED judge then scores runs 1..k-1
    for reliable@k. Reversing that order buys nothing and costs k times the
    labelling.

    Run 0 rather than the last run, because the last run moves under a top-up: a
    scenario re-captured from 3 to 5 would change the row the human already
    scored, and the correlation would then be against text nobody labelled.
    """
    from tests.evals import corpus  # noqa: PLC0415

    path = RESPONSES_DIR / f"{scenario_id}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No recorded response for {scenario_id}. "
            "Run capture_responses.py first."
        )
    return corpus.load_run(path, corpus.RUN_ZERO)


def deflected_response_ids(scenario_ids: list[str]) -> list[str]:
    """Which of these recorded responses are the PII firewall's deflection.

    BACKLOG 7.29. Four of the twenty E2E-6 responses came back byte-identical as
    the deflection, and the corpus was accepted as clean because the check that
    ran looked for empties, short answers and provider-error text. A deflection
    is none of those: it is a well-formed sentence of the right length.

    It is also unscorable. Grading it measures the firewall rather than the judge,
    so a human score against one is a number about the wrong thing, and it enters
    the correlation as though it were about grounding.

    `PII_DEFLECTION` is imported rather than copied. A second copy of that string
    would go stale the first time the wording changes, and this function would
    then report a clean corpus by failing to recognise the deflection.
    """
    from app.utils.pii_firewall import PII_DEFLECTION  # noqa: PLC0415
    from tests.evals import corpus  # noqa: PLC0415

    # Deduped: `scenario_ids` is one entry per ROW, and a scenario scored on two
    # dimensions appears twice. `scorable_rows` counts rows and wants the
    # duplicates; this list names scenarios and would print one twice.
    deflected = []
    for sid in dict.fromkeys(scenario_ids):
        path = RESPONSES_DIR / f"{sid}.json"
        if not path.exists():
            continue
        try:
            # Run 0, because run 0 is the row the human scores. A deflection in
            # a later run is a real finding, and it is validate_corpus.py's:
            # here the only question is whether THIS row can be labelled.
            recorded = corpus.load_run(path, corpus.RUN_ZERO)
        except (json.JSONDecodeError, corpus.CorpusShapeError):
            continue
        if (recorded.get("response_text") or "").strip() == PII_DEFLECTION:
            deflected.append(sid)
    return sorted(deflected)


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

    # BACKLOG 7.29: a deflected response cannot be scored for the judge, so it
    # does not count towards the pairs a calibration run could ever produce.
    # Blocking only when it actually bites, which is when too few scorable rows
    # remain: reporting it always, and blocking only sometimes, is the difference
    # between telling the owner something and stopping them.
    deflected = deflected_response_ids(all_referenced_ids)
    scorable_rows = len([s for s in all_referenced_ids if s and s not in set(deflected)])
    if deflected and scorable_rows < MIN_PAIRS:
        blocking.append(
            f"{len(deflected)} recorded response(s) are the PII deflection and cannot be "
            f"scored ({', '.join(deflected)}), leaving {scorable_rows} scorable row(s) "
            f"against the {MIN_PAIRS} needed - re-capture them"
        )

    awaiting_owner = parsed["valid"] < MIN_PAIRS

    return {
        "scenarios_present": len(scenario_files),
        "deflected_responses": deflected,
        "scorable_rows": scorable_rows,
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
    """Render a readiness report and return the exit code it implies.

    Never returns EXIT_CALIBRATED. Readiness is a statement about the INPUTS;
    calibration is a statement about the judge, and this function makes no judge
    call. See the module docstring's note on --check.
    """
    print("Calibration readiness (no judge calls, no network)\n")
    print(f"  scenarios on disk          : {report['scenarios_present']}")
    print(
        f"  rows with a human verdict  : {report['human_scores_valid']} labelled "
        f"/ {report['human_scores_attempted']} present"
    )
    print(f"  recorded responses on disk : {report['responses_present']}")

    if report["deflected_responses"]:
        print(
            f"  PII-deflected, unscorable  : {', '.join(report['deflected_responses'])} "
            f"({report['scorable_rows']} scorable row(s) remain)"
        )

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
            "verdicts needed are filled in.\n"
            "  Label BINARY - pass or fail - and write WHY in `notes`. A 1-5 score is\n"
            "  optional: a human cannot hold a scale steady across many rows, and the\n"
            "  gate is Cohen's kappa on the binary label.\n"
            "  That column is yours. Do not let anything - or anyone - fill it for you:\n"
            "  a judge calibrated against model-written labels measures agreement with\n"
            "  itself, which is exactly the tautology this file exists to detect.\n"
        )

    if report["ready_to_calibrate"]:
        print("READY - every input is present; run without --check to compute the correlation.")
        print("Ready is not calibrated: no judge has been called and rho is unknown.")
        return EXIT_READY_TO_CALIBRATE

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

    **The GATE is Cohen's kappa on the binary verdicts** (BACKLOG 8.2b). Spearman
    is still computed and still reported, over whichever rows carry an optional
    1-5 `human_score`, but it no longer decides anything: it is not
    chance-corrected, and on a mostly-good corpus most of its agreement is luck.

    Returns:
        {"status", "kappa", "matthews", "cells", "rho", "pairs", "pair_rate",
        "attempted", "valid", "errors", "table"}. Every statistic is None
        whenever it was not computed, so a caller can never read a number out of
        a run that did not make the measurement. `pair_rate` is pairs/valid, the
        fraction of the human-labelled set that produced a comparable pair, which
        is the judge's own success rate on this run, and it is GATED rather than
        merely reported: see MIN_PAIR_RATE.
    """
    parsed = read_human_score_rows()

    if parsed["missing_file"]:
        return {
            "status": STATUS_SETUP_ERROR,
            "kappa": None,
            "matthews": None,
            "cells": None,
            "rho": None,
            "pairs": 0,
            "pair_rate": None,
            "attempted": 0,
            "valid": 0,
            "errors": [f"{HUMAN_SCORES_CSV} not found."],
            "table": [],
        }

    if parsed["valid"] == 0:
        return {
            "status": STATUS_NOT_CALIBRATED_YET,
            "kappa": None,
            "matthews": None,
            "cells": None,
            "rho": None,
            "pairs": 0,
            "pair_rate": None,
            "attempted": parsed["attempted"],
            "valid": 0,
            "errors": [],
            "table": [],
        }

    human_scores: list[float] = []
    judge_scores: list[float] = []
    binary_pairs: list[tuple[bool, bool]] = []
    errors: list[str] = []
    table: list[dict] = []

    for row in parsed["rows"]:
        sid = row["scenario_id"]
        dim = row["dimension"]
        h_score = row["human_score"]
        h_passed = row["human_passed"]

        try:
            scenario = load_scenario(sid)
            response = load_response(sid)
        except FileNotFoundError as exc:
            errors.append(str(exc))
            table.append({"scenario_id": sid, "dimension": dim,
                          "human_verdict": row["human_verdict"], "human": h_score,
                          "judge_verdict": None, "judge": None,
                          "reason": f"ERROR: {exc}"})
            continue

        verdict = judge_fn(dim, build_transcript(scenario, response),
                           response.get("tool_calls_log", []))
        j_score = verdict["score"]

        # The judge wrapper returns score=0 with verdict="ERROR" on any
        # exception. Zero is not a low score, it is the absence of one, and
        # correlating it would move rho with the failure rate of the API.
        if j_score == 0:
            errors.append(f"{sid}/{dim}: judge returned ERROR — {verdict['reason']}")
            table.append({"scenario_id": sid, "dimension": dim,
                          "human_verdict": row["human_verdict"], "human": h_score,
                          "judge_verdict": None, "judge": None,
                          "reason": verdict["reason"]})
            continue

        # The GATE's pair: two binary labels. Every usable row contributes one,
        # because `human_verdict` is mandatory.
        binary_pairs.append((h_passed, verdict["verdict"] == "PASS"))

        # Spearman's pair: only rows where the human ALSO gave a 1-5. Reported,
        # not gated, and its own count is reported beside it so a rho over three
        # of ten rows cannot read as a rho over ten.
        if h_score is not None:
            human_scores.append(float(h_score))
            judge_scores.append(float(j_score))

        table.append({"scenario_id": sid, "dimension": dim,
                      "human_verdict": row["human_verdict"], "human": h_score,
                      "judge_verdict": verdict["verdict"], "judge": j_score,
                      "reason": verdict["reason"]})

    pairs = len(binary_pairs)
    pair_rate = pairs / parsed["valid"]
    cells = confusion(binary_pairs)

    if pairs < MIN_PAIRS:
        return {
            "status": STATUS_NOT_CALIBRATED_YET,
            "kappa": None,
            "matthews": None,
            "cells": cells,
            "rho": None,
            "pairs": pairs,
            "pair_rate": pair_rate,
            "attempted": parsed["attempted"],
            "valid": parsed["valid"],
            "errors": errors,
            "table": table,
        }

    # The denominator gate. `pairs` alone answers "is there enough to correlate";
    # this answers "did the judge actually score the set the human labelled".
    if pair_rate < MIN_PAIR_RATE:
        return {
            "status": STATUS_NOT_CALIBRATED_YET,
            "kappa": None,
            "matthews": None,
            "cells": cells,
            "rho": None,
            "pairs": pairs,
            "pair_rate": pair_rate,
            "attempted": parsed["attempted"],
            "valid": parsed["valid"],
            "errors": errors + [
                f"Only {pairs} of {parsed['valid']} human-labelled row(s) produced a "
                f"judge score ({pair_rate:.0%}, floor {MIN_PAIR_RATE:.0%}) - a "
                "correlation over the rows that happened to succeed is not a "
                "measurement of the set that was scored."
            ],
            "table": table,
        }

    kappa = cohens_kappa(cells)
    mcc = matthews(cells)

    # Spearman is REPORTED, over whichever rows carried an optional 1-5. NaN when
    # one side has no variance, which is the common case on a mostly-good corpus
    # and is exactly why it stopped being the gate.
    rho = spearman(human_scores, judge_scores) if len(human_scores) >= MIN_PAIRS else float("nan")

    result = {
        "kappa": None if kappa != kappa else kappa,
        "matthews": None if mcc != mcc else mcc,
        "cells": cells,
        "rho": None if rho != rho else rho,
        "scored_pairs": len(human_scores),
        "pairs": pairs,
        "pair_rate": pair_rate,
        "attempted": parsed["attempted"],
        "valid": parsed["valid"],
        "errors": errors,
        "table": table,
    }

    if kappa != kappa:
        # Undefined, not zero. Both raters labelled everything the same way, so
        # chance agreement is already certain and this set cannot distinguish a
        # good judge from a coin. Matthews is reported if it survived; neither is
        # a pass.
        result["status"] = STATUS_NOT_CALIBRATED_YET
        result["errors"] = errors + [
            "Cohen's kappa is undefined: every label on one side is identical, so "
            "chance agreement is certain and this set cannot measure the judge. "
            "Label a scenario the other way, or score more rows."
        ]
        return result

    result["status"] = STATUS_CALIBRATED if kappa >= KAPPA_THRESHOLD else STATUS_NOT_CALIBRATED
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _print_agreement(result: dict) -> None:
    """The 2x2 first, then the statistics, because the cells say what to DO.

    A single coefficient cannot distinguish "the judge is too harsh" from "the
    product is broken", and those need different people to do different things.
    """
    cells = result.get("cells") or {}
    print("Agreement, human against judge:\n")
    print(f"{'':>22}  {'judge PASS':>12}  {'judge FAIL':>12}")
    print(f"{'human PASS':>22}  {cells.get('both_pass', 0):>12}  "
          f"{cells.get('judge_too_harsh', 0):>12}")
    print(f"{'human FAIL':>22}  {cells.get('judge_too_lenient', 0):>12}  "
          f"{cells.get('both_fail', 0):>12}")
    print()
    if cells.get("judge_too_lenient"):
        print(f"  {cells['judge_too_lenient']} row(s) the judge PASSED and the human "
              "FAILED. Bad answers reach customers.")
    if cells.get("judge_too_harsh"):
        print(f"  {cells['judge_too_harsh']} row(s) the judge FAILED and the human "
              "PASSED. Read the judge's stated reasons.")
    if cells.get("both_fail"):
        print(f"  {cells['both_fail']} row(s) BOTH failed. That is the product, "
              "not the eval.")
    print()

    kappa = result.get("kappa")
    mcc = result.get("matthews")
    rho = result.get("rho")
    print(f"  Cohen's kappa   {kappa:.3f}   GATE, floor {KAPPA_THRESHOLD}"
          if kappa is not None else
          "  Cohen's kappa   undefined   one label on both sides; chance agreement is certain")
    print(f"  Matthews        {mcc:.3f}   reported; read this when the corpus is lopsided"
          if mcc is not None else
          "  Matthews        undefined   a row or column of the 2x2 is empty")
    if rho is not None:
        print(f"  Spearman rho    {rho:.3f}   reported over {result.get('scored_pairs', 0)} "
              f"row(s) with a 1-5 score, AI-SPEC 5.2's {THRESHOLD}. NOT the gate")
    else:
        print("  Spearman rho    not computed   too few rows carried an optional 1-5 score")
    print(f"\n  {result['pairs']} labelled row(s) produced a verdict pair "
          f"({result['pair_rate']:.0%} of {result['valid']}).")


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
        rate = result.get("pair_rate")
        print(
            f"NOT CALIBRATED YET - {result['pairs']} usable pair(s) out of "
            f"{result['valid']} labelled row(s)"
            + (f" ({rate:.0%})" if rate is not None else "")
            + f"; {MIN_PAIRS} pairs and {MIN_PAIR_RATE:.0%} of the set are needed.\n"
            "This is neither a pass nor a judge failure: the measurement has not been made.\n"
            "Run with --check to see exactly which input is missing. If it is the\n"
            "human_score column, that one is the owner's and must be filled by a human."
        )
    else:
        _print_agreement(result)
        if status == STATUS_CALIBRATED:
            print(
                f"PASS - the judge is calibrated (kappa {result['kappa']:.3f} >= "
                f"{KAPPA_THRESHOLD}) over {result['pair_rate']:.0%} of the labelled set."
            )
        else:
            print(
                f"FAIL - the judge is NOT calibrated (kappa {result['kappa']:.3f} < "
                f"{KAPPA_THRESHOLD}).\n"
                "Read the confusion matrix above before touching judge.py: if the "
                "both-fail\ncell is the large one, the AI SYSTEM is what is broken and "
                "tuning the judge\nwill only teach it to agree with a bad product."
            )

    return EXIT_CODE_FOR_STATUS[status]


if __name__ == "__main__":
    sys.exit(main())
