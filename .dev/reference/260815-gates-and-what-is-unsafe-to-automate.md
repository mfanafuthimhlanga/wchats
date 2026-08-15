# The declared gate battery, and what must never go in it

`.dev/gates.json` is JSON and cannot carry the reasoning that makes it safe. That reasoning is here.
Read it before widening the battery.

## What is declared

| Lane | Command | Observed runtime |
|---|---|---|
| `fast` | `cd apps/api && .venv/Scripts/python.exe -m pytest tests/unit -q` | ~480s, 2243 tests |
| `full` | `fast`, then admin `tsc`, `check:no-dusk-tokens`, `check:ops-room-wiring`, `test:unit`, then widget build and size check | 10 to 12 min |

`timeoutSec` is **900**. The unit suite was observed at 409s, 441s, 475s and 481s on this machine.
A 120s timeout would have failed all four, and a gate that always fails gets switched off.

## Why the unit suite is safe unattended, and the precedence that makes it so

This repo's `.env` points `CONTROL_DB_URL` and `CONTROL_DB_SYNC_URL` at **live Neon production**
(`ep-falling-glade-...sa-east-1`), and `REDIS_URL` at production Upstash. That is observed, not
inferred.

The unit suite does not reach them because `tests/conftest.py:33-65` sets those variables with
`os.environ.setdefault(...)` at module scope, and pydantic-settings ranks `os.environ` above
`env_file`. The synthetic test URL therefore wins over the production one.

**That precedence is the only thing standing between `pytest tests/unit` and production.** Nothing
asserts it and no comment in the conftest mentions it. Anyone rewriting that block, including
whoever fixes `1.23` (which is about that same block masking the boot contract), must preserve it.

## What must never be declared

- **`tests/integration`, never in `fast` and never unattended.** It needs
  `INTEGRATION_TESTS_ENABLED=1`, a live Postgres and Redis, and it has no conftest precedence
  protecting it. `test_ver01_adversarial_harness` **spends money** (~$0.024 per run, 30 live
  Actor-gate calls). `test_red_team_rtx` is uncosted and drives `claude-sonnet-4-6` attackers,
  plausibly dollars.
- **Anything that provisions Neon.** `test_worker_kill.py` was once one exported `NEON_API_KEY` away
  from creating real billable projects with no teardown. It is stubbed now. A future test may not be.
- **`python` on its own.** Use `.venv/Scripts/python.exe`. The `celery` console script is not on PATH
  either (`1.1`), which is why every worker command in this repo is `python -m celery`.

## Two operational facts a gate runner needs

1. **`uv sync --extra dev` alone uninstalls docling.** Since 2026-08-13 the unit suite includes
   `test_chunking_service.py` and `test_docling_service.py`, which need the `pipeline` extra. Restore
   with both: `uv sync --extra dev --extra pipeline`.
2. **Run long suites detached.** Ordinary backgrounded runs were killed mid-suite three times in one
   week, at roughly 3%, 5% and 31%. A detached `Start-Process ... -RedirectStandardOutput` outlives
   the shell that launched it and completed every time. Each kill costs a full re-run.

## The reporting rule this battery serves

A gate that was skipped is **unobserved**, never a pass. A suite that could not run because a service
was missing did not pass. One that hit the timeout did not run.

This repo has been burned by the inverse. `4.4` records fourteen docling-gated tests that had never
run in repo history while reading as green, and `1.13` records that a permanently skipped test is a
claim nobody checks.
