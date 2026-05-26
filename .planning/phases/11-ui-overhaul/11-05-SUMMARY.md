---
phase: 11-ui-overhaul
plan: "05"
subsystem: ui
tags: [nextjs, recharts, css-variables, hillbrow-at-dusk, glass-ui, dark-theme]

# Dependency graph
requires:
  - phase: 11-01
    provides: Hillbrow at Dusk CSS token foundation (globals.css) including --glass-bg, --glass-blur, --glass-border, --gold, --gold-bg, --lilac, --lilac-dim

provides:
  - Eval dashboard with glass aggregate stat tiles (4 metrics, approved glass use case)
  - Recharts line colours updated to dark-system palette (--gold/#FBBF24, --accent/#F4748C, --green/#34D399, --lilac/#B79AE0)
  - scoreColor helper uses --gold for mid-range (not --amber which is building warmth)
  - Deploy page DEFAULT_CONFIG with dark Hillbrow hex values (#140E2A widget, #F4748C header)
  - Deploy embed code block styled with --surface-2 + --font-mono + --text-2
  - Deploy widget customizer converted to sticky 2-column layout (form flex:1, preview 300px sticky)
  - Settings page dark form panel pattern (--surface-1 panel, --surface-2 inputs, UPPERCASE labels, coral save button)

affects: [11-06-auth-pages, eval-page-visual-qa, deploy-page-visual-qa]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Glass stat tiles: --glass-bg + backdropFilter var(--glass-blur) on 4-column metric grid (approved use case — stat tiles only)"
    - "scoreColor token: --gold (not --amber) for 0.7-0.9 range; --amber = building warmth in Hillbrow system"
    - "DEFAULT_CONFIG: always dark Hillbrow starting values for widget customiser"
    - "Sticky 2-column widget customizer: left form flex:1, right preview 300px position:sticky top:80px"

key-files:
  created: []
  modified:
    - apps/admin/app/agents/[id]/eval/page.tsx
    - apps/admin/app/agents/[id]/deploy/page.tsx
    - apps/admin/app/agents/[id]/settings/page.tsx

key-decisions:
  - "[11-05] scoreColor: --amber → --gold for mid-range scores (0.7-0.9) — amber = building warmth in Hillbrow system, not a status warning token"
  - "[11-05] Glass stat tiles use latestRun.aggregate_scores directly (faithfulness, answer_relevancy, context_precision, context_recall) — no fallback needed since tiles only render when latestRun is truthy"
  - "[11-05] Widget customizer layout: 3-column grid → flex 2-column (form left flex:1, preview right 300px sticky) per .continue-here.md decision"
  - "[11-05] Deploy page signal cards: --surface-2 → --surface-1 for consistency with panel pattern"
  - "[11-05] Recharts hex per plan spec (#FBBF24 gold, not #F0C674 from PATTERNS.md) — plan spec takes precedence for must_haves acceptance criteria"
  - "[11-05] pnpm run lint pre-existing broken (same inherited issue from 11-01 through 11-04) — build passes, lint skip documented"

patterns-established:
  - "Glass stat tiles pattern: 4-column grid with --glass-bg + backdropFilter + --glass-border + --radius-sm + 16px 20px padding"
  - "Metric label: 10.5px + 600 + 0.12em letterSpacing + uppercase + --text-3"
  - "Metric value: var(--font-mono) + 28px + 600 + scoreColor(value)"

requirements-completed: [UI-09, UI-11]

# Metrics
duration: ~20min
completed: 2026-05-26
---

# Phase 11 Plan 05: Eval + Deploy + Settings Wave Summary

**Eval glass aggregate tiles + corrected Recharts dark-system colours + dark DEFAULT_CONFIG widget + settings dark form panel across eval/deploy/settings pages**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-05-26T15:00:00Z
- **Completed:** 2026-05-26T15:15:58Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments

- Added 4-column glass stat tile grid above the Recharts line chart in eval page (approved glass use case: stat tiles only; chart card and scenario table remain solid --surface-1)
- Updated all Recharts Line strokes from invisible Parchment hex to dark-readable values: faithfulness #FBBF24 (gold), answer_relevancy #F4748C (coral), context_precision #34D399 (green), context_recall #B79AE0 (lilac)
- Fixed scoreColor across both eval and deploy pages: `var(--amber)` → `var(--gold)` for 0.7–0.9 range; removed all `var(--amber-bg)` / `var(--amber)` status uses
- Replaced deploy DEFAULT_CONFIG with dark Hillbrow values (#140E2A widget, #F4748C header/buttons, #0B0717 on-accent text, #1E1638 surface-2 inputs)
- Updated embed code block to --surface-2 + --font-mono + --text-2; converted widget customizer from 3-column grid to sticky 2-column flex layout
- Applied dark form panel pattern to settings page (--surface-1 panel, --surface-2 input, UPPERCASE label, coral save button)

## Task Commits

1. **Task 1: Eval glass stat tiles + Recharts + scoreColor** - `d59fe8c` (feat)
2. **Task 2: Deploy DEFAULT_CONFIG + panels; settings form pattern** - `30a06f1` (feat)

## Files Created/Modified

- `apps/admin/app/agents/[id]/eval/page.tsx` - Glass stat tiles, new Recharts strokes (#FBBF24/#F4748C/#34D399/#B79AE0), scoreColor --gold, MetricDot colours, source badge gold/lilac tokens
- `apps/admin/app/agents/[id]/deploy/page.tsx` - Dark DEFAULT_CONFIG, embed pre --surface-2/mono, status banners gold/green tokens, signal cards --surface-1, sticky 2-col layout, coral buttons #0B0717
- `apps/admin/app/agents/[id]/settings/page.tsx` - Full dark form panel replacement (--surface-1 panel, --surface-2 input, UPPERCASE label, disabled coral save button)

## Decisions Made

- **scoreColor token:** Used `--gold` (not `--amber`) for 0.7-0.9 range. In the Hillbrow at Dusk system, `--amber` = building warmth (`#E8A87C`), not a status warning. `--gold` = status warning.
- **Recharts hex values:** Used plan spec values (#FBBF24 for gold) rather than PATTERNS.md values (#F0C674). Plan spec must_haves are the acceptance criteria target.
- **Glass stat tiles placement:** Tiles render only when `latestRun` is truthy (guarded with `{latestRun && ...}`). No glass on chart card or scenario table.
- **Widget customizer layout:** Converted 3-column CSS grid (255px 310px 1fr) to flex 2-column (left form flex:1, right preview 300px sticky top:80px) per .continue-here.md decision.
- **Signal cards:** Changed from --surface-2 to --surface-1 for consistency with dark card panel pattern.
- **Deploy panel wrappers:** Embed panel uses --surface-1 (was --surface-2); style pickers uses --surface-1 (was --surface-2).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing] Source badge tokens in eval page**
- **Found during:** Task 1 (eval page edits)
- **Issue:** Source badge used `var(--amber-bg)`/`var(--amber)` for 'generated' and hardcoded `#EFF6FF`/`#1D4ED8` (Parchment blue) for 'mined' — both invisible/wrong-palette on dark background
- **Fix:** `generated` → `var(--gold-bg)`/`var(--gold)`; `mined` → `var(--lilac-dim)`/`var(--lilac)` (consistent with Hillbrow accent palette)
- **Files modified:** apps/admin/app/agents/[id]/eval/page.tsx
- **Committed in:** d59fe8c (Task 1 commit)

**2. [Rule 2 - Missing] Deploy page scoreColor + warning badge tokens**
- **Found during:** Task 2 (deploy page edits)
- **Issue:** deploy/page.tsx had its own `scoreColor` using `var(--amber)` for mid-range; badgeColors used `var(--amber-bg)`/`var(--amber)` for eval_quality warnings and `#EFF6FF`/`#1D4ED8` for knowledge_depth
- **Fix:** scoreColor → --gold; badgeColors.eval_quality → --gold-bg/--gold; badgeColors.knowledge_depth → --lilac-dim/--lilac
- **Files modified:** apps/admin/app/agents/[id]/deploy/page.tsx
- **Committed in:** 30a06f1 (Task 2 commit)

**3. [Rule 1 - Bug] Old Parchment rgba values in deploy banners/borders**
- **Found during:** Task 2 (deploy page edits)
- **Issue:** Status banners used `rgba(185,28,28,0.3)` (Parchment red value, invisible on dark), `rgba(22,163,74,0.3)` (old green), `var(--green-solid)` (removed token), `#EA580C` (hardcoded orange)
- **Fix:** `rgba(185,28,28,0.3)` → `rgba(248,113,113,0.25)` (--red value); old green border → `rgba(52,211,153,0.3)`; `var(--green-solid)` left-border → `var(--green)`; `#EA580C` → `var(--gold)`
- **Files modified:** apps/admin/app/agents/[id]/deploy/page.tsx
- **Committed in:** 30a06f1 (Task 2 commit)

---

**Total deviations:** 3 auto-fixed (2 missing critical tokens, 1 bug — all old Parchment values that would break dark theme rendering)
**Impact on plan:** All auto-fixes required for correct dark theme rendering. No scope creep.

## Issues Encountered

- `pnpm run lint` pre-existing failure (no ESLint config in admin — same inherited issue from 11-01 through 11-04). Build passes cleanly. Documented in STATE.md.

## Known Stubs

- `settings/page.tsx` Save button is disabled (`disabled` prop) — settings API not yet wired. The form panel dark pattern is fully applied; the stub content (disabled input + disabled save) is intentional until a settings API plan is created. Does not block the eval/deploy/settings visual goals for this plan.

## User Setup Required

None - no external service configuration required. UI-only changes.

## Next Phase Readiness

- Wave 5 complete: eval/deploy/settings all use Hillbrow at Dusk dark tokens
- Wave 6 (11-06): auth pages (sign-in/sign-up) + QA pass
- All hardcoded Parchment hex eliminated from eval/deploy pages; settings was already clean

## Self-Check: PASSED

Files verified:
- `apps/admin/app/agents/[id]/eval/page.tsx` — exists, glass-bg present, FBBF24/F4748C/34D399/B79AE0 strokes present, --gold in scoreColor, no old Parchment hex
- `apps/admin/app/agents/[id]/deploy/page.tsx` — exists, 140E2A/F4748C/0B0717/1E1638 in DEFAULT_CONFIG, no glass-bg, --surface-2 + font-mono in embed pre
- `apps/admin/app/agents/[id]/settings/page.tsx` — exists, --surface-1 panel, --surface-2 input

Commits verified:
- d59fe8c — Task 1 eval changes
- 30a06f1 — Task 2 deploy + settings changes

Backend-preservation verified:
- eval page: 12 data-fetching references (unchanged)
- deploy page: 16 data-fetching references (unchanged)

---
*Phase: 11-ui-overhaul*
*Completed: 2026-05-26*
