---
phase: 20-frontend-cutover
plan: 06
subsystem: ui
tags: [nextjs, react, gotham-design-system, agents-dashboard]

requires:
  - phase: 20-frontend-cutover
    provides: "Gotham tokens/globals.css base, Rail/PageChrome shell (agents/layout.tsx), Zone/Chip/Btn/EmptyState primitives (20-03/20-04)"
provides:
  - "Gotham-restyled AgentCard.tsx (.zone.card, stretched-link, verdict chips)"
  - "Rebuilt agents/page.tsx dashboard on the Gotham shell, GET /agents preserved verbatim"
  - "Ported .agents/.card*/.hair/.metrics/.vh CSS classes into globals.css (agents.html source)"
affects: [20-07, 20-08, ui-review, agents-dashboard]

tech-stack:
  added: []
  patterns:
    - "Stretched-link card pattern: single real <a> via `.card-open::after { inset:0 }`, other interactive elements (delete controls) raised via `position:relative; z-index:1`"
    - "Honest-empty metrics: fields with no backing data (docs/pass-rate/sessions) render as literal placeholders ('—' / 'pending'), never fabricated numbers"

key-files:
  created: []
  modified:
    - apps/admin/app/components/AgentCard.tsx
    - apps/admin/app/agents/page.tsx
    - apps/admin/app/globals.css

key-decisions:
  - "AgentCard's `role` prop is retained in the data contract (API-shape compatibility) but no longer rendered — the Gotham agents.html card design has no role/icon slot"
  - "Mapped agent status 'error' to the seal (gate-shut) verdict chip and hair-divider colour since it's the only status this endpoint's payload gives enough signal to treat as a gate-shut claim; no separate 'Gate shut' second chip is added since GET /agents does not return checklist/deployment-blocked state"
  - "Dropped the dusk-only All/Live/Testing/Draft filter strip — not part of the agents.html design contract and no equivalent exists in the Gotham source; keeping it would be adding chrome outside the ported design, not preserving functionality tied to data integrity"
  - "Ported agents.html's page-scoped .agents/.card*/.hair/.metrics/.vh CSS into globals.css rather than inlining as React style objects, matching the existing convention set by .zone/.chip in the same file"

requirements-completed: [UI2-03]

duration: 15min
completed: 2026-07-15
status: complete
---

# Phase 20 Plan 06: Gotham agents dashboard + AgentCard Summary

**Rebuilt `/agents` and `AgentCard` on the Gotham `.zone.card` contract — stretched single-anchor cards with verdict chips, honest-empty metrics, and the fake NL command-strip cut entirely; `GET /api/v1/agents` and `POST /me/provision` preserved verbatim.**

## Performance

- **Duration:** ~15 min
- **Completed:** 2026-07-15
- **Tasks:** 2
- **Files modified:** 3 (AgentCard.tsx, agents/page.tsx, globals.css)

## Accomplishments
- `AgentCard` restyled to a Gotham `.zone.card`: name (`h3`) + mono id, status → verdict-chip mapping (`live`/`mute`/`seal`) via the shared `Chip` primitive, a `.hair` divider (oxblood on error/shut), a 3-col `.metrics` row, `.card-foot` with created date + stretched "Open →" link — exactly one real `<a>` in the tree.
- Rebuilt `agents/page.tsx`: `.page-head` with dynamic sub-copy, `.agents` grid, zero-agents `EmptyState`, all real fetches (`/me/provision`, `GET /api/v1/agents`, `DELETE /api/v1/agents/{id}`) preserved byte-for-byte from the dusk version.
- Cut the prototype's fake NL command-strip entirely (keyword-match dispatch with no backend) per must-fix 4 / UI-SPEC §6.2 option (a).
- Ported the missing agents.html-scoped CSS (`.agents`, `.card`, `.card-top`, `.card-name`, `.card-id`, `.card-chips`, `.hair`, `.metrics`, `.card-foot`, `.card-open`, `.vh`) into `globals.css` since these weren't part of the shared Gotham token/component foundation from earlier plans.
- Delete-confirm controls kept working under the new stretched-link card via `position:relative; z-index:1` on a new `.card-actions` class, restyled with the shared `Btn` primitive (`seal`/`ghost` variants).

## Task Commits

Each task was committed atomically:

