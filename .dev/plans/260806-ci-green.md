# CI green — make the gate real for the first time

**Branch:** `fix/ci-green` off `feat/eval-foundation` (`7b2c68d`) · **Engine:**
`.dev/workflows/ci-green.workflow.js` — 3 sequential gated phases, each `impl → adversarial review →
bounded fix`, then the tier-2 judge.

**Scope:** `.github/workflows/ci.yml`, `apps/api/pyproject.toml`, `apps/api/tests/integration/conftest.py`,
and whatever lint/type fixes require across `apps/api/`. No behaviour change. No new dependency
beyond `pytest-cov`.

---

## Why

CI has **never been green**. All 7 recorded runs (2026-07-27 → 2026-08-06) failed, and only 7 exist
because `ci.yml` triggers on push/PR to `main` and `main` sat unpushed for 8 weeks. A gate nobody
reads is not a gate — the same defect class as `.dev/reference/measurement-layer-audit.md` D3 and
`retro.md` Family B, this time in the build system rather than the product.

None of the four failures was introduced by the eval-foundation work. All are pre-existing.

## Why this branches off `feat/eval-foundation` and not `main`

The eval branch adds ~16k lines and 458 tests. Fixing lint against `main` alone would leave every new
eval file unlinted, and merging PR 2 would reintroduce violations into a just-cleaned tree. Cleaning
the tip cleans everything once.

Consequence: this becomes the third PR in the stack. Merge order is #1 → #2 → #3.

## Phases

### P1 — The infrastructure, and the trigger gap

Three bugs, each small, one of which is why nobody has seen a CI result on the current work at all:

1. **`ci.yml` triggers only on PRs targeting `main`** (`on.pull_request.branches: [main]`). Every
   stacked PR therefore gets **zero checks** — PR #2's 16k-line diff has never been CI-verified.
   Change to run on all pull requests, keeping `push` restricted to `main`.
2. **`pytest-cov` is missing.** `ci.yml:74` passes `--cov=app --cov-fail-under=80`; the `dev` extra is
   `pytest`, `pytest-asyncio`, `respx`. pytest exits 4 with `unrecognized arguments`. Add
   `pytest-cov` to the `dev` extra. **Report the real coverage number the first time it runs** — if it
   is below 80 the gate fails for a true reason, and that is a finding to surface, not to paper over
   by lowering the threshold. Do not change `--cov-fail-under` without recording why.
3. **A hardcoded absolute path.** `apps/api/tests/integration/conftest.py:221` sets
   `cwd="/c/Users/Bantu/mzansi-agentive/veridian/apps/api"` — one developer's machine, and the *old*
   project name. Derive it from `pathlib.Path(__file__)` instead.

Also record, do not fix: `pyproject.toml`'s ruff config uses deprecated top-level `select`/`ignore`
(warned on every run). P2 owns it if it is in the way.

**Tests:** the conftest path resolves correctly from any working directory; a test that would fail if
it were hardcoded again.

### P2 — ruff to zero

461 violations. `--fix` handles 427, and **that is the dangerous part of this plan.**

```
257  I001  unsorted-imports              auto
151  F401  unused-import                 auto — RISKY, see below
 21  F841  unused-variable               manual
 15  F541  f-string-missing-placeholders auto
  8  E402  module-import-not-at-top      manual — LIKELY DELIBERATE
  6  F811  redefined-while-unused        manual
  3  F821  undefined-name                benign, see below
```

**F401 is not safely auto-fixable in this codebase.** An import can exist for its side effect, and
removing it breaks something no linter can see. Before accepting any F401 removal, check whether the
import is:
- a SQLAlchemy model imported so its table registers on the metadata,
- an Alembic revision or `env.py` import,
- a re-export in an `__init__.py` (the public surface of a package),
- a Celery task module imported so the task registers,
- a pytest fixture/conftest import,
- part of the `claude_agent_sdk` import-guard pattern — this repo has a **recorded test-ordering bug**
  where `test_agent_chat_routes.py` importing `app.main` pulled in the real SDK first and broke
  `test_agent_tools.py`'s `if "claude_agent_sdk" not in sys.modules` guard, turning 18 tests red only
  in full-suite order.

If an import is load-bearing, keep it and silence it explicitly (`# noqa: F401` with a one-line reason,
or a re-export in `__all__`) rather than deleting it.

**E402's 8 late imports are probably deliberate** — this codebase uses function-local and late imports
to break circular dependencies (`red_team_service.py` imports `red_team_probe` inside function bodies
for exactly this reason, documented at `:816`). Do not hoist an import to the top of a module to
satisfy a linter and reintroduce a cycle. `# noqa: E402` with the reason is the correct fix where the
lateness is structural.

**The 3 F821s are benign** — `test_stripe_live.py:77,94` string annotations naming types whose imports
are deliberately function-local because the module is gated. A `TYPE_CHECKING` import is the clean fix.

