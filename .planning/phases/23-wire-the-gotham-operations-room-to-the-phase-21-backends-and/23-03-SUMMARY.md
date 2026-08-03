---
phase: 23-wire-the-gotham-operations-room-to-the-phase-21-backends-and
plan: 03
subsystem: testing
tags: [playwright, typescript, static-analysis, pure-functions, gotham]

requires: []
provides:
  - "opsFormat.ts — the pure render/derivation layer every region component in 23-05..23-08 imports"
  - "a browserless Playwright runner (playwright.unit.config.ts) and its proof spec (ops-format.spec.ts)"
  - "check-ops-room-wiring.mjs — the standing reachability-and-honesty gate, red by design until 23-08 lands"
affects: [23-05, 23-06, 23-07, 23-08]

tech-stack:
  added: []
  patterns:
    - "Pure-function render layer proven browserless, imported (not re-implemented) by every later region component"
    - "Static repository gate modelled line-for-line on an existing shipped gate (check-no-dusk-tokens.mjs)"
    - "Report-mode (--report) on a static gate so parallel/sequential later plans can assert only their own region flipped"

key-files:
  created:
    - apps/admin/app/agents/[id]/components/opsFormat.ts
    - apps/admin/tests-unit/ops-format.spec.ts
    - apps/admin/playwright.unit.config.ts
    - apps/admin/scripts/check-ops-room-wiring.mjs
    - .planning/phases/23-wire-the-gotham-operations-room-to-the-phase-21-backends-and/deferred-items.md
  modified:
    - apps/admin/package.json

key-decisions:
  - "OpenFinding's type mirrors the REAL shipped shape of redteam_programme_service.py::read_programme (verified against source, which had landed via 23-02 by the time this task ran), not only the UI-SPEC's abbreviated example — it carries run_id/strategy_id/created_at in addition to the UI-SPEC's seven listed fields."
  - "renderStalenessField is number-only, matching the other two cell renderers' shape exactly, rather than a generic <T> function — a TypeScript generic's declaration syntax (`<T>`) trips Task 1's own JSX-detection regex (/<[A-Z]/); drift_detected's boolean-to-chip mapping is left to the future call site via the exported isMetricsSentinel predicate directly, since a Chip verdict is not a string this module's cell renderers return."
  - "The Judgement honesty check (judgement-tiles-honest) uses the plan's own documented escape hatch: an exact-occurrence count of `className=\"chan-untracked\"` (currently 2, both illegitimate) rather than locating the Judgement <section>'s markup boundaries, since that CSS class has exactly one legitimate consumer today and no other region uses it."
  - "Reachability checks scan only page.tsx + its own components/ directory, not the full apps/admin/app/agents/[id]/ tree — deploy/, soul/, ingest/, eval/, and settings/ are separate pages under the same [id] route param and are not \"the operations room\"; honesty checks scan the whole app/ directory instead, since a false capability claim would be equally dishonest anywhere in the console."

requirements-completed: [WIRE-01, WIRE-02, WIRE-03, WIRE-04]

