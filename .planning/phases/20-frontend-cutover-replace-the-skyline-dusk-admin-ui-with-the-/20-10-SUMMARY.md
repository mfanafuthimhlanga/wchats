---
phase: 20-frontend-cutover
plan: 10
subsystem: ui
tags: [nextjs, react, gotham-design-system, ingest, sse, svg-animation]

# Dependency graph
requires:
  - phase: 20-frontend-cutover
    provides: "Gotham tokens/components (globals.css bone-on-graphite system, Chip/Ledger/EmptyState/PageChrome/Rail primitives, agents/[id] shell) — 20-03/20-04"
provides:
  - Gotham-skinned ingest page at /agents/[id]/ingest (Upload-file/Add-URL tabs, KB ledger, SSE-driven HIVE chunk swarm)
  - Restyled DocumentDetailModal (Chip-based status badges, .zone/.well tokens)
affects: [phase-21 (any future document-management UI work)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Page-scoped CSS via `<style dangerouslySetInnerHTML={{ __html: PAGE_CSS }} />` const string (matches soul/page.tsx and agents/[id]/page.tsx convention)"
    - "Colour-law status mapping: raw backend enum -> closed ChipVerdict union via a small mapping function, never raw hex — Chip enforces the law by construction"
    - "SVG-attribute colour resolved via getComputedStyle at draw time (CSS custom properties do not resolve in raw SVG presentation attributes) — same technique documented for eval.html's channel-colour fix"
    - "Real-state-gated decorative animation: the HIVE swarm mounts only on the optimistic (currently-uploading) row, i.e. only on the actual SSE-driven transition-to-parsing event, never on page load/refetch of an already-pending row"

key-files:
  created: []
  modified:
    - apps/admin/app/agents/[id]/ingest/page.tsx
    - apps/admin/app/agents/[id]/ingest/DocumentDetailModal.tsx

key-decisions:
  - "Dropped ingest.html's Retry button and fabricated failure-reason string for failed documents — no backend retry endpoint exists (only DELETE), and no failure-reason field exists in DocumentListResponse. Shipping a Retry button with no real handler would violate UI-SPEC §10 anti-pattern 6 (decoration in a functional slot). The existing, real Delete action is kept instead."
  - "HIVE chunk swarm renders only on optimisticDocs rows (the live SSE upload-in-flight state), not on real documents already sitting in parse_status='pending'/'processing' from a prior session/page reload — mirrors the prototype's own two distinct examples (a transient parseInto() swarm on fresh add vs. a plain chip-live+elapsed row for an already-pending document with no swarm)."
  - "Swarm dot colour is read via getComputedStyle('--live') once per swarm build (matching the prototype's own per-dot literal-at-creation pattern) rather than re-read every animation frame — sufction for the ~1.8s one-shot animation and avoids an unnecessary per-frame DOM read."
  - "Corrected the dropzone copy from the prototype's 'Drop PDF, DOCX or MD' (DOCX is not in ACCEPTED_EXTENSIONS) to 'Drop PDF, PNG, JPG or MD', matching the real accepted file types the upload endpoint enforces — copy accuracy over verbatim prototype text."
  - "File upload/URL fetch use a real native <label htmlFor>/<input type=file> pair (visually hidden input, standard sibling-selector focus styling) instead of a custom role=button/tabIndex div — matches ingest.html's own accessible pattern and is a net a11y improvement over the previous dusk build's div-based control."

requirements-completed: [UI2-04]

# Metrics
duration: 55min
completed: 2026-07-15
status: complete
---

# Phase 20 Plan 10: Ingest page Gotham rebuild Summary

**Rebuilt `/agents/[id]/ingest` in the Gotham bone-on-graphite system — roving-tab Upload-file/Add-URL panel, a real `.ledger` knowledge-base table, and a `--live`-coloured HIVE chunk swarm layered over the real SSE-driven upload flow — then restyled `DocumentDetailModal` to the same token set with its detail-fetch wiring untouched.**

## Performance

- **Duration:** ~55 min
- **Completed:** 2026-07-15
- **Tasks:** 2/2
- **Files modified:** 2

## Accomplishments

- Two-tab `role="tablist"` panel (Upload file / Add URL) with arrow-key/Home/End roving-tabindex navigation, ported from `ingest.html`'s tab script
- Real `<label htmlFor>` + hidden `<input type="file">` dropzone (`.drop`), drag/drop preserved, plus a real URL `<form>` with the existing validation (`http(s)://` prefix check)
- Knowledge-base table rebuilt on the Gotham `Ledger`/`LedgerColHead`/`LedgerRowHead` primitives (real `<caption>`, `scope`-correct headers) reading `GET /api/v1/agents/{id}/documents`
- Status badges rebuilt on the `Chip` primitive via a closed `parseStatusVerdict()` mapping (`pass`/`live`/`seal`/`mute`) — colour-is-a-verdict enforced by construction, no amber/warning tier
- HIVE chunk swarm (40-dot golden-angle-spiral SVG particle system, `MAX_SPEED` cap unchanged) ported and colour-fixed: dot fill now reads the resolved `--live` bone value via `getComputedStyle` at draw time instead of the retired brass hex literal; `prefers-reduced-motion` renders dots pre-settled with no animation loop
- Swarm mounts only on `optimisticDocs` rows — the actual SSE-driven "a document just started parsing" event — not on page-load-time pending/processing rows, matching the prototype's own two distinct treatments
- `DocumentDetailModal` restyled to `.zone`/`.well` + Chip tokens; `GET .../documents/{documentId}/detail` fetch, focus-trap, Escape-to-close, and focus-restore-on-close all preserved unchanged
- All existing real functionality preserved verbatim: `POST /documents` upload, SSE progress stream (`readSseProgress`, including the `event:`-line parser and terminal-event teardown), `DELETE /documents/{id}`, agent-not-ready guard, "Next: Run evals" CTA

## Task Commits

1. **Task 1: Rebuild ingest page (tabs, KB ledger, SSE-driven swarm, colour fix)** - `5657085` (feat)
2. **Task 2: Restyle DocumentDetailModal to Gotham tokens** - `57eb0a6` (feat)

**Plan metadata:** (this commit, docs)

## Files Created/Modified

- `apps/admin/app/agents/[id]/ingest/page.tsx` — Full rebuild: Gotham tabs/dropzone/ledger, `ChunkSwarm` component (colour-fixed, reduced-motion-aware), preserved upload/SSE/delete logic verbatim
- `apps/admin/app/agents/[id]/ingest/DocumentDetailModal.tsx` — Restyle only: dusk tokens/classes swapped for Gotham (`.zone`/`.well`, `--ink*`/`--hairline*`/`--r-*`, `Chip` primitive for status badges), structure and fetch wiring unchanged

## Decisions Made

- Retry button and failure-reason string dropped for failed documents (no backend retry endpoint or reason field exists) — kept the real Delete action instead, per UI-SPEC §10's prohibition on shipping fake interaction handlers.
- Swarm animation gated to the optimistic (SSE-live) row only, not every persisted pending/processing document — a documented, deliberate scoping matching the prototype's own two distinct row examples (fresh-add transient swarm vs. persisted still-processing plain chip).
- Dropzone copy corrected to list the real accepted file types (PDF/PNG/JPG/MD) rather than the prototype's literal (and inaccurate for this backend) "PDF, DOCX or MD" text.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed literal `#C79A3C` string from a source-code comment**

- **Found during:** Task 1 acceptance verification
- **Issue:** The first draft's explanatory comment describing the colour fix literally contained the substring `#C79A3C` (documenting what was removed), which the acceptance grep `grep -n "#C79A3C" page.tsx` flagged as a false positive — the same class of issue 20-09's summary documented for the string `mountGotham`.
- **Fix:** Reworded the comment to describe the fix ("the retired brass gold literal") without repeating the literal hex digits. No functional code was affected.
- **Files modified:** `apps/admin/app/agents/[id]/ingest/page.tsx`
- **Commit:** folded into `5657085` (caught before the task commit)

No other deviations — the plan's must_haves truths, prohibitions, and threat-model mitigations were all satisfied as specified.

## Issues Encountered

None beyond the comment/grep false-positive above, which was caught and fixed before committing.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- `/agents/[id]/ingest` is fully ported to Gotham with `pnpm --dir apps/admin build` passing (0 TypeScript errors), no retired brass hex/token residue (verified via negative grep), the real `/documents` upload + SSE flow preserved (verified via positive grep + code review), and no dusk tokens/classes remaining in either modified file.
- `DocumentDetailModal` detail-fetch wiring (`GET .../documents/{documentId}/detail`) is unchanged and verified present via grep.
- Known scoping choice for future phases: the HIVE swarm only decorates the live-SSE upload transition, not a document that returns to "processing" from a background/retry mechanism outside this build (none exists yet). If a real retry/reprocess endpoint is added in a future phase, that action should also trigger an `optimisticDocs`-style row so the swarm continues to only represent a real, live "parsing right now" transition rather than a persisted state.

---
*Phase: 20-frontend-cutover*
*Completed: 2026-07-15*

## Self-Check: PASSED
- FOUND: apps/admin/app/agents/[id]/ingest/page.tsx
- FOUND: apps/admin/app/agents/[id]/ingest/DocumentDetailModal.tsx
- FOUND: .planning/phases/20-frontend-cutover-replace-the-skyline-dusk-admin-ui-with-the-/20-10-SUMMARY.md
- FOUND: 5657085 (git log)
- FOUND: 57eb0a6 (git log)
