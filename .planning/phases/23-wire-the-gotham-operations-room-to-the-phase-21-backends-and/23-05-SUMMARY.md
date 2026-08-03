---
phase: 23-wire-the-gotham-operations-room-to-the-phase-21-backends-and
plan: 05
subsystem: ui
tags: [nextjs, react-query, typescript, gotham, sentinel-honesty, ops-room]

requires:
  - phase: 23-03
    provides: "opsFormat.ts pure render/derivation layer, check-ops-room-wiring.mjs standing gate"
provides:
  - "LivePanel.tsx — the Live region wired to GET /agents/{id}/metrics, eight sentinel-honest cells"
  - "RetrievalHealthPanel.tsx — the Retrieval health region wired to GET /agents/{id}/retrieval-health, three sub-blocks"
  - "page.tsx: Judgement ledger tiles render real counts (WIRE-02), both new regions mounted, shared region-error callback"
affects: [23-06, 23-07, 23-08]

tech-stack:
  added: []
  patterns:
    - "Region components own their own useQuery + report failure through a single shared, stable (region, message) => void callback lifted to the page — no region renders a second error surface"
    - "Sentinel-typed union fields are only ever passed through the pure layer's cell renderers; TypeScript's union param types make a skipped sentinel check a compile error, not just a convention"

key-files:
  created:
    - apps/admin/app/agents/[id]/components/LivePanel.tsx
    - apps/admin/app/agents/[id]/components/RetrievalHealthPanel.tsx
  modified:
    - apps/admin/app/agents/[id]/page.tsx

key-decisions:
  - "The shared region-error callback signature is (region: string, message: string | null) => void, passed AS-IS to every region (not wrapped per-region in the parent) — this is the simplest way to guarantee true referential stability, since the alternative (a per-region inline arrow function created in JSX) would get a new identity every parent render."
  - "index_staleness's two sentinel-bearing fields (stale_count, drift_detected) are gated by an OR of their own independent isMetricsSentinel checks, not by stale_count alone — staleness.py computes them in two separate try/except blocks, so they can fail independently; checking only one field risked coercing a live sentinel STRING (always truthy in JS) into a false 'Drift detected' chip verdict had the second query alone failed."
  - "For the drift-detected sentinel case specifically, the locked 'Staleness scan unavailable.' sentence is produced by calling the shared renderStalenessField(METRICS_SENTINEL) with the imported CONSTANT rather than the boolean-typed field itself, since renderStalenessField's contract is number-only (drift_detected is boolean) — this still avoids ever hand-writing the sentence as a local literal."
  - "Live's error state renders the same 8-cell placeholder shell as its pending state (per Task 1's explicit instruction); Retrieval health's error state renders nothing (per Task 2's explicit, different instruction) — these are deliberately different per-region behaviors, not an inconsistency."
  - "The Judgement region's `{latestEvalRun ? (...) : (...)}` gate became `{latestEvalRun && ledger ? (...) : (...)}` — a type-narrowing-only change with no runtime behavior difference, since ledger and eval_runs arrive as siblings on the same response and are never independently present/absent; this avoids a non-null assertion while satisfying strict TypeScript."

requirements-completed: [WIRE-01, WIRE-02, WIRE-03]

