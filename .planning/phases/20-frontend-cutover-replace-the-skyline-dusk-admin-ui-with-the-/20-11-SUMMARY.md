---
phase: 20-frontend-cutover
plan: 11
subsystem: ui
tags: [nextjs, react, svg, eval, ragas, accessibility, gotham-design-system]

# Dependency graph
requires:
  - phase: 20-frontend-cutover (plans 20-03, 20-04)
    provides: "Gotham tokens/components (globals.css bone-on-graphite system incl. --ch-1..4, Chip/Ledger/EmptyState/PageChrome/Rail primitives, agents/[id] shell)"
provides:
  - "Gotham-skinned /agents/[id]/eval page: VITALS-pattern telemetry chart, CHORUS judge, scenario ledger"
  - "--ch-1..4 bone-luminance channel colour pattern for eval telemetry (must-fix 1 applied)"
affects: [frontend-cutover remaining wave-2/3 plans touching agents/[id] routes]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "getComputedStyle(document.documentElement).getPropertyValue('--ch-N') read at draw time to resolve CSS custom properties for SVG stroke/fill presentation attributes (same technique as the ingest HIVE swarm colour fix in 20-10)"
    - "VITALS leader-line pin layout: ResizeObserver-driven pixel-space measurement of trace endpoints, MIN_GAP collision-avoidance nudging between numerals, narrow-breakpoint (900px) collapse to a static 2-col .pin grid"
    - "Word-by-word judge typeset via setInterval + React state (revealed word count) instead of direct DOM textContent mutation, with a separate always-current vh/aria-live echo for screen readers"

key-files:
  created: []
  modified:
    - "apps/admin/app/agents/[id]/eval/page.tsx"

key-decisions:
  - "Judge verdict sentence is generated client-side from real aggregate_scores/scenario pass-fail counts (buildVerdict()) rather than hardcoded — no judge-summary LLM endpoint exists on the backend (apps/api/app/api/v1/evals.py has no such field), matching the plan's 'generate from real data or omit' instruction"
  - "Telemetry chart Y-domain is computed dynamically from the actual run data (padded min/max, clamped to [0,1]) rather than the prototype's fixed 0.80-0.95 range, so the chart stays legible regardless of how many runs exist or how tight/wide the score spread is"
  - "Scenario ledger surfaces only Faithfulness + Relevancy columns (per UI-SPEC S6.7 and the eval.html source), not all four ragas metrics — context_recall/context_precision are aggregate-only, visible in the telemetry chart's four channels"
  - "Dropped the old dusk 'Back to configure' link and tab-based Pass rates/Scenarios split — Gotham Shell B's Rail is the only nav chrome per agents/layout.tsx, and eval.html renders telemetry + judge + ledger as one continuous page, matching the already-ported ingest/soul pages' convention"

requirements-completed: [UI2-05]

# Metrics
duration: ~35min
completed: 2026-07-15
status: complete
---

# Phase 20 Plan 11: Gotham eval telemetry + judge + scenario ledger Summary

**Rebuilt `/agents/[id]/eval` on the Gotham `.telemetry`/`.judge`/`.ledger` contract — VITALS leader-line chart with the four ragas channels recoloured to `--ch-1..4` bone luminance (fixing the prototype's clearest colour-is-a-verdict violation), a CHORUS word-by-word judge generated from real run data, and a real scenario ledger; `GET /eval-runs` (+`/results`) and `POST /eval-runs/trigger` preserved verbatim.**

## Performance

- **Duration:** ~35 min
- **Tasks:** 1/1 completed
- **Files modified:** 1

## Accomplishments
- Rebuilt the eval telemetry chart as a custom SVG component (`TelemetryChart`) porting eval.html's VITALS leader-line pattern (pixel-space `layout()` via `ResizeObserver`, `MIN_GAP=44` collision-avoidance, narrow-breakpoint collapse to a static 2-col pin grid) — generalized from the prototype's fixed 8-point demo data to a dynamic N-run dataset with an adaptive Y-domain.
- Applied must-fix 1: the four channel traces, leader-line dots, and pin swatches resolve `--ch-1..4` (`#E7E5E1`/`#A9AFB1`/`#7C8386`/`#565C5F`) via `getComputedStyle` at draw time — no literal brand hex anywhere in the file (verified by negative grep).
- Rebuilt the judge as a word-by-word typeset paragraph (30ms/word, `.caret` reusing the global `@keyframes caret`) with a `prefers-reduced-motion` branch that sets the full sentence instantly with no caret, plus a permanently-current visually-hidden `role="status" aria-live="polite"` echo carrying the whole sentence at once.
- Rebuilt the scenario table on the shared `Ledger`/`Chip` primitives with `data-verdict="pass"|"fail"` driving the fail-row numeral emphasis rule from eval.html (`'.ledger tr[data-verdict="fail"] td.num { color: var(--ink) }'`).
- Preserved the real `GET /eval-runs`, `GET /eval-runs/{id}/results`, and `POST /eval-runs/trigger` fetches, the 5s poll-while-running effect, and the trigger error-toast handling verbatim from the dusk build.

## Task Commits

1. **Task 1: Rebuild eval page (telemetry chart --ch-1..4, judge, ledger, real data)** - `85cb49b` (feat)

_No TDD tasks in this plan._

## Files Created/Modified
- `apps/admin/app/agents/[id]/eval/page.tsx` - Full rebuild: Gotham `.page`/`.telemetry`/`.judge`/`.ledger` chrome, `TelemetryChart` (VITALS leader-line SVG chart, --ch-1..4 colours), `buildVerdict()` real-data judge sentence, `useChannelColors()`/`useReducedMotion()` hooks, page-scoped `PAGE_CSS` ported from eval.html's `<style>` block. Real data fetches (`eval-runs`, `results`, `trigger`) and polling logic carried over unchanged.

## Decisions Made
See `key-decisions` in frontmatter above.

## Deviations from Plan

None - plan executed exactly as written. One implementation-detail note: the plan's acceptance-criteria grep (`grep -nE "#C79A3C|#5FA3C7|#4FA88A|#9C8DC4"`) is a literal string match, so the initial draft's explanatory code comments (which named the retired hex literals for documentation purposes) tripped the negative grep even though no such value was ever assigned to a variable or style. Reworded those comments to describe the retired hues without repeating their hex strings, so the automated check passes cleanly — this is a documentation-only follow-up within Task 1, not a separate deviation.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- `/agents/[id]/eval` is fully ported to Gotham with the colour-law fix applied and real endpoints preserved — ready for the remaining wave-2/3 plans (deploy, settings) to follow the same primitives/pattern.
- No blockers for downstream plans. The `TelemetryChart`'s adaptive-domain / leader-line-layout pattern is reusable if a future phase needs another multi-channel SVG chart elsewhere in the console.

---
*Phase: 20-frontend-cutover*
*Completed: 2026-07-15*

## Self-Check: PASSED
- FOUND: apps/admin/app/agents/[id]/eval/page.tsx
- FOUND: .planning/phases/20-frontend-cutover-replace-the-skyline-dusk-admin-ui-with-the-/20-11-SUMMARY.md
- FOUND commit: 85cb49b
