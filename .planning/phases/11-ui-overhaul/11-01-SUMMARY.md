---
phase: 11-ui-overhaul
plan: "01"
subsystem: admin-ui
tags: [css-tokens, design-system, hillbrow-at-dusk, wave-1]
dependency_graph:
  requires: []
  provides: [hillbrow-at-dusk-token-system, skyline-background, svg-logo, fraunces-font]
  affects: [all-admin-ui-routes]
tech_stack:
  added:
    - Fraunces variable font via Google Fonts (display headings)
    - skyline-w-chats.png (2.5MB Johannesburg skyline photograph as whole-app background)
    - logo-mark.svg (coral-gradient chat-square mark)
    - wordmark.svg (w.chats Fraunces lockup)
  patterns:
    - CSS custom property cascade (all components auto-update via :root token replacement)
    - body::before veil overlay at 0.45 opacity for legibility
    - body::after film grain at 0.025 opacity for OLED texture
    - #__next z-index: 1 above pseudo-elements
key_files:
  created:
    - apps/admin/public/skyline-w-chats.png
    - apps/admin/public/logo-mark.svg
    - apps/admin/public/wordmark.svg
    - .planning/phases/11-ui-overhaul/11-01-SUMMARY.md
  modified:
    - apps/admin/app/globals.css
    - apps/admin/app/layout.tsx
    - apps/admin/app/components/TopNav.tsx
    - apps/admin/app/agents/[id]/layout.tsx
    - apps/admin/app/page.tsx
decisions:
  - "[11-01] Veil opacity set to 0.45 per .continue-here.md canonical value (not 0.72 from CONTEXT.md)"
  - "[11-01] --surface-1 set to #140E2A per .continue-here.md canonical (not #1E1638 from colors_and_type.css)"
  - "[11-01] pnpm run lint is pre-existing broken (no ESLint config in project) — not introduced by this plan"
  - "[11-01] page.tsx logo fixed as Rule 2 deviation (--font-pixelify now undefined, falls back to system sans without fix)"
metrics:
  duration: ~25 min
  completed: 2026-05-26
  tasks: 2
  files: 9
---

# Phase 11 Plan 01: Token Foundation + Background Summary

Replaced the entire Parchment & Wine CSS token system with Hillbrow at Dusk and established the Johannesburg skyline photograph as the whole-app persistent background.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Replace globals.css token system + body background rules | 76e7a61 | apps/admin/app/globals.css |
| 2 | Copy static assets + update layout.tsx + TopNav.tsx + agents/[id]/layout.tsx | 64e3986 | 6 files + 3 assets |

## What Was Built

**Token system replacement:** The entire `:root` block in `globals.css` was replaced — 25+ Parchment & Wine tokens (light beige surfaces, wine accent, warm-grey borders) replaced with Hillbrow at Dusk tokens (deep indigo surfaces, sunset coral accent, lavender borders, violet-tinted shadows).

**Skyline background:** `body` now loads `skyline-w-chats.png` (2.5MB Johannesburg skyline photograph) as a fixed full-cover background. `body::before` provides a 0.45-opacity indigo veil with coral/lilac accent radials for legibility. `body::after` adds film grain at 0.025 opacity. `#__next` sits at z-index: 1 above both pseudo-elements.

**Typography:** Removed `localFont`/FungkyBrowDEMO import from `layout.tsx`. Added Google Fonts `<head>` link loading Fraunces (variable, all axes), Inter (300-800), and JetBrains Mono (400-600). Added `--font-display` CSS variable pointing to Fraunces.

**Clerk appearance:** Updated `clerkAppearance` in `layout.tsx` from Parchment hex values to Hillbrow at Dusk dark values (coral primary, surface-1 card background, lilac-border borders).

**SVG logo:** TopNav.tsx and page.tsx now show `logo-mark.svg` (30x30) + `wordmark.svg` (height 20px). Spinning animation and font-pixelify span removed. TopNav background changed from `var(--bg)` to `var(--surface-1)`.

**Transparent layout wrapper:** `agents/[id]/layout.tsx` changed from `background: var(--bg)` to `background: transparent` — the city shows through the agent detail layout.

## Verification Results

All plan verification criteria pass:

- Old palette absent: `F0E8E0|7B1C3A|FFFCF9|F7F0EA` — CLEAN from globals.css :root
- Old font absent: `font-pixelify|FungkyBrowDEMO|Pixelify` — CLEAN from all app/*.tsx files
- Skyline background present: `url('/skyline-w-chats.png')` in globals.css body
- Fraunces font link: `fonts.googleapis.com` in layout.tsx head
- Veil at 0.45: `rgba(11, 7, 23, 0.45)` in body::before
- `#__next` z-index: 1 — present in globals.css
- logo-mark.svg in TopNav — PASS
- No spin/animation on new logo — PASS
- skyline-w-chats.png: 2,545,131 bytes (>1MB) — PASS
- `pnpm run build` exits zero — PASS

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical Functionality] Fixed font-pixelify reference in page.tsx**
- **Found during:** Task 2 acceptance criteria verification
- **Issue:** `apps/admin/app/page.tsx` line 61 had `fontFamily: 'var(--font-pixelify)'` on the logo span. With `--font-pixelify` removed from `:root`, this would resolve to system sans-serif (wrong fallback, no visual token).
- **Fix:** Replaced the spinning lettermann PNG + font-pixelify span with `logo-mark.svg` + `wordmark.svg` (same pattern as TopNav.tsx). The page.tsx header logo section now matches the new identity.
- **Files modified:** `apps/admin/app/page.tsx`
- **Commit:** 64e3986 (included in Task 2 commit)

### Pre-existing Issues (not introduced by this plan)

**`pnpm run lint` broken:** `next lint` command fails with "Invalid project directory provided, no such directory: lint" — Next.js 16 is interpreting "lint" as a directory argument. The project has no ESLint config file (`.eslintrc.json` etc). This was confirmed broken on the pre-plan codebase via `git stash` test. Not introduced by this plan. Logged to deferred items.

## Known Stubs

None — this plan is pure token/asset replacement with no data-rendering logic.

## Threat Flags

None — pure CSS/JSX visual restyle, no security-relevant code paths changed.

## Self-Check: PASSED

Files created/modified:
- apps/admin/app/globals.css: exists, contains `#0B0717` and `skyline-w-chats.png`
- apps/admin/app/layout.tsx: exists, contains `fonts.googleapis.com` and `#F4748C`
- apps/admin/app/components/TopNav.tsx: exists, contains `logo-mark.svg`, no spin
- apps/admin/app/agents/[id]/layout.tsx: exists, background is `transparent`
- apps/admin/app/page.tsx: exists, contains `logo-mark.svg`, no font-pixelify
- apps/admin/public/skyline-w-chats.png: exists, 2,545,131 bytes
- apps/admin/public/logo-mark.svg: exists, 938 bytes
- apps/admin/public/wordmark.svg: exists, 815 bytes

Commits verified:
- 76e7a61: feat(11-01): replace globals.css Parchment & Wine tokens with Hillbrow at Dusk
- 64e3986: feat(11-01): copy static assets + update layout.tsx, TopNav.tsx, agents/[id]/layout.tsx