coverage:
  - id: D1
    description: "opsFormat.ts: 20 exported pure functions/consts (2 sentinel constants, 2 independently-named predicates, 6 formatters, 3 cell renderers, 4 gate derivations, 2 verdict mappings, the canary renderer) — every decision about how a backend value looks on screen, provable without a browser, server, or session"
    requirement: "WIRE-01"
    verification:
      - kind: unit
        ref: "apps/admin/tests-unit/ops-format.spec.ts (45 assertions, all exports named)"
        status: pass
    human_judgment: false
  - id: D2
    description: "Browserless Playwright runner (playwright.unit.config.ts) outside the shipped e2e config's test directory, proven to run with no browser/server in ~6s"
    verification:
      - kind: unit
        ref: "npx playwright test -c playwright.unit.config.ts --reporter=list → 45 passed"
        status: pass
      - kind: other
        ref: "diff-scope gate: playwright.config.ts byte-unchanged, tests/ byte-unchanged, testDir does not reach tests-unit"
        status: pass
    human_judgment: false
  - id: D3
    description: "check-ops-room-wiring.mjs: 5 honesty checks (3 WIRE-03 false claims + shared phrase + WIRE-02 Judgement tiles) and 6 reachability checks (one per region, WIRE-01/WIRE-04) — red against the current tree, report mode shows per-check status with which plan flips it"
    requirement: "WIRE-03"
    verification:
      - kind: other
        ref: "node scripts/check-ops-room-wiring.mjs (exit 1, all 11 checks OPEN with real file:line evidence matching v1.2-MILESTONE-AUDIT.md's own findings)"
        status: pass
      - kind: other
        ref: "node scripts/check-ops-room-wiring.mjs --report (11 PASS/OPEN lines, stable ids)"
        status: pass
    human_judgment: false
  - id: D4
    description: "Comment-stripping pass demonstrated in both directions: a false-claim literal inserted outside a comment is named by the gate; the identical literal inserted inside a comment produces no finding"
    requirement: "WIRE-03"
    verification:
      - kind: other
        ref: "manual guard-removal demonstration, both directions, restored from HEAD before judging — real output captured in this SUMMARY's Deviations/Verification section"
        status: pass
    human_judgment: false
  - id: D5
    description: "npx tsc --noEmit clean across apps/admin"
    verification:
      - kind: other
        ref: "npx tsc --noEmit"
        status: fail
    human_judgment: true
    rationale: "Fails on a pre-existing, unrelated TypeScript error in apps/admin/tests/reduced-motion.spec.ts:18 that this plan is contractually forbidden to fix (tests/ and playwright.config.ts must stay byte-unchanged) and definitively did not cause — confirmed by removing this plan's three new files entirely and re-running tsc, which reproduced the identical error. See Deviations section and deferred-items.md."

duration: 55min
completed: 2026-08-03
status: complete
---

# Phase 23 Plan 03: Validation Layer — opsFormat.ts and the Static Wiring Gate Summary

**A 20-function pure render/derivation layer proven by 45 browserless assertions, plus a static repository gate (modelled on the shipped dusk-token gate) that scans for the three WIRE-03 false claims and six Phase-21 route fragments and is currently red against all 11 checks — exactly the coverage the v1.2 milestone audit found nowhere in the repository.**

## Performance

- **Duration:** ~55 min
- **Started:** 2026-08-03T10:10:00Z (approx, first tool call)
- **Completed:** 2026-08-03T11:05:00Z (approx)
- **Tasks:** 2/2 completed
- **Files modified:** 5 (4 created, 1 modified) + 1 out-of-scope-log file

## Accomplishments

