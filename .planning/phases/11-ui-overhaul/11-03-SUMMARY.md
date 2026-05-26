---
phase: 11-ui-overhaul
plan: "03"
subsystem: admin-ui
tags: [agents-dashboard, agent-card, fraunces, greeting-strip, hillbrow-at-dusk, wave-3]
dependency_graph:
  requires: [hillbrow-at-dusk-token-system, skyline-background, fraunces-font]
  provides: [agents-greeting-strip, transparent-agents-wrapper, agent-card-dark-surface, gold-status-tokens]
  affects: [apps/admin/app/agents/page.tsx, apps/admin/app/components/AgentCard.tsx]
tech_stack:
  added: []
  patterns:
    - "Greeting strip: var(--bg) + two off-axis radial gradients (coral 8%, lilac 6%) as a full-width band"
    - "Fraunces italic-coral em pattern for name/accent words (SOFT 100, fontWeight 300, var(--accent))"
    - "UPPERCASE TRACKED micro-label: 10.5px / fontWeight 600 / letterSpacing 0.12em / uppercase"
    - "Status chip uses var(--gold-bg)/var(--gold) for pending/provisioning (amber tokens gone from this component)"
    - "Hover state: useState(false) + onMouseEnter/Leave → translateY(-2px) + borderTop coral (var(--border-hard))"
    - "Agent name: Fraunces 600 with fontVariationSettings opsz 144 SOFT 30"
key_files:
  created: []
  modified:
    - apps/admin/app/agents/page.tsx
    - apps/admin/app/components/AgentCard.tsx
decisions:
  - "[11-03] Greeting strip name: 'there' placeholder used (Clerk useUser hook not already present on page — added auth hook would break the plan's data-fetching-only constraint)"
  - "[11-03] amber-bg in agents/[id]/ files (ingest, deploy, eval, soul, page) are Wave 4/5 scope — not touched in Plan 03"
  - "[11-03] getTimeGreeting helper added inline in page.tsx — derives Good morning/afternoon/evening from hour for the UPPERCASE TRACKED micro-label"
  - "[11-03] pnpm run lint pre-existing broken (inherited from 11-01) — build passes, lint skip documented"
metrics:
  duration: ~15 min
  completed: 2026-05-26
  tasks: 2
  files: 2
---

# Phase 11 Plan 03: Agents Dashboard + AgentCard Summary

Updated the agents dashboard with a Fraunces italic greeting strip and fixed 3-column grid, and restyled AgentCard.tsx to the Hillbrow at Dusk spec: --surface-1 solid background, coral/green/gold status chips with UPPERCASE TRACKED labels, hover translateY(-2px) with coral top border, and Fraunces 600 agent name.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Update agents/page.tsx — greeting strip + transparent wrapper + 3-column grid | 9d1965a | apps/admin/app/agents/page.tsx |
| 2 | Update AgentCard.tsx — surface-1 bg + hover effects + gold status tokens + Fraunces name | 17e8e35 | apps/admin/app/components/AgentCard.tsx |

## What Was Built

**Agents dashboard (page.tsx):** The outer page wrapper now uses `background: transparent` — the Johannesburg skyline shows through. A full-width greeting strip `div` was added before the content area with `background: var(--bg)` and two radial gradients (coral top-right 8%, lilac bottom-left 6%). Inside the strip: an UPPERCASE TRACKED micro-label showing time period and agent count (e.g., "EVENING · 3 AGENTS"), followed by a Fraunces h1 greeting with an italic-coral `<em>` for the name placeholder "there" (using `fontVariationSettings: '"opsz" 144, "SOFT" 100'`). The agent grid was updated from `repeat(auto-fill, minmax(280px, 1fr))` with 20px gap to `repeat(3, 1fr)` with 16px gap. The primary button color changed from `#fff` to `#0B0717` (dark text on coral fill) and text to sentence case "Create agent". The empty state now uses a glass eyebrow pill and a Fraunces italic-coral accent heading.

**AgentCard.tsx:** The STATUS_COLORS map was updated — `pending` and `provisioning` now use `var(--gold-bg)` and `var(--gold)` (the `--amber-bg`/`--amber` tokens in the new system represent building warmth, not status warnings). A `useState(false)` hover state drives the card's interactive behavior: `onMouseEnter`/`onMouseLeave` toggle translateY(-2px) lift, border switching from `var(--border-soft)` to `var(--border)`, and `borderTop` switching to `var(--border-hard)` (coral). Card background is `var(--surface-1)` (was `var(--bg)`). `borderRadius` is `var(--radius-md)` (was `var(--radius-xs)`). The agent name `h3` now uses `fontFamily: var(--font-display)`, `fontSize: 17px`, `fontWeight: 600`, `fontVariationSettings: '"opsz" 144, "SOFT" 30'`. Status chips are UPPERCASE TRACKED (10.5px, 600, 0.12em, uppercase) with `borderRadius: var(--radius-pill)`. The role label is also UPPERCASE TRACKED micro-label style.

