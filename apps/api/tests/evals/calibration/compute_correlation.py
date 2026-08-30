"""
compute_correlation.py - Calibrate the LLM judge against human labels.

Reads the calibration sheet (scenario_id, dimension, human_verdict,
human_score, notes), loads run 0 of each recorded response, calls the judge,
and measures agreement.

THE GATE IS COHEN'S KAPPA ON BINARY VERDICTS (BACKLOG 8.2b, owner decision
2026-08-18). Spearman is still computed over whichever rows carry an optional
1-5 score, and still reported, but it no longer decides anything.

    gate       Cohen's kappa on (human_verdict, judge verdict), against two
               intervals bootstrapped from the same labels - never a constant
    reported   Matthews correlation, the 2x2 confusion matrix, Spearman rho

THE THRESHOLD IS DERIVED, NOT CHOSEN (BACKLOG 8.2c, owner instruction
2026-08-18: "the kappa measurement must not be a choice it must be derived from
data"). `KAPPA_THRESHOLD = 0.6` is gone. Both halves below are required:

    (a) beats chance      judge_ci_low  > 0
    (b) reaches ceiling   judge_ci_high >= human_ci_low

The ceiling is the owner's own test-retest kappa over the SAME rows, from the
blind second pass in human_scores_pass2.csv. A judge cannot be expected to agree
with a human more than that human agrees with themself, so that is the scale.
With no second pass on file the harness reports NOT CALIBRATED YET.

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
    python apps/api/tests/evals/calibration/compute_correlation.py --emit-second-pass

    --check reports readiness only. It touches no network and no API key: it
    says which inputs the harness has and which it is missing, which is the
    question worth asking before spending a judge call per row.

WHOSE COLUMN human_score IS
    The owner's, and no one else's. Every cell ships empty by design. An
    agent-filled calibration set would silently destroy the only instrument
    that can say whether ANY judge in this system is trustworthy - the
    Gatekeeper, the Auditor, the Strategist, classify_severity, and the Actor
    gate that runs synchronously before money moves. Nothing in this file
    writes to human_scores.csv; it is opened for reading only, and
    tests/unit/test_calibration_harness.py pins both halves of that.

WHY THE EXIT CODES CHANGED (audit D7)
    The shipped script exited 0 both when the judge was calibrated and when
    nobody had scored anything yet, calling the second case "informational".
    In CI, in a checklist, or in a summary, exit 0 reads as success - so an
    instrument that had never been given a single label reported the same
    thing as one that had passed. Missing data is never passing data. An
    unscored file now exits EXIT_NOT_CALIBRATED_YET, which is neither pass nor
    fail but is distinguishable from both.

Requirements:
    - responses/ populated via capture_responses.py (needs a live, ingested
      agent and AGENT_E2E_ENABLED=1 - see --check for what is missing)
    - the calibration sheet has `human_verdict` filled by a human (pass/fail).
      `human_score` (1-5) is optional and feeds the reported Spearman only
    - human_scores_pass2.csv holds the SAME rows labelled a second time, blind.
      Emit it with --emit-second-pass; it is what measures the ceiling
    - ANTHROPIC_API_KEY set in environment

WHAT A RUN LEAVES BEHIND (ticket #53)
    `calibration.json`, beside the sheets, holding this run's status, verdict
    parts, intervals and counts as `app.domain.calibration_status` defines them.
    Nothing on the deploy path can import this script, so its answer travels as
    data. Every scoring run overwrites it, `setup_error` included, because the
    latest reading is the one a deploy should act on. `--check` and
    `--emit-second-pass` write no artifact, because neither scores anything.

Exit codes:
    0 (EXIT_CALIBRATED)         - both halves of the gate passed over
                                  >= MIN_PAIRS pairs
    1 (EXIT_NOT_CALIBRATED)     - a half was MEASURED and FAILED: the judge's
                                  interval includes 0, or it tops out below the
                                  human's own lower bound
    2 (EXIT_SETUP_ERROR)        - missing files, unusable rows, other setup error
    3 (EXIT_NOT_CALIBRATED_YET) - no human scores, no blind second pass, fewer
                                  than MIN_PAIRS usable
                                  pairs, or the judge failed too many of its own
                                  calls to have measured the set. NOT a pass and
                                  NOT a failure of the judge: the measurement has
                                  not been made.
    4 (EXIT_READY_TO_CALIBRATE) - `--check` only: every input is present and the
                                  correlation has NOT been computed.
    5 (EXIT_SECOND_PASS_EMITTED) - `--emit-second-pass` only: the blind sheet was
                                  written and nothing was measured.

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
import dataclasses
import datetime
import json
import os
import pathlib
import random
import sys
from typing import TYPE_CHECKING

from tests.evals.calibration.agreement import (
    bootstrap_kappa,
    calibration_verdict,
    cohens_kappa,
    confusion,
    human_ceiling,
    paired_difference,
)

if TYPE_CHECKING:
    from app.domain.calibration_status import CalibrationStatus
    from app.domain.judge_identity import JudgeIdentity

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CALIBRATION_DIR = pathlib.Path(__file__).parent
EVALS_DIR = CALIBRATION_DIR.parent
SCENARIOS_DIR = EVALS_DIR / "scenarios"
RESPONSES_DIR = EVALS_DIR / "responses"
HUMAN_SCORES_CSV = CALIBRATION_DIR / "human_scores.csv"

# The SECOND labelling pass, and it is a separate FILE rather than a second
# column, because the ceiling it measures is only worth anything if the pass is
# BLIND. A `human_verdict_2` column sits next to the first verdict on the same
# row, so the labeller reads their own answer while writing the new one and the
# number that comes out measures their memory instead of their consistency. That
# would inflate the ceiling towards 1.0 and refuse judges for the wrong reason.
#
# Same header, same reader, same validation: it is the same instrument asked a
# second time. `--emit-second-pass` writes it, shuffled and empty.
HUMAN_SCORES_PASS2_CSV = CALIBRATION_DIR / "human_scores_pass2.csv"

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
# AND THERE IS NO KAPPA_THRESHOLD ANY MORE (BACKLOG 8.2c). It was 0.6, the
# Landis-Koch band boundary for "substantial" - a 1977 rule of thumb published
# with no empirical basis, so it was not merely unmeasured here, it was never
# measured anywhere. The owner refused it on 2026-08-18: "the kappa measurement
# must not be a choice it must be derived from data."
#
# What decides now is two intervals computed from the labels themselves, in
# agreement.py, and BOTH are required:
#
#     (a) beats chance      judge_ci_low  > 0
#     (b) reaches ceiling   judge_ci_high >= human_ci_low
#
# (b) is the one that carries the scale: a judge cannot be expected to agree
# with a human more than that human agrees with THEMSELF, so the owner's
# test-retest kappa over the same rows is the ceiling. With no second pass on
# file there is no scale, and the harness refuses rather than inventing one.

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

#: The column the gate reads. Its ABSENCE is reported as a header problem rather
#: than as ten unlabelled rows: those need different actions from the owner.
REQUIRED_COLUMN = "human_verdict"

# Spearman over two points is not a correlation, it is a line through two
# points. spearman() already returns nan below three; this names the same floor
# so the status machinery can say "not calibrated yet" rather than "nan".
MIN_PAIRS = 3

#: How many rows must carry the RARER label before any interval here is a
#: measurement. Derived, not chosen: a bootstrap resample loses a label entirely
#: with probability about `e^-m`, so m=1 leaves 37% of resamples uninformative
#: and m=2 leaves 13%, which is the line `MAX_UNDEFINED_FRACTION` draws.
#:
#: It exists because `--check` said "3 of the 3 human verdicts needed" and no
#: 3-row sheet can produce a usable ceiling at all: the owner labelled exactly
#: what they were told to, paid for a judge call per row, and got
#: "not a measurement".
MIN_MINORITY_ROWS = 2

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

# Also not a status. `--emit-second-pass` writes a sheet and measures nothing,
# so it may not share a code with any outcome that did measure something. Four
# outcomes got four codes for that reason (audit D7); this is the fifth.
EXIT_SECOND_PASS_EMITTED = 5

#: Everything `main` accepts. Anything else refuses rather than spending money.
KNOWN_FLAGS = ("--check", "--emit-second-pass")

USAGE = (
    "compute_correlation.py - calibrate the LLM judge against human labels",
    "",
    "  (no arguments)        run the calibration. COSTS one judge call per labelled row.",
    "  --check               report which inputs are present. No judge calls, no network.",
    "  --emit-second-pass    write the blind second-pass sheet, shuffled and empty.",
    "",
    "Exit codes: 0 calibrated, 1 not calibrated, 2 setup error,",
    "            3 not calibrated yet, 4 --check ready, 5 second pass emitted.",
)

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

    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        # F3, adversarial review 2026-08-18. Nothing checked that the gate column
        # was PRESENT, so `Human_Verdict` or a space-padded ` human_verdict` -
        # what a spreadsheet round trip produces - yielded ten copies of
        # "human_verdict not filled in yet" over a fully labelled file. That is
        # the one message this harness exists to make trustworthy, and a missing
        # column is a different problem from an unlabelled row.
        #
        # `utf-8-sig` above is the other half: Excel's "CSV UTF-8" writes a BOM,
        # which made the first header `\ufeffscenario_id`, so every row parsed as
        # VALID with an empty `scenario_id` and readiness reported READY over a
        # file that produced zero pairs.
        fieldnames = [(name or "").strip() for name in (reader.fieldnames or [])]
        if REQUIRED_COLUMN not in fieldnames:
            return {
                "attempted": 0,
                "valid": 0,
                "rows": [],
                "unusable": [
                    f"{csv_path.name} has no `{REQUIRED_COLUMN}` column. Found: "
                    f"{fieldnames or '(no header row)'}. This is a header problem, not an "
                    "unlabelled sheet; a spreadsheet round trip renames or pads headers."
                ],
                "missing_file": False,
            }

        for raw_row in reader:
            attempted += 1
            # Keys are STRIPPED, because a spreadsheet round trip pads them and
            # ` human_verdict` is recoverable data rather than a broken sheet.
            # DictReader keys on the raw header, so without this the column is
            # present by name and absent by lookup.
            row = {(k or "").strip(): v for k, v in raw_row.items()}
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


def read_second_pass(path: pathlib.Path | None = None) -> dict:
    """The blind re-label, keyed by (scenario_id, dimension).

    Read by the SAME function as the first sheet, because it is the same
    instrument asked a second time - so the BOM handling, the header check and
    the pass/fail validation all apply to it for free, and a spreadsheet round
    trip cannot make it read as unlabelled.

    Returns `{"verdicts", "attempted", "valid", "unusable", "missing_file"}`
    where `verdicts` maps (scenario_id, dimension) -> bool.
    """
    parsed = read_human_score_rows(path or HUMAN_SCORES_PASS2_CSV)

    # A dict comprehension over the rows was last-wins and silent. A copy-pasted
    # row is the single most ordinary spreadsheet accident, and it overwrote a
    # blind verdict, counted twice towards "rows re-labelled", and lowered the
    # ceiling by a full kappa point with nothing printed.
    verdicts: dict[tuple[str, str], bool] = {}
    repeated: set[tuple[str, str]] = set()
    for row in parsed["rows"]:
        key = (row["scenario_id"], row["dimension"])
        if key in verdicts or key in repeated:
            # Neither copy wins. A row the labeller answered twice has no single
            # blind verdict, and picking one silently is what made a duplicate
            # lower the ceiling by a full kappa point with nothing printed.
            repeated.add(key)
            verdicts.pop(key, None)
            continue
        verdicts[key] = row["human_passed"]

    duplicates = [
        f"{scenario_id}/{dimension} appears more than once, so it has no single "
        "blind verdict. Delete the extra row."
        for scenario_id, dimension in sorted(repeated)
    ]

    return {
        "verdicts": verdicts,
        "duplicates": duplicates,
        "attempted": parsed["attempted"],
        "valid": len(verdicts),
        "unusable": parsed["unusable"] + duplicates,
        "missing_file": parsed["missing_file"],
    }


def ceiling_pairs_for(rows: list[tuple[str, str, bool]], second: dict) -> dict:
    """Pair each judged row's first verdict with its blind second verdict.

    `rows` is the set of (scenario_id, dimension, human_passed) that ACTUALLY
    ENTERED the judge's matrix, and the ceiling is measured over exactly those
    and no others. Two reasons, and the second is the load-bearing one:

      - The ceiling caps the judge, so it has to describe the rows the judge was
        measured on. A row the judge never scored says nothing about what
        agreement was achievable on the rows it did.
      - The two intervals are then computed at the SAME n, so comparing them is
        a statement about agreement rather than about sample size. A ceiling
        measured over thirty rows against a judge measured over ten would be the
        tighter interval by construction, and the judge would fail (b) for being
        outnumbered.

    A row missing its second verdict is NAMED, and the ceiling is withheld
    rather than computed over the subset that happens to be complete: that
    subset is not a random sample of the sheet, it is whichever rows the
    labeller got to.
    """
    pairs: list[tuple[bool, bool]] = []
    missing: list[str] = []
    for scenario_id, dimension, human_passed in rows:
        key = (scenario_id, dimension)
        if key in second["verdicts"]:
            pairs.append((human_passed, second["verdicts"][key]))
        else:
            missing.append(f"{scenario_id}/{dimension}")
    return {"pairs": pairs, "missing": missing}


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
    from app.domain.pii_firewall import PII_DEFLECTION  # noqa: PLC0415
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
        with HUMAN_SCORES_CSV.open(newline="", encoding="utf-8-sig") as f:
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

    # BACKLOG 8.2c. The ceiling is an OWNER input like the verdicts are, and a
    # missing one is reported here rather than discovered after a paid judge run
    # whose numbers nobody may read.
    second = read_second_pass()
    rows_without_second = [
        f"{r['scenario_id']}/{r['dimension']}"
        for r in parsed["rows"]
        if (r["scenario_id"], r["dimension"]) not in second["verdicts"]
    ]
    awaiting_ceiling = parsed["valid"] == 0 or bool(rows_without_second)
    awaiting_owner = parsed["valid"] < MIN_PAIRS or awaiting_ceiling

    # How many rows carry each label. Kappa needs BOTH present, and a resample
    # loses a label entirely with probability about e^-m where m is the minority
    # count, so m = 1 gives 37% uninformative resamples and m = 2 gives 13%.
    # `--check` used to say READY over a sheet that could only ever produce
    # "not a measurement", after the owner had paid for a judge call per row.
    passes = sum(1 for r in parsed["rows"] if r["human_passed"])
    fails = parsed["valid"] - passes
    minority = min(passes, fails)
    balance_is_workable = minority >= MIN_MINORITY_ROWS

    # `--check` is documented as saying which inputs are missing, and the run
    # needs this one. It is read from os.environ, not from .env: the settings
    # loader and the Anthropic client read different places, which has cost four
    # debugging cycles in this repo (CLAUDE.md, 1.28).
    api_key_exported = bool(os.environ.get("ANTHROPIC_API_KEY"))

    return {
        "scenarios_present": len(scenario_files),
        "second_pass_valid": second["valid"],
        "second_pass_unusable": second["unusable"],
        "second_pass_missing_file": second["missing_file"],
        # Only rows that PAIR with the first sheet count as progress. Counting
        # every readable row let an edited or re-emitted sheet print
        # "8 / 6 needed for the ceiling" and READY while zero rows paired.
        "second_pass_paired": len(
            [r for r in parsed["rows"]
             if (r["scenario_id"], r["dimension"]) in second["verdicts"]]
        ),
        "rows_without_second_verdict": rows_without_second,
        "awaiting_ceiling": awaiting_ceiling,
        "label_pass_rows": passes,
        "label_fail_rows": fails,
        "minority_label_rows": minority,
        "balance_is_workable": balance_is_workable,
        "api_key_exported": api_key_exported,
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
        # A judge run costs a call per row. Every condition here is checkable
        # locally, so none of them should be discovered after the money is spent.
        "ready_to_calibrate": (
            not blocking and not awaiting_owner and balance_is_workable
            and api_key_exported
        ),
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
    print(
        f"  rows re-labelled blind     : {report['second_pass_paired']} "
        + ("(no second-pass sheet yet)" if report["second_pass_missing_file"] else
           f"/ {report['human_scores_valid']} needed for the ceiling")
    )
    print(
        f"  label balance              : {report['label_pass_rows']} pass, "
        f"{report['label_fail_rows']} fail"
        + ("" if report["balance_is_workable"] else
           f"   NOT ENOUGH: {MIN_MINORITY_ROWS} rows must carry each label")
    )
    print(
        "  ANTHROPIC_API_KEY          : "
        + ("exported" if report["api_key_exported"] else
           "NOT in os.environ. Present in .env is not enough: the settings "
           "loader and the Anthropic client read different places (1.28)")
    )

    # Every row either sheet could not read, by name and with its reason.
    # These were computed and thrown away, so a sheet whose verdicts read
    # `y` reported as "0 labelled" and named nothing: defect F3, fixed on
    # the header axis in 8.2b and still live on the value axis until 8.2d.
    for label, reasons in (
        ("human_scores.csv", report["human_scores_unusable"]),
        (HUMAN_SCORES_PASS2_CSV.name, report["second_pass_unusable"]),
    ):
        if reasons:
            print()
            print(f"  {label} - {len(reasons)} row(s) could not be read:")
            for reason in reasons:
                print(f"    - {reason}")

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
    if report["human_scores_valid"] < MIN_PAIRS:
        print(
            f"Awaiting the owner: {report['human_scores_valid']} of {report['human_scores_attempted']} rows on the "
            "sheet carry a verdict. Every row you do not intend to label should be deleted.\n"
            "  Label BINARY - pass or fail - and write WHY in `notes`. A 1-5 score is\n"
            "  optional: a human cannot hold a scale steady across many rows, and the\n"
            "  gate is Cohen's kappa on the binary label.\n"
            "  That column is yours. Do not let anything - or anyone - fill it for you:\n"
            "  a judge calibrated against model-written labels measures agreement with\n"
            "  itself, which is exactly the tautology this file exists to detect.\n"
        )

    if report["awaiting_ceiling"]:
        # Named even when the first sheet is empty too. Both are the owner's, both
        # are missing, and a report that mentions only the nearer one produces a
        # second evening of work nobody planned for.
        print("Awaiting the owner: the HUMAN CEILING. Label the same rows a SECOND time, blind.")
        if not report["human_scores_valid"]:
            print("  After the first sheet above, not instead of it.")
        print("  Run --emit-second-pass to write a shuffled sheet with the verdict column")
        print("  empty, then fill it WITHOUT opening the first one. A judge cannot be")
        print("  expected to agree with you more than you agree with yourself, so your own")
        print("  test-retest kappa is the scale every judge kappa is read against. Without")
        print("  it there is no scale, and the harness reports NOT CALIBRATED YET rather")
        print("  than inventing a number.")
        print()

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
            "judge_interval": None,
            "ceiling_interval": None,
            "difference_interval": None,
            "gate": None,
            "matthews": None,
            "cells": None,
            "rho": None,
            "scored_pairs": 0,
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
            "judge_interval": None,
            "ceiling_interval": None,
            "difference_interval": None,
            "gate": None,
            "matthews": None,
            "cells": None,
            "rho": None,
            "scored_pairs": 0,
            "pairs": 0,
            "pair_rate": None,
            "attempted": parsed["attempted"],
            "valid": 0,
            "errors": [],
            "table": [],
        }

    # Excluded from the gate, not merely reported by `readiness()`. See F11 below.
    deflected = set(deflected_response_ids([r["scenario_id"] for r in parsed["rows"]]))

    human_scores: list[float] = []
    judge_scores: list[float] = []
    binary_pairs: list[tuple[bool, bool]] = []
    # The rows the judge was actually measured on. The human ceiling is
    # measured over exactly these, so the two intervals share an n.
    judged_rows: list[tuple[str, str, bool]] = []
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
                          "judge_identity": None,
                          "reason": f"ERROR: {exc}"})
            continue

        verdict = judge_fn(dim, build_transcript(scenario, response),
                           response.get("tool_calls_log", []))
        j_score = verdict["score"]

        # The judge wrapper returns score=0 with verdict="ERROR" on any
        # exception. Zero is not a low score, it is the absence of one, and
        # correlating it would move rho with the failure rate of the API.
        if j_score == 0:
            errors.append(f"{sid}/{dim}: judge returned ERROR: {verdict['reason']}")
            table.append({"scenario_id": sid, "dimension": dim,
                          "human_verdict": row["human_verdict"], "human": h_score,
                          "judge_verdict": None, "judge": None,
                          "judge_identity": verdict.get("judge_identity"),
                          "reason": verdict["reason"]})
            continue

        # F11, adversarial review 2026-08-18. `deflected_response_ids` was called
        # from `readiness()` and NOWHERE ELSE, so a labelled PII deflection was
        # judged and landed in the confusion matrix that decides STATUS_CALIBRATED
        # - exactly what its own docstring says must not happen. Grading a
        # deflection measures the firewall, and a human label against one is a
        # number about the wrong thing.
        if sid in deflected:
            errors.append(
                f"{sid}/{dim}: recorded response is the PII firewall's deflection, so it "
                "is excluded from the agreement matrix. Re-capture it."
            )
            table.append({"scenario_id": sid, "dimension": dim,
                          "human_verdict": row["human_verdict"], "human": h_score,
                          "judge_verdict": None, "judge": None,
                          "judge_identity": verdict.get("judge_identity"),
                          "reason": "excluded: PII deflection"})
            continue

        # The GATE's pair: two binary labels. Every usable row contributes one,
        # because `human_verdict` is mandatory.
        binary_pairs.append((h_passed, verdict["verdict"] == "PASS"))
        judged_rows.append((sid, dim, h_passed))

        # Spearman's pair: only rows where the human ALSO gave a 1-5. Reported,
        # not gated, and its own count is reported beside it so a rho over three
        # of ten rows cannot read as a rho over ten.
        if h_score is not None:
            human_scores.append(float(h_score))
            judge_scores.append(float(j_score))

        table.append({"scenario_id": sid, "dimension": dim,
                      "human_verdict": row["human_verdict"], "human": h_score,
                      "judge_verdict": verdict["verdict"], "judge": j_score,
                      # `.get`, because the identity travels on the row and a
                      # judge_fn that reports none has none. An absent key is
                      # the same fact as a null one here.
                      "judge_identity": verdict.get("judge_identity"),
                      "reason": verdict["reason"]})

    pairs = len(binary_pairs)
    pair_rate = pairs / parsed["valid"]
    cells = confusion(binary_pairs)

    if pairs < MIN_PAIRS:
        return {
            "status": STATUS_NOT_CALIBRATED_YET,
            "kappa": None,
            "judge_interval": None,
            "ceiling_interval": None,
            "difference_interval": None,
            "gate": None,
            "matthews": None,
            "cells": cells,
            "rho": None,
            "scored_pairs": 0,
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
            "judge_interval": None,
            "ceiling_interval": None,
            "difference_interval": None,
            "gate": None,
            "matthews": None,
            "cells": cells,
            "rho": None,
            "scored_pairs": 0,
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

    # BACKLOG 8.2c. The gate is two intervals bootstrapped from these same
    # labels, never a constant. `judge` is what this run measured; `ceiling` is
    # what the labeller achieves against themself on the SAME rows.
    judge_interval = bootstrap_kappa(binary_pairs)
    second = read_second_pass()
    ceiling = ceiling_pairs_for(judged_rows, second)

    ceiling_interval = None
    difference_interval = None
    if not ceiling["missing"]:
        ceiling_interval = human_ceiling(ceiling["pairs"])
        # BACKLOG 8.2d. Both sequences are indexed by the same judged rows in the
        # same order, so one resample drives both and the human's first-pass
        # label vector cancels. The previous rule compared the two marginal
        # intervals, which is the overlapping-CI fallacy AND, against a
        # self-consistent labeller, reduced to "at most 3 disagreements" at any n.
        difference_interval = paired_difference(binary_pairs, ceiling["pairs"])

    if ceiling["missing"]:
        errors = errors + [
            f"{len(ceiling['missing'])} of {pairs} judged row(s) have no blind second "
            f"verdict, so the human ceiling was NOT computed: {', '.join(ceiling['missing'])}. "
            f"Add them to {HUMAN_SCORES_PASS2_CSV.name} (it already exists, so "
            "`--emit-second-pass` will refuse rather than overwrite your labels), then "
            "run again."
        ]

    # Everything the second sheet could not read is the owner's to fix, so it
    # travels with the errors rather than dying inside the reader (BLOCK 4 of the
    # 2026-08-19 mutation review: this list was computed and discarded, which is
    # defect F3 reintroduced one file over).
    if second["unusable"]:
        errors = errors + [
            f"{HUMAN_SCORES_PASS2_CSV.name}: {reason}" for reason in second["unusable"]
        ]

    gate = calibration_verdict(judge_interval, ceiling_interval, difference_interval)

    result = {
        "kappa": None if kappa != kappa else kappa,
        "matthews": None if mcc != mcc else mcc,
        "cells": cells,
        "rho": None if rho != rho else rho,
        "judge_interval": judge_interval,
        "ceiling_interval": ceiling_interval,
        "difference_interval": difference_interval,
        "gate": gate,
        "scored_pairs": len(human_scores),
        "pairs": pairs,
        "pair_rate": pair_rate,
        "attempted": parsed["attempted"],
        "valid": parsed["valid"],
        "errors": errors + gate["reasons"],
        "table": table,
    }

    if kappa != kappa:
        # Undefined, not zero. Both raters labelled everything the same way, so
        # chance agreement is already certain and this set cannot distinguish a
        # good judge from a coin. Matthews is reported if it survived; neither is
        # a pass. The bootstrap reaches the same conclusion one step later; this
        # branch keeps the specific message, and orders it first.
        result["status"] = STATUS_NOT_CALIBRATED_YET
        result["errors"] = errors + [
            "Cohen's kappa is undefined: one of the two raters used a single label for "
            "every row, so the arithmetic forces agreement to equal chance whatever the "
            "other rater did. Label a scenario the other way, or score more rows."
        ] + gate["reasons"]
        return result

    # A FAILED half is a measurement and reports NOT CALIBRATED. A MISSING half -
    # an unusable judge interval, or a ceiling nobody has labelled - is an
    # absence and reports NOT CALIBRATED YET. The distinction is the whole
    # point of the four exit codes: one tells the owner to fix the judge, the
    # other tells them the measurement has not been made.
    if gate["calibrated"]:
        result["status"] = STATUS_CALIBRATED
    elif gate["beats_chance"] is False or gate["reaches_ceiling"] is False:
        result["status"] = STATUS_NOT_CALIBRATED
    else:
        result["status"] = STATUS_NOT_CALIBRATED_YET
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

    # A run that stopped at a floor computed NOTHING, and printing "one label on
    # both sides" over a matrix that plainly shows two labels is worse than
    # printing nothing: it sends the owner to relabel a sheet that is fine.
    if result.get("gate") is None:
        print("  Cohen's kappa   not computed   the run stopped at a floor above")
        print("  Matthews        not computed")
        print(f"  {result['pairs']} labelled row(s) produced a verdict pair.")
        return
    print(f"  Cohen's kappa   {kappa:.3f}   the point estimate; the INTERVAL is the gate"
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
    _print_gate(result)


def _print_gate(result: dict) -> None:
    """The two intervals and which half each one answers (BACKLOG 8.2c).

    Printed as intervals rather than as a mark against a number, because there
    is no number: the gate compares one measured interval against another, and a
    reader who cannot see both cannot tell a judge that is wrong from a corpus
    too small to say anything about one.
    """
    gate = result.get("gate")
    if not gate:
        return

    print()
    print("The gate, derived from these labels and from no constant:")
    print()
    _print_interval("judge 95% CI", result.get("judge_interval"),
                    "(a)  above 0 means the judge beats chance")
    _print_interval("your ceiling", result.get("ceiling_interval"),
                    "(b1) above 0 means your two passes agree better than chance")
    _print_interval("you minus judge", result.get("difference_interval"),
                    "(b2) above 0 ALL THROUGH means you beat the judge")
    print()
    print(f"  (a)  the judge beats chance          {_half(gate['beats_chance'])}")
    print(f"  (b1) your labels set a scale         {_half(gate['ceiling_beats_chance'])}")
    print(f"  (b2) the judge reaches that scale    {_half(gate['reaches_ceiling'])}")


def _print_interval(label: str, interval: dict | None, note: str) -> None:
    """An interval that was never measured must not render as a bad one."""
    if interval is None:
        print(f"  {label:<16}never measured   {note}")
    elif interval.get("usable"):
        print(f"  {label:<16}[{interval['low']:+.3f}, {interval['high']:+.3f}]   {note}")
    else:
        print(f"  {label:<16}not a measurement   "
              f"{interval['undefined_fraction']:.0%} of resamples had no kappa at all")


def _half(value: bool | None) -> str:
    """A missing half is not a failed one, and must never print as one."""
    if value is True:
        return "yes"
    if value is False:
        return "NO"
    return "not measured"


#: Fixed so a re-emit after a deleted file produces the same sheet, and so a
#: test can assert the order changed rather than assert against luck.
SECOND_PASS_SHUFFLE_SEED = 20260818


def emit_second_pass(path: pathlib.Path | None = None) -> tuple[int, list[str]]:
    """Write the blind re-labelling sheet. Returns (exit code, messages).

    Three refusals, and each one protects the ceiling from being a number about
    something else:

      - **The file already exists.** Overwriting it destroys labels only the
        owner can produce. Delete it deliberately if that is really the intent.
      - **The first pass is incomplete.** Labelling both sheets in one sitting is
        not a test-retest; it is one pass copied. Finish the first, then come
        back.
      - **There are no rows.** Nothing to re-label.

    What it writes: `scenario_id`, `dimension`, and an EMPTY `human_verdict`,
    shuffled. No `notes` column, because pass one's notes are the owner's own
    reasoning and reading them back is reading back the answer.
    """
    target = path or HUMAN_SCORES_PASS2_CSV
    if target.exists():
        return EXIT_SETUP_ERROR, [
            f"{target.name} already exists and was NOT overwritten. It holds labels "
            "only you can produce; delete it by hand if you really mean to start over."
        ]

    parsed = read_human_score_rows()
    if parsed["missing_file"]:
        return EXIT_SETUP_ERROR, [f"{HUMAN_SCORES_CSV.name} not found."]
    if parsed["unusable"] or parsed["valid"] == 0:
        return EXIT_SETUP_ERROR, [
            f"the first pass is not finished: {parsed['valid']} of {parsed['attempted']} "
            "row(s) carry a verdict. Labelling both sheets in one sitting is one pass "
            "copied, not a test-retest, so this refuses rather than emitting a partial "
            "sheet you cannot re-emit later. Either finish the rows below, or DELETE the "
            "ones you do not intend to label - the ceiling is measured over the rows that "
            "reach the judge, so a row you never label costs nothing by being absent."
        ] + parsed["unusable"]

    rows = [(r["scenario_id"], r["dimension"]) for r in parsed["rows"]]
    random.Random(SECOND_PASS_SHUFFLE_SEED).shuffle(rows)

    # Written by hand rather than through the stdlib CSV writer: the harness must
    # never own a code path that can serialise a verdict into a calibration
    # sheet, and tests/unit/test_calibration_harness.py asserts on the absence of
    # those two names in this file's source.
    body = "scenario_id,dimension,human_verdict"
    for scenario_id, dimension in rows:
        body += "%s%s,%s," % (chr(10), scenario_id, dimension)
    target.write_text(body + chr(10), encoding="utf-8")

    return EXIT_SECOND_PASS_EMITTED, [
        f"Wrote {target.name}: {len(rows)} row(s), shuffled, verdict column empty.",
        "Fill it WITHOUT opening the first sheet. Leave time between the two passes if",
        "you can - what is being measured is how consistently you judge these rows, and",
        "that number caps every judge this harness will ever grade.",
    ]


# ---------------------------------------------------------------------------
# The artifact. This run's answer, in the shape the app reads (ticket #53)
# ---------------------------------------------------------------------------

#: Which build of this script produced an artifact. Bumped BY HAND when the
#: mapping onto `CalibrationStatus` changes what a field means, so a reader
#: holding an older artifact can tell it was built under different rules. Not a
#: checksum of this file and it must not become one. Editing a print statement
#: is not a change to the mapping.
HARNESS_VERSION = "compute_correlation.py@1"

#: Where a scoring run leaves its record: beside the sheets it read, which is
#: the one directory this harness owns.
CALIBRATION_ARTIFACT_JSON = CALIBRATION_DIR / "calibration.json"

def judge_identity_for_run(result: dict) -> JudgeIdentity | None:
    """The one Judge every scored row in this run reported, or None.

    THE ROWS ARE THE SOURCE, AND THERE IS NO TABLE. This read a static
    `JUDGE_IDENTITY_BY_DIMENSION` keyed on the dimension column, so the identity
    on an artifact was an assertion about which Judge ought to have scored those
    rows. Nothing compared it with the Judge that did, and a hand-filled row
    there would have made an artifact say `calibrated` about a Judge that never
    saw the set. `judge_fn` now reports its own identity per verdict
    (`tests/evals/judge.py:judge`), and that is what an artifact carries.

    SCORED ROWS ONLY, which is the ones carrying a `judge_verdict`. A row whose
    scenario would not load, whose judge errored, or that was excluded as a PII
    deflection contributed nothing to the kappa, so the Judge behind it does not
    describe the figure.

    None when two rows report different Judges, None when they report none, and
    None when the run scored no rows at all. Picking the first of several would
    report one row's Judge as the Judge behind the whole figure, which is the
    rule `eval_service.run_judge_identity` already applies to an eval run's
    records.
    """
    identities = {
        entry.get("judge_identity")
        for entry in result.get("table") or []
        if entry.get("judge_verdict")
    }
    if len(identities) == 1:
        return identities.pop()
    return None


def labelled_at() -> str | None:
    """When the sheet this figure covers was last written, as ISO 8601 UTC.

    The sheet's mtime, because no column in it records when a row was labelled
    and the harness may not invent a date. None when there is no sheet, which is
    the `setup_error` case. Section 9 of
    `.dev/reference/260818-llm-eval-fundamentals.md` reads alignment decay off
    this field, so a stale figure can be seen to be stale.
    """
    try:
        stamp = HUMAN_SCORES_CSV.stat().st_mtime
    except OSError:
        return None
    return datetime.datetime.fromtimestamp(stamp, datetime.UTC).isoformat()


def calibration_record(result: dict) -> CalibrationStatus:
    """This run, as `app.domain.calibration_status` holds it.

    ONE JUDGEMENT LIVES HERE AND IT IS NAMED. A run whose scored rows report no
    single Judge leaves `not_calibrated_yet` rather than the harness's own
    `calibrated`, even when the gate passed, because a kappa with no Judge
    attached is a number about a judge nobody can name. A deploy reading it sees
    `no_single_judge_identity`, which says what to fix.

    Every other status is written as the harness reached it, `setup_error`
    included. A run that could not read its own inputs states a fact about the
    inputs, not about any Judge, and `judge_identity: null` on that record is
    honest rather than disqualifying.

    THE FIGURES SURVIVE THE DOWNGRADE. The status becomes `not_calibrated_yet`
    and the kappa, the Matthews and all three intervals stay on the record, the
    same way they stay on a `not_calibrated` run that also names no Judge. They
    were dropped, and there was no rule behind it. The numbers are true of the
    rows that were scored whatever the record concludes about the Judge, and an
    owner reading `no_single_judge_identity` beside a kappa of 0.83 learns that
    the labelling is worth attaching to a nameable Judge.
    """
    from app.domain.calibration_status import CalibrationStatus  # noqa: PLC0415

    identity = judge_identity_for_run(result)
    if identity is None and result["status"] == STATUS_CALIBRATED:
        # The reason token is spelled by `ABSENT_REASONS`, which the loader
        # stamps from too, so the writer and the reader share one vocabulary.
        return dataclasses.replace(
            CalibrationStatus.from_harness(
                result,
                status=STATUS_NOT_CALIBRATED_YET,
                judge_identity=None,
                labelled_at=labelled_at(),
                harness_version=HARNESS_VERSION,
            ),
            reason="no_single_judge_identity",
        )
    return CalibrationStatus.from_harness(
        result,
        status=result["status"],
        judge_identity=identity,
        labelled_at=labelled_at(),
        harness_version=HARNESS_VERSION,
    )


def written_at() -> str:
    """Now, as ISO 8601 UTC. Stamped by the writer and by nothing else.

    On the record rather than derived from the file's mtime, because a file gets
    copied, restored and checked out and its mtime moves with all three, while
    the sentence "this reading was taken at" belongs to the run that took it.
    `labelled_at` dates the labels and this one dates the reading of them, and a
    gap between the two is the alignment decay section 9 of
    `.dev/reference/260818-llm-eval-fundamentals.md` is about.
    """
    return datetime.datetime.now(datetime.UTC).isoformat()


def write_harness_raised(path: pathlib.Path, exc: BaseException) -> None:
    """Replace the artifact with a `setup_error` saying this run raised.

    A run that dies leaves the PREVIOUS run's answer sitting at the path, and
    the loader has no way to tell that from an answer about today. It reports
    last week's kappa about a judge nobody scored anything with this week, which
    is the one failure this whole record exists to stop.

    THE TYPE NAME ONLY, never the message. That is #96's class of defect. An
    exception string carries whatever the raiser put in it, a DSN and a key included, and
    this file is read by a deploy summary and committed by hand.

    Best effort by design. It is called from an `except` on the way to re-raising
    the original, so a disk that will not take this write must not replace the
    exception the operator needs to see with one about the artifact.
    """
    from app.domain.calibration_status import (  # noqa: PLC0415
        STATUS_SETUP_ERROR,
        CalibrationStatus,
    )

    try:
        record = CalibrationStatus(
            status=STATUS_SETUP_ERROR,
            reason=f"harness_raised:{type(exc).__name__}",
            labelled_at=labelled_at(),
            harness_version=HARNESS_VERSION,
            written_at=written_at(),
        )
        staged = path.with_name(path.name + ".partial")
        staged.write_text(
            json.dumps(record.payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        os.replace(staged, path)
    except Exception as write_failure:  # noqa: BLE001
        print(f"Could not write {path.name} after the run raised: {write_failure}\n")


def write_calibration_artifact(result: dict, path: pathlib.Path) -> pathlib.Path:
    """Leave this run's record at `path`, overwriting whatever was there.

    IT OVERWRITES, where the second-pass sheet refuses to. That sheet holds
    labels only the owner can produce; this file holds one run's reading of them,
    and a run that kept the older file would let a judge that has since regressed
    go on reporting the figure it earned last week.

    THE OVERWRITE IS ONE `os.replace`. `Path.write_text` truncates first and then
    writes, so a process killed between the two leaves a file that is neither
    run's answer, and the loader reads it as `unreadable` over a judge nobody
    asked about. The record goes to a sibling temp file and replaces the target
    in one operation, so a reader sees the old artifact or the new one.

    JSON, indented and key-sorted, so two runs produce a diff a person can read.
    The path is a parameter rather than the module constant so a test can send an
    artifact somewhere other than the tree the owner labelled.
    """
    record = dataclasses.replace(calibration_record(result), written_at=written_at())
    staged = path.with_name(path.name + ".partial")
    staged.write_text(
        json.dumps(record.payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    os.replace(staged, path)
    return path


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the exit code rather than calling sys.exit."""
    args = sys.argv[1:] if argv is None else argv

    # A run costs one judge call per labelled row. `--help`, `-h` and every typo
    # of `--check` used to fall straight through to that, spend the money, and
    # exit 1 - which a shell reads as "the judge is not calibrated". Unknown
    # arguments now refuse before anything is imported or billed.
    unknown = [a for a in args if a not in KNOWN_FLAGS]
    if unknown or "--help" in args or "-h" in args:
        for line in USAGE:
            print(line)
        return EXIT_SETUP_ERROR if unknown else EXIT_READY_TO_CALIBRATE

    if "--emit-second-pass" in args:
        code, messages = emit_second_pass()
        for message in messages:
            print(message)
        return code

    if "--check" in args:
        return print_readiness(readiness())

    from tests.evals.judge import judge  # noqa: PLC0415

    try:
        result = compute_correlation(judge)
    except Exception as exc:
        write_harness_raised(CALIBRATION_ARTIFACT_JSON, exc)
        raise

    # Before the report, so a run that dies formatting its own output still
    # leaves the record. An OSError here is a disk problem rather than a judge
    # problem, and it may not cost the operator the report they paid for.
    try:
        write_calibration_artifact(result, CALIBRATION_ARTIFACT_JSON)
    except OSError as exc:
        print(f"Could not write {CALIBRATION_ARTIFACT_JSON.name}: {exc}\n")
    except Exception as exc:
        write_harness_raised(CALIBRATION_ARTIFACT_JSON, exc)
        raise

    print(
        f"Calibration run - {result['valid']} scored / {result['attempted']} rows present\n"
    )
    if result["table"]:
        # F1, adversarial review 2026-08-18. This formatter did `j - entry["human"]`
        # and `f"{entry['human']:>6}"`, and 8.2b made `human_score` OPTIONAL. The
        # harness's own printed guidance tells the owner to fill only the binary
        # column, so the INTENDED workflow produces `human_score is None` and this
        # raised TypeError before printing anything. The gate had already computed
        # kappa correctly; the CLI died on the way to showing it, and a shell reads
        # the non-zero exit as "not calibrated" over a judge that was.
        #
        # The VERDICTS are the gate, so they lead. The 1-5 scores are optional and
        # render as "-" when absent.
        print(
            f"{'Scenario':<10}  {'Dimension':<28}  {'Human':>6}  {'Judge':>6}  "
            f"{'Score':>11}  Reason"
        )
        print("-" * 104)
        for entry in result["table"]:
            human_score, judge_score = entry["human"], entry["judge"]
            if human_score is None or judge_score is None:
                score_cell = "-"
            else:
                score_cell = f"{human_score}/{judge_score} ({judge_score - human_score:+d})"
            print(
                f"{entry['scenario_id']:<10}  {entry['dimension']:<28}  "
                f"{(entry.get('human_verdict') or '-'):>6}  "
                f"{(entry.get('judge_verdict') or 'ERR'):>6}  "
                f"{score_cell:>11}  {entry['reason'][:52]}"
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
        # F12, adversarial review 2026-08-18. This branch also carries the
        # KAPPA-UNDEFINED case, where both floors were MET and the cells are fully
        # populated. It printed no matrix, named the two floors as the cause when
        # neither had failed, and pointed at `human_score` - which has not been the
        # gate column since 8.2b. The matrix is the diagnostic on exactly this
        # path: it is what shows that both raters used one label.
        if result.get("cells") and sum(result["cells"].values()):
            _print_agreement(result)
        rate = result.get("pair_rate")
        floors_met = (
            result["pairs"] >= MIN_PAIRS and rate is not None and rate >= MIN_PAIR_RATE
        )
        if floors_met:
            # It used to assert the cause here: "one label was used for every
            # row". That printed over a run whose kappa was 1.000 four lines
            # above, and the remedy it named - relabel a scenario the other way -
            # means writing a verdict the owner does not believe. The gate knows
            # which part was not measured; say that instead of guessing.
            print(
                "NOT CALIBRATED YET - every labelled row produced a pair, and the "
                "measurement still could not be made."
            )
            for reason in (result.get("gate") or {}).get("reasons", []):
                print(f"  {reason}")

        else:
            print(
                f"NOT CALIBRATED YET - {result['pairs']} usable pair(s) out of "
                f"{result['valid']} labelled row(s)"
                + (f" ({rate:.0%})" if rate is not None else "")
                + f"; {MIN_PAIRS} pairs and {MIN_PAIR_RATE:.0%} of the set are needed.\n"
                "This is neither a pass nor a judge failure: the measurement has not been "
                "made.\nRun with --check to see which input is missing. If it is the "
                f"`{REQUIRED_COLUMN}` column,\nthat one is the owner's and must be filled "
                "by a human."
            )
    else:
        _print_agreement(result)
        if status == STATUS_CALIBRATED:
            print(
                f"PASS - the judge is calibrated over {result['pair_rate']:.0%} of the "
                "labelled set. Its kappa interval clears chance AND reaches\n"
                "the labeller's own test-retest interval, so it is not distinguishably "
                "worse than\n"
                "the person who wrote the labels."
            )
        else:
            print(
                "FAIL - the judge is NOT calibrated. The half that failed is named "
                "above, with the interval that decided it.\n"
                "Read the confusion matrix above before touching judge.py: if the "
                "both-fail\ncell is the large one, the AI SYSTEM is what is broken and "
                "tuning the judge\nwill only teach it to agree with a bad product."
            )

    return EXIT_CODE_FOR_STATUS[status]


if __name__ == "__main__":
    sys.exit(main())
