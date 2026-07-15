---
phase: 20-frontend-cutover
plan: 09
subsystem: ui
tags: [nextjs, react, gotham-design-system, soul-editor, css-only-visualization]

# Dependency graph
requires:
  - phase: 20-frontend-cutover
    provides: Gotham tokens/components (globals.css bone-on-graphite system, Btn/Chip/Zone/EmptyState/PageChrome/Rail primitives, agents/[id] shell)
provides:
  - Gotham-skinned soul editor at /agents/[id]/soul (Identity, Temperament dials, Rules, live prompt preview)
  - CSS-only temperament fallback pattern (.scene-fallback) for confining three.js off authenticated routes
affects: [phase-21 (agent bench / trace grading), any future soul-field schema change]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Page-scoped CSS via `<style dangerouslySetInnerHTML={{ __html: PAGE_CSS }} />` const string (matches agents/[id]/page.tsx and agents/new/page.tsx convention)"
    - "Dial-state-into-existing-field mapping: UI-only numeric controls compose into an existing free-text backend field instead of inventing new schema"
    - "commit-on-blur/Enter, discard-on-Escape draft row pattern for dynamic add/remove lists (RuleList component)"

key-files:
  created: []
  modified:
    - apps/admin/app/agents/[id]/soul/page.tsx

key-decisions:
  - "Warmth/Rigor/Candor dials have no backend column (AgentSoulUpdate only has name/soul_role/soul_voice/soul_do_list/soul_donot_list) — their banded description text composes into the existing soul_voice field on save, per UI-SPEC §6.5's explicit 'do not invent new soul fields' constraint. This means the real backend prompt's 'Voice and tone:' line now shows the dial-derived text, not free-typed prose."
  - "Dropped the soul.html 'Greeting' field entirely — no backing field exists for it and the plan's must_haves truths don't require it; adding an unpersisted input would be a stub."
  - "Replaced the three.js #scene VESSEL mount with a CSS-only 3-bar readout (.scene-fallback) driven by live dial values, using only --live/--hairline tokens (no new hue, respects 'colour is a verdict' law)."
  - "On load, dial positions are best-effort reverse-parsed from soul_voice via regex; agents with pre-existing free-typed voice text default to neutral 50/50/50 (documented tradeoff, not a bug)."

patterns-established:
  - "CSS-only fallback for prototype WebGL specimens confined off a route: reflect the same state (dial values) with plain bars/tokens instead of a decorative placeholder."

requirements-completed: [UI2-04]

# Metrics
duration: 35min
completed: 2026-07-15
status: complete
---

# Phase 20 Plan 09: Soul editor Gotham rebuild Summary

**Rebuilt `/agents/[id]/soul` in the Gotham bone-on-graphite system with Warmth/Rigor/Candor dials, Do/Do-not rule lists, and a live-regenerated prompt preview — dropping the prototype's three.js specimen for a CSS-only bar readout and mapping dial state onto the existing `soul_voice` field so the PATCH payload shape is unchanged.**

## Performance

- **Duration:** ~35 min
- **Completed:** 2026-07-15
- **Tasks:** 1/1
- **Files modified:** 1

## Accomplishments
- Two-column Gotham `.soul` layout (form-col + sticky prompt-preview object pane), single column below 1000px
- Identity (name, role select), Temperament (3 range dials with live mono readout + 3-band description text), Rules (Do/Do-not dynamic add/remove with commit-on-blur/Enter, discard-on-Escape)
- CSS-only temperament visualization (`.scene-fallback`) replaces the prototype's three.js VESSEL mount — no WebGL/CDN surface on this authenticated route
- Sticky `.savebar` with dirty-state tracking ("Unsaved changes" / "Last saved {timestamp}") announced once, Draft chip, and the existing Save/Saving/Next-CTA/Error button state machine preserved
- `Save soul` still calls `PATCH /api/v1/agents/{id}` with the identical field set (`name`, `soul_role`, `soul_voice`, `soul_do_list`, `soul_donot_list`)
- Live prompt preview (`<pre>` in the sticky object pane) regenerates on every keystroke/dial move from real form state, matching the actual backend `build_system_prompt` template shape (`Voice and tone: {voice}`) — never a placeholder

## Task Commits

1. **Task 1: Rebuild soul editor (dials + rules + prompt preview, CSS-only temperament)** - `a9cc8fd` (feat)

**Plan metadata:** (this commit, docs)

## Files Created/Modified
- `apps/admin/app/agents/[id]/soul/page.tsx` - Full rebuild: Gotham layout/tokens, Warmth/Rigor/Candor dials, RuleList component (commit-on-blur/Enter/Escape), CSS-only `.scene-fallback`, preserved PATCH save mutation

## Decisions Made
- Dial values have no backend column; per UI-SPEC §6.5's explicit constraint ("do not invent new soul fields"), their band descriptions compose into the existing `soul_voice` field on save. This is a deliberate re-mapping of an existing free-text field to a structured-then-serialized representation, not a new field.
- Dropped the prototype's "Greeting" field (no backing schema field, not in this plan's must_haves truths) rather than ship an unpersisted stub input.
- CSS-only fallback for the dial preview box uses only existing `--live`/`--hairline` tokens — no new hue introduced, consistent with the Gotham "colour is a verdict" design law.

## Deviations from Plan

None - plan executed exactly as written. The dial-to-`soul_voice` field mapping and the Greeting-field omission were both already directed by the plan's own constraints (`§6.5` "map dials onto whatever soul fields the current PATCH payload already expects... do not invent new soul fields") and the must_haves truths list, not independent scope decisions.

## Issues Encountered
- First-draft explanatory code comment for the three.js drop literally contained the substring `mountGotham` (inside prose, not a call), which the acceptance grep `mountGotham|from 'three'|import\('three'\)` would have flagged as a false positive. Reworded the comment to describe the fix without repeating that identifier. No functional code was affected.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- `/agents/[id]/soul` is fully ported to Gotham with no three.js on the route (verified via negative grep) and the PATCH save contract preserved (verified via positive grep + `pnpm --dir apps/admin build` passing with 0 TypeScript errors).
- Known tradeoff for future phases: dial positions round-trip only for agents saved through this rebuilt page (regex-parsed back from `soul_voice`); pre-existing agents with free-typed voice descriptions will show neutral 50/50/50 dials until first re-save. If a future phase wants durable numeric Warmth/Rigor/Candor storage, that requires a schema migration (Rule 4 architectural change, out of this plan's scope).

---
*Phase: 20-frontend-cutover*
*Completed: 2026-07-15*

## Self-Check: PASSED
- FOUND: apps/admin/app/agents/[id]/soul/page.tsx
- FOUND: .planning/phases/20-frontend-cutover-replace-the-skyline-dusk-admin-ui-with-the-/20-09-SUMMARY.md
- FOUND: a9cc8fd (git log)
