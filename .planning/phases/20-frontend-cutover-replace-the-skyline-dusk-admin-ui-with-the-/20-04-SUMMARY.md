---
phase: 20-frontend-cutover
plan: 04
subsystem: ui
tags: [nextjs, react, gotham, design-system, rail-nav, admin]

# Dependency graph
requires:
  - phase: 20-frontend-cutover (earlier Wave-1 plans)
    provides: "Gotham design tokens in globals.css, re-themed root layout.tsx, GateProvider/useGate context"
provides:
  - "Shared components/gotham/ primitive library: Rail, icons, PageChrome, Zone, Chip, Ledger, Btn, EmptyState"
  - "Console Rail shell mounted on all /agents/** routes (agents/layout.tsx)"
  - "agents/[id]/layout.tsx simplified to a passthrough (stepper sidebar removed)"
affects: [20-frontend-cutover Wave 2-3 plans (agents dashboard, agent-new, agent operations room, soul, ingest, eval, deploy, settings rebuilds)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Verdict-only Chip API: `verdict` prop is a closed union (live/pass/fail/seal/mute), no raw colour prop exists on the component"
    - "Ledger enforces caption + th scope via LedgerColHead/LedgerRowHead helper components rather than raw <th>"
    - "PageChrome cross-offset presets (RAIL_4_DESKTOP/MOBILE, RAIL_5_DESKTOP/MOBILE) exported for reuse by per-route plans"
    - "Rail active-state fallback: Agents lights up whenever no more specific route segment (Ingest/Eval/Deploy/Settings) matches, mirroring every rail-bearing prototype page's own aria-current markup"
    - "href-less <a class=\"rail-btn\"> used for disabled rail glyphs (matches CSS selector for correct sizing without faking navigation)"

key-files:
  created:
    - apps/admin/app/components/gotham/Rail.tsx
    - apps/admin/app/components/gotham/icons.tsx
    - apps/admin/app/components/gotham/PageChrome.tsx
    - apps/admin/app/components/gotham/Zone.tsx
    - apps/admin/app/components/gotham/Chip.tsx
    - apps/admin/app/components/gotham/Ledger.tsx
    - apps/admin/app/components/gotham/Btn.tsx
    - apps/admin/app/components/gotham/EmptyState.tsx
  modified:
    - apps/admin/app/agents/layout.tsx
    - apps/admin/app/agents/[id]/layout.tsx

key-decisions:
  - "Standardized Rail's icon glyphs on the prototype's 18x18 viewBox family (agents.html/ingest.html/eval.html/deploy.html/agent-new.html) rather than the divergent 24x24 family (agent.html/soul.html/settings.html), since only the 18x18 family's .rail-mark is a real <a> linking home as UI-SPEC S5.1-B requires"
  - "Rail active-state precedence: Settings > Deploy > Eval > Ingest > Agents (fallback), matching every rail-bearing prototype page's literal aria-current markup rather than the UI-SPEC prose's 'leave none active' alternative for the overview/soul pages"
  - "When no agent id is resolvable from the pathname (dashboard, /agents/new), Ingest/Eval/Deploy render as href-less <a> elements (styled, non-interactive) instead of real links, avoiding fake navigation to a route that doesn't exist yet"

requirements-completed: [UI2-01, UI2-05]

# Metrics
duration: ~30min
completed: 2026-07-15
status: complete
---

# Phase 20 Plan 04: Gotham Shared Component Library + Console Shell Summary

**Built the shared Gotham primitive library (Rail/icons/PageChrome/Zone/Chip/Ledger/Btn/EmptyState) and mounted the fixed-rail console shell on all /agents routes, replacing TopNav and the JourneyStepper sidebar layout.**

## Performance

- **Duration:** ~30 min
- **Tasks:** 3/3 completed
- **Files modified:** 10 (8 created, 2 rewritten)

## Accomplishments
- `components/gotham/Rail.tsx` + `icons.tsx`: pathname-driven console rail with bespoke inline SVG glyphs (no icon-library import), dashboard (4-icon) and operations (4-icon + Settings) variants.
- `components/gotham/PageChrome.tsx`: graticule/bloom/four-cross background layers + skip link, with reusable per-family cross-offset presets (`RAIL_4_*`, `RAIL_5_*`).
- `components/gotham/Chip.tsx`: verdict-only chip primitive — `color`/`background` props do not exist on the component, so a fourth hue cannot enter the system through it.
- `components/gotham/Ledger.tsx`, `Zone.tsx`, `Btn.tsx`, `EmptyState.tsx`: remaining shared primitives per the UI-SPEC S14 component inventory.
- `agents/layout.tsx` now mounts `<PageChrome/>` + `<Rail/>` + a `.deck` main wrapper in place of `<TopNav/>`.
- `agents/[id]/layout.tsx` is now a one-line `.tint` passthrough — the provisioning-stepper sidebar (and its agent/documents/eval-runs queries) is gone from every operations sub-route; that stepper logic still backs `/agents/new` only, untouched.

## Task Commits

1. **Task 1: Rail + icons + PageChrome** - `7f04d2e` (feat)
2. **Task 2: Zone / Chip / Ledger / Btn / EmptyState primitives** - `0e7406c` (feat)
3. **Task 3: Mount the console shell (agents/layout Rail; [id]/layout passthrough)** - `93fb445` (feat)

## Files Created/Modified
- `apps/admin/app/components/gotham/Rail.tsx` - fixed 56px rail, usePathname active-state, dashboard vs operations variant
- `apps/admin/app/components/gotham/icons.tsx` - bespoke stroke-SVG glyphs (Agents/Ingest/Eval/Deploy/Settings + Check/Doc/Lock)
- `apps/admin/app/components/gotham/PageChrome.tsx` - graticule + bloom + 4 crosses + skip link, RAIL_4/RAIL_5 offset presets
- `apps/admin/app/components/gotham/Zone.tsx` - `.zone` panel primitive, `data-live` prop, polymorphic `as`
- `apps/admin/app/components/gotham/Chip.tsx` - verdict-only chip (live/pass/fail/seal/mute)
- `apps/admin/app/components/gotham/Ledger.tsx` - `.ledger` table wrapper + LedgerColHead/LedgerRowHead/LedgerCell helpers
- `apps/admin/app/components/gotham/Btn.tsx` - `.btn` primary/ghost/seal + native disabled
- `apps/admin/app/components/gotham/EmptyState.tsx` - heading + body + optional link, no default copy
- `apps/admin/app/agents/layout.tsx` - Rail shell replacing TopNav
- `apps/admin/app/agents/[id]/layout.tsx` - passthrough replacing the JourneyStepper sidebar layout

## Decisions Made
- **Icon family choice:** the prototype set has two divergent SVG icon families for the same rail glyphs (18x18 viewBox on agents/ingest/eval/deploy/agent-new vs 24x24 viewBox on agent/soul/settings). Standardized on the 18x18 family — it's the majority and the only one whose `.rail-mark` is a real link, matching the literal UI-SPEC S5.1-B requirement. Documented in `icons.tsx`'s file-level comment.
- **Rail active-state rule:** implemented as a precedence chain (Settings > Deploy > Eval > Ingest > Agents-fallback) driven entirely by pathname substring matches. This reproduces every rail-bearing prototype page's actual `aria-current` markup exactly (including agent.html/soul.html/agent-new.html all marking Agents active), rather than following the UI-SPEC prose's softer "leave none active" suggestion for the overview/soul pages — the prototype source is unambiguous on this point across all 7 pages checked.
- **No-agent-id rail state:** on `/agents` and `/agents/new` there is no current agent, so Ingest/Eval/Deploy render as href-less `<a class="rail-btn">` elements — visually identical sizing (the CSS selector `.rail a.rail-btn` matches any anchor regardless of href), but non-interactive, avoiding a broken or fake link (UI-SPEC S10 anti-pattern: no decoration/fake affordance in a functional slot).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed literal "lucide"/"TopNav"/"JourneyStepper" substrings from doc comments**
- **Found during:** Task 1 and Task 3, after running the plan's own automated verify commands
- **Issue:** Explanatory code comments in `icons.tsx` and both layout files used the literal words "lucide", "TopNav", and "JourneyStepper" to describe what was intentionally NOT used/kept. The plan's automated verification runs `! grep -rn "lucide" app/components/gotham`, `! grep -n "TopNav" app/agents/layout.tsx`, and `! grep -n "JourneyStepper" "app/agents/[id]/layout.tsx"` — negated greps that fail if the substring appears anywhere in the file, including comments.
- **Fix:** Reworded the comments to convey the same intent ("a third-party icon package", "the old dusk top-bar", "a step-provisioning sidebar component") without the literal disallowed substrings.
- **Files modified:** `apps/admin/app/components/gotham/icons.tsx`, `apps/admin/app/agents/layout.tsx`, `apps/admin/app/agents/[id]/layout.tsx`
- **Verification:** Re-ran all three grep checks — all now correctly return no matches (exit 1); full build re-run stayed green.
- **Committed in:** `7f04d2e`, `93fb445` (part of the same task commits — the comments were fixed before committing, not as a follow-up)

---

**Total deviations:** 1 auto-fixed (1 bug in self-authored comments, caught by the plan's own verify script before commit)
**Impact on plan:** No scope creep — cosmetic wording fix only, functionality unchanged.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All 8 shared primitives are import-ready for Wave 2-3 route-rebuild plans (agents dashboard, agent-new, agent operations room, soul, ingest, eval, deploy, settings).
- The Rail shell is live on every `/agents/**` route today; sub-pages still render their old dusk content inside the new shell until their own rebuild plans land — this is expected and not a regression (this plan's scope was shell + primitives only, not per-page rebuilds).
- `RAIL_5_DESKTOP`/`RAIL_5_MOBILE` cross-offset presets are ready for the operations-room family (agent/soul/settings) pages when those plans build `PageChrome offsets={RAIL_5_DESKTOP} mobileOffsets={RAIL_5_MOBILE}`.
- No blockers identified.

## Acceptance Verification

- `corepack pnpm --dir apps/admin build` — green, all `/agents`, `/agents/new`, `/agents/[id]`, and its 5 sub-routes compile and render.
- `grep -rn "lucide" apps/admin/app/components/gotham` — no matches (bespoke SVG only).
- `grep -n "usePathname" apps/admin/app/components/gotham/Rail.tsx` — matches; Rail sets `aria-current={active ? 'page' : undefined}` per icon.
- `grep -n "TopNav" apps/admin/app/agents/layout.tsx` — no matches; `grep -n "Rail" apps/admin/app/agents/layout.tsx` — matches.
- `grep -n "JourneyStepper" "apps/admin/app/agents/[id]/layout.tsx"` — no matches.
- Chip.tsx has no `color`/`background` prop — `verdict` union only.
- Ledger.tsx renders a visually-hidden `<caption>`; `LedgerColHead`/`LedgerRowHead` enforce `scope="col"`/`scope="row"`.
- No nested `<a>` inside another `<a>` anywhere in the new components (EmptyState renders exactly one `Link`; Zone renders no anchor by default).

## Self-Check: PASSED

All 10 created/modified files verified present on disk; all 3 task commit hashes (`7f04d2e`, `0e7406c`, `93fb445`) verified present in git log.

---
*Phase: 20-frontend-cutover*
*Completed: 2026-07-15*
