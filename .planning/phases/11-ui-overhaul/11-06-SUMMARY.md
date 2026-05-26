---
phase: 11-ui-overhaul
plan: "06"
status: complete
completed: "2026-05-26"
wave: 6
commits:
  - ef20eec
  - 872ad71
duration: ~40 min
tasks_completed: 2
files_modified: 8
---

# Plan 11-06 Summary — Auth Pages + Visual QA Gate (Final)

## What Was Built

Wave 6 completed the Hillbrow at Dusk overhaul. Task 1 applied auth page and component changes; Task 2 resolved 9 visual QA failures that were documented in the pause handoff (.continue-here.md).

**Task 1 — Auth pages + UserAvatar + SignOutTab (ef20eec):**
- `sign-in/[[...sign-in]]/page.tsx` — transparent `<main>` wrapper, logo-mark.svg + wordmark.svg above the Clerk card
- `sign-up/[[...sign-up]]/page.tsx` — identical transparent pattern
- `UserAvatar.tsx` — lilac ring `rgba(183,154,224,0.6)`, Lucide icon → `var(--accent)`
- `SignOutTab.tsx` — LogOut icon → `var(--red)`

**Task 2 — Visual QA fixes (872ad71):**
All 9 failures from the .continue-here.md handoff resolved:
- **Landing headline**: `clamp(32px,4vw,52px)` → `clamp(48px,6.4vw,86px)` per spec
- **Hero sub**: `16px/var(--text-3)/480px` → `19px/var(--text-2)/560px` per spec
- **Hero layout**: section `min-height:720px`, padding `80px 56px 120px`, grid `minmax(0,1fr) minmax(0,0.9fr)` gap `80px`
- **Trust strip**: nums `22px` → `30px`, labels UPPERCASE TRACKED `10.5px/0.1em`, padding-top `28px`
- **Right panel**: `HeroSteps` animation replaced with new static `HeroPipeline` component (BUILD PIPELINE · AGENT ALPHA, 4 steps: 2 done/green, 1 active/coral-pulsing, 1 locked)
- **Agents Refresh button**: removed (not in reference)
- **Stat card labels**: corrected to exact spec (`CONVERSATIONS · 7D`, `FAITHFULNESS · MEDIAN`, `P95 LATENCY`, `COST · 7D`)
- **Sub-line under greeting**: added ("You have N live agents, M in test, D draft." 14px var(--text-3))
- **"Your agents" section header**: title 16px/700 + count badge (accent-bg) + filter tabs (All/Live/Testing/Draft each with count)
- **AgentCard icons**: 🤖 → lucide SVG per role (`MessageCircle`/support, `Zap`/sales, `Settings`/helpdesk, `Bot`/fallback)
- **AgentCard status badge**: `Ready/Provisioning` → `+ LIVE` (green), `+ TESTING` (gold), `+ BUILDING` (lilac-dim)

## Token Regression Greps (all CLEAN)

| Grep | Result |
|------|--------|
| Old palette hex in globals.css (F0E8E0, 7B1C3A, FFFCF9, F7F0EA) | CLEAN |
| Old fonts (font-pixelify, FungkyBrowDEMO, Pixelify) | CLEAN |
| Hardcoded #ef4444 / #EF4444 in changed components | CLEAN |

## Acceptance Criteria

| Criterion | Status |
|-----------|--------|
| Sign-in: transparent wrapper + logo above Clerk card | ✓ PASS |
| Sign-up: same transparent pattern | ✓ PASS |
| UserAvatar: lilac ring rgba(183,154,224,0.6) | ✓ PASS |
| UserAvatar: Lucide icon uses var(--accent) | ✓ PASS |
| SignOutTab: icon uses var(--red) | ✓ PASS |
| Landing headline: clamp(48px,6.4vw,86px) | ✓ PASS |
| Hero sub: 19px / --text-2 / 560px | ✓ PASS |
| Right panel: BUILD PIPELINE static card | ✓ PASS |
| Agents: Refresh button removed | ✓ PASS |
| Agents: Stat labels per spec | ✓ PASS |
| Agents: Sub-line under greeting | ✓ PASS |
| Agents: "Your agents" header + count + filter tabs | ✓ PASS |
| AgentCard: SVG role icons | ✓ PASS |
| AgentCard: + LIVE / + TESTING / + BUILDING badge | ✓ PASS |
| pnpm run build exits zero | ✓ PASS |
| Token regression greps CLEAN | ✓ PASS |

## Decisions

- `--lilac-bg` not in token system; used `--lilac-dim` (rgba(183,154,224,0.12)) for BUILDING badge background
- HeroPipeline is a new static component (no animation) — keeps the right panel light-weight; HeroSteps remains in codebase for potential future use
- Filter tabs use client-side state (`activeFilter`) to filter the `agents` array without additional fetch
- `--amber-bg` refs in AlertsBanner.tsx and DocumentDetailModal.tsx are pre-existing out-of-scope files; documented deviation
