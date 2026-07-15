---
phase: 20-frontend-cutover
plan: 01
subsystem: testing
tags: [playwright, axe-core, a11y, node, admin, next.js]

# Dependency graph
requires:
  - phase: 20-frontend-cutover
    provides: "20-02 pinned three@0.185.1 (file-serialization dependency on package.json only, no functional dependency)"
provides:
  - "Node Playwright (@playwright/test) + @axe-core/playwright installed as devDependencies in apps/admin, chromium browser downloaded"
  - "apps/admin/playwright.config.ts with 3 named viewport projects (desktop-1440, laptop-1280, tablet-900) and a webServer block booting `pnpm dev` on localhost:3000"
  - "apps/admin/scripts/check-no-dusk-tokens.mjs — SC1/UI2-07 grep gate over app/ + public/, reports file:line + forbidden filenames, currently non-zero (~650 findings) as expected pre-cutover"
  - "Four runnable Playwright spec stubs under apps/admin/tests/ (smoke, overflow, reduced-motion, a11y), all test.fixme()'d, discoverable via `playwright test --list` (30 tests across 3 projects x 4 files)"
  - "package.json scripts test:e2e and check:no-dusk-tokens"
affects: ["20-03 (token cutover — flips the grep gate closer to green)", "20-14 (final dusk-page deletion — grep gate should reach zero findings)", "20-15 (fills in the four spec stubs with real assertions, the Wave 5 parity gate)"]

# Tech tracking
tech-stack:
  added: ["@playwright/test@1.61.1 (devDependency)", "@axe-core/playwright@4.12.1 (devDependency)", "chromium browser (via `playwright install`)"]
  patterns: ["Node-based Playwright harness replacing the broken Python launcher (scripts/verify_new_page.py) per project constraint", "test.fixme() stub pattern for specs that depend on not-yet-rebuilt routes — file parses/enumerates now, real assertions land in a later wave"]

key-files:
  created:
    - apps/admin/playwright.config.ts
    - apps/admin/scripts/check-no-dusk-tokens.mjs
    - apps/admin/tests/smoke.spec.ts
    - apps/admin/tests/overflow.spec.ts
    - apps/admin/tests/reduced-motion.spec.ts
    - apps/admin/tests/a11y.spec.ts
  modified:
    - apps/admin/package.json
    - apps/admin/pnpm-lock.yaml

key-decisions:
  - "playwright.config.ts webServer.command uses `pnpm dev` (not `pnpm --dir apps/admin dev`) because Playwright's webServer spawns with cwd = the config file's own directory (apps/admin), so the --dir flag would have resolved to a nonexistent apps/admin/apps/admin nested path"
  - "check-no-dusk-tokens.mjs forbidden-marker list built from UI-SPEC §10 anti-pattern 2's enumeration (glass/accent/lilac/cyan/amber/gold/text-1..4/radius-/shadow-/font-display/bg-deep/brass/Fraunces/amber-console/skyline) plus the literal skyline-w-chats.png filename, checked both as file content and as a forbidden basename (since public/ assets are binary and can't be content-grepped)"
  - "Comment stripping implemented as: replace /* ... */ blocks with same-length whitespace (preserves line numbers for accurate file:line reporting), then truncate JS/TS-family lines at the first // — a best-effort textual pass, not a full parser, documented as a limitation in the script header"

requirements-completed: [UI2-08]

# Metrics
duration: ~15min
completed: 2026-07-15
status: complete
---

# Phase 20 Plan 01: Playwright + axe Validation Harness Summary

**Installed Node `@playwright/test` + `@axe-core/playwright` with a 3-viewport config, a ~650-finding SC1/UI2-07 token-grep gate, and four runnable-but-fixme'd Playwright spec stubs for the Wave 5 parity gate.**

## Performance

- **Duration:** ~15 min
- **Tasks:** 2/2 completed
- **Files modified:** 7 (2 modified: package.json, pnpm-lock.yaml; 5 created: playwright.config.ts, check-no-dusk-tokens.mjs, 4 spec files)

## Accomplishments
- `@playwright/test@1.61.1` + `@axe-core/playwright@4.12.1` installed as devDependencies; chromium browser downloaded and verified (`playwright --version` → 1.61.1)
- `playwright.config.ts` authored with 3 named viewport projects (desktop-1440, laptop-1280, tablet-900, all height 900) and a `webServer` block
- `check-no-dusk-tokens.mjs` grep gate authored and run: reports 652 findings across dusk-era pages (`.glass-strong`, `.on-photo`, `--text-1..4`, `--accent`, `--lilac`, `--gold`, `--radius-*`, `--font-display`, `Fraunces`, etc.) plus the forbidden `public/skyline-w-chats.png` asset filename — non-zero exit is the correct, expected Wave 0 state
- Four spec stubs (`smoke.spec.ts`, `overflow.spec.ts`, `reduced-motion.spec.ts`, `a11y.spec.ts`) created; `playwright test --list` enumerates 30 tests (3 projects × 4 files × per-route tests) with zero config errors
- `pnpm --dir apps/admin build` verified still compiling cleanly (no regression from the config/script additions)

## Task Commits

Each task was committed atomically:

