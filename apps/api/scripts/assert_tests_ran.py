"""Fail a CI step whose test run asserted nothing (#102).

WHAT WENT WRONG WITHOUT IT
    The "Eval (deterministic checks)" job reported a green check over this::

        collecting ... collected 2 items / 1 deselected / 1 selected
        tests/evals/run_evals.py::test_deterministic_dimensions_d5_d6_d7 SKIPPED
        ======================= 1 skipped, 1 deselected in 0.08s ==================

    One test, skipped, 0.08 seconds, under a job name claiming five checks. The
    test itself is right to skip: `run_evals.py` converts an unmeasured run into
    a skip rather than a pass, because a skip reads as unobserved and a pass
    reads as evidence. The workflow then converted the skip back into a passing
    check, which puts the false reading one layer up. This is the rule CLAUDE.md
    states for a metric applied to a job: a gate over zero observations is
    unknown, never pass.

WHAT IT READS
    The JUnit XML pytest writes with `--junitxml`. `passed` is not an attribute
    there, so it is derived: tests minus failures, errors and skips. A run whose
    derived count is zero exits 1 and names what it saw. Everything else,
    including a run with real failures, is left to pytest's own exit code, which
    the step already honours.

    THE DERIVATION UNDERCOUNTS, AND THAT DIRECTION IS THE SAFE ONE. A test that
    passes and then errors in teardown is one `tests` and one `errors`, so it
    subtracts twice and can drive the count below zero. Undercounting turns a
    real run red, which a reader investigates; overcounting would turn an empty
    run green, which nobody looks at again. The count is floored at zero so the
    message says "0 passed" rather than a negative number nobody can act on.

    A REPORT IT CANNOT PARSE IS ALSO A FAILURE. An empty file, a truncated one,
    or a pytest error message where the XML should be all mean the step wrote
    nothing readable, which is unknown and never a pass. Each one exits 1 naming
    the file's size and its first line, rather than raising the parser's
    traceback at a reader who then cannot tell a broken report from a broken
    script.

USAGE
    python -m pytest ... --junitxml=out.xml
    python scripts/assert_tests_ran.py out.xml
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def counts(report: Path) -> tuple[int, int, int, int]:
    """(tests, failures, errors, skipped) summed over every suite in the report."""
    root = ET.parse(report).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    totals = [0, 0, 0, 0]
    for suite in suites:
        for index, name in enumerate(("tests", "failures", "errors", "skipped")):
            totals[index] += int(suite.get(name, 0))
    return totals[0], totals[1], totals[2], totals[3]


def passed_count(tests: int, failures: int, errors: int, skipped: int) -> int:
    """How many tests passed, derived, since JUnit records no such attribute.

    FLOORED AT ZERO. A test that passes and then errors in teardown is one
    `tests` and one `errors`, so it subtracts twice; one that fails AND errors in
    teardown subtracts three times and takes the total below zero. The
    undercount is the safe direction, because it turns a real run red and a
    reader investigates that. The floor is about the message: a negative count
    reads as a broken script rather than a finding about the run.
    """
    return max(0, tests - failures - errors - skipped)


def head(report: Path, limit: int = 200) -> str:
    """The first non-blank line of `report`, so a message can say what was there."""
    try:
        text = report.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"<unreadable: {exc}>"
    line = next((stripped for stripped in map(str.strip, text.splitlines()) if stripped), "")
    return line[:limit] + ("..." if len(line) > limit else "")


def unreadable(report: Path, exc: ET.ParseError) -> int:
    """Say what the file holds instead of XML, then fail closed."""
    print(
        f"{report} is not parseable XML ({exc}). It holds {report.stat().st_size} "
        f"bytes and begins {head(report)!r}. A step whose report cannot be read "
        f"asserted nothing that can be read, which is unknown, never a pass (#102). "
        f"An empty file usually means pytest died before it wrote the report, so "
        f"read the step's own output above.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: assert_tests_ran.py <junit.xml>", file=sys.stderr)
        return 2

    report = Path(argv[1])
    if not report.exists():
        print(
            f"{report} does not exist, so the test step wrote no report and nothing "
            f"can be said about what it ran.",
            file=sys.stderr,
        )
        return 1

    try:
        tests, failures, errors, skipped = counts(report)
    except ET.ParseError as exc:
        return unreadable(report, exc)

    passed = passed_count(tests, failures, errors, skipped)
    if passed > 0:
        print(f"{passed} passed, {skipped} skipped, {failures} failed, {errors} errored in {report}")
        return 0

    print(
        f"NOTHING PASSED. {report} records {tests} tests: {skipped} skipped, "
        f"{failures} failed, {errors} errored. A step that asserted nothing is "
        f"unknown, never a pass (#102). Read the -rs lines above for each skip's "
        f"reason.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