**Verification is the whole point of this phase:** the unit suite must remain at **1657 passed /
11 skipped / 0 failed**. A single test lost to an import removal is a blocker, not an acceptable
delta. Run it before and after and report both numbers.

### P3 — mypy to zero

`python -m mypy app/ --ignore-missing-imports --strict-optional`. Two distinct classes, and they need
different answers:

1. **`config.py:193` — ~10 `Missing named argument` errors on a `Settings()` call.** This is
   pydantic-settings loading from the environment; mypy cannot model it. **This is a false positive
   and must not be "fixed" by passing fake arguments.** Correct fixes, in order of preference: the
   pydantic mypy plugin (`[tool.mypy] plugins = ["pydantic.mypy"]`), or a narrowly-scoped ignore at
   that call site with a comment naming why. Never widen strictness settings globally to make it pass.
2. **Genuine `str | None → str` assignments** — `retrieval_metrics_service.py:80`,
   `identity_service.py:273`, and any others the full run surfaces. These are real: a `None` reaching
   a `str` is a latent `AttributeError`. **Fix the narrowing, do not cast.** If a value genuinely
   cannot be `None` at that point, assert it and let the assertion be the proof; if it can, handle it.

**Tests:** for every genuine narrowing fixed, a test that exercises the `None` path — otherwise the
fix is a type-checker appeasement with no behavioural evidence.

## Non-goals

- Any product behaviour change. This branch changes types, imports, config and CI only.
- Lowering `--cov-fail-under` to make coverage pass (report the real number instead).
- Touching the frontend gates (`tsc`, `check:no-dusk-tokens`, `check:ops-room-wiring`) — they are not
  in `ci.yml` at all, which is its own gap and a separate plan.
- The `nightly.yml` E2E workflow, also failing, also pre-existing.

## Risks

- **The whole phase-2 diff is mechanical and enormous**, which is exactly the shape of change that
  hides one real breakage among 400 harmless ones. The test count is the guard; treat any movement in
  it as a blocker.
- **`--cov-fail-under=80` has never run.** Real coverage is unknown and may be well below 80, in which
  case P1 turns one broken check into one honestly-failing check. That is progress, and it must be
  reported plainly rather than tuned away.
- **The integration job may fail for further reasons** once the hardcoded path is fixed — it has never
  reached the actual tests. Expect a second layer.

---

## Execution log — findings that outlive the phase that found them

Appended by each phase after its work lands. The scope sections above are the contract and are not
edited; this is the record of what executing them actually turned up. A commit message is the weakest
durable location for a finding, so anything a later phase or a reviewer needs goes here too.

### P1 (recorded, not fixed)

- `apps/api/pyproject.toml` used ruff's deprecated top-level `select`/`ignore`. **Closed by P2.**
- `apps/api/pyproject.toml:42` declares `PyJWT[cryptography]==2.12.1`; pyjwt 2.12.1 ships no extra
  named `cryptography`, so uv warns on every resolve and the extra is silently ignored. Nothing breaks
  today, but the dependency does not express what it thinks it does. **Still open.**
- No job anywhere executes the 10 docling-gated tests; they have never run in repo history.
  `tests/unit/test_pipeline_patch_targets.py` keeps their patch targets falsifiable but asserts nothing
  about their behaviour. **Still open.**

### P2 (ruff 456 → 0)

Closed: the ruff config deprecation. Everything below is open.

- **`tests/unit/test_services.py::TestWaitForNeonReady::test_wait_for_neon_ready_retries_then_succeeds`
  is intermittently red.** Observed 1 failure in 11 identical full-suite runs of the same tree
  (~9%); passes in isolation. It patches `app.services.neon.time.sleep`, which is the shared `time` module's
  attribute, so the mock is process-global for the duration, and `mock_sleep.assert_called_once_with(1)`
  is falsified by any other caller. Five daemon threads are alive throughout the unit run —
  `OtelBatchSpanRecordProcessor`, `MediaUploadConsumer`, `ScoreIngestionConsumer`,
  `PromptCacheRefreshConsumer`, `_flush_loop` — started at import time by
  `app/services/validation_service.py:29` constructing `Langfuse()`. Pre-existing: the decorators and
  the assertion are byte-identical to the pre-P2 tree. **This matters for the branch's own goal**: the
  CI unit job runs with `-x`, so one occurrence aborts the run and *additionally* prints a coverage
  failure, which reads as a code regression. Not fixed here because the mechanism was inferred, never
  captured — no traceback was obtained in 6 runs — and weakening an assertion on an unproven diagnosis
  is the papering-over this branch exists to stop. Whoever picks this up: the cheapest next step is a
  loop of the gate command capturing `--tb=long`, to find out which of the three assertions failed.
  If it is `mock_sleep.assert_called_once_with(1)`, the thread hypothesis is confirmed and the fix is
  `assert_any_call(1)` — `mock_create_engine.call_count == 2` already pins the loop to exactly one
  sleep, so nothing real is lost. Take the green baseline for that loop from the coverage bullet
  below (it is per-commit, and it moves whenever a test is added): a passing run whose count differs
  from it is a stale record, not the flake and not a regression, so reconcile it against a fresh
  measurement before chasing anything.
