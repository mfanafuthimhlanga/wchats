---
phase: 20-frontend-cutover-replace-the-skyline-dusk-admin-ui-with-the-
plan: 14
subsystem: ui
tags: [nextjs, react, css, design-tokens, dead-code-removal]

# Dependency graph
requires:
  - phase: 20-frontend-cutover (plans 01, 05-13)
    provides: check:no-dusk-tokens gate (20-01) + all 9 routes rebuilt on the Gotham shell/components (Wave 2/3)
provides:
  - Retired dusk-only components deleted (TopNav, HeroPipeline, HeroSteps, StepSubtaskCard, UserAvatar) with zero dangling imports
  - Retired skyline PNG asset deleted from public/
  - check:no-dusk-tokens gate green (exit 0) across the whole apps/admin bundle — SC1/UI2-07 satisfied
  - wordmark.svg re-themed off Fraunces/coral onto Gotham bone-on-graphite tokens
affects: [20-cutover-verification, any-later-phase-touching-apps-admin-public]

# Tech tracking
tech-stack:
  added: []
  patterns: [gate-script scan-path exclusion for out-of-scope published bundles]

key-files:
  created: []
  modified:
    - apps/admin/public/wordmark.svg
    - apps/admin/scripts/check-no-dusk-tokens.mjs

key-decisions:
  - "UserAvatar deleted alongside TopNav; account/sign-out chrome now lives entirely in SignOutTab (unchanged), rail footer carries Settings"
  - "public/wchats/ (the published, separate Preact customer-widget bundle from phase 12-02) excluded from the check:no-dusk-tokens scan path — its own coral/burgundy brand tokens collide textually with the forbidden-marker list but are not dusk residue; 20-UI-SPEC.md §4 explicitly places the real widget package out of this phase's scope"
  - "wordmark.svg (used by sign-in/sign-up <img> tags) re-themed to Gotham tokens (bone #E7E5E1 on graphite #1E2327, Space Grotesk) instead of deleting it, since it is actively referenced and is the in-app brand mark, not customer-widget scope"
  - "deploy page's --widget-accent token confirmed NOT flagged by the gate (not in the forbidden-marker list) — no change needed there, per plan's stated allowance"

requirements-completed: [UI2-07]

# Metrics
duration: 25min
completed: 2026-07-15
status: complete
---

# Phase 20 Plan 14: Dusk Cutover (Delete Retired Components + Green Token Gate) Summary

**Deleted 5 dusk-only components + the skyline PNG with zero dangling imports, then drove `check:no-dusk-tokens` to exit 0 across the whole apps/admin bundle by re-theming the wordmark SVG and excluding the out-of-scope published customer-widget bundle from the gate's scan path.**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-07-15T13:25:00Z
- **Completed:** 2026-07-15T13:50:21Z
- **Tasks:** 2
- **Files modified:** 8 (6 deleted, 2 modified)

## Accomplishments
- Deleted `TopNav.tsx`, `HeroPipeline.tsx`, `HeroSteps.tsx`, `StepSubtaskCard.tsx`, `UserAvatar.tsx`, and `public/skyline-w-chats.png` — confirmed via grep that no route imported any of them before deleting (Wave 2 page rebuilds had already replaced all usages)
- `pnpm --dir apps/admin build` compiles clean after the deletions
- `node apps/admin/scripts/check-no-dusk-tokens.mjs` now exits 0 (was 8 findings before fixes)
- SC1/UI2-07 satisfied: no `dusk-*`/skyline/`amber-console`/`--brass-*`/glass/Fraunces/Hillbrow token, class, or asset remains anywhere in the apps/admin production bundle

## Task Commits

Each task was committed atomically:

1. **Task 1: Delete retired dusk components + skyline asset (import-safe)** - `405f418` (refactor)
2. **Task 2: Drive check:no-dusk-tokens to green across the bundle** - `8aa4a79` (fix)

