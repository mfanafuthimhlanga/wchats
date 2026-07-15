---
phase: 20-frontend-cutover
plan: 07
subsystem: ui
tags: [nextjs, react, gotham-design-system, provisioning-wizard]

requires:
  - phase: 20-frontend-cutover
    provides: "Gotham tokens/globals.css base, Rail/PageChrome shell (agents/layout.tsx), gotham/icons.tsx bespoke glyph set (20-03/20-04)"
provides:
  - "deriveStepState — named, exported, grep-able step-gating function (JourneyStepper.tsx)"
  - "Gotham-restyled .stepper/.station provisioning stepper"
  - "Rebuilt agents/new/page.tsx: .page.prov/.build two-column wizard, role segmented control, cold instruments panel, on-create checklist driven by real request/poll timing"
affects: [20-08, ui-review, provisioning-flow]

tech-stack:
  added: []
  patterns:
    - "Real-timing progress mapping: a small ProvisionStage (0/1/2) integer set inline inside the mutationFn at each await boundary, combined with pollQuery.data presence and the terminal polled status, drives a 4-row checklist honestly — no fixed-duration setTimeout stagger"
    - "Page-scoped CSS via a static (non-interpolated) dangerouslySetInnerHTML <style> block, matching the existing prov-spinner precedent — used here for both the .stepper/.station rules (JourneyStepper.tsx) and the .build/.f/.seg/.instruments/.oc-* rules (agents/new/page.tsx), since none of these classes exist in the shared globals.css foundation"

key-files:
  created: []
  modified:
    - apps/admin/app/components/JourneyStepper.tsx
    - apps/admin/app/agents/new/page.tsx

key-decisions:
  - "Extracted the dusk build's inline isCurrentPage-override step-state computation into a named exported deriveStepState(step, currentStepKey) function — same behaviour, now grep-able/testable per the plan's acceptance gate"
  - "Simplified JourneyStepperProps to steps-only, dropping agentName/agentRole — the horizontal .stepper/.station contract has no header slot (the page's own <h1> already carries that context), and JourneyStepper is now used only by agents/new/page.tsx (apps/admin/app/agents/[id]/layout.tsx's own comment confirms the sidebar/stepper was already dropped from the operations sub-routes by an earlier plan)"
  - "Replaced the auto-redirect-on-ready (router.push) with the prototype's explicit .done block + 'Open the agent' CTA, focus moved to it on completion — matches UI-SPEC S6.3's explicit focus-management instruction; the underlying POST/POST/GET-poll sequence and request bodies are unchanged"
  - "Repurposed the prototype's 'tone segmented control' pattern to the real, already-existing (but previously unrenderable) role field (support/sales/helpdesk) instead of adding a second, decorative 'tone' concept with no backend field to bind to — avoids UI-SPEC S10's 'decoration in a functional slot' anti-pattern while fixing a real UX gap (role could not be set in the UI before this plan) and giving the segmented control the one non-focus --live exception UI-SPEC S6.3 grants it"
  - "Mapped the 4-row on-create checklist onto the 3 real async checkpoints the backend actually exposes (provision-call success, create-call success + agent_id, first poll response, terminal 'ready' status) rather than inventing a 4th signal — documented in-code as an honest best-effort mapping, not a fabricated timeline"
  - "Kept the existing primaryRole/businessDomain text fields (still local-only, not sent in the POST body) rather than removing them or changing the request shape — matches the non-regression prohibition on endpoint body shape"
  - "Interpreted UI-SPEC S6.3's '--live nowhere except focus-visible + segmented-selected' colour law as targeting verdict chips/gate colouring specifically (not baseline .btn-primary chrome or the active-station/done-checklist-tick treatment), since the source prototype (agent-new.html) itself uses --live in exactly those same baseline-chrome places — ported faithfully rather than stripping colour the prototype itself renders"

requirements-completed: [UI2-04]

duration: 20min
completed: 2026-07-15
status: complete
---

