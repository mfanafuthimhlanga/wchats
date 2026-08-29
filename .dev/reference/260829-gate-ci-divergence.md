# Two gates, one tree, different verdicts

`apps/api/scripts/gates.py` and `.github/workflows/ci.yml` both decide whether the backend
is clean. They do not check the same things, and neither one reports the gap. A session
that runs the local gate and sees it pass has learned nothing about whether CI will pass.

Measured 2026-08-29 on `chore/hygiene` at `1003dad`.

## What each one runs

| Check | `gates.py static` | `gates.py full` | CI |
|---|---|---|---|
| ruff | yes | yes | yes |
| import contracts | yes | yes | no |
| complexity (lizard) | yes | yes | no |
| source assertions | yes | yes | no |
| unit tests | no | yes | yes, plus `--cov-fail-under=80` |
| mypy | no | no | yes |
| integration tests | no | no | yes |
| eval checks | no | no | yes |

`grep -c mypy apps/api/scripts/gates.py` returns 0.
`grep -c "lint-imports\|lizard\|gates.py" .github/workflows/ci.yml` returns 0.

## Three members of the class, each measured

**1. A pinned violation that CI rejects.** `RUFF_BASELINE` held
`("app/worker/tasks/pipeline/chunk.py", "I001"): 1`. The local gate printed
`clean against the 1 pinned baseline violation(s)` and exited 0. CI runs bare
`ruff check apps/api/app/ apps/api/tests/` and exited 1 on that same file and rule.
`4eb3325` put the violation on main on 2026-08-24. Every run checked since then carried
the red Lint check: `main@1be7c9d`, merged PR #87, and open PRs #108, #110, #111, #112.
Closed by `1003dad`, which sorted the import block and emptied the dict.

**2. CI installs ruff unpinned.** The Lint job's step is `pip install ruff`, so the version
moves without a commit. On 2026-08-29 CI ran ruff 0.16.5 while `apps/api/.venv` held
0.16.3. A ruff release that adds a rule reds the Lint job with nothing changed in the repo,
and no local run can predict it. Open.

**3. mypy runs only in CI.** #92 records 148 errors across 12 files accumulating since
2026-08-11. Nothing local runs mypy, so the accumulation had no gate to cross. Open.

## What closes the class

One script has to define "clean". Either CI calls `scripts/gates.py` for the checks it
shares, or `gates.py` grows a mypy step and the workflow pins its tool versions to the ones
`pyproject.toml` installs. Fixing member 1 alone repairs an instance and leaves the class
open, which is FM-011.

Related: #95 (the ruff path width), #92 (mypy).
