---
phase: 11-ui-overhaul
plan: "06"
status: complete
completed: "2026-05-26"
wave: 6
commits:
  - ef20eec
duration: ~15 min
tasks_completed: 2
files_modified: 4
---

# Plan 11-06 Summary — Auth Pages + Visual QA Gate

## What Was Built

Wave 6 completed the Hillbrow at Dusk overhaul with four file changes and a full visual QA pass.

**Task 1 — Auth pages + UserAvatar + SignOutTab (ef20eec):**
- `sign-in/[[...sign-in]]/page.tsx` — transparent `<main>` wrapper (background: transparent, flexDirection: column, gap: 24px), logo-mark.svg + wordmark.svg above the Clerk card, `<SignIn fallbackRedirectUrl="/agents" />`; no surface-1 double-wrap (Clerk card uses dark appearance from layout.tsx)
- `sign-up/[[...sign-up]]/page.tsx` — identical transparent pattern with `<SignUp fallbackRedirectUrl="/agents" />`
- `UserAvatar.tsx` — outer div wrapper with `border: '1px solid rgba(183, 154, 224, 0.6)'`, `borderRadius: '50%'`, `padding: '1px'`; Lucide User icon color changed from `#ef4444` → `var(--accent)` (coral)
- `SignOutTab.tsx` — LogOut icon color changed from `'#ef4444'` → `'var(--red)'`

**Task 2 — Visual QA (Playwright + build):**
- Fresh `.next` cache cleared to resolve stale-CSS issue (old dev server was serving cached Parchment & Wine build)
- Playwright screenshots confirmed:
  - Landing: Johannesburg skyline visible, dark indigo veil, coral CTAs, Fraunces headline with strikethrough ✓
  - Sign-in: skyline backdrop, logo above dark Clerk card, coral Continue button ✓
  - Sign-up: same transparent auth pattern confirmed ✓
- `pnpm run build` passed: compiled (2.6 min), TypeScript clean, all 12 routes generated

## Token Regression Greps (all CLEAN)

| Grep | Result |
|------|--------|
| Old palette hex in globals.css (F0E8E0, 7B1C3A, FFFCF9, F7F0EA) | CLEAN |
| Old fonts (font-pixelify, FungkyBrowDEMO, Pixelify) | CLEAN |
| Old orange tokens (--orange, --orange-dim) | CLEAN |
| Hardcoded #ef4444 / #EF4444 | CLEAN |
| amber-bg / --amber-bg | AlertsBanner.tsx + DocumentDetailModal.tsx (pre-existing, out-of-scope — documented in .continue-here.md) |

## Acceptance Criteria

| Criterion | Status |
|-----------|--------|
| Sign-in: transparent wrapper + logo above Clerk card | ✓ PASS |
| Sign-up: same transparent pattern | ✓ PASS |
| UserAvatar: lilac ring rgba(183,154,224,0.6) | ✓ PASS |
| UserAvatar: Lucide icon uses var(--accent) not #ef4444 | ✓ PASS |
| SignOutTab: icon uses var(--red) | ✓ PASS |
| Token regressions CLEAN | ✓ PASS (with documented exceptions) |
| pnpm run build exits zero | ✓ PASS |
| Visual audit: skyline visible across all screens | ✓ PASS (Playwright screenshots confirm) |

## Decisions

- Stale `.next` cache from previous build caused dev server to serve old Parchment & Wine CSS — cleared cache and restarted to unblock visual QA
- `background-attachment: fixed` not rendered in headless Playwright — patched to `scroll` in Playwright test only (source CSS unchanged)
- `--amber-bg` refs in AlertsBanner.tsx and DocumentDetailModal.tsx are pre-existing out-of-scope files; noted as known deviation in phase documentation