# Phase 20 Plan 07: Gotham provisioning wizard (agents/new) Summary

**Rebuilt `/agents/new` on the `.page.prov`/`.build`/`.stepper` Gotham contract — 4-station hairline stepper (steps 2-4 locked), chromeless two-column form + cold instruments panel, on-create checklist driven by real request/poll timing; `POST /me/provision` -> `POST /api/v1/agents` -> poll `GET /api/v1/agents/{id}` preserved verbatim.**

## Performance

- **Duration:** ~20 min
- **Completed:** 2026-07-15
- **Tasks:** 2
- **Files modified:** 2 (JourneyStepper.tsx, agents/new/page.tsx)

## Accomplishments
- `JourneyStepper` restyled to the `.stepper`/`.station` visual contract (4 stations on one hairline rule, lock glyph + `data-locked="true"` + `opacity: 0.62` for locked stations, active station lit via `aria-current="step"`), with `deriveStepState` extracted as a named, exported, byte-for-byte-identical function.
- `agents/new/page.tsx` fully rebuilt from `prototypes/gotham/agent-new.html`: `.page.prov` header, the restyled stepper (Provision active, Configure/Test/Deploy locked), the two-column `.build` (chromeless `.f` rows for name/primary role/business domain, a role segmented control, `.voice` line, `.actions` with Create agent/Cancel/elapsed counter, `.done` block) and the right-hand "Instruments · no signal yet" panel (4 dead-flat `.flatline` readouts + the 4-row on-create checklist).
- Create -> provision -> poll sequence preserved verbatim: same `POST /me/provision`, same `POST /api/v1/agents` body shape (`{ name, role, soul: { voice: '', do: [], do_not: [] } }`), same `GET /api/v1/agents/{id}` poll loop and 2s interval.
- On-create checklist ticks and the elapsed counter derive from real request/response + poll events (a `ProvisionStage` marker set at each `await` boundary inside the mutation, plus poll-data presence and terminal status) — no fixed-duration `setTimeout` staging.
- Zero verdict colour introduced beyond focus-visible outlines, the role segmented control's selected state, and the baseline `--live` chrome the prototype itself already uses (primary buttons, active station, done-checklist ticks).

## Task Commits

Each task was committed atomically:

1. **Task 1: Restyle JourneyStepper to .stepper/.station (logic unchanged)** - `25ba620` (feat)
2. **Task 2: Rebuild agents/new/page.tsx (provisioning, endpoints preserved)** - `00f8fc3` (feat)

## Files Created/Modified
- `apps/admin/app/components/JourneyStepper.tsx` - Restyled to `.stepper`/`.station`; `deriveStepState` extracted as a named export; props simplified to `steps`-only; bespoke `LockIcon` replaces `lucide-react`
- `apps/admin/app/agents/new/page.tsx` - Full rebuild: Gotham two-column provisioning wizard, real-timing checklist, `.done` block + focus-managed "Open the agent" CTA, page-scoped CSS (`.build`/`.f`/`.seg`/`.instruments`/`.oc-*`) ported as a static `<style>` block

## Decisions Made
See `key-decisions` in frontmatter above for the full reasoning; summarized:
- `deriveStepState` extracted (not invented) from the prior inline logic.
- `JourneyStepperProps` simplified to `steps`-only (component now has exactly one caller).
- Auto-redirect replaced with the prototype's manual `.done` + "Open the agent" CTA + focus management.
- The prototype's "tone segmented control" repurposed to the real `role` field rather than added as a second, unwired decorative control.
- On-create checklist mapped onto the 3 real signals the backend exposes, not a 4th invented one.
- `primaryRole`/`businessDomain` fields kept local-only (unchanged from the prior dusk build) — the `POST /api/v1/agents` body shape was not altered.
- `--live` usage on this page matches the source prototype's own usage (baseline buttons, active station, done-tick colour) — treated as baseline chrome, not a "verdict" per UI-SPEC's colour law intent.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added a role selector to the form**
- **Found during:** Task 2 (page rebuild)
- **Issue:** The prior dusk `agents/new/page.tsx` defaulted `role` to `'support'` and sent it in the `POST /api/v1/agents` body, but never rendered any control for the user to change it — role selection was silently inaccessible in the UI.
- **Fix:** Repurposed the prototype's segmented-control widget pattern (visually identical to its "tone" control) to the real `role` field (Support/Sales/Helpdesk), wired to the existing state and request body — no new backend field, no request-shape change.
- **Files modified:** apps/admin/app/agents/new/page.tsx
- **Verification:** `pnpm --dir apps/admin build` compiles; `grep -n "/api/v1/agents"` confirms the body shape (`name`, `role`, `soul`) is unchanged.
- **Committed in:** 00f8fc3 (Task 2 commit)

