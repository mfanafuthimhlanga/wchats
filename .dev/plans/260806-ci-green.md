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
