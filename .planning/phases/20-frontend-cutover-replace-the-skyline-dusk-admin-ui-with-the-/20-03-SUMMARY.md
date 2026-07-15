---
phase: 20-frontend-cutover
plan: 03
subsystem: ui
tags: [nextjs, css-custom-properties, clerk, design-tokens, react-context]

# Dependency graph
requires:
  - phase: 20-frontend-cutover (plan 20-02)
    provides: apps/admin/pnpm-workspace.yaml packages:['.'] + three installed
provides:
  - Gotham "Bone on Graphite" token system live in apps/admin/app/globals.css (--bg/--ink/--live/--pass/--fail/--seal/--ch-1..4/--display/--sans/--voice/--mono)
  - data-gate="blocked" root-attribute recolour mechanism + .tint 600ms transition
  - Gotham global component classes (.rail/.deck/.page/.section/.zone/.chip/.ledger/.btn/.command/.well/.graticule/.bloom/.cross/.skip)
  - Root layout re-themed to Gotham fonts (Space Grotesk/Inter/JetBrains Mono/Newsreader) + Gotham Clerk appearance
  - GateProvider (single writer of document.documentElement.dataset.gate) mounted at root
affects: [20-frontend-cutover remaining waves (route rebuilds against these tokens/classes), 20-14 full-bundle no-dusk sweep]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Colour is a verdict: --live/--pass/--fail/--seal are the only two hue families in the system, everything else is graphite/bone luminance"
    - "Single-writer gate pattern: GateProvider owns document.documentElement.dataset.gate exclusively, resets to 'open' on unmount to prevent stuck-red state across client navigations"

key-files:
  created:
    - apps/admin/app/components/gotham/GateProvider.tsx
  modified:
    - apps/admin/app/globals.css
    - apps/admin/app/layout.tsx

key-decisions:
  - "Removed dusk-only decorative keyframes (pulse-ring, blink-dot, slide-in-up, bounce-dot, shimmer, hero-fade-in, otp-pop, blink-caret, spin-cw) and .preview-panel media query during the globals.css rewrite — they referenced hardcoded dusk-coral rgba values or dusk-only layout utilities not in the Gotham class inventory (tokens.css/app.css) and are out of scope for this foundation plan; components still referencing them (HeroPipeline, HeroSteps, soul/deploy preview panels) are rebuilt in later waves per the plan's own sequencing note ('Full-bundle no-dusk sweep completes in 20-14')."
  - "Restyled .sign-out-tab background from --bg-deep (dusk) to --surface and its accent stripe from --red to --seal, keeping the peek/hover/focus-visible behavior identical to the original"

requirements-completed: [UI2-01]

# Metrics
duration: 35min
completed: 2026-07-15
status: complete
---

# Phase 20 Plan 03: Gotham Token Foundation (globals.css, layout.tsx, GateProvider) Summary

**Ported the Gotham "Bone on Graphite" token system and gate mechanism verbatim into globals.css, re-themed the root layout's fonts and Clerk appearance, and added the single-writer GateProvider context — the foundation every later Phase 20 route rebuild depends on.**

## Performance

- **Duration:** ~35 min
- **Completed:** 2026-07-15T10:26:08Z
- **Tasks:** 2/2
- **Files modified:** 3 (1 created, 2 modified)

## Accomplishments
- `globals.css` now exposes the full Gotham `:root` token set (surfaces, hairlines, ink, live, pass/fail/seal, the four `--ch-*` eval channels, the display/sans/voice/mono type stack, radii, z-index scale), the `:root[data-gate="blocked"]` override block, and the `.tint` transition class — all ported verbatim from `prototypes/gotham/tokens.css`.
- Every Gotham global component class from `prototypes/gotham/app.css` is now in `globals.css`: `.graticule`/`.bloom`/`.cross`, `.rail`/`.rail-mark`/`.rail-btn`/`.spacer`/`.deck`, `.page`/`.page-head`/`.section`/`.zone`, `.label`/`.num`/`.mono`/`.voice`/`.caret`, `.btn*`, form controls + range slider, `.chip*`/`.dot*`, `.ledger`, `.well`, `.command`/`.kbd`, `.skip`, the 900px rail-collapse breakpoint, and the reduced-motion blanket.
- All dusk-era tokens/classes removed from `globals.css`: the skyline photo body treatment (`background-image: url('/skyline-w-chats.png')` + scrim/noise `::before`/`::after`), `.glass`/`.glass-strong`/`.glass-nav`/`.on-photo`, the `@supports not (backdrop-filter)` fallback, and the entire dusk `:root` (glass tiers, sunset-coral `--accent*`, jacaranda `--lilac*`, tower `--cyan`, building `--amber*`, Fraunces display font).
- `.sign-out-tab` preserved and restyled to bone/graphite with a `--seal` accent stripe (was `--bg-deep`/`--red`).
- `layout.tsx` now links Space Grotesk + Inter + JetBrains Mono + Newsreader (single `css2` href) instead of Fraunces/Inter/JetBrains Mono, and `clerkAppearance` is fully re-themed to Gotham hexes (`colorPrimary` bone `#E7E5E1`, `colorBackground` `#15181B`, `colorInputBackground` `#08090B`, `colorDanger` `#E5484D`, `borderRadius` `6px`) with all `backdropFilter`/glass-rgba values removed from the Clerk `card` element.
- `<body className="tint">` added; `GateProvider` created and mounted around `{children}` inside `QueryProvider`/`ClerkProvider`.
- `GateProvider` is the sole writer of `document.documentElement.dataset.gate`: it writes on every gate-state change and resets to `'open'` on unmount, satisfying the Pitfall 5 requirement that a blocked gate never leaks across client-side navigations.