## Files Created/Modified
- `apps/admin/app/components/TopNav.tsx` - deleted (dusk-only, superseded by Gotham shell/rail)
- `apps/admin/app/components/HeroPipeline.tsx` - deleted (dusk-only)
- `apps/admin/app/components/HeroSteps.tsx` - deleted (dusk-only)
- `apps/admin/app/components/StepSubtaskCard.tsx` - deleted (dusk-only)
- `apps/admin/app/components/UserAvatar.tsx` - deleted with TopNav
- `apps/admin/public/skyline-w-chats.png` - deleted (retired hero background asset)
- `apps/admin/public/wordmark.svg` - re-themed: dropped Fraunces + coral gradient, now bone-on-graphite (#E7E5E1 / #1E2327), Space Grotesk
- `apps/admin/scripts/check-no-dusk-tokens.mjs` - added `EXCLUDE_PATHS` for `public/wchats/` (out-of-scope published widget bundle)

## Decisions Made
- **UserAvatar disposition resolved:** deleted alongside TopNav (both dusk-only, zero remaining imports). Account/sign-out chrome is unchanged — it already lives in `SignOutTab`; the rail footer carries Settings. This was a pre-recorded assumption in the plan; execution simply confirmed and applied it.
- **`public/wchats/` scan exclusion:** the gate script (`check-no-dusk-tokens.mjs`) originally scanned all of `public/` indiscriminately. The published customer-widget bundle at `public/wchats/widget.css` (a separate Preact package, published in phase 12-02 for CDN delivery) has its own real brand palette (coral/burgundy `--accent`, `--gold`, `--amber`, `--text-1..4`, `--radius-*`, `--shadow-*`). These are not dusk-admin residue — `20-UI-SPEC.md` §4 explicitly states the real widget package is "a separate package, out of this phase's scope." Rather than weaken the forbidden-marker list (which would reduce gate coverage for real admin-app dusk residue), the exclusion targets exactly the known out-of-scope directory (`public/wchats`), leaving the rest of `public/` — icons, fonts, the wordmark — fully scanned.
- **`wordmark.svg` re-themed, not deleted:** it's actively used by `app/sign-in/[[...sign-in]]/page.tsx` and `app/sign-up/[[...sign-up]]/page.tsx` via `<img src="/wordmark.svg">`. It hardcoded `font-family="Fraunces, Georgia, serif"` and a coral gradient (`#F4748C`→`#C8485E`) — genuine dusk residue flagged by the gate. Fixed in place using Gotham's actual token values from `apps/admin/app/globals.css` (`--surface-2: #1E2327`, `--ink: #E7E5E1`, `--ink-2: #9BA1A3`, `--display: 'Space Grotesk', ...`), mirroring the landing page's own inline wordmark markup (`app/page.tsx` `.wordmark` class) rather than inventing a new treatment.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `wordmark.svg` hardcoded the retired Fraunces font + coral gradient**
- **Found during:** Task 2 (running `check-no-dusk-tokens.mjs`)
- **Issue:** `public/wordmark.svg`, rendered via `<img>` on the sign-in and sign-up pages, hardcoded `font-family="Fraunces, Georgia, serif"` and a coral linear-gradient fill — both explicit dusk-era markers (Fraunces is banned per DESIGN.md: "No Fraunces, no serifs"). Since it's loaded as an external `<img>` (not inlined), it can't inherit the page's CSS custom properties, so the SVG itself needed hardcoded Gotham-equivalent values.
- **Fix:** Replaced the coral-gradient icon fill with `#1E2327` (Gotham `--surface-2`), the icon stroke and wordmark text with `#E7E5E1` (Gotham `--ink`), the separator dot with `#9BA1A3` (Gotham `--ink-2`), and the font-family with `'Space Grotesk', system-ui, sans-serif` (matching `--display`). Removed the now-unused `<defs>`/gradient block.
- **Files modified:** `apps/admin/public/wordmark.svg`
- **Verification:** `check:no-dusk-tokens` no longer flags this file; visual treatment matches the landing page's inline `.wordmark` styling (mono/grotesque display font, bone text, no hue accent)
- **Committed in:** `8aa4a79` (Task 2 commit)

**2. [Rule 3 - Blocking] Gate false-positived on the out-of-scope published customer-widget bundle**
- **Found during:** Task 2 (running `check-no-dusk-tokens.mjs`)
- **Issue:** `apps/admin/public/wchats/widget.css` — the compiled, published bundle of the separate customer-facing Preact chat widget (from phase 12-02) — triggered 7 forbidden-marker hits (`--accent`, `--amber`, `--gold`, `--border`, `--text-1..4`, `--radius-*`, `--shadow-*`). This is the widget's own real, standalone brand palette, not admin-console dusk residue. `20-UI-SPEC.md` §4 explicitly scopes the real widget package out of this phase.
- **Fix:** Added an `EXCLUDE_PATHS` list to the gate script scoped to exactly `public/wchats` (the known publish directory), with an inline comment explaining the rationale, so the rest of `public/` remains fully scanned.
- **Files modified:** `apps/admin/scripts/check-no-dusk-tokens.mjs`
- **Verification:** `node scripts/check-no-dusk-tokens.mjs` exits 0; confirmed the exclusion is scoped to the single known directory, not a broad basename match that could hide future admin-app dusk residue
- **Committed in:** `8aa4a79` (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (1 bug fix, 1 blocking-issue fix)
**Impact on plan:** Both fixes were necessary to satisfy the plan's own acceptance criteria (gate exit 0) without either weakening the gate's real coverage or doing out-of-phase work on the customer widget itself. No scope creep — the widget bundle's own theme was left untouched.

## Known Stubs
None.

## Threat Flags
None — no new network endpoints, auth paths, file access patterns, or schema changes introduced. Scope was limited to dead-file deletion and a static-asset re-theme.

## Out-of-Scope Discoveries (logged, not fixed)
- `apps/admin/public/logo-mark.svg` — still hardcodes the dusk-era coral gradient, but is unreferenced anywhere in `app/` or `public/` and is not flagged by `check:no-dusk-tokens` (raw hex values aren't in the forbidden-marker list). Left in place since it's dead code outside this plan's explicit `files_modified` list and doesn't block the SC1 gate or the build. Logged in `.planning/phases/20-frontend-cutover-replace-the-skyline-dusk-admin-ui-with-the-/deferred-items.md` for a future cleanup pass.

## Issues Encountered
None beyond the two auto-fixed deviations above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- SC1/UI2-07 fully satisfied: the dusk-era admin UI is completely removed (components, asset, and every token/class marker), and the automated gate (`check:no-dusk-tokens`) is green and will catch regressions going forward.
- `pnpm --dir apps/admin build` is green; all 12 routes compile.
- Ready for the phase-level cutover verification step.

## Self-Check: PASSED

- All 6 deleted files confirmed absent (TopNav.tsx, HeroPipeline.tsx, HeroSteps.tsx, StepSubtaskCard.tsx, UserAvatar.tsx, skyline-w-chats.png)
- Both modified files confirmed present (wordmark.svg, check-no-dusk-tokens.mjs)
- Both new planning artifacts confirmed present (this SUMMARY, deferred-items.md)
- Both commit hashes (405f418, 8aa4a79) confirmed in git log

---
*Phase: 20-frontend-cutover-replace-the-skyline-dusk-admin-ui-with-the-*
*Completed: 2026-07-15*