- **`opsFormat.ts`** (362 lines): every decision about how a Phase-21 backend value renders is now a named, pure, exported function — two independently-named sentinel predicates that each reference only their own constant (so neither can be satisfied by the other spelling), six formatters that only accept `number` (forcing a sentinel check before any formatting can happen), three cell renderers producing the three UI-SPEC §5 locked sentences character for character, four gate derivations over `OpenFinding[]` (the pure half of WIRE-04's stale-verdict fix, provable before any component exists), two verdict mappings returning Chip's imported closed union, and the canary-percent renderer.
- **`ops-format.spec.ts`** (391 lines, 45 assertions): every export named at least once; the four load-bearing assertion classes the plan called out by name are all present — a zero renders as a formatted zero for every cell renderer (WIRE-02's defect class), each sentinel predicate rejects the other spelling (Pitfall 3), every sentinel sentence is compared against a literal copied from the UI-SPEC rather than the module's own constant, and the gate derivation flips from true to false when a list's only critical finding is removed (the entire WIRE-04 fix, in four lines).
- **`playwright.unit.config.ts`**: a genuinely separate, browserless runner — no `webServer`, no `projects`, no `baseURL` — that runs the whole spec in under 7 seconds with zero browser or server launched.
- **`check-ops-room-wiring.mjs`** (357 lines): modelled line-for-line on the shipped `check-no-dusk-tokens.mjs`. 11 named checks (5 honesty, 6 reachability), a `--report` mode naming which later plan (23-05 through 23-08) flips each one, and — observed, not assumed — currently exits 1 against the real tree with evidence matching `v1.2-MILESTONE-AUDIT.md`'s own findings exactly (the three false-claim lines, and zero references to any of the six Phase 21 route fragments under the operations room).
- **Both required guard-removal demonstrations run and observed** (not merely described): a false-claim literal inserted into a scratch file outside a comment produced a new, named finding; the identical literal inserted inside a `//` comment in the same file produced no finding at all — proving the comment-stripping pass works rather than being assumed. Both mutations were restored from HEAD (the scratch file deleted) before recording either result.

## Task Commits

Each task was committed atomically, split into TDD RED/GREEN per the executor's TDD protocol:

1. **Task 1 RED: failing spec + browserless config** — `52d3e27` (test) — real observed failure: `Cannot find module '../app/agents/[id]/components/opsFormat'`
2. **Task 1 GREEN: opsFormat.ts implementation + package.json script** — `0a7f771` (feat) — 45/45 assertions pass
3. **Task 2: static wiring gate + package.json script** — `4a09274` (feat) — gate red against current tree (11/11 OPEN), report mode confirmed, both demonstrations observed

**Plan metadata:** this commit (docs: complete plan) — see final commit below.

## Files Created/Modified

- `apps/admin/app/agents/[id]/components/opsFormat.ts` — the pure render/derivation layer (new)
- `apps/admin/tests-unit/ops-format.spec.ts` — its browserless proof, 45 assertions (new)
- `apps/admin/playwright.unit.config.ts` — the browserless runner config (new)
- `apps/admin/scripts/check-ops-room-wiring.mjs` — the static reachability-and-honesty gate (new)
- `apps/admin/package.json` — gains two scripts, `test:unit` and `check:ops-room-wiring`; dependency/devDependency blocks byte-unchanged
- `.planning/phases/23-.../deferred-items.md` — logs the pre-existing, out-of-scope `tsc` blocker (new)

## Decisions Made

- **`OpenFinding`'s shape mirrors the real, shipped `redteam_programme_service.py::read_programme` response**, not merely `23-UI-SPEC.md §3.2`'s abbreviated example. By the time this task ran, `23-02` had already landed (commit `432888b`), so the real shape — `id, run_id, strategy_id, severity, attack_vector, probe_message, agent_response, turn_count, created_at, description` — was verified source, not a forward-looking guess. This is a superset of the UI-SPEC's seven-field illustration; nothing about it contradicts the UI-SPEC, it is simply more complete.
- **`renderStalenessField` stays number-only** rather than becoming a generic `<T>` function that could also serve `drift_detected` (a boolean feeding a Chip verdict, not a formatted string). A TypeScript generic parameter's own declaration syntax (`renderStalenessField<T>(`) contains the substring `<T`, which trips Task 1's own structural verify command (`if(/<[A-Z]/.test(m)) throw new Error('opsFormat contains JSX')` — a crude-by-design heuristic that cannot distinguish a generic type parameter from JSX). Rather than fight the gate meant to keep this module honest, `renderStalenessField` stayed parallel in shape to the other two cell renderers (`(value, format) => string`), and `drift_detected`'s sentinel check is documented to reuse the exported `isMetricsSentinel` predicate directly at its future call site.
- **The Judgement honesty check (`judgement-tiles-honest`) uses the plan's own documented escape hatch** rather than locating the Judgement `<section>`'s markup boundaries: it asserts an exact count of `className="chan-untracked"` usages (must be zero) rather than scoping a search to the region's own block. This is safe because that CSS class has exactly one legitimate consumer in the whole codebase today (the two WIRE-02 tiles) and no other region emits it — confirmed by reading every region's current markup this session, all four unwired regions render a bare `<EmptyState>` with no `chan-untracked` usage anywhere.
- **Reachability checks scan only `page.tsx` + its own `components/` subdirectory**, not the whole `apps/admin/app/agents/[id]/` tree. `deploy/`, `soul/`, `ingest/`, `eval/`, and `settings/` are separate pages sharing the same `[id]` route parameter but are not "the operations room" WIRE-01 is about; scanning them would risk a false PASS if an unrelated sibling page happened to mention a route fragment for an unrelated reason. Honesty checks, by contrast, scan the whole `app/` directory, since a false capability claim (or the evasion phrase behind it) would be equally dishonest anywhere in the console, not only inside the operations room.
- **The "phrase all three claims share" is `"in a future release"`, not `"ships in a future release"`.** Verified character-for-character against all three literals read directly from `page.tsx`: claim 3 ("Version history, canary releases and rollback ship in a future release.") uses the singular verb "ship", not "ships", so a check for "ships in a future release" would miss it. "in a future release" is the true common substring across all three, confirmed present in each verbatim.

## Deviations from Plan

### Auto-fixed / handled inline

**1. [Minor — citation accuracy, no code impact] The plan's read_first cites `metrics.py:59` for the dollars-vs-cents comment; the actual source is `metrics_service.py:31`.**
- **Found during:** Task 1, drafting `formatDollars`'s comment.
- **Issue:** Task 1's `<read_first>` says "the per-session cost formatter... do not reuse it for the per-session cost: `metrics.py:59` documents that field as dollars." Grepped both `metrics.py` and `metrics_service.py` for "dollars"/"DOLLARS"/"cost_usd" this session: `metrics.py` line 59 is `window_days: int = Query(7, ge=1, le=90),` — it says nothing about dollars anywhere in the file. `metrics_service.py:31` (`cost_per_session = SUM(turn_metrics.cost_usd) / COUNT(DISTINCT conversation_id)`) is the actual evidence — the `cost_usd` column name (this codebase's convention: `_usd` for dollars, `_cents` for cents, e.g. `max_amount_cents` elsewhere) is what establishes the field is dollars, not cents.
- **Fix:** `formatDollars`'s comment in `opsFormat.ts` cites `metrics_service.py:31`'s `cost_usd` column and the `_usd`/`_cents` naming convention directly, not the plan's stated `metrics.py:59`. Per the instruction "if the plan's instructions disagree with what the source actually says, the SOURCE wins" — the plan's *substance* (cost_per_session is dollars, not cents) is correct and unchanged; only the pinpoint line citation was wrong, and the comment now points at the real evidence.
- **Files modified:** `apps/admin/app/agents/[id]/components/opsFormat.ts` (comment only, no behavior change).
- **Verification:** `grep -n "dollars|cost_usd" apps/api/app/api/v1/metrics.py apps/api/app/services/metrics_service.py` — zero hits in `metrics.py`, one hit (`cost_usd`) in `metrics_service.py:31`.

### Reported, not fixed (out of scope)

**2. [Pre-existing, out-of-scope] `npx tsc --noEmit` fails repo-wide on `apps/admin/tests/reduced-motion.spec.ts:18`, unrelated to this plan.**
- **Found during:** Task 1's `<verify>` block, first automated command.
- **Issue:** `test.use({ reducedMotion: 'reduce' })` fails to type-check: `error TS2353: Object literal may only specify known properties, and 'reducedMotion' does not exist in type 'Fixtures<...>'`, even though `reducedMotion` is a genuine, documented Playwright option (confirmed present in `playwright-core@1.61.1`'s own `types.d.ts`).
- **Why this is not a bug in this plan's work:** confirmed pre-existing two ways, not assumed. (a) The exact line is byte-identical at commit `5b3365f`, a docs-only Phase 23 planning commit that predates any Phase 23 execution. (b) This plan's three new files (`opsFormat.ts`, `ops-format.spec.ts`, `playwright.unit.config.ts`) were temporarily moved entirely out of the working tree and `npx tsc --noEmit` was re-run — the identical error reproduced with zero files from this plan present, then the files were restored.
- **Why it is not fixed here:** the only available fixes (editing `apps/admin/tests/reduced-motion.spec.ts`, editing `apps/admin/playwright.config.ts`, or bumping/deduping a Playwright-family dependency) are each explicitly forbidden by this plan's own acceptance criteria and this phase's hard constraints (`tests/` and `playwright.config.ts` byte-unchanged; no dependency-block change). There is no in-scope path to green.
- **Consequence:** the combined verify command in Task 1 (`npx tsc --noEmit && node scripts/check-no-dusk-tokens.mjs && npx playwright test -c playwright.unit.config.ts --reporter=list && echo OPS-FORMAT-UNIT-OK`) does not print its `OK` sentinel — the chain short-circuits on the pre-existing `tsc` failure. Every command after it in the chain was verified independently and passes (see Verification section below).
- **Logged:** `.planning/phases/23-.../deferred-items.md`.
- **Files modified:** none (deliberately — fixing this would violate the byte-unchanged constraint).

---

**Total deviations:** 1 auto-fixed (citation accuracy, no behavior change), 1 reported-not-fixed (pre-existing, out-of-scope, contractually unfixable within this plan).
**Impact on plan:** Zero impact on this plan's own deliverables — every acceptance criterion for `opsFormat.ts`, its spec, the browserless config, and the wiring gate is independently verified and passing. The one gap is a repo-wide, pre-existing TypeScript issue this plan neither caused nor has a legal path to fix.

## Verification — real output, run this session

**Task 1** (`cd apps/admin`):
- `npx tsc --noEmit` → **exit 1** — pre-existing failure in `tests/reduced-motion.spec.ts:18`, confirmed unrelated (see Deviations #2). Isolation test: removing this plan's 3 new files entirely and re-running reproduces the identical single error.
- `node scripts/check-no-dusk-tokens.mjs` → exit 0, `PASS -- no retired dusk/skyline/amber-console markers found.`
- `npx playwright test -c playwright.unit.config.ts --reporter=list` → **45 passed** (5.7-6.9s across repeated runs), no browser/server launched.
- Structural gate (React-import/JSX/export-count/spec-coverage/e2e-config-reach/package-script) → `OPS-FORMAT-STRUCTURE-OK exports=20`.
- Diff-scope gate (`git diff` on `package.json` excluding dependency/devDependency lines; `playwright.config.ts` + `tests/` untouched) → `E2E-SUITE-UNTOUCHED-OK`.

**Task 2** (`cd apps/admin`):
- `node scripts/check-ops-room-wiring.mjs --report | tee ... && grep -c '^(PASS|OPEN) '` → 11 lines, all `OPEN` (none of the six regions wired yet) → `OPS-WIRING-GATE-REPORT-OK`.
- `node scripts/check-ops-room-wiring.mjs` (no flag) → **exit 1**, 11 outstanding items named with exact file:line evidence → `OPS-WIRING-GATE-IS-RED-AS-EXPECTED`.
- Structural gate (comment-stripping present, no self-scan, named package script) + `check-no-dusk-tokens.mjs` + package.json diff-scope → `OPS-WIRING-GATE-SHAPE-OK`, `GATE-WIRING-OK`.
- **Demonstration A** (literal outside a comment, in a new scratch file under `apps/admin/app/`): gate named it — `app\__gate_demo_scratch__.ts:5  export const demoClaim = "Retrieval health instrumentation ships in a future release."` — alongside the pre-existing `page.tsx:405` hit. Restored (file deleted) before judging.
- **Demonstration B** (identical literal, inside a `//` comment, same scratch file location): gate's evidence for that check showed **only** `page.tsx:405` — the scratch file's commented occurrence produced zero additional findings. Restored (file deleted) before judging.
- Post-demonstration re-run confirmed the report output byte-identical to the pre-demonstration baseline.

## Issues Encountered

See Deviations #2 above — the pre-existing `tsc --noEmit` failure in the shipped e2e test suite, out of scope for this plan, logged in `deferred-items.md`.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- `opsFormat.ts`'s 20 exports and `check-ops-room-wiring.mjs`'s 11 named checks are exactly what `23-05` (Live, Retrieval health, Judgement), `23-06` (Adversary), `23-07` (The prompt), and `23-08` (The bench, plus the final all-six-regions check) each assert against before writing a line of component code, per this plan's own success criteria.
- **Flag for whichever plan or phase-close step next runs a repo-wide `tsc --noEmit` gate:** it will still show the one pre-existing, unrelated failure in `tests/reduced-motion.spec.ts:18` documented above and in `deferred-items.md`, until a plan with permission to touch `apps/admin/tests/` or its dependencies fixes it. No plan in Phase 23 has that permission (every plan's constraints require `tests/`/`playwright.config.ts` to stay byte-unchanged and forbid dependency changes), so this is expected to persist through the whole phase and should not be mistaken for a regression introduced by a later plan.
- No blockers for `23-04` through `23-08` — none of their `files_modified` overlap this plan's, and this plan's own files are fully committed.

## Self-Check: PASSED

All 6 created files confirmed present on disk (`opsFormat.ts`, `ops-format.spec.ts`,
`playwright.unit.config.ts`, `check-ops-room-wiring.mjs`, `deferred-items.md`, this SUMMARY).
All 3 task commit hashes (`52d3e27`, `0a7f771`, `4a09274`) confirmed present in `git log`.

---
*Phase: 23-wire-the-gotham-operations-room-to-the-phase-21-backends-and*
*Plan: 03*
*Completed: 2026-08-03*