## Task Commits

Each task was committed atomically:

1. **Task 1: Replace globals.css with the Gotham token + class system** - `12d7294` (feat)
2. **Task 2: Re-theme root layout.tsx (fonts + Clerk) and add GateProvider** - `482b5c7` (feat)

_Note: no TDD tasks in this plan._

## Files Created/Modified
- `apps/admin/app/globals.css` - Rewritten: Gotham `:root` tokens + gate override + `.tint`, Gotham global classes, dusk tokens/classes removed, `.sign-out-tab` restyled
- `apps/admin/app/layout.tsx` - Gotham font links, re-themed `clerkAppearance`, `<body className="tint">`, `GateProvider` mounted
- `apps/admin/app/components/gotham/GateProvider.tsx` - New: `GateProvider` context + `useGate()` hook, single writer of `data-gate`

## Decisions Made
- Removed dusk-only decorative `@keyframes` (pulse-ring, blink-dot, slide-in-up, bounce-dot, shimmer, hero-fade-in, otp-pop, blink-caret, spin-cw) and the `.preview-panel` media query during the rewrite, since they belong to the dusk visual language and aren't in the Gotham token/class inventory this plan ports. Components that still reference them (`HeroPipeline.tsx`, `HeroSteps.tsx`, the soul/deploy `.preview-panel` regions) are unaffected at the build level (unresolved CSS class names don't fail a Next.js build) and are rebuilt against Gotham markup in later Phase 20 waves, consistent with this plan's own scope note that the full no-dusk sweep completes in plan 20-14.
- Restyled `.sign-out-tab` to bone/graphite (`--surface` background, `--seal` accent stripe) rather than deleting it, per the plan's explicit "preserve, restyled" instruction.
- Font weights/href for the Google Fonts link follow the plan's task-body specification exactly (`Inter:wght@400;500;600;700`, `JetBrains+Mono:wght@400;500;700`, `Newsreader:ital,opsz,wght@0,6..72,400;1,6..72,400;1,6..72,500`, `Space+Grotesk:wght@500;600;700`), which carries a slightly broader weight range than the trimmed `prototypes/gotham/index.html` `<link>` — both satisfy the acceptance grep (`Space+Grotesk`/`Newsreader` present, `Fraunces` absent).

## Deviations from Plan

None - plan executed exactly as written. (See "Decisions Made" above for in-scope judgment calls the plan's action text left implicit — none required Rule 4 architectural sign-off.)

## Issues Encountered
- `pnpm` was not resolvable directly on the Bash tool's `PATH` in this environment; used `corepack pnpm build` as the equivalent invocation (corepack is present and pinned to the project's pnpm version). Both task-level builds and the final plan-level build passed via this path.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- The Gotham token/class/gate foundation is live; subsequent Phase 20 plans (route rebuilds in Waves 2-3) can now consume `var(--ch-1..4)`, `.zone`/`.chip`/`.ledger`/`.btn*`/`.rail`/`.page` etc. and `useGate()` directly.
- No blockers. Pages not yet rebuilt (landing, agents dashboard, agent operations room, etc.) will render unstyled/partially-styled where they still reference dusk-only classes (`.glass`, `.on-photo`, the removed keyframes) until their respective plans land — expected per this plan's foundation-only scope and the phase's wave sequencing.

---
*Phase: 20-frontend-cutover*
*Completed: 2026-07-15*

## Self-Check: PASSED

- FOUND: apps/admin/app/globals.css
- FOUND: apps/admin/app/layout.tsx
- FOUND: apps/admin/app/components/gotham/GateProvider.tsx
- FOUND: .planning/phases/20-frontend-cutover-replace-the-skyline-dusk-admin-ui-with-the-/20-03-SUMMARY.md
- FOUND commit: 12d7294
- FOUND commit: 482b5c7