- **Five `patch("app...")` sites in the suite name something that does not exist**, across two
  `(file, target)` pairs. All pre-existing, both pinned — with a reason *and now an exact site count* —
  in `tests/unit/test_patch_targets_resolve.py::_KNOWN_BROKEN`. The full list, so a later phase knows
  when it is done:
  - `tests/integration/test_ingestion_chain.py` → `app.services.chunking_service.HybridChunker`,
    **4 sites** (lines 580, 720, 865, 955). The identical defect P1 corrected in
    `tests/unit/test_chunking_service.py`, still live in the integration copy, which has never reached
    a test.
  - `tests/integration/test_actor_latency.py` → `app.services.transactional.tools.get_adapter`,
    **1 site** (line 221). The module binds `get_adapter_for_skill`; the old name was never re-pointed.

  There is no sixth. Re-measure rather than trusting this paragraph — from `apps/api`:
  `.venv/Scripts/python.exe tests/unit/test_patch_targets_resolve.py` prints the three counts
  (`targets_scanned` / `unresolvable_sites` / `pinned_targets`; **1157 / 5 / 2** at `50d97f9`).
- **`tests/integration/test_integration_e2e.py` has zero T-16-01 credential-leak coverage.** The block
  that claimed it built two strings and looped over six forbidden patterns with `pass` as the body.
  The dead code is gone and a comment marks the gap; the real assertion still needs writing, by a phase
  that can execute the module.
- **`tests/unit/test_parse_task.py`'s `mock_parse.assert_not_called()` is vacuously true.**
  `pipeline/parse.py` only ever calls `parse_document_from_bytes`, so the patched `parse_document` could
  not have been called. The import is kept (`# noqa: F401`) because deleting it turns four tests into
  AttributeErrors — observed — but the assertion proves nothing.
- **Coverage margin narrowed to 0.86 points.** CI's own command
  (`pytest tests/unit -x --tb=short --cov=app --cov-fail-under=80`) gives **1671 passed, 13 skipped,
  80.86%** against `--cov-fail-under=80` at `50d97f9`, down from the 81.17% P1 measured at `8bf225e`
  (1668 passed, 13 skipped) — clearing F401 deletes covered module-level import statements. Measured on
  Windows with `apps/api/.env` present; CI is Linux with no `.env` and a fresh pip resolve. The
  threshold was not touched.

  The two skips CI sees and the repo gate does not are `test_chunking_service.py` and
  `test_docling_service.py`, which the gate command `--ignore`s and CI collects-and-skips (docling is
  not installed here). So the same tree reads 1671/11 under the gate command and 1671/13 under CI's.

  The P2 review-fix commit adds one guard test, taking the branch to **1672 passed / 13 skipped /
  80.89%** under CI's command and 1672/11 under the gate command. Note the coverage moved without any
  `app/` change and without the new test importing `app`: 80.86% → 80.89% on the same product code, so
  the percentage jitters by a few hundredths run to run (the Langfuse daemon threads execute `app/`
  code on their own schedule). At a 0.86-point margin that is harmless; it is worth knowing before
  anyone reads a small coverage move as a real one.

#### Corrections to this record (re-measured 2026-08-06 at `50d97f9`)

Three counts above and in `b318bda`'s commit message were transcribed from a scroll-back rather than
re-measured, and each was wrong by one step. Corrected in place above; kept here because a branch whose
whole claim is *the numbers are measured* should show its own misses rather than quietly restate them.

| Claimed | Actual | How it went wrong |
| --- | --- | --- |
| `1668 passed, 13 skipped` under CI's command, post-P2 | `1671 passed, 13 skipped` | The **pre-P2** pass count, paired with the post-P2 coverage figure. Verified: a worktree at `8bf225e` gives exactly 1668/13/81.17%. |
| "Six `patch(...)` targets ... do not exist" | **5** sites / **2** pinned pairs | Neither reading of the module's output is six. `b318bda` repeats it. |
| "all **1158** `patch(...)`/`setattr(...)` string targets" (`b318bda`) | **1157** | Off by one. The module docstring's looser "~1150" was never wrong. |

Two of the three are now checked by the suite rather than by prose:
`test_patch_targets_resolve.py::test_known_broken_site_counts_are_exact` pins each entry's site count
and asserts the pins account for every unresolvable site, so "five" fails the build if it drifts either
way — including the case that motivated it, a later phase fixing 3 of the 4 `HybridChunker` sites and
leaving the older assertions green. The scanned-target total stays a loose lower bound on purpose
(pinning 1157 exactly would break on every test that adds a `patch`); `_measure()` and the module's
`__main__` entry point exist so it is re-read in one command instead of remembered.
