"""CI's Eval job collects tests that assert on a clean checkout (#194).

`responses/` is gitignored and only nightly fills it, so a test reading it
skips in CI, `assert_tests_ran.py` turns an all-skip run red, and `main` was
red on every commit. The job's `-k` expression now selects the two checks that
read committed files. This file runs pytest's own collection with that
expression, so the selection is pytest's, not a copy of its matching rules.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
API = REPO_ROOT / "apps" / "api"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def eval_step() -> dict:
    steps = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]["eval-deterministic"]["steps"]
    found = [s for s in steps if "tests/evals/run_evals.py" in (s.get("run") or "")]
    assert len(found) == 1, f"expected one step running run_evals.py, found {len(found)}"
    return found[0]


def collected(step: dict) -> set[str]:
    line = next(text for text in step["run"].splitlines() if "pytest" in text)
    args = shlex.split(line)
    k = args[args.index("-k") + 1]
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/evals/run_evals.py", "--collect-only", "-q", "-k", k],
        cwd=API, capture_output=True, text=True, check=False,
    )
    return {row.split("::")[-1] for row in out.stdout.splitlines() if "::" in row}


def test_the_eval_job_collects_the_checks_that_read_committed_files():
    got = collected(eval_step())
    assert got == {"test_deterministic_escalation_band", "test_deterministic_widget_bundle_size"}, got


def test_the_eval_job_requires_the_bundle_it_built():
    assert eval_step().get("env", {}).get("EVAL_REQUIRE_BUNDLE") == "1"
