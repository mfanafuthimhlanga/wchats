---
phase: 20-frontend-cutover
plan: 02
subsystem: ui
tags: [three, threejs, webgl, types, pnpm, apps-admin, supply-chain]

# Dependency graph
requires:
  - phase: 20-frontend-cutover
    provides: RESEARCH.md Package Legitimacy Audit ([SUS] false-positive note for three/@types/three)
provides:
  - three@0.185.1 pinned in apps/admin/package.json dependencies
  - "@types/three@0.185.1 pinned in apps/admin/package.json devDependencies"
  - pnpm-lock.yaml resolution for both packages
  - CDN trust boundary removed (prototype's unpkg.com/three import never reached apps/admin)
affects: [20-05 (landing/auth three.js specimen, client-only dynamic import + code-split)]

# Tech tracking
tech-stack:
  added: ["three@0.185.1", "@types/three@0.185.1"]
  patterns: []

key-files:
  created: []
  modified:
    - apps/admin/package.json
    - apps/admin/pnpm-lock.yaml
    - apps/admin/pnpm-workspace.yaml

key-decisions:
  - "Supply-chain legitimacy checkpoint for three + @types/three was pre-cleared by the orchestrator with verified npm registry evidence (mrdoob/three.js, DefinitelyTyped, millions of weekly downloads) — install proceeded directly without re-blocking"
  - "Added a minimal packages: ['.'] field to apps/admin/pnpm-workspace.yaml to unblock pnpm 9.15.9, which refuses any install/add when a pnpm-workspace.yaml exists without a packages field (Rule 3 blocking-issue fix, not part of the plan's original files_modified list)"

requirements-completed: [UI2-02]

# Metrics
duration: 25min
completed: 2026-07-15
status: complete
---

# Phase 20 Plan 02: three + @types/three pinned install Summary

**Provisioned three@0.185.1 and @types/three@0.185.1 as exact-pinned apps/admin dependencies via pnpm, replacing the Gotham prototype's unpkg.com CDN import and clearing the way for 20-05's code-split landing/auth specimen.**

## Performance

- **Duration:** ~25 min (includes an unplanned pnpm-workspace repair and a 7m22s stale node_modules reinstall)
- **Started:** 2026-07-15T09:46:00Z
- **Completed:** 2026-07-15T10:10:50Z
- **Tasks:** 2/2 (Task 1 checkpoint pre-cleared by orchestrator; Task 2 auto)
- **Files modified:** 3 (package.json, pnpm-lock.yaml, pnpm-workspace.yaml)

## Accomplishments
- `three@0.185.1` added to `apps/admin/package.json` dependencies (exact pin, no caret)
- `@types/three@0.185.1` added to `apps/admin/package.json` devDependencies (exact pin, no caret)
- `apps/admin/pnpm-lock.yaml` records both resolutions (verified via grep: `three@0.185.1` and `'@types/three@0.185.1'` present)
- `pnpm --dir apps/admin build` compiles clean (Next.js 16.2.6 Turbopack, TypeScript pass, all 12 routes generated)
- Confirmed no `unpkg.com/three` or `import('https...` CDN references anywhere in `apps/admin/app`
- three was NOT installed into `apps/widget` or any shared package — scoped to `apps/admin` only

## Task Commits

1. **Task 1: Supply-chain legitimacy checkpoint** - pre-cleared by orchestrator (no code change; documented here per protocol, not a git commit)
2. **Task 2: Install three + @types/three (pinned) and verify build** - `60f6ada` (feat)

**Plan metadata:** (pending — final docs commit follows this SUMMARY)

## Files Created/Modified
- `apps/admin/package.json` - added `three: "0.185.1"` (dependencies) and `@types/three: "0.185.1"` (devDependencies)
- `apps/admin/pnpm-lock.yaml` - locked resolution for `three@0.185.1` and `@types/three@0.185.1`
- `apps/admin/pnpm-workspace.yaml` - added `packages: ['.']` (deviation, see below) alongside the pre-existing `allowBuilds` block, unchanged

## Decisions Made
- Honored the orchestrator's pre-cleared supply-chain checkpoint evidence (npm registry: `three` → mrdoob/three.js, MIT, not deprecated; `@types/three` → DefinitelyTyped) rather than re-blocking on Task 1 — the checkpoint's `[SUS]` flag was a documented release-cadence false positive, and the operator-facing verification was already satisfied before this run started.
- Used `corepack pnpm` (via `corepack prepare pnpm@9 --activate`) instead of a bare `pnpm` binary, since no global pnpm was on PATH in this environment and `corepack enable` failed with EPERM (no write access to `Program Files\nodejs`). `corepack pnpm <cmd>` works without needing the shim installed system-wide.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Repaired apps/admin/pnpm-workspace.yaml missing `packages` field**
- **Found during:** Task 2 (Install three + @types/three)
- **Issue:** `apps/admin/pnpm-workspace.yaml` contained only an `allowBuilds` block with no `packages` field. pnpm 9.15.9 treats any directory containing a `pnpm-workspace.yaml` as a workspace root and hard-errors on `pnpm add`/`pnpm install` with `packages field missing or empty` unless a `packages` array is present. This blocked every install command and was not caused by this task's changes — pre-existing repo state.
- **Fix:** Added `packages:\n  - '.'` to the top of `apps/admin/pnpm-workspace.yaml`, declaring the app directory itself as the sole workspace package (correct given `apps/admin` has no root-level `package.json` or nested workspace — it is a standalone pnpm project that happens to have a `pnpm-workspace.yaml` for the `allowBuilds` setting). Left `allowBuilds` untouched.
- **Files modified:** apps/admin/pnpm-workspace.yaml
- **Verification:** `pnpm install` and `pnpm add` both succeed after the fix; `pnpm add three@0.185.1` then correctly warned `ERR_PNPM_ADDING_TO_ROOT` (expected workspace-root behavior) and completed cleanly with `-w`.
- **Committed in:** 60f6ada (Task 2 commit)

**2. [Rule 3 - Blocking] Regenerated stale node_modules (virtual-store-dir path mismatch)**
- **Found during:** Task 2, immediately after the pnpm-workspace.yaml fix
- **Issue:** `node_modules/.modules.yaml` recorded a `virtualStoreDir` pointing at an old project path (`...\veridian\apps\admin\node_modules\.pnpm`) — a leftover from before the repo/directory was renamed to `wchats`. pnpm refused to proceed (`ERR_PNPM_VIRTUAL_STORE_DIR_MAX_LENGTH_DIFF`) and required a full reinstall to recreate `node_modules` against the current path.
- **Fix:** Ran `pnpm install` (non-interactively via `CI=true`) to remove and rebuild `node_modules` from the existing lockfile. All 113 previously-locked packages resolved identically (no dependency drift) before the `three`/`@types/three` additions were layered on top.
- **Files modified:** none tracked (node_modules is gitignored); no lockfile change from this step alone
- **Verification:** Post-reinstall `pnpm add three@0.185.1 -w` and `pnpm add -D @types/three@0.185.1 -w` both completed successfully; final `pnpm build` green.
- **Committed in:** n/a (node_modules is not version-controlled)

---

**Total deviations:** 2 auto-fixed (both Rule 3 - blocking issues, both pre-existing repo/environment state unrelated to the plan's intended scope, neither introduced new dependency versions or CDN references)
**Impact on plan:** Both fixes were prerequisites for running any pnpm command in this environment at all — without them Task 2 could not execute. No scope creep: the only intentional dependency changes are the two packages the plan specified, at the exact pinned versions specified.

## Issues Encountered
- Global `pnpm` binary not on PATH; `corepack enable` failed with `EPERM` writing to `C:\Program Files\nodejs\pnpx` (no admin rights in this shell). Resolved by using `corepack pnpm <cmd>` directly (works without the global shim) after `corepack prepare pnpm@9 --activate`.
- A pre-existing peer-dependency warning from `@clerk/nextjs`/`@clerk/react`/`@clerk/shared` (expects React `~19.0.3|~19.1.4|~19.2.3|~19.3.0-0`, found `19.2.0`) surfaced on every install. This is unrelated to `three`/`@types/three` and out of scope for this plan — not fixed, not blocking (`pnpm build` still passes). Logged here for visibility, not added to deferred-items.md since it predates this plan entirely and does not affect this plan's acceptance criteria.

## User Setup Required
None - no external service configuration required. The legitimacy checkpoint (Task 1) required human/operator verification, which the orchestrator confirmed was already satisfied with documented registry evidence before this execution began.

## Next Phase Readiness
- `three` and `@types/three` are installed, pinned, and lockfile-recorded in `apps/admin` — 20-05 (landing/auth three.js specimen) can now add a client-only dynamic `import('three')` without any CDN dependency.
- `apps/admin` build is green with the new dependency present but not yet imported anywhere (as intended — 20-05 owns first usage and the code-split boundary).
- No blockers for 20-05.

---
*Phase: 20-frontend-cutover*
*Completed: 2026-07-15*

## Self-Check: PASSED
- FOUND: apps/admin/package.json
- FOUND: apps/admin/pnpm-lock.yaml
- FOUND: apps/admin/pnpm-workspace.yaml
- FOUND: 20-02-SUMMARY.md
- FOUND: commit 60f6ada
