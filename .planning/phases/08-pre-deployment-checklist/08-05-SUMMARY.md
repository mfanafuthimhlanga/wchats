---
phase: "08"
plan: "05"
subsystem: admin-ui
tags: [pre-deploy, checklist, ui, tsx, state-machine]
dependency_graph:
  requires: [08-04]
  provides: [DEP-04, DEP-05, DEP-06]
  affects: [apps/admin/app/agents/[id]/deploy/page.tsx]
tech_stack:
  added: []
  patterns: [ChecklistState machine, 5-state UI, polling useEffect, warning acknowledgment gate]
key_files:
  modified:
    - apps/admin/app/agents/[id]/deploy/page.tsx
decisions:
  - DeployTab renamed from 'design' to 'customize'; 'predeploy' added; order is customize|predeploy|embed
  - Default active tab changed from 'embed' to 'customize' per user journey: customise -> check -> embed
  - All 5 checklist states implemented as inline JSX in a single IIFE for the 'complete' branch
  - scoreColor() added inline (score >= 0.9 green, >= 0.7 amber, < 0.7 red) matching eval/page.tsx
  - Warning acknowledgment fires POST acknowledge on check; approve enabled only when acknowledged.size === warnings.length
metrics:
  duration: "~20 min"
  completed: "2026-05-24"
  tasks: 2
  files: 1
---

# Phase 08 Plan 05: Pre-Deploy Tab UI Summary

Pre-Deploy Check tab added to the deploy page with a 5-state ChecklistState machine: idle, running, complete (block/ship_with_warnings/ship), and approved. Tab order resequenced to Customise Widget | Pre-Deploy Check | Embed Code, reflecting the owner journey.

## Tasks Completed

| Task | Description | Commit |
|------|-------------|--------|
| 1 | Extend types, state, polling useEffect, 3-tab nav | 9531b78 |
| 2 | Pre-Deploy tab panel — all 5 state renders | 9531b78 |

Note: Both tasks were implemented in a single atomic edit pass on the file, committed together as 9531b78.

## What Was Built

### apps/admin/app/agents/[id]/deploy/page.tsx

- `DeployTab` type changed to `'customize' | 'predeploy' | 'embed'`; all `'design'` occurrences renamed to `'customize'`
- `ChecklistState` union type and `ChecklistRun` interface added
- `checklistState` and `acknowledged` state added
- `scoreColor()` helper added (matches eval/page.tsx)
- Polling `useEffect`: `setInterval(..., 3000)`, clears on `status === 'complete'` or `'failed'`
- Three tab buttons in correct DOM order: Customise Widget | Pre-Deploy Check | Embed Code
- **State 1 (idle):** centered dashed card, "Ready to deploy your agent?", Run CTA that POSTs to `/checklist-runs`
- **State 2 (running):** spinner with `spin-cw` animation, "Checking your agent's readiness..."
- **States 3/4/4b (complete):** banner (red/amber/green) + 4 signal cards grid (EVAL QUALITY, SECURITY, CORPUS COVERAGE, KNOWLEDGE DEPTH)
- **ship_with_warnings:** per-warning checkbox rows with category badges; Approve enabled only when all checked
- **Approve button:** `disabled` + `aria-disabled="true"` when blocked or warnings unacknowledged; POSTs to `/approve-deployment`
- **State 5 (approved):** green "● Live" badge, "Your agent is live", link to Embed Code tab

## Deviations from Plan

### Note: Tasks 1 and 2 committed together

Both tasks were logically distinct but the panel JSX for Task 2 was implemented in the same edit session as Task 1 types/state changes. A single commit captures both. This is an implementation efficiency, not a deviation from requirements.

### Auto-fixed: TypeScript `unknown` render errors

- **Found during:** TypeScript verification after initial implementation
- **Issue:** `evalSummary.last_run_at` and `redTeamSummary.last_run_at` typed as `unknown` could not be used as ReactNode directly
- **Fix:** Changed conditional from `?.last_run_at &&` to `?.last_run_at != null &&` and rendered via `String(...)` cast
- **Files modified:** apps/admin/app/agents/[id]/deploy/page.tsx
- **Commit:** 9531b78

## Acceptance Criteria Verified

- [x] `type DeployTab = 'customize' | 'predeploy' | 'embed'` present
- [x] `useState<ChecklistState>({ kind: 'idle' })` present
- [x] `useState<DeployTab>('customize')` present
- [x] `setInterval` and `3000` (polling) present
- [x] `id="tab-customize"` present
- [x] `id="tab-predeploy"` present
- [x] `aria-controls="panel-predeploy"` present
- [x] `panel-predeploy` present
- [x] `checklist-runs` API call present
- [x] `approve-deployment` API call present
- [x] `spin-cw` spinner animation present
- [x] "Checking your agent's readiness" copy present
- [x] "Ready to deploy your agent?" copy present
- [x] "Your agent is live" copy present
- [x] "Deployment blocked" copy present
- [x] "Acknowledge each warning to proceed" copy present
- [x] `aria-disabled` on approve button present
- [x] `pnpm tsc --noEmit` exits 0

## Known Stubs

None — Pre-Deploy tab renders live API data. Signal cards display real field values from `run.report`. No hardcoded placeholder data is wired to UI rendering.

## Threat Surface Scan

No new network endpoints introduced. All API calls use existing Clerk Bearer token pattern. No new files created — single file modification only.

## Self-Check: PASSED

- apps/admin/app/agents/[id]/deploy/page.tsx: FOUND (modified)
- Commit 9531b78: FOUND (git log confirmed)
- pnpm tsc --noEmit: exits 0 (confirmed)
