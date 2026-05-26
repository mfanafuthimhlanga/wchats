---
phase: 11-ui-overhaul
plan: "02"
subsystem: admin-ui
tags: [landing-page, glass-nav, fraunces, hero-steps, hillbrow-at-dusk, wave-2]
dependency_graph:
  requires: [hillbrow-at-dusk-token-system, skyline-background, svg-logo, fraunces-font]
  provides: [transparent-landing-page, glass-nav-scroll, fraunces-headline, trust-strip, herosteps-coral-green]
  affects: [apps/admin/app/page.tsx, apps/admin/app/components/HeroSteps.tsx]
tech_stack:
  added: []
  patterns:
    - "'use client' + useEffect scroll listener for glass-nav scroll-reactive transition"
    - "Fraunces display heading with fontVariationSettings opsz 144 SOFT 30"
    - "Strikethrough + italic-coral headline composition pattern"
    - "Glass eyebrow pill: var(--glass-bg) + var(--glass-blur) + var(--glass-border)"
    - "Trust strip: JetBrains Mono values + UPPERCASE TRACKED labels"
key_files:
  created: []
  modified:
    - apps/admin/app/page.tsx
    - apps/admin/app/components/HeroSteps.tsx
decisions:
  - "[11-02] page.tsx converted to 'use client' — scroll listener for glass nav requires useEffect; no sub-component extraction needed"
  - "[11-02] Primary CTA color: #0B0717 (--text-on-accent) not '#fff' — dark text on coral fill as per design system"
  - "[11-02] pnpm run lint pre-existing broken (inherited from 11-01) — build passes, lint skip documented"
  - "[11-02] amber-bg in non-scope files (AgentCard, ingest, deploy, eval) are Wave 3/4/5 work — not touched in Plan 02"
metrics:
  duration: ~20 min
  completed: 2026-05-26
  tasks: 2
  files: 2
---

# Phase 11 Plan 02: Landing Page Rebuild Summary

Rebuilt `page.tsx` with the Hillbrow at Dusk identity — transparent hero wrapper, scroll-reactive glass nav, Fraunces headline with strikethrough/italic-coral pattern, coral primary CTA, trust strip with JetBrains Mono metrics, and HeroSteps in the right column. Updated HeroSteps.tsx to replace all removed token references (`--orange`, `--orange-dim`, `--green-solid`, hardcoded hex values) with coral/green equivalents.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Rebuild page.tsx — transparent hero + glass nav + Fraunces heading + trust strip | 32fa8b7 | apps/admin/app/page.tsx |
| 2 | Update HeroSteps.tsx — replace removed tokens with coral/green equivalents | ce7a61b | apps/admin/app/components/HeroSteps.tsx |

## What Was Built

**Landing page rebuild (`page.tsx`):** The file was completely rewritten as a `'use client'` component. A `useEffect` scroll listener sets `scrolled` state when `window.scrollY > 60`, toggling the nav between glass (`var(--glass-bg)` + `var(--glass-blur)`) and solid (`var(--bg-elev)`). The page `<main>` and hero `<section>` now use `background: transparent` — the Johannesburg skyline photograph shows through. The eyebrow pill uses glass tokens (glass-bg, glass-blur, glass-border, pill border-radius) with UPPERCASE TRACKED JetBrains Mono text. The Fraunces h1 uses `fontVariationSettings: '"opsz" 144, "SOFT" 30'`, with a strikethrough `<span>` (line-through coral) for "is the hard part." and an italic-coral `<em>` (SOFT 100) for "The layer underneath is." The primary CTA uses `var(--accent)` background with `#0B0717` (dark) text. The trust strip renders three items — each with a JetBrains Mono value and an UPPERCASE TRACKED label below.

**HeroSteps.tsx token replacement:** Seven targeted replacements across the step card rendering logic, connector lines, widget live dot, citation chip border, verified badge border, and Stripe pay button. All `var(--orange)` → `var(--accent)`, `var(--orange-dim)` → `var(--accent-dim)`, `var(--green-solid)` → `var(--green)`. The hardcoded RGBA values for the citation chip border and verified badge border were also updated to the coral/green equivalents from the new token system.

## Verification Results

All plan verification criteria pass:

- Old tokens absent: `orange|green-solid|F0E8E0|7B1C3A|font-pixelify|spin-cw` — CLEAN from page.tsx and HeroSteps.tsx
- `var(--font-display)` in page.tsx — PRESENT (line 125)
- `glass-bg|glass-blur` in page.tsx — PRESENT (lines 35, 36, 99, 100)
- `line-through` in page.tsx — PRESENT (line 139)
- `var(--accent)` in page.tsx — PRESENT (multiple matches)
- `background: 'transparent'` in page.tsx — PRESENT (lines 19, 77)
- `var(--accent)` in HeroSteps.tsx — multiple matches (active step border, circle, live dot, citation chip, subColor)
- `var(--green)` in HeroSteps.tsx — multiple matches (done circle, connector, Stripe button, widget live dot, verified badge)
- `pnpm run build` exits zero — PASS

## Deviations from Plan

### Pre-existing Issues (not introduced by this plan)

**`pnpm run lint` broken:** Inherited from 11-01. `next lint` interprets "lint" as a directory argument; no ESLint config file exists. Build passes — functional verification complete.

**`amber-bg` in non-scope files:** `grep -rn "amber-bg" apps/admin/app/` finds occurrences in AgentCard.tsx, deploy/page.tsx, eval/page.tsx, ingest/page.tsx, and agents/[id]/page.tsx. These are Wave 3/4/5 scope (Agents Dashboard, AgentCard, Journey Screens, Eval, Deploy). Plan 02 scope is `page.tsx` and `HeroSteps.tsx` only — these are untouched pre-existing uses.

## Known Stubs

None — this plan is a pure visual restyle of public-facing components with no data-rendering logic.

## Threat Flags

None — pure CSS/JSX visual restyle of the public landing page. No security-relevant code paths changed.

## Self-Check: PASSED

Files modified:
- apps/admin/app/page.tsx: exists, contains `var(--font-display)`, `var(--glass-bg)`, `line-through`, `var(--accent)`, `background: 'transparent'`
- apps/admin/app/components/HeroSteps.tsx: exists, contains `var(--accent)` (multiple), `var(--green)` (multiple), zero references to `--orange`, `--orange-dim`, `--green-solid`

Commits verified:
- 32fa8b7: feat(11-02): rebuild landing page with transparent hero, glass nav, Fraunces headline
- ce7a61b: feat(11-02): replace removed tokens in HeroSteps.tsx with coral/green equivalents