## Verification Results

All plan verification criteria pass:

- `pnpm run build` exits zero — PASS (both tasks)
- `grep -n "repeat(3, 1fr)" apps/admin/app/agents/page.tsx` — PASS (line 335)
- `grep -n "var(--font-display)" apps/admin/app/agents/page.tsx` — PASS (lines 185, 295)
- `grep -n "radial-gradient" apps/admin/app/agents/page.tsx` — PASS (lines 166, 167)
- `grep -n "background: 'transparent'" apps/admin/app/agents/page.tsx` — PASS (lines 50, 160)
- `grep -n "var(--accent)" apps/admin/app/agents/page.tsx` — PASS (multiple matches)
- `grep -n "amber-bg\|--amber\b" apps/admin/app/components/AgentCard.tsx` — CLEAN (no matches)
- `grep -n "gold-bg\|var(--gold)" apps/admin/app/components/AgentCard.tsx` — PASS (lines 28, 29)
- `grep -n "var(--surface-1)" apps/admin/app/components/AgentCard.tsx` — PASS (line 118)
- `grep -n "translateY" apps/admin/app/components/AgentCard.tsx` — PASS (line 125)
- `grep -n "var(--font-display)" apps/admin/app/components/AgentCard.tsx` — PASS (line 153)
- `grep -n "textTransform: 'uppercase'" apps/admin/app/components/AgentCard.tsx` — PASS (lines 170, 186)

## Deviations from Plan

### Auto-fixed Issues

None — plan executed as written.

### Decisions / Clarifications

**1. Greeting strip name: "there" placeholder**
- **Decision:** The `agents/page.tsx` already has `useAuth()` imported for `getToken`, but does not import `useUser` from `@clerk/nextjs`. The plan's `read_first` instruction says: "If user name is not available in the page, use a placeholder like 'there' for now — do not add a new auth hook if not already present."
- **Action:** Used "there" as the name. No additional auth hook added.
- **Rationale:** Stays within the "do not change data fetching, auth logic" constraint.

### Pre-existing Issues (not introduced by this plan)

**`pnpm run lint` broken:** Inherited from 11-01. `next lint` interprets "lint" as a directory argument; no ESLint config exists. Build passes — functional verification complete.

**amber-bg in `agents/[id]/` scope files:** `grep -rn "amber-bg\|var(--amber)" apps/admin/app/agents/` returns matches in `agents/[id]/page.tsx`, `ingest/page.tsx`, `ingest/DocumentDetailModal.tsx`, `deploy/page.tsx`, `eval/page.tsx`, and `soul/page.tsx`. These are Wave 4/5/6 scope and untouched pre-existing uses. Plan 03 scope is `agents/page.tsx` and `AgentCard.tsx` only.

## Known Stubs

**Greeting name:** "there" is a stub for the logged-in user's first name. `apps/admin/app/agents/page.tsx` has `useAuth()` from Clerk for token fetching, but `useUser()` (which exposes `user.firstName`) is not imported. A future wave (or a quick deviation fix in Wave 6's QA pass) should wire the actual name. Tracked here as stub — it does not prevent the plan's goal from being achieved (the greeting strip, gradient, Fraunces heading, and grid are all implemented correctly).

## Threat Flags

None — pure CSS/JSX visual restyle, no security-relevant code paths changed. Auth, data fetching, and route logic are preserved exactly.

## Self-Check: PASSED

Files modified:
- apps/admin/app/agents/page.tsx: exists, contains `var(--font-display)`, `radial-gradient`, `background: 'transparent'`, `repeat(3, 1fr)`, `var(--accent)`
- apps/admin/app/components/AgentCard.tsx: exists, contains `var(--gold-bg)`, `var(--gold)`, `var(--surface-1)`, `translateY`, `var(--font-display)`, `textTransform: 'uppercase'`

Commits verified:
- 9d1965a: feat(11-03): add greeting strip, transparent wrapper, 3-col grid to agents dashboard
- 17e8e35: feat(11-03): restyle AgentCard with surface-1 bg, hover effects, gold status tokens, Fraunces name