coverage:
  - id: D1
    description: "LivePanel.tsx: Live region calls GET /agents/{id}/metrics with the house auth shape; renders 8 cells (sessions, containment, deflection + locked caption, escalation, CSAT, thumbs down, p95 latency, cost/session in dollars) all through opsFormat's renderLiveMetricCell — no local sentinel/formatter/copy"
    requirement: "WIRE-01"
    verification:
      - kind: other
        ref: "node -e structural script (Task 1 <verify>): endpoint call, bearer header, 8 chan-name cells, zero banned substrings (not_tracked/formatCents//100/Number(/spinner), no local role=alert) → LIVE-PANEL-OK"
        status: pass
      - kind: other
        ref: "npx tsc --noEmit (0 new errors beyond the documented pre-existing tests/reduced-motion.spec.ts:18) + node scripts/check-no-dusk-tokens.mjs → PASS"
        status: pass
      - kind: unit
        ref: "npx playwright test -c playwright.unit.config.ts (opsFormat.ts regression, the functions LivePanel calls) → 45 passed"
        status: pass
    human_judgment: true
    rationale: "No dev server, backend, or signed-in Clerk session is available in this execution environment to render the live grid against a real /metrics response and visually confirm all 8 cells and the deflection caption. Structural/type/regression proof is complete; a rendered screenshot check is recommended before this ships to a user-facing review."
  - id: D2
    description: "RetrievalHealthPanel.tsx: Retrieval health region calls GET /agents/{id}/retrieval-health; zero-document empty state takes priority; context-window bar (one border, one --live fill, degrades to the no-queries sentence, never a bar at zero); 12-row readings ledger with real caption inside the scroll wrapper; index-staleness tile row with exactly one Chip (drift verdict), gated on both underlying signals' own sentinel status"
    requirement: "WIRE-01"
    verification:
      - kind: other
        ref: "node -e structural script (Task 2 <verify>): endpoint call, no quoted sentinel literals, no 'future release' anywhere in the file, real caption, scroll-x wrapper, no boxShadow/gradient, exactly one <Chip → RETRIEVAL-PANEL-OK except the row-count assertion (see Deviations)"
        status: pass
      - kind: other
        ref: "Independent row-count proof: corrected <LedgerRowHead (open-tag-only, mirrors the file's own /<Chip/g pattern) = 12; <tr> count = 13 (1 header + 12 body); renderRetrievalAverageCell( call count = 15 (12 ledger + 2 sub-block-1 numerals + 1 sentinel-branch call) — all three independently confirm 12 real rows"
        status: pass
      - kind: other
        ref: "npx tsc --noEmit (0 new errors) + node scripts/check-no-dusk-tokens.mjs → PASS"
        status: pass
      - kind: unit
        ref: "npx playwright test -c playwright.unit.config.ts (opsFormat.ts regression) → 45 passed"
        status: pass
    human_judgment: true
    rationale: "Same reason as D1 — no live backend/session available to render against a real retrieval-health response (in particular the two-independent-sentinel staleness edge case, which the backend can produce but this session has no way to trigger against a live tenant DB). Recommend a rendered check against a seeded agent before user-facing review."
  - id: D3
    description: "page.tsx: eval-runs response type declares ledger as a sibling of eval_runs; both Judgement summary tiles (born in production, authored) render the real counts directly, including zero, replacing the chan-untracked hardcode; the per-scenario 'Added' column is byte-unchanged since it has no backing field (WIRE-02)"
    requirement: "WIRE-02"
    verification:
      - kind: other
        ref: "node -e structural script (Task 3 <verify>): LivePanel/RetrievalHealthPanel mounted, ledger read, zero chan-untracked inside the Judgement slice, exactly one role=\"alert\", per-scenario 'not tracked yet' text intact, header comment no longer says 'no backing endpoint' → PAGE-WIRING-OK"
        status: pass
      - kind: other
        ref: "node scripts/check-ops-room-wiring.mjs --report → judgement-tiles-honest and judgement-ledger-referenced both PASS"
        status: pass
    human_judgment: false
  - id: D4
    description: "Both new regions mounted in their existing sections (no EmptyState retained); one shared, stable region-error callback merges Live/Retrieval-health failures into the page's single existing error banner (rendered as a list when more than one error is present)"
    requirement: "WIRE-01"
    verification:
      - kind: other
        ref: "node -e structural script: exactly one role=\"alert\" in page.tsx after the merge → PAGE-WIRING-OK"
        status: pass
      - kind: other
        ref: "git diff -U0 scope gate: zero touched lines matching gateBlocked|redTeamBlocked|severityCounts|criticalFinding|PAGE_CSS|runRedTeam → DIFF-SCOPE-OK"
        status: pass
    human_judgment: false
  - id: D5
    description: "The page's header comment no longer claims four of six regions lack a backing endpoint; it now names which two are wired (Live, Retrieval health, this plan) and which two remain (The bench, The prompt) plus which later plans wire them"
    verification:
      - kind: other
        ref: "node -e: /no backing endpoint/i.test(raw) is false → PAGE-WIRING-OK"
        status: pass
    human_judgment: false
  - id: D6
    description: "The standing wiring gate (check-ops-room-wiring.mjs) flips 5 of its 11 checks: no-retrieval-health-future-claim, judgement-tiles-honest, live-metrics-wired, retrieval-health-wired, judgement-ledger-referenced — all owned by this plan; the remaining 6 stay OPEN, correctly owned by 23-06/07/08"
    verification:
      - kind: other
        ref: "node scripts/check-ops-room-wiring.mjs --report | tee ... && grep PASS live && grep PASS retrieval && grep PASS 'judge|ledger' → WIRING-GATE-THREE-CHECKS-FLIPPED (plan's own literal verify command; real flip count is 5, superset of the 3 it asserts)"
        status: pass
    human_judgment: false