1. **Task 1: Restyle AgentCard to .zone.card (stretched-link)** - `046235e` (feat)
2. **Task 2: Rebuild agents/page.tsx (GET /agents, cut command strip, empty state)** - `47c76b7` (feat)

_Task 1's commit also carries the supporting `globals.css` CSS additions (`.card*`, `.hair`, `.metrics`, `.agents`, `.vh`) — these classes are required for `AgentCard.tsx` (and, in Task 2, `agents/page.tsx`) to render/compile against; they were not yet ported by any prior plan._

## Files Created/Modified
- `apps/admin/app/components/AgentCard.tsx` - Restyled to `.zone.card`: verdict-chip status mapping, single stretched anchor, honest-empty metrics row, delete-confirm flow preserved
- `apps/admin/app/agents/page.tsx` - Rebuilt dashboard: Gotham page-head/grid/empty-state, all data fetches preserved verbatim, fake command strip cut, dusk filter strip dropped
- `apps/admin/app/globals.css` - Added `.agents`/`.card*`/`.hair`/`.metrics`/`.vh` CSS (ported from `prototypes/gotham/agents.html`'s page-scoped `<style>` block)

## Decisions Made
- Kept `role` in `AgentCardProps` for data-contract compatibility but stopped rendering it — the Gotham card design has no role/icon slot.
- Mapped `status === 'error'` to the seal (gate-shut) verdict and oxblood hair-divider, since it's the only status signal available from `GET /agents` that maps to a "gate shut" claim; did not fabricate a second "Gate shut" chip since checklist/deployment-blocked data isn't part of this endpoint's payload.
- Dropped the dusk-only All/Live/Testing/Draft filter strip (no equivalent in `agents.html`) to match the ported design contract exactly rather than carrying forward un-spec'd chrome.
- Placed the ported agents.html CSS in `globals.css` (matching the existing `.zone`/`.chip` convention) rather than inlining it as React style objects.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Ported missing `.agents`/`.card*`/`.hair`/`.metrics`/`.vh` CSS into globals.css**
- **Found during:** Task 1 (AgentCard restyle)
- **Issue:** The plan's `<read_first>` correctly identifies the `.zone.card` markup contract in `agents.html`, but the classes it depends on (`.card`, `.card-top`, `.card-name`, `.card-id`, `.card-chips`, `.hair`, `.metrics`, `.card-foot`, `.card-open`, `.agents` grid, `.vh` visually-hidden utility) exist only in `agents.html`'s own page-scoped `<style>` block, not in the shared `globals.css` foundation built by the earlier 20-03/20-04 plans (which only ported `.zone`/`.chip-*`/`.btn-*`). Without these, the card markup would render unstyled.
- **Fix:** Ported the classes verbatim (token names already matched — `var(--hairline)`, `var(--ink-3)`, `var(--seal)`, `var(--r-panel)`, etc. are identical between `agents.html` and `globals.css`) into `globals.css`, plus one addition not in the prototype (`.card-actions { position:relative; z-index:1 }`) needed so the existing delete-confirm controls remain clickable under the new `.card-open::after` stretched-link overlay.
- **Files modified:** apps/admin/app/globals.css
- **Verification:** `pnpm --dir apps/admin build` compiles; visual class names present and match `.card[data-shut="true"] .hair` / `.card-open::after` selectors from the prototype exactly.
- **Committed in:** 046235e (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 missing critical)
**Impact on plan:** Necessary for the restyled card/grid to render at all; no scope creep — pure CSS port of classes the plan's own `<read_first>` already pointed at in `agents.html`.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- `AgentCard` and the agents dashboard are fully Gotham; `GET /api/v1/agents` and `POST /me/provision` verified unchanged (grep + build gates).
- Route smoke test (agent cards render, single anchor per card, filter behavior removed) deferred to Wave 5 per the plan's `<verification>` note — not part of this plan's automated gates.
- Delete-confirm flow under the stretched-link card should get a manual click-through check during Wave 5 QA to confirm the `.card-actions` z-index fix behaves as expected in a real browser (build/grep gates cannot verify runtime click-hit-testing).

---
*Phase: 20-frontend-cutover*
*Completed: 2026-07-15*

## Self-Check: PASSED

All files (AgentCard.tsx, agents/page.tsx, globals.css, this SUMMARY.md) and both task commits (046235e, 47c76b7) verified present.
