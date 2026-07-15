---
phase: 20-frontend-cutover
plan: 05
subsystem: ui
tags: [nextjs, react, three.js, clerk, gotham, landing-page, code-splitting]

# Dependency graph
requires:
  - phase: 20-02
    provides: three/@types/three npm dependency, Gotham design tokens in globals.css
  - phase: 20-03
    provides: GateProvider/useGate context, clerkAppearance theming in layout.tsx
  - phase: 20-04
    provides: gotham/{PageChrome,Btn,Chip,Ledger,Zone} component primitives
provides:
  - Client-only SceneMount three.js specimen (core shader, cage, armillary rings, points cloud, gate lerp)
  - Rebuilt landing page (/) as a routed Gotham page with hero, evidence ledger, live gate demo, three-step grid, footer
  - Re-skinned /sign-in and /sign-up bare-shell chrome (Clerk logic unchanged)
affects: [20-06, 20-07, ui-review, phase-21]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Client-only WebGL mount: dynamic import('three') inside useEffect only, wrapped in next/dynamic(ssr:false) at the call site — never a top-level `import ... from 'three'`"
    - "Gate-driven specimen: SceneMount reads useGate() into a ref so the per-frame rAF loop lerps colour without remounting the whole scene on every gate flip"
    - "Page-scoped CSS via inline <style> tag for landing-only classes not shared with the rail shell (topnav/.hero/.gate-grid/.steps/.foot)"

key-files:
  created:
    - apps/admin/app/components/gotham/SceneMount.tsx
  modified:
    - apps/admin/app/page.tsx
    - apps/admin/app/sign-in/[[...sign-in]]/page.tsx
    - apps/admin/app/sign-up/[[...sign-up]]/page.tsx

key-decisions:
  - "SceneMount reads useGate() via a ref (not a prop) so a gate flip on the landing page updates the WebGL specimen's per-frame lerp without unmounting/remounting the three.js scene"
  - "Dropped the old dusk landing's live-widget demo <Script> embed (tunnel-URL constant) entirely — no prototype/index.html equivalent exists and the plan's action list only names hero/evidence/gate/steps/footer sections to port; this is scoped as a full rebuild per UI-SPEC §7"
  - "Auth routes (sign-in/sign-up) default to PageChrome's static bloom/graticule treatment, no SceneMount — OQ1 permits mounting the specimen there but the plan default is to keep auth light; three.js remains absent from every route except /"
  - "Kept the existing public/wordmark.svg <img> on auth pages rather than porting the CSS .wordmark text treatment, since sign-in/sign-up are separate route bundles from page.tsx's scoped <style> tag"

patterns-established:
  - "SceneMount.tsx: the one and only three.js entry point in apps/admin; any future page wanting the specimen must dynamic-import THIS component via next/dynamic(ssr:false), never import three directly"

requirements-completed: [UI2-02]

# Metrics
duration: ~20min
completed: 2026-07-15
status: complete
---

# Phase 20 Plan 05: Gotham landing page + confined three.js specimen Summary

**Client-only three.js SceneMount (core shader/cage/rings/points-cloud, gate-driven colour lerp) code-split behind `next/dynamic(ssr:false)`, mounted only on the rebuilt `/` landing route; sign-in/sign-up re-skinned to the bare Gotham shell with Clerk logic untouched.**

## Performance

- **Duration:** ~20 min
- **Tasks:** 3/3 completed
- **Files modified:** 4 (1 created, 3 modified)

## Accomplishments
- `SceneMount.tsx` ports the full `scene.js` `mountGotham` body (core `ShaderMaterial`, wireframe cage, two armillary rings, fibonacci-sphere `Points` cloud with deterministic failing scenarios, per-frame gate-colour lerp, resize, reduced-motion 0.2x, `visibilitychange` pause/resume) into an idiomatic React client component with a full teardown (`cancelAnimationFrame`, listener removal, `renderer.dispose()`, geometry/material disposal)
- Landing page (`/`) rebuilt as a real routed Next.js page on Shell A (bare topnav): hero with CTAs + static stats, the specimen mounted via `next/dynamic(..., { ssr: false })`, a 5-row evidence ledger, a fully client-side "THE GATE" demo wired to `useGate()`, a three-step `rule-double` grid, and a footer with real product copy (no "prototype" wording)
- `/sign-in` and `/sign-up` re-skinned to the Gotham bare shell (`PageChrome` graticule/bloom/crosshairs) with `<SignIn>`/`<SignUp>` and their `fallbackRedirectUrl` props left byte-for-byte unchanged
- Must-fix 5 applied: `BRASS`/`BRASS_HOT` renamed to `LIVE`/`LIVE_HOT` (same hex values); zero `brass|petrol|oxblood` residue in any file this plan touched
- OQ1 resolved and recorded: specimen ships on landing only; auth gets the lightweight static bloom treatment by default; three.js is imported nowhere else in `apps/admin/app` (confirmed via repo-wide grep)

## Task Commits

Each task was committed atomically:

1. **Task 1: SceneMount — client-only three specimen** - `1c9f1b3` (feat)
2. **Task 2: Rebuild landing page.tsx from index.html** - `3c2f54d` (feat)
3. **Task 3: Re-skin sign-in + sign-up to Gotham** - `ce82b30` (feat)