**2. [Rule 3 - Blocking] Ported page-scoped CSS not present in globals.css**
- **Found during:** Both tasks
- **Issue:** `.stepper`/`.station` (JourneyStepper) and `.build`/`.f`/`.f-set`/`.seg`/`.voice-line`/`.actions`/`.elapsed`/`.done`/`.instruments`/`.chan`/`.flatline`/`.oc*` (agents/new page) exist only in `agent-new.html`'s own page-scoped `<style>` block in the prototype source, not in the shared `globals.css` foundation from earlier plans (which only ported `.page`/`.page-head`/`.zone`/`.chip`/`.btn`/`.ledger`/`.label` etc). Without these classes the rebuilt markup would render unstyled.
- **Fix:** Ported the CSS verbatim (token names already match) as static, non-interpolated `<style dangerouslySetInnerHTML>` blocks scoped to each component — consistent with the plan's own instruction to "keep the existing dangerouslySetInnerHTML keyframe-CSS pattern static-only" and with this plan's `files_modified` list (adding to the shared `globals.css` would have touched a third file outside scope).
- **Files modified:** apps/admin/app/components/JourneyStepper.tsx, apps/admin/app/agents/new/page.tsx (both already in scope — no extra files touched)
- **Verification:** `pnpm --dir apps/admin build` compiles; visual classes match the prototype's own selectors (`.oc-row[data-done="true"] .glyph`, `.seg input:checked + label`, `.station[data-locked="true"]`) exactly.
- **Committed in:** 25ba620 (Task 1), 00f8fc3 (Task 2)

---

**Total deviations:** 2 auto-fixed (1 missing critical, 1 blocking)
**Impact on plan:** Both are necessary for the rebuilt page to render and function per the design contract; no scope creep — the role-selector fix repairs an existing UX gap without touching the preserved endpoint shape, and the CSS port stays within this plan's two files.

## Threat Flags

None — the only trust boundary this plan touches (client -> FastAPI provision/create/poll) is unchanged from the dusk build; the `dangerouslySetInnerHTML` blocks added in this plan are static string literals with no interpolated data, per the plan's own T-20-07-02 mitigation.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- `agents/new` is fully Gotham; `POST /me/provision`, `POST /api/v1/agents`, and the `GET /api/v1/agents/{id}` poll loop verified unchanged (grep + build gates).
- Steps 2-4 lock state is unconditional on this page by design (the operations-room sub-routes no longer mount `JourneyStepper` at all, per `apps/admin/app/agents/[id]/layout.tsx`'s own comment) — the Wave 5 provisioning smoke test should confirm this visually and confirm the `.done` -> "Open the agent" click still lands on `/agents/{id}` correctly.
- The on-create checklist's real-timing mapping (3 real signals onto 4 UI rows) should get a manual click-through during Wave 5 QA against a live backend to confirm the perceived pacing feels reasonable, since build/grep gates cannot verify runtime timing feel.

---
*Phase: 20-frontend-cutover*
*Completed: 2026-07-15*

## Self-Check: PASSED

All files (JourneyStepper.tsx, agents/new/page.tsx, this SUMMARY.md) and both task commits (25ba620, 00f8fc3) verified present.
