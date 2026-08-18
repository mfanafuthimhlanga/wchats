r"""Refuse a contaminated calibration corpus at capture time, not at scoring time.

    .venv/Scripts/python.exe tests/evals/validate_corpus.py

Every check here exists because a corpus was accepted as clean and was not. The
E2E-6 set passed the checks that were run (no empties, nothing under 120 chars,
no provider-error text) and still carried four PII deflections and twenty
unnamed tool calls, and that was found by a person reading files rather than by
anything automatic.

A deflection is a well-formed sentence of ordinary length. A tool call named ""
is well-formed JSON. **Malformed-output checks cannot see a well-formed wrong
answer**, so each check below names a specific failure this system is known to
produce and looks for that by value.

Exit codes, distinct because they mean different things:

    0  clean
    1  FATAL - rows that cannot be scored at all. Re-capture is required.
    4  BLIND - the corpus is scorable, but at least one judge dimension has no
       evidence to judge against, so its verdict is decided by the capture
       format rather than by the answer. Scoring it produces a number about the
       wrong thing.
    2  setup error

4 is separate from 1 on purpose. A blind dimension is not a broken row: the
answers are real and a human can score them. It is the JUDGE half of the
correlation that cannot see, and pooling that into "contaminated" would either
overstate the damage or, worse, get muted as noise.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from app.utils.pii_firewall import PII_DEFLECTION  # noqa: E402

RESPONSES_DIR = pathlib.Path(__file__).parent / "responses"

EXIT_CLEAN = 0
EXIT_FATAL = 1
EXIT_SETUP = 2
EXIT_BLIND = 4

#: Below this a response is not an answer. The number is the one the E2E-6
#: acceptance used, kept so this file supersedes that check rather than adding
#: a second opinion beside it.
MIN_ANSWER_CHARS = 120

#: Text that means the PROVIDER failed, not that the agent answered badly. Four
#: files were deleted for carrying this on 2026-08-17 rather than kept and
#: scored (`260817-e2e6-capture-blocked.md`).
_PROVIDER_ERROR = re.compile(
    r"rate.?limit|quota exceeded|service unavailable|internal server error|"
    r"api error|upstream error|try again later",
    re.IGNORECASE,
)


def _fatal_findings(sid: str, record: dict) -> list[str]:
    """Reasons this row cannot be scored by anyone."""
    findings = []
    text = (record.get("response_text") or "").strip()
    calls = record.get("tool_calls_log") or []

    if not text:
        findings.append("response_text is empty")
    elif text == PII_DEFLECTION:
        # BACKLOG 7.29. Scoring a deflection measures the firewall, not the judge.
        findings.append("response is the PII firewall's deflection")
    elif len(text) < MIN_ANSWER_CHARS:
        findings.append(f"response is {len(text)} chars, under the {MIN_ANSWER_CHARS} floor")

    if text and _PROVIDER_ERROR.search(text):
        findings.append("response carries provider-error text")

    unnamed = sum(1 for c in calls if not (c.get("tool_name") or "").strip())
    if unnamed:
        # BACKLOG 7.29, second finding. `run_evals.py` counts escalate and
        # clarify calls BY NAME, and the judge is handed this log verbatim, so
        # an unnamed call is invisible to both.
        findings.append(f"{unnamed} of {len(calls)} tool call(s) have no tool_name")

    return findings


def _looks_like_retrieve(call: dict) -> bool:
    """True for a retrieve call, INCLUDING one whose name was lost in capture.

    Deliberately not `tool_name == "retrieve"` alone. While every call carried
    `tool_name: ""`, a name-only test made the blind check unreachable, so the
    unnamed-tool defect MASKED the missing-chunk defect and fixing the first
    would have revealed the second on the next run. One capture costs live agent
    turns; a validator that reveals its findings one per run is a validator that
    schedules the re-runs it exists to prevent. `input.query` is the shape
    `retrieve_tool` is called with and nothing else in the toolset uses it.
    """
    name = (call.get("tool_name") or "").strip()
    if name == "retrieve":
        return True
    return not name and "query" in (call.get("input") or {})


def _blind_findings(sid: str, record: dict) -> list[str]:
    """Reasons a judge dimension has no evidence, however good the answer is."""
    findings = []
    for call in record.get("tool_calls_log") or []:
        if not _looks_like_retrieve(call):
            continue
        result = call.get("result")
        if not result:
            # grounding_fidelity's rubric: "Every factual claim is traceable to
            # a retrieved chunk PROVIDED IN THE TOOL_CALLS LOG." With no chunk
            # provided, the rubric's PASS branch is unreachable and every row
            # must FAIL regardless of the answer.
            findings.append("a retrieve call carries no result, so grounding_fidelity cannot pass")
            break
    return findings


def validate(responses_dir: pathlib.Path | None = None) -> dict:
    directory = responses_dir or RESPONSES_DIR
    if not directory.exists():
        return {"setup_error": f"{directory} does not exist - run capture_responses.py first"}

    files = sorted(directory.glob("S-*.json"))
    if not files:
        return {"setup_error": f"no S-*.json in {directory}"}

    fatal: dict[str, list[str]] = {}
    blind: dict[str, list[str]] = {}
    for path in files:
        sid = path.stem
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            fatal[sid] = [f"not valid JSON: {exc}"]
            continue
        if found := _fatal_findings(sid, record):
            fatal[sid] = found
        if found := _blind_findings(sid, record):
            blind[sid] = found

    return {"checked": len(files), "fatal": fatal, "blind": blind, "setup_error": None}


def report(result: dict) -> int:
    if result.get("setup_error"):
        print(f"SETUP: {result['setup_error']}")
        return EXIT_SETUP

    print(f"Corpus validation: {result['checked']} recorded response(s)\n")

    for sid, reasons in sorted(result["fatal"].items()):
        for reason in reasons:
            print(f"  FATAL  {sid}  {reason}")
    for sid, reasons in sorted(result["blind"].items()):
        for reason in reasons:
            print(f"  BLIND  {sid}  {reason}")

    if not result["fatal"] and not result["blind"]:
        print("  clean")
        print(f"\nCLEAN - {result['checked']} response(s), nothing known to contaminate them.")
        return EXIT_CLEAN

    print()
    if result["fatal"]:
        print(
            f"FATAL - {len(result['fatal'])} row(s) cannot be scored by anyone. Re-capture is "
            "required; scoring around them measures the defect."
        )
        return EXIT_FATAL
    print(
        f"BLIND - {len(result['blind'])} row(s) give a judge dimension nothing to judge against. "
        "The answers are real and a human can score them, but the judge's half of the "
        "correlation is decided by the capture format."
    )
    return EXIT_BLIND


def main() -> int:
    return report(validate())


if __name__ == "__main__":
    raise SystemExit(main())