**Plan metadata:** (this commit, docs)

## Files Created/Modified
- `apps/admin/app/components/gotham/SceneMount.tsx` - Client-only three.js specimen; dynamic-imports `three` inside `useEffect`, drives its render loop off `useGate()`, disposes cleanly on unmount
- `apps/admin/app/page.tsx` - Full rebuild: Gotham landing route (hero, specimen mount, evidence ledger, gate demo, three-step grid, footer), page-scoped `<style>` block for the landing-only CSS classes
- `apps/admin/app/sign-in/[[...sign-in]]/page.tsx` - Re-skinned to `PageChrome` bare shell; Clerk `<SignIn>` unchanged
- `apps/admin/app/sign-up/[[...sign-up]]/page.tsx` - Re-skinned to `PageChrome` bare shell; Clerk `<SignUp>` unchanged

## Decisions Made
- `SceneMount` takes `{ scenarios, fails }` as props but reads the live/blocked gate state through `useGate()` internally (not as a prop), storing it in a ref that the `requestAnimationFrame` loop polls each frame — this matches `scene.js`'s original design (`window.gotham.gate` polled per frame) while integrating with the React `GateProvider` context instead of a global `window` object
- The old dusk landing's demo widget `<Script>` embed (`WCHATS_TUNNEL_API_BASE` constant + `/wchats/widget.js`) was not carried into the rebuild — it has no counterpart in `index.html` and the plan's action list scopes Task 2 to hero/evidence/gate/steps/footer only; this is a full-rebuild replacement per UI-SPEC §7's route table, not an accidental drop
- Kept `public/wordmark.svg` `<img>` on the two auth pages instead of porting the landing page's inline `.wordmark` CSS treatment, since that CSS lives in a page-scoped `<style>` tag inside `page.tsx` that does not travel to the separate sign-in/sign-up route bundles

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] TypeScript narrowing failure on `host` inside the nested `resize()` closure**
- **Found during:** Task 1 (build verification — `pnpm build` type-check step)
- **Issue:** `const host = hostRef.current; if (!host) return` narrows `host` to `HTMLDivElement` in the outer effect scope, but TypeScript resets that narrowing inside a `function resize() {}` declared later in the same `.then()` callback (a known TS control-flow limitation for nested function declarations vs. arrow functions), producing `Property 'clientWidth' does not exist on type 'HTMLDivElement | null'`
- **Fix:** Added an explicit `if (!host || !renderer) return` guard at the top of `resize()` itself
- **Files modified:** `apps/admin/app/components/gotham/SceneMount.tsx`
- **Verification:** `pnpm build` (via `corepack pnpm --dir apps/admin build`) completes with `Finished TypeScript` and no errors
- **Committed in:** `1c9f1b3` (Task 1 commit — fixed before commit, not a separate follow-up)

**2. [Rule 1 - Bug] "prototype" substring leaking into a page.tsx doc comment via the word "prototypes"**
- **Found during:** Task 2 (acceptance grep — `grep -ni "prototype" apps/admin/app/page.tsx`)
- **Issue:** The file's header doc comment referenced `prototypes/gotham/index.html` as its source, which matches the case-insensitive `prototype` acceptance grep even though it's a source-file citation, not shipped UI copy
- **Fix:** Reworded the comment to "the Gotham design source's index.html" — same information, no `prototype` substring
- **Files modified:** `apps/admin/app/page.tsx`
- **Verification:** `grep -ni "prototype" apps/admin/app/page.tsx` returns no matches
- **Committed in:** `3c2f54d` (Task 2 commit — fixed before commit)

---

**Total deviations:** 2 auto-fixed (2 Rule 1 bug fixes, both caught by this plan's own acceptance checks before commit)
**Impact on plan:** Both fixes were required for the plan's own stated acceptance criteria to pass; no scope creep, no architectural changes.

## Issues Encountered
None beyond the two auto-fixed items above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Landing (`/`) is now a real routed page with the confined, code-split three.js specimen; `pnpm --dir apps/admin build` is green with no SSR `window is not defined` crash
- `/agents/[id]/page.tsx` still imports `StepSubtaskCard` (out of scope for this plan — not in `files_modified`); that component's disposition per UI-SPEC §7.1 is "Delete" and should be handled by whichever later 20-0X plan rebuilds `/agents/[id]`
- `components/HeroPipeline.tsx` and `components/HeroSteps.tsx` are now unused by `page.tsx` but were left in place (not in this plan's `files_modified`) — dead-file cleanup deferred to a later wave/plan per UI-SPEC §7.1
- Auth chrome (sign-in/sign-up) is ready for the route-smoke verification pass (Wave 5): `<canvas>` should be absent on `/sign-in`/`/sign-up` (confirmed by construction — no `SceneMount` import in either file) and present on `/`

---
*Phase: 20-frontend-cutover*
*Completed: 2026-07-15*

## Self-Check: PASSED

All created/modified files and all three task commits (`1c9f1b3`, `3c2f54d`, `ce82b30`) verified present on disk / in `git log`.