1. **Task 1: Install Playwright + axe and author playwright.config.ts** - `881261a` (feat)
2. **Task 2: Author the token-grep gate + four Playwright spec stubs** - `e8c8298` (test)

_No separate plan-metadata commit — the orchestrator owns STATE.md/ROADMAP.md updates for this plan per the execution brief._

## Files Created/Modified
- `apps/admin/playwright.config.ts` - Playwright config: testDir `./tests`, 3 viewport projects, webServer boots `pnpm dev` on :3000
- `apps/admin/scripts/check-no-dusk-tokens.mjs` - Recursive text-content + filename scan of `app/` and `public/` for retired dusk-theme markers; exits non-zero with a file:line report when found
- `apps/admin/tests/smoke.spec.ts` - Per-route (`/`, `/agents`, `/agents/new`) no-console-error load stub, `@smoke`-tagged describe block, `test.fixme()`
- `apps/admin/tests/overflow.spec.ts` - Per-route `scrollWidth <= clientWidth` stub, relies on the 3 config-level viewport projects
- `apps/admin/tests/reduced-motion.spec.ts` - `test.use({ reducedMotion: 'reduce' })` + placeholder `matchMedia` assertion
- `apps/admin/tests/a11y.spec.ts` - Per-route `AxeBuilder().analyze()` stub, filters critical/serious violations
- `apps/admin/package.json` - Added `test:e2e` and `check:no-dusk-tokens` scripts; added the two new devDependencies
- `apps/admin/pnpm-lock.yaml` - Regenerated by `pnpm add` (companion to the package.json dependency change, not separately listed in the plan's `files_modified` but required for lockfile/dependency-tree consistency)

## Decisions Made
- Used `pnpm dev` instead of the plan's literal `pnpm --dir apps/admin dev` inside `webServer.command`, because Playwright resolves the webServer's working directory to the config file's own directory by default — the `--dir` form would have looked for a nonexistent nested `apps/admin/apps/admin`. The plan's prose was describing the *concept* (boot the admin dev server) using the same command style used throughout the plan for repo-root invocation; the literal in-config command needed to be relative to its own cwd.
- Built the grep gate's forbidden-marker list directly from UI-SPEC §10 anti-pattern 2's named token families rather than reusing VALIDATION.md's SC1 sampling grep verbatim (which also lists the bare string `"Hillbrow"` — that string is legitimate in-app demo/example content unrelated to the retired design system, e.g. the "Hillbrow Realty" demo agent name and placeholder copy, so including it would produce permanent false positives after cutover). This matches the plan's own Task 2 action text, which does not list "Hillbrow" among the markers to grep.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Committed the regenerated pnpm-lock.yaml alongside package.json**
- **Found during:** Task 1 (dependency install)
- **Issue:** `pnpm add -D @playwright/test @axe-core/playwright` regenerated `apps/admin/pnpm-lock.yaml`, which is not listed in the plan's `files_modified` but must stay in sync with `package.json` for reproducible installs
- **Fix:** Staged and committed `pnpm-lock.yaml` in the same Task 1 commit as `package.json`
- **Files modified:** `apps/admin/pnpm-lock.yaml`
- **Verification:** `pnpm --dir apps/admin build` succeeds; `playwright test --list` and the grep gate both run cleanly against the installed deps
- **Committed in:** `881261a` (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking — lockfile consistency)
**Impact on plan:** No scope creep; a standard lockfile-follows-manifest correction required for a reproducible install.

## Issues Encountered
- `pnpm add -D` initially refused with `ERR_PNPM_ADDING_TO_ROOT` because `apps/admin/pnpm-workspace.yaml` declares `packages: ['.']`, making pnpm treat the install as workspace-root-adjacent. Resolved by re-running with `-w` (`pnpm add -D -w ...`), which is the correct/expected flag for this self-contained single-package workspace layout (confirmed pre-existing and legitimate per the plan's key_context).
- `playwright test --list` returned exit code 1 with "No tests found" immediately after Task 1 (before Task 2's spec files existed) — this is Playwright's normal "zero matching test files" message, not a config/schema error; confirmed by re-running after Task 2 added the spec files, which produced a clean 30-test enumeration with exit 0.

## Known Stubs
All four spec files under `apps/admin/tests/` contain `test.fixme()`-marked test bodies that do not assert real behavior yet — this is intentional per the plan (`type="auto"`, task 2's action explicitly authorizes fixme stubs "that depend on not-yet-rebuilt pages"). They parse, compile, and enumerate correctly under `playwright test --list`; 20-15 (Wave 5 parity gate) replaces the fixme placeholders with real assertions once the Gotham routes exist.

## Next Phase Readiness
- The browser-test harness is fully installed and runnable: `pnpm --dir apps/admin exec playwright test --list` and `node apps/admin/scripts/check-no-dusk-tokens.mjs` are both available to every subsequent wave for fast feedback sampling.
- The grep gate currently fails loudly and specifically (652 findings) — this is the correct baseline; later waves (20-03 token cutover, 20-14 dusk-page deletion) should watch this count trend to zero, not flip to green in one step.
- No blockers for Wave 2+ route-rebuild plans.

---
*Phase: 20-frontend-cutover*
*Completed: 2026-07-15*

## Self-Check: PASSED

All 6 created files verified present on disk; both task commits (881261a, e8c8298) verified in git log.