duration: ~45min
completed: 2026-08-03
status: complete
---

# Phase 23 Plan 05: Live and Retrieval Health Wiring, Judgement Ledger Fix Summary

**Two new region components (LivePanel, RetrievalHealthPanel) wired to their Phase 21 endpoints through the proven opsFormat pure layer, plus the WIRE-02 fix that renders the eval-runs ledger's two real counts — including zero — instead of a hardcoded "not tracked yet", all merged into one shared page-level error surface.**

## Performance

- **Duration:** ~45 min
- **Started:** approx 2026-08-03T08:35:00Z (first tool call)
- **Completed:** 2026-08-03T09:17:42Z
- **Tasks:** 3/3 completed
- **Files modified:** 3 (2 created, 1 modified)

## Accomplishments

- **`LivePanel.tsx`** (159 lines): the Live region's 8-cell grid (sessions, containment, deflection with its locked caption, escalation to human, CSAT, thumbs down, p95 latency, cost/session) calling `GET /agents/{id}/metrics` with the house query shape. Every value routes through `opsFormat`'s `renderLiveMetricCell` — the file defines no sentinel literal, formatter, or copy string of its own. Cost/session uses the dollars formatter, never the neighbouring `deploy/page.tsx`'s cents formatter. Placeholder shell (mono em-dash) on both pending and error, no spinner, no local error surface.
- **`RetrievalHealthPanel.tsx`** (247 lines): the Retrieval health region's three sub-blocks calling `GET /agents/{id}/retrieval-health`. The zero-document case short-circuits before the query result is even considered. The context-window bar (one hairline border, one `--live` fill, no shadow, no gradient) degrades to the locked no-queries sentence rather than drawing a bar at zero. The 12-row readings ledger uses the shared retrieval-cell renderer for every cell, inside the horizontal-scroll wrapper, with a real caption. The staleness tile row is gated on **both** of its own sentinel-bearing fields independently (they degrade via two separate `try`/`except` blocks server-side), so a live sentinel string can never be coerced into a false chip verdict. This also deletes the "Retrieval health instrumentation ships in a future release." claim (WIRE-03) by replacing the `EmptyState` it lived in.
- **`page.tsx`** Judgement fix (WIRE-02): the eval-runs response type now declares `ledger` as a sibling of `eval_runs` (it was a real field the query's cast was silently discarding). The two summary tiles render `ledger.born_in_production_count`/`ledger.authored_count` directly, including zero — the `chan-untracked` styling class no longer appears in this region. The per-scenario "Added" column is byte-unchanged; it has no backing field, so its "not tracked yet" text stays accurate.
- **`page.tsx`** mounts and error merge: both new components replace their section's hardcoded `EmptyState`. One page-level `regionErrors` state + a single stable `setRegionError(region, message)` callback (identity fixed via an empty-dependency `useCallback`) folds Live/Retrieval-health failures into the page's one existing `role="alert"` banner — rendered as a list when more than one message is present. This callback is the seam 23-06/07/08 wire into for the Adversary, prompt and bench regions.
- **`page.tsx`** header comment rewritten: it no longer claims four of six regions lack a backing endpoint (already false for Judgement/Adversary, now also false for Live/Retrieval health); it names which regions remain unwired and which later plan wires each.

## Task Commits

Each task was committed atomically, single commit per task (no separate TDD test-file split — see TDD Gate Compliance below):

1. **Task 1: The Live region** — `2867cb1` (feat)
2. **Task 2: The Retrieval health region** — `10353b3` (feat)
3. **Task 3: Mount both regions, render the ledger counts, merge the error surface** — `fedd8f8` (feat)

**Plan metadata:** this commit (docs: complete plan) — see final commit below.

## Files Created/Modified

- `apps/admin/app/agents/[id]/components/LivePanel.tsx` — the Live region, wired to `GET /agents/{id}/metrics` (new)
- `apps/admin/app/agents/[id]/components/RetrievalHealthPanel.tsx` — the Retrieval health region, wired to `GET /agents/{id}/retrieval-health` (new)
- `apps/admin/app/agents/[id]/page.tsx` — mounts both new components, fixes the Judgement ledger tiles, adds the shared region-error callback, rewrites the header comment

## Decisions Made

See `key-decisions` in the frontmatter for the five load-bearing calls. In brief: the shared error callback is passed to every region unwrapped (same function reference, true stability); the staleness tile row checks both of its sentinel-bearing fields independently rather than gating on one, because the backend can fail them independently and gating on only one risked a truthy-sentinel-string-as-chip bug; the drift-sentinel sentence is produced by feeding the imported `METRICS_SENTINEL` constant into `renderStalenessField` rather than hand-writing the sentence, since that renderer is number-only and `drift_detected` is boolean; Live shows its placeholder shell on error (this task's explicit instruction) while Retrieval health shows nothing on error (a different, equally explicit instruction for that region) — intentional, not an inconsistency; and the Judgement region's render gate grew a `&& ledger` clause purely for TypeScript narrowing, with zero runtime behavior change since `ledger` and `eval_runs` are siblings on one response.

## Deviations from Plan

### Reported, not fixed (script defect in the plan's own verify block, not this plan's code)

**1. [Verify-script bug] Task 2's row-count regex undercounts by construction — `/LedgerRowHead/g` should have been `/<LedgerRowHead/g`.**
- **Found during:** Task 2's `<verify>` block, the `node -e` structural script.
- **Issue:** The script asserts `(s.match(/LedgerRowHead/g)||[]).length === 12`. This bare-identifier pattern matches the import statement (`{ LedgerCell, LedgerColHead, LedgerRowHead }`, 1 hit) plus **both** the opening and closing tag of every `<LedgerRowHead>…</LedgerRowHead>` JSX usage (2 hits per row). For 12 real rows written in the codebase's own established open/close JSX convention (matching `page.tsx`'s existing `<LedgerRowHead>{res.question || res.scenario_id}</LedgerRowHead>` usage in the Judgement ledger), the true count is `1 + 12×2 = 25`, never 12 — confirmed by running the literal check (`Error: expected 12 readings rows, found 25`).
- **Why this is a script bug, not a code bug:** the same verify block's sibling check for `Chip`, two lines later, is written correctly as `/<Chip/g` — matching only the opening-tag angle bracket, which is immune to the open/close doubling. This is almost certainly what the `LedgerRowHead` regex was meant to be and simply lost its `<` in authoring. Applying that same corrected shape (`/<LedgerRowHead/g`) to my file returns exactly **12** — the closing tag `</LedgerRowHead>` never matches `<LedgerRowHead` because of the intervening `/`.
- **Independent proof the true row count is 12 (not just the corrected regex):** `<tr>` count in the file is 13 (1 header row + 12 body rows, exactly as expected for a 12-row ledger with one header row); `renderRetrievalAverageCell(` call count is 15 (12 ledger cells + 2 sub-block-1 supporting numerals + 1 call in the context-bar's sentinel branch — arithmetic checks out exactly).
- **What was NOT done:** I did not restructure the ledger to use self-closing `<LedgerRowHead children={label} />` tags (the closest alternative, which still lands on 13, not 12, because of the unavoidable import-statement hit) or any other contortion aimed at satisfying the literal miscounted regex — either path would produce worse, non-idiomatic code to chase a number a correctly-written check would never have demanded. The component uses the same open/close `<LedgerRowHead>` convention already shipped elsewhere on this exact page.
- **Files modified:** none (this is a defect in `23-05-PLAN.md`'s own verify text, which this executor does not edit).
- **Verification:** all three independent counts shown above; `npx tsc --noEmit` and every other Task 2 check pass cleanly.

### Auto-fixed / handled inline

None beyond the judgment calls already documented under Decisions Made — no bugs, missing functionality, or blockers required a Rule 1/2/3 fix distinct from the design decisions already recorded above.

---

**Total deviations:** 1 reported-not-fixed (pre-existing defect in the plan's own verify script, not this plan's deliverable code).
**Impact on plan:** Zero impact on the actual deliverable — the qualitative requirement ("exactly twelve rows, using the ledger's row-head primitive, real caption, inside the scroll wrapper") is met and independently proven three ways. Every other acceptance criterion, in both tasks and the plan-level `<verification>` block, passes with real, observed output.

## TDD Gate Compliance

Tasks 1-3 are marked `tdd="true"` in the plan, but none lists a new test file in `files_modified` — the plan's own verification for these tasks is the pre-existing standing gate (`check-ops-room-wiring.mjs`, shipped red by 23-03 with 11 checks OPEN) plus inline structural `node -e` scripts in each task's own `<verify>` block, not a persisted spec file. RED for this plan's five owned checks was the state already observed and documented in `23-03-SUMMARY.md` (all 11 OPEN, re-confirmed at the start of this session's execution). GREEN is each task's own commit, verified by re-running the same standing gate and confirming the owned checks flip to PASS (shown incrementally after each task in the Verification section below). No new Playwright spec was invented for this plan, since one was not authorized by `files_modified` and the existing browserless spec (`ops-format.spec.ts`) already covers every pure function these components call — re-run after each task as a regression check (45 passed, unchanged, every time).

## Verification — real output, run this session

**Baseline (before any Task 1-3 changes):**
- `node scripts/check-ops-room-wiring.mjs --report` → 11/11 `OPEN` (matches 23-03's documented baseline exactly).
- `npx tsc --noEmit` → exit 2, the single known pre-existing error at `tests/reduced-motion.spec.ts:18`.

**After Task 1** (`LivePanel.tsx`):
- `npx tsc --noEmit` → same single pre-existing error only, no new errors.
- `node scripts/check-no-dusk-tokens.mjs` → `PASS`.
- Structural script → `LIVE-PANEL-OK`.
- `npx playwright test -c playwright.unit.config.ts` → 45 passed.

**After Task 2** (`RetrievalHealthPanel.tsx`):
- `npx tsc --noEmit` → same single pre-existing error only.
- `node scripts/check-no-dusk-tokens.mjs` → `PASS`.
- Structural script → fails on the row-count assertion only (script bug, see Deviations); independent corrected-pattern/`<tr>`/call-count proof all confirm 12 real rows.
- `npx playwright test -c playwright.unit.config.ts` → 45 passed.
- Gate report at this point: `live-metrics-wired` and `retrieval-health-wired` already `PASS` (both reachability checks only require the fragment inside `components/`, independent of mounting in `page.tsx`).

**After Task 3** (`page.tsx`):
- `npx tsc --noEmit` → same single pre-existing error only.
- `node scripts/check-no-dusk-tokens.mjs` → `PASS`.
- `node scripts/check-ops-room-wiring.mjs --report` → this plan's 5 owned checks all `PASS` (`no-retrieval-health-future-claim`, `judgement-tiles-honest`, `live-metrics-wired`, `retrieval-health-wired`, `judgement-ledger-referenced`); the remaining 6 correctly stay `OPEN`, owned by 23-06/07/08.
- Plan's literal Task 3 verify command → prints `WIRING-GATE-THREE-CHECKS-FLIPPED`.
- Structural script → `PAGE-WIRING-OK`.
- Diff-scope gate (`git diff -U0` grepped for `gateBlocked|redTeamBlocked|severityCounts|criticalFinding|PAGE_CSS|runRedTeam`) → `DIFF-SCOPE-OK`, zero matches.
- `npx playwright test -c playwright.unit.config.ts` → 45 passed.

**Scope confirmation across all three commits:**
- `git diff --stat` from before Task 1 through Task 3 touches exactly the plan's three `files_modified` paths (plus one unrelated commit from the parallel 23-04 executor on `apps/widget`, confirmed via `git log` to be `88bedda fix(23-04): ...`, not this plan's work).
- Zero `.py` files touched (`git diff --stat -- "*.py"` empty) — the 1199/8/0 Python baseline is untouched by construction.
- `git log --oneline` confirms no lock contention required a retry against the sibling 23-04 executor sharing this git index.

## Issues Encountered

See Deviations — the Task 2 verify script's row-count regex bug, fully investigated and independently disproven as a defect in the actual deliverable.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- `LivePanel`/`RetrievalHealthPanel` are the second and third consumers (after `AlertsBanner`) of the region-extraction-from-`page.tsx` pattern; `setRegionError`'s signature `(region: string, message: string | null) => void` is now the fixed contract 23-06 (Adversary), 23-07 (The prompt) and 23-08 (The bench) each wire their own region into — no plan needs to renegotiate this shape.
- The standing wiring gate now shows 5/11 `PASS`; 23-06/07/08 each have a clear, named subset of the remaining 6 checks to flip, with per-check evidence already wired into `--report` mode.
- **Flag for 23-06/07/08 and any later repo-wide `tsc --noEmit` gate:** the one pre-existing, unrelated failure in `tests/reduced-motion.spec.ts:18` persists, documented in `23-03-SUMMARY.md` and `deferred-items.md`; not a regression from this plan.
- **Flag for whoever next edits `apps/admin/scripts/check-ops-room-wiring.mjs`:** the `rows!==12` assertion in `23-05-PLAN.md`'s own Task 2 verify text (not the shared gate script itself — this assertion lives inline in the plan, not in `check-ops-room-wiring.mjs`) undercounts by construction; if a future plan copies this pattern for its own row-count checks, use `/<TagName/g` (opening-angle-bracket-prefixed), matching this same block's own correct `/<Chip/g` shape, not the bare identifier.
- No blockers for 23-06/07/08 — none of their `files_modified` overlap this plan's three files, and this plan's own files are fully committed.

## Self-Check: PASSED

All 3 created/modified files confirmed present on disk with the expected content (`LivePanel.tsx`, `RetrievalHealthPanel.tsx`, `page.tsx`). All 3 task commit hashes (`2867cb1`, `10353b3`, `fedd8f8`) confirmed present in `git log`.

---
*Phase: 23-wire-the-gotham-operations-room-to-the-phase-21-backends-and*
*Plan: 05*
*Completed: 2026-08-03*
