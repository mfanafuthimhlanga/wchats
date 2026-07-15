---
phase: 20-frontend-cutover
plan: 08
subsystem: ui
tags: [nextjs, react, gotham-design-system, agent-operations-room, tanstack-query, gate-mechanism]

# Dependency graph
requires:
  - phase: 20-frontend-cutover (plan 20-03)
    provides: "Gotham tokens/globals.css, GateProvider/useGate context mounted at root layout"
  - phase: 20-frontend-cutover (plan 20-04)
    provides: "Rail/PageChrome console shell (agents/layout.tsx), Zone/Chip/Ledger/Btn/EmptyState primitives"
provides:
  - "Six-region agent operations room (agents/[id]/page.tsx) — Live, Retrieval health, The bench, Judgement, Adversary, The prompt in fixed order"
  - "Real eval-runs + red-team-runs wiring for Judgement/Adversary regions, honest-empty for Live/Retrieval health/The bench/The prompt"
  - "Real checklist-runs + red-team deployment_blocked + folded red_team_critical alert gate derivation, single useGate().setGate() writer per page"
  - "Restyled + relocated AlertsBanner.tsx (Gotham tokens, GET /alerts + resolve wiring unchanged, gate fold + Judgement eval_regression chip via onAlertsChange)"
affects: [20-frontend-cutover Wave 3 (eval/deploy page rebuilds reuse the same eval-runs/checklist-runs/red-team-runs fetch patterns), ui-review, phase-21 (Live/Retrieval health/bench/prompt regions await their backends)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Single-writer gate derivation per page: one useEffect computes gateBlocked from checklist recommendation + red-team deployment_blocked + an unresolved red_team_critical alert, then calls setGate() once — AlertsBanner may independently call setGate('blocked') as defense-in-depth but never 'open', so two writers can never fight over reopening the gate"
    - "onAlertsChange lift: AlertsBanner keeps its own GET /alerts fetch + local state, but lifts the current alert list to the parent page via a callback prop so the page can fold red_team_critical into its gate computation and eval_regression into a Judgement-region chip without duplicating the fetch"
    - "Honest-empty for unreturned fields: eval_scenarios has no created_at/trace-id in the results API response, so the ledger's Added column and Origin's trace linkage render 'not tracked yet' / a mapped source label rather than fabricating a date or trace id"
    - "Page-scoped CSS via a static (non-interpolated) dangerouslySetInnerHTML <style> block for agent.html page-local classes (.ident/.gatebar/.chans/.chan*/.sev*/.critical/.foot-note/.prompt-acts) that have no equivalent in the shared globals.css Gotham port — same precedent as agents/new/page.tsx (20-07)"

key-files:
  created: []
  modified:
    - apps/admin/app/agents/[id]/page.tsx
    - apps/admin/app/agents/[id]/components/AlertsBanner.tsx

key-decisions:
  - "Adversary summary omits a 'Coverage %' tile (the prototype's 5th sev-cell) since no aggregate coverage percentage is returned by any endpoint — only the 4 severity-count tiles (Critical/High/Medium/Low), which ARE real, are rendered; padding a 5th tile with a placeholder value was judged less honest than simply not claiming a metric that doesn't exist yet, distinct from the born-in-production/authored/coverage-table cases where the prototype's own field exists and is explicitly called out in UI-SPEC S12 as an honest-empty tile"
  - "Judgement ledger's 'Added' column renders 'not tracked yet' for every row — GET /eval-runs/{id}/results has no created_at/trace-id field (only scenario_id/question/source/scores/passed), so showing a date would be fabrication; documented here rather than silently dropping the column so a future Phase 21 pass can wire it once the field exists"
  - "'Contain and file as a scenario' (agent.html's containBtn) was NOT ported — no backend action exists to file a red-team finding into the eval suite (that's the Adversary/Judgement flywheel link, not yet built per AGENT-MGMT-GAPS.md). Only 'Run the programme' (a real POST /red-team-runs trigger) is wired."
  - "Gate ownership: page.tsx's single useEffect (checklist + red-team + alerts) is the only place that calls setGate('open'); AlertsBanner.tsx independently calls setGate('blocked') on an unresolved red_team_critical alert as a second, additive signal that can only escalate, never clobber a legitimate open-gate transition — avoids the two-writer race a naive per-component gate effect would introduce"

requirements-completed: [UI2-05]

# Metrics
duration: ~55min
completed: 2026-07-15
status: complete
---

# Phase 20 Plan 08: Agent Operations Room (Gotham six-region ops room) Summary

**Rebuilt `/agents/[id]` as the Gotham six-region operations room — real data for Judgement (eval-runs) and Adversary (red-team-runs), honest empty states for the four regions with no backend yet, and a gatebar that derives from real checklist/red-team/alert data through a single `useGate()` writer instead of a page-local toggle.**

## Performance

- **Duration:** ~55 min
- **Completed:** 2026-07-15
- **Tasks:** 2/2
- **Files modified:** 2

## Accomplishments

- `apps/admin/app/agents/[id]/page.tsx` rebuilt from `prototypes/gotham/agent.html`: header (`.ident` + Serving chip), `.gatebar` under a `.rule-double` top border, then all six `.section` regions in fixed order (Live, Retrieval health, The bench, Judgement, Adversary, The prompt).
- Live, Retrieval health, The bench, The prompt render the region shell + a single `<EmptyState>` using the exact UI-SPEC §12 copy — no `mulberry32` seeded traces, hardcoded `channels`/`traces` arrays, or fabricated numbers anywhere. Retrieval health's `section-head` meta honestly surfaces the real document count (from the preserved `documents` query), never a fabricated retrieval metric.
- Judgement wires `GET /api/v1/agents/{id}/eval-runs` + `.../eval-runs/{id}/results`: the 5-tile summary shows real scenarios/held/failed counts, `born in production`/`authored` render `not tracked yet` (OPS-12 provenance not shipped), and the scenario ledger maps the real `source` enum to an Origin column and the real `passed` flag + `faithfulness` score to a pass/fail verdict chip.
- Adversary wires `GET /api/v1/agents/{id}/red-team-runs`: real severity tile counts computed from the latest run's `findings` JSON, a real `.critical` banner when `deployment_blocked` is true (sourced from an actual `severity==='critical'` finding's description), and `POST /api/v1/agents/{id}/red-team-runs` wired to "Run the programme" (disabled unless the agent is `ready`, matching the endpoint's own guard). The per-strategy coverage ledger renders an honest-empty block per §12 copy.
- Gatebar + `data-gate`: a single `useEffect` derives `gateBlocked` from `GET /checklist-runs` (`recommendation === 'block'`) OR the latest red-team run's `deployment_blocked` OR an unresolved `red_team_critical` alert, then calls `useGate().setGate()` once — no component hand-colours itself.
- `AlertsBanner.tsx` restyled to Gotham chip/banner tokens (the prior `var(--gold)`/`var(--red)`/`var(--text-*)`/`glass-strong` references are dusk tokens removed in 20-03 and would have silently failed at runtime — Rule 1 fix, see Deviations). `GET .../alerts` + `POST .../alerts/{id}/resolve` wiring is byte-for-byte unchanged. It now lifts its alert list to the page via `onAlertsChange` (feeding the gate computation and a Judgement-region `eval_regression` chip) and independently calls `setGate('blocked')` on an unresolved `red_team_critical` alert as an additive, escalate-only signal.

## Task Commits

Each task was committed atomically:

1. **Task 1: Ops-room shell + header/gatebar + four honest-empty regions** - `3b24e4e` (feat)
2. **Task 2: Wire Judgement + Adversary + gatebar + relocate AlertsBanner** - `7a2017e` (feat)

_Note: no TDD tasks in this plan._

## Files Created/Modified

- `apps/admin/app/agents/[id]/page.tsx` — Rebuilt: six-region Gotham operations room, real eval-runs/red-team-runs/checklist-runs wiring, gate derivation, page-scoped CSS for agent.html's page-local classes
- `apps/admin/app/agents/[id]/components/AlertsBanner.tsx` — Restyled to Gotham tokens, `onAlertsChange` lift + gate-fold `setGate('blocked')`, data/resolve wiring unchanged

## Decisions Made

See `key-decisions` in the frontmatter above (Adversary coverage-tile omission, Judgement ledger's honest-empty "Added" column, dropped "Contain and file" demo action, single-writer + additive-escalation gate architecture).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed the literal "mulberry32" substring from a doc comment**
- **Found during:** Task 1, after running the plan's own automated negative-grep verify command
- **Issue:** The file-level doc comment explaining what was intentionally NOT ported used the literal word "mulberry32" (describing the prototype's seeded-noise generator by name). The plan's automated verify runs `! grep -nE "mulberry32|traces\[|channels\s*=\s*\[" ...` — a negated grep that fails on any occurrence anywhere in the file, including comments.
- **Fix:** Reworded the comment to convey the same intent ("client-side seeded-noise demo data") without the literal disallowed substring.
- **Files modified:** `apps/admin/app/agents/[id]/page.tsx`
- **Verification:** Re-ran the negative grep — clean (exit 1, no matches); full build stayed green.
- **Committed in:** `3b24e4e` (fixed before commit, not a follow-up)

**2. [Rule 1 - Bug] AlertsBanner referenced retired dusk CSS custom properties**
- **Found during:** Task 2, while restyling AlertsBanner per the plan's explicit instruction
- **Issue:** The pre-existing `AlertsBanner.tsx` used `var(--gold)`, `var(--gold-bg)`, `var(--red)`, `var(--red-bg)`, `var(--text-1)`, `var(--text-2)`, `var(--text-3)`, `var(--radius-sm)`, `var(--radius-pill)`, and the `glass-strong` class — all dusk-era tokens/classes that plan 20-03 removed from `globals.css`. Left unstyled, an undefined CSS custom property resolves to nothing (invalid declaration, silently dropped), which would have rendered the alert banner with broken/invisible styling once this page mounted on the Gotham token system.
- **Fix:** Replaced every reference with the equivalent Gotham token/primitive: `Chip` component (`verdict="seal"` for critical, `verdict="mute"` for warning — no third "warning" hue per UI-SPEC §8 law 5), `var(--ink)`/`var(--ink-2)`/`var(--ink-3)`, `var(--seal-dim)`/`var(--hairline)`, `var(--r-panel)`.
- **Files modified:** `apps/admin/app/agents/[id]/components/AlertsBanner.tsx`
- **Verification:** Build compiles; visual verdict-chip rendering follows the same closed `Chip` union already enforced elsewhere in the Gotham component library.
- **Committed in:** `7a2017e`

---

**Total deviations:** 2 auto-fixed (1 self-authored-comment substring bug caught by the plan's own verify script, 1 pre-existing dusk-token-reference bug surfaced by this plan's explicit restyle instruction)
**Impact on plan:** No scope creep — both are within-file correctness fixes required to satisfy the plan's own acceptance criteria and the "no dusk residue" anti-pattern (UI-SPEC §10 #2).

## Issues Encountered

- `pnpm` is not resolvable directly on the Bash tool's `PATH` in this environment; used `corepack pnpm build` (same workaround documented in 20-03's summary). Both task-level builds and the final plan-level build passed via this path.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- Judgement/Adversary regions are real and ready for Wave 5 UAT smoke testing against a live agent with at least one eval run and one red-team run.
- Live, Retrieval health, The bench, and The prompt regions remain intentionally honest-empty pending their Phase 21 backends (aggregate metrics table, retrieval telemetry table, failure-triage bench endpoints, prompt_versions table — see `AGENT-MGMT-GAPS.md`).
- The gatebar's "last verified" timestamp currently falls back between the latest checklist run's `created_at` and the latest red-team run's `finished_at` — once Phase 21 adds a dedicated `approve-deployment`/gate-history read, this can be tightened to a single authoritative source.
- No blockers identified.

## Known Stubs

- **Judgement ledger "Added" column** (`apps/admin/app/agents/[id]/page.tsx`): renders the literal string `not tracked yet` for every scenario row. `GET /eval-runs/{id}/results` does not return a `created_at`/`trace_id` field on `eval_scenarios`, so no real date exists to show. Not a regression — the prior dusk build never rendered a suite ledger at all. Resolves once Phase 21 (OPS-12 provenance columns) or a small API addition exposes `eval_scenarios.created_at`.
- **Adversary "Coverage %" tile**: intentionally omitted rather than stubbed (see Decisions) — no coverage percentage is computed anywhere in the backend; the per-strategy coverage ledger below the severity tiles already carries the honest-empty message covering this gap.

## Acceptance Verification

- `corepack pnpm build` — green after both tasks (TypeScript + Turbopack compile clean).
- `grep -nE "mulberry32|traces\[|channels\s*=\s*\[" "app/agents/[id]/page.tsx"` — no matches.
- `grep -n "aria-labelledby=" "app/agents/[id]/page.tsx"` — six matches, in order: `live-h`, `rag-h`, `bench-h`, `judge-h`, `adv-h`, `prompt-h`.
- `grep -n "eval-runs" "app/agents/[id]/page.tsx"` — matches (list + results fetch + query keys).
- `grep -n "red-team-runs" "app/agents/[id]/page.tsx"` — matches (GET list + POST trigger + query keys).
- `grep -n "checklist-runs" "app/agents/[id]/page.tsx"` — matches.
- `grep -n "/alerts" "app/agents/[id]/components/AlertsBanner.tsx"` — matches (GET list + POST resolve), unchanged from the prior dusk build.
- `grep -n "setGate\|useGate" "app/agents/[id]/components/AlertsBanner.tsx"` — matches.
- Every `Chip` `verdict` prop use is one of `live`/`pass`/`fail`/`seal`/`mute` — no raw colour introduced anywhere in this plan's changes.
- All `apiBase` fetch calls in both modified files target only endpoints in UI-SPEC §9's preservation map (`/agents/{id}`, `/agents/{id}/documents`, `/agents/{id}/eval-runs[/{id}/results]`, `/agents/{id}/red-team-runs`, `/agents/{id}/checklist-runs`, `/agents/{id}/alerts[/{id}/resolve]`) — no invented endpoint.

## Self-Check: PASSED

- FOUND: apps/admin/app/agents/[id]/page.tsx
- FOUND: apps/admin/app/agents/[id]/components/AlertsBanner.tsx
- FOUND: .planning/phases/20-frontend-cutover-replace-the-skyline-dusk-admin-ui-with-the-/20-08-SUMMARY.md
- FOUND commit: 3b24e4e
- FOUND commit: 7a2017e

---
*Phase: 20-frontend-cutover*
*Completed: 2026-07-15*
