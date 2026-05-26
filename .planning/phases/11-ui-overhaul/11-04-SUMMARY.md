---
phase: 11-ui-overhaul
plan: "04"
subsystem: ui
tags: [nextjs, react, css-tokens, hillbrow-at-dusk, design-system]

requires:
  - phase: 11-01
    provides: "Hillbrow at Dusk token foundation in globals.css; --accent, --green, --gold, --surface-1, --surface-2 all live"

provides:
  - "JourneyStepper: coral active state, green done state, --surface-1 aside background, zero hardcoded Parchment hex"
  - "StepSubtaskCard: --surface-1 card background (elevated from --surface-2)"
  - "agents/[id]/page.tsx: STATUS_COLORS uses --gold/--gold-bg for pending/provisioning"
  - "agents/new/page.tsx: transparent outer, --surface-1 form panel, --surface-2 inputs, UPPERCASE TRACKED labels, coral CTA with #0B0717"
  - "soul/page.tsx: Fraunces italic-coral agent name, dark form panel, --border-soft textarea borders, --gold voice warning"
  - "ingest/page.tsx: gold status chips (not amber), --accent-dim upload zone hover, document filenames in font-mono"

affects:
  - "11-05 (eval/deploy/settings pages)"
  - "11-06 (auth pages QA pass)"

tech-stack:
  added: []
  patterns:
    - "Amber→Gold status token rename: STATUS_COLORS / PARSE_STATUS_COLORS use --gold-bg/--gold (not --amber-bg/--amber)"
    - "Coral active state: background var(--accent-dim), border var(--border-hard)"
    - "Green done state: background var(--green-bg), border rgba(52,211,153,0.25), circle var(--green)"
    - "Form panel dark pattern: --surface-1 wrapper, --surface-2 inputs, UPPERCASE TRACKED labels 10.5px/0.12em"
    - "Fraunces italic-coral for agent names: font-display, italic, weight 300, var(--accent), fontVariationSettings opsz 144 SOFT 100"

key-files:
  created: []
  modified:
    - apps/admin/app/components/JourneyStepper.tsx
    - apps/admin/app/components/StepSubtaskCard.tsx
    - apps/admin/app/agents/[id]/page.tsx
    - apps/admin/app/agents/new/page.tsx
    - apps/admin/app/agents/[id]/soul/page.tsx
    - apps/admin/app/agents/[id]/ingest/page.tsx

key-decisions:
  - "[11-04] JourneyStepper done-step border uses rgba(52,211,153,0.25) — this is var(--green) at 25% opacity matching the new dark token system"
  - "[11-04] soul/page form panel uses margin:32px inside the layout flex container — keeps visual breathing room without adding a wrapper div"
  - "[11-04] ingest/page agent-not-ready warning updated to --gold-bg/--gold (was --amber-bg/--amber) — ensures consistent status-colour semantics across all amber→gold rename sites"
  - "[11-04] pnpm run lint pre-existing broken — same inherited issue from 11-01; build passes, lint skip documented"

patterns-established:
  - "Pattern: UPPERCASE TRACKED labels use fontSize 10.5px, fontWeight 600, letterSpacing 0.12em, textTransform uppercase, color var(--text-3)"
  - "Pattern: Coral primary CTA always uses color #0B0717 (--text-on-accent), not #fff"

requirements-completed:
  - UI-11

duration: ~20min
completed: 2026-05-26
---

# Phase 11 Plan 04: Agent Journey Screens Summary

**Six agent journey files rethemed to Hillbrow at Dusk: JourneyStepper coral/green active+done states, StepSubtaskCard elevated to --surface-1, amber→gold status token rename across three files, Fraunces italic-coral agent name on soul page, dark form panels with UPPERCASE TRACKED labels.**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-05-26T14:40:00Z
- **Completed:** 2026-05-26T15:05:00Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments

- Eliminated all hardcoded Parchment hex (#D97706, #16A34A, rgba(217,119,6,...), rgba(22,163,74,...)) from JourneyStepper.tsx; replaced with coral and green tokens
- Applied amber→gold status token rename across agents/[id]/page.tsx, soul/page.tsx (voice warning), and ingest/page.tsx (PARSE_STATUS_COLORS + agent-not-ready banner + optimistic badge)
- Upgraded form panels: agents/new gets --surface-1 panel wrapper; soul/page gets --surface-1 form panel with margin; all input fields use --border-soft
- Fraunces italic-coral applied to agent name display in soul/page.tsx heading
- Upload zone in ingest/page updated to --surface-1 idle / --accent-dim hover with correct border tokens

## Task Commits

1. **Task 1: Fix JourneyStepper + StepSubtaskCard + agents/[id] gold tokens** - `b8b18a3` (feat)
2. **Task 2: agents/new + soul + ingest pages — dark form panels, Fraunces italic name, gold status chips** - `1c1f851` (feat)

## Files Created/Modified

- `apps/admin/app/components/JourneyStepper.tsx` - aside bg --surface-1; active step coral tokens; done step green tokens; connector green; zero hardcoded hex
- `apps/admin/app/components/StepSubtaskCard.tsx` - card bg --surface-2→--surface-1
- `apps/admin/app/agents/[id]/page.tsx` - STATUS_COLORS pending/provisioning --amber-bg/--amber→--gold-bg/--gold
- `apps/admin/app/agents/new/page.tsx` - form panel --surface-1; inputs --surface-2/--border-soft; labels 10.5px/0.12em; CTA #0B0717
- `apps/admin/app/agents/[id]/soul/page.tsx` - Fraunces italic-coral agent name; form panel --surface-1; textarea --border-soft; voice warning --gold; save btn #0B0717
- `apps/admin/app/agents/[id]/ingest/page.tsx` - PARSE_STATUS_COLORS/banner/badge --gold; upload zone --surface-1/--border dashed idle, --accent-dim/--border-hard hover; filename font-mono/text-2

## Decisions Made

- Done-step border in JourneyStepper uses `rgba(52,211,153,0.25)` — literal green at 25% opacity, since `--green-border` is not defined as a separate token in globals.css; this matches the PATTERNS.md target.
- soul/page.tsx form panel uses `margin: 32px` on the panel div rather than a separate wrapper — preserves the existing flex layout (flex-row with preview panel) while giving the form the dark card treatment.
- ingest/page.tsx agent-not-ready warning uses `rgba(240,198,116,0.25)` for the border — consistent with --gold-bg colour family.

## Deviations from Plan

None — plan executed exactly as written. All six files updated per PATTERNS.md targets. Build passes.

## Issues Encountered

- pnpm run lint is pre-existing broken (no ESLint config in admin, inherited from Wave 1). Build passes cleanly. Documented in previous summaries.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- Wave 4 complete: all six agent journey files use Hillbrow at Dusk tokens
- Wave 5 ready: eval/page.tsx (glass stat tiles, Recharts colours, scoreColor --gold), deploy/page.tsx (DEFAULT_CONFIG dark), settings/page.tsx (form panel pattern)
- No blockers

---
*Phase: 11-ui-overhaul*
*Completed: 2026-05-26*
