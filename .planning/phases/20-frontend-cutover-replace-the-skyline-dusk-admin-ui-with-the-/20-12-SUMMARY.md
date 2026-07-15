---
phase: 20-frontend-cutover
plan: 12
subsystem: ui
tags: [nextjs, react, react-query, gotham, deploy, widget-config, checklist-runs]

# Dependency graph
requires:
  - phase: 20-frontend-cutover
    provides: "Gotham tokens/components (globals.css, Btn/Chip/Ledger/Zone/GateProvider/PageChrome) from waves 20-03/20-04"
provides:
  - "Rebuilt agents/[id]/deploy/page.tsx in the Gotham design system"
affects: [21-agent-management-gaps]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Gate state derived from real recommendation + deployment_blocked, written via useGate() (GateProvider), never a page-local toggle"
    - "--widget-accent scoped to .preview/.stage only — a component-local exception token that never resolves off --live/--seal, so the customer widget preview never repaints on data-gate=blocked"
    - "Poll the checklist-runs LIST endpoint (newest row status==='running') instead of GET-by-id, avoiding a Celery-task-id vs DB-row-id mismatch"

key-files:
  created: []
  modified:
    - apps/admin/app/agents/[id]/deploy/page.tsx

key-decisions:
  - "Dropped the prototype's .rig 'Test the gate' simulate/clear buttons entirely (must-fix 4) — no fake gate-state chrome ships"
  - "Kept the real widget_config.appearance enum (floating-button/floating-mini-modal/slide-out-panel) instead of the prototype's placeholder float/panel/inline copy, re-skinned to Gotham tiles — non-regression on the real endpoint shape"
  - "Fixed a pre-existing bug: polling GET /checklist-runs/{id} with the Celery task id (which the trigger POST returns) never matches the DB row id the Celery task creates for itself, so it 404s forever. Switched to polling the checklist-runs LIST endpoint while the newest row is 'running'"
  - "Gate table rows source: Evals pass rate + Red team + Knowledge base derived from the real checklist-run report (eval_summary/red_team_summary/corpus_stats); Soul row derived from real agent.soul_role/soul_voice presence via a new GET /agents/{id} fetch (that endpoint already exists and is used elsewhere)"
  - "Dropped the color/typography widget customization UI (9 color pickers + font/radius pickers) — UI-SPEC S6.8 specifies only the appearance-tiles radio group for this page. The full WidgetConfig (including colors/typography) is still loaded on GET and always re-sent complete on every appearance-tile save, so no prior customization is lost even though this page no longer edits it directly"
  - "Widget preview is decorative and does not reflect the saved widget_config colors — it uses the fixed light --widget-accent exception palette per UI-SPEC S4, independent of both data-gate and the user's saved colors"

requirements-completed: [UI2-06]

duration: 45min
completed: 2026-07-15
status: complete
---

# Phase 20 Plan 12: Deploy page Gotham rebuild Summary

**Deploy page (`/agents/[id]/deploy`) rebuilt as a two-column Gotham `.bench` — a real-data gate ledger, embed snippet, and appearance-mode tiles on the left, a sticky light-palette customer widget preview on the right — with the prototype's fake "Test the gate" buttons dropped entirely.**

## Performance

- **Duration:** ~45 min
- **Completed:** 2026-07-15T15:14:29+02:00
- **Tasks:** 1
- **Files modified:** 1

## Accomplishments
- Replaced the tabbed dusk deploy page (customise/predeploy/embed tabs) with the Gotham single-scroll `.bench` layout from `deploy.html`: gate + embed + appearance on the left, sticky widget preview on the right (hidden ≤1100px)
- Gate table (4 rows: Evals pass rate / Red team / Knowledge base / Soul) driven entirely by the real latest `checklist-runs` report + a live `GET /agents/{id}` fetch for the Soul row — `data-gate` root attribute set once via `useGate()`, mirroring the pattern already established in `agents/[id]/page.tsx`
- Dropped the prototype's `.rig` "Test the gate" simulate/clear buttons (must-fix 4) — verified absent via grep
- Preserved `POST/GET checklist-runs`, `POST .../acknowledge`, `POST approve-deployment`, and `GET/POST widget-config` endpoints verbatim (same request/response shapes as the prior dusk page)
- Customer widget preview retained, using the fixed light `--widget-accent` exception palette (UI-SPEC §4), scoped to `.preview`/`.stage` only, `aria-hidden` decorative, and does not repaint on `data-gate="blocked"`
- Appearance tiles wired as a real radio group to the real `widget_config.appearance` enum, auto-saving on selection (merges into the full loaded config so colors/typography are never clobbered)
- Fixed a real bug found while porting the polling logic (see Deviations)

## Task Commits

1. **Task 1: Rebuild deploy page (gate, embed, appearance, widget preview; drop test-gate buttons)** - `aa8080a` (feat)

## Files Created/Modified
- `apps/admin/app/agents/[id]/deploy/page.tsx` - Full Gotham rebuild of the deploy page (783 insertions, 1331 deletions vs. the prior dusk implementation)

## Decisions Made
- See `key-decisions` in frontmatter above (gate derivation, appearance-enum non-regression, list-polling bug fix, Soul row data source, dropped color/typography editing UI per UI-SPEC §6.8 scope).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed checklist-run polling using the wrong id**
- **Found during:** Task 1 (porting the pre-deploy check polling logic)
- **Issue:** The prior dusk page polled `GET /checklist-runs/{checklist_run_id}` using the id returned by the trigger `POST /checklist-runs` response. That response returns the **Celery task id** (`apps/api/app/api/v1/deployment.py` line ~120: `return {"checklist_run_id": task.id, ...}`), but the Celery task itself creates a brand-new `ChecklistRun` DB row with its own `gen_random_uuid()` id (`apps/api/app/worker/tasks/runtime/deployment.py` line ~125) — the two ids never match. Polling by that id would 404 indefinitely, leaving the UI stuck on "Checking…" forever until a manual page refresh.
- **Fix:** Replaced the by-id poll with a poll of the `GET /checklist-runs` LIST endpoint (already ordered newest-first), using `refetchInterval` while the newest row's `status === 'running'`. This is the same row the trigger POST just inserted, so it is strictly correct and removes the id-mismatch dependency entirely.
- **Files modified:** `apps/admin/app/agents/[id]/deploy/page.tsx`
- **Verification:** `pnpm --dir apps/admin build` passes TypeScript; the list-based polling logic type-checks against the real `ChecklistRunListResponse` shape (`{runs: [...]}`).
- **Committed in:** `aa8080a` (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 bug fix)
**Impact on plan:** The fix is necessary for the checklist-run trigger flow to ever complete in the UI without a manual refresh; no scope creep — it replaces one endpoint-polling strategy with another already-existing endpoint (`GET /checklist-runs`), no new backend surface introduced.

## Issues Encountered
None beyond the deviation documented above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Deploy page is fully re-skinned to Gotham; UI2-06 requirements met (gate reflects real state, widget preview retained in the light exception palette, must-fix 4 applied, endpoints preserved)
- No blockers for subsequent phase-20 waves. Phase 21 (agent-management-gaps) may want to revisit whether widget color/typography customization should get its own dedicated UI surface, since this page no longer exposes those controls (the backend fields and endpoint remain fully intact)

---
*Phase: 20-frontend-cutover*
*Completed: 2026-07-15*

## Self-Check: PASSED
- FOUND: apps/admin/app/agents/[id]/deploy/page.tsx
- FOUND: .planning/phases/20-frontend-cutover-replace-the-skyline-dusk-admin-ui-with-the-/20-12-SUMMARY.md
- FOUND: commit aa8080a
