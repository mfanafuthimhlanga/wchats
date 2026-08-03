---
phase: 23-wire-the-gotham-operations-room-to-the-phase-21-backends-and
plan: 08
subsystem: ui
tags: [nextjs, react-query, typescript, gotham, roving-listbox, ops-room, keyboard-a11y]

requires:
  - phase: 23-07
    provides: "the shared setRegionError(region, message) callback (23-05), opsFormat.ts's judgeVerdictToChip/gradeToChip, the .cap-confirm* precedent (unused here — this region ships no staged confirm), and the wiring gate standing at 10/11 PASS with only bench-traces-wired open"
provides:
  - "BenchPane.tsx — the bench region: a net-new two-pane roving listbox (role=listbox/option, roving tab index, arrow/Home/End moving selection and focus together), the enlarger, and the three grade actions (file/hold/dismiss) with P/H/X keyboard shortcuts"
  - "page.tsx's bench section mounted on real data, its header comment corrected, and the two-pane layout CSS (.bench-panes/.bench-sheet/.bench-enlarger) — the last false claim/empty-state placeholder gone, all six regions wired"
affects: [23-09]

tech-stack:
  added: []
  patterns:
    - "Roving tabindex computed from render state (effectiveSelectedId = selectedId ?? traces[0]?.trace_id ?? null) rather than a mount-time useEffect — the first render already has a real tab stop and a populated enlarger, no one-frame gap where no option carries tabIndex 0."
    - "Two separate onKeyDown handlers at two container levels: the listbox (.bench-sheet) owns Arrow/Home/End, catching events bubbling from every option button exactly once; the outer two-pane wrapper (.bench-panes) owns the P/H/X grade shortcuts, catching them from anywhere in the region including the enlarger's own action buttons."
    - "Busy state is Record<string, Grade> (which grade is in flight per trace id), not Record<string, boolean> — lets the resting button show the correct action-specific busy label ('Filing…'/'Holding…'/'Dismissing…') from one lookup, mirroring PromptVersionPanel's own per-action busy-state shape (23-07)."
    - "A filed trace's three actions use aria-disabled + the shared .is-disabled class rather than the native disabled attribute, so the control stays focusable and a screen-reader user tabbing through still lands on it and hears the locked caption explaining why it is inert — native disabled would silently remove it from the tab order."
key-files:
  created:
    - apps/admin/app/agents/[id]/components/BenchPane.tsx
  modified:
    - apps/admin/app/agents/[id]/page.tsx

key-decisions:
  - "The plan's threat register names a fourth shortcut guard — 'a confirmation is open anywhere in the region' — alongside modifier/form-control/already-filed. This region ships no staged confirmation anywhere (23-UI-SPEC.md §4.3 locks file/hold/dismiss as plain, unstaged actions), so there is structurally nothing that can ever be open. Implemented as a named, commented constant (confirmationOpen = false) rather than either inventing dead UI state or silently dropping the guard from the code — the guard is present and honestly documented as vacuous by design, not missing."
  - "Two-pane layout classes (.bench-panes/.bench-sheet/.bench-enlarger) are named distinctly from deploy/page.tsx's own .bench grid — a different two-column layout for a different page. Verified the two never load together and the class collision the check specifically forbids does not occur."
  - "The tally is never read from the grade mutation's own response body for rendering — it is always re-read from the (invalidated, refetched) listing query, the same source the initial render uses. This makes 'never incremented locally' true by construction rather than by a per-call carve-out, and matches the pattern the sibling regions (Adversary, prompt) already use (invalidate-and-refetch, never optimistic-merge a mutation response into a locally held count)."
  - "The live-announcement sentence is computed at the moment handleGrade is called (capturing the trace's position in the CURRENT traces array) and passed as a per-call onSuccess callback to the mutation, rather than recomputed inside the mutation-level onSuccess — avoids any staleness risk from a refetch landing between the click and the mutation resolving."

requirements-completed: [WIRE-01]

coverage:
  - id: D1
    description: "BenchPane.tsx calls GET /agents/{id}/traces?status=failing and POST .../traces/{trace_id}/grade and no other route; the sheet is a role=listbox of role=option buttons with a roving tab index, arrow/Home/End move selection and focus together; the three grade keys are handled and ignored under the modifier, form-control, and already-filed guards (the fourth guard, confirmation-open, is a documented constant since no staged confirm exists in this region); the graded badge uses opsFormat's neutral gradeToChip mapping; the judge-voice .voice treatment appears exactly once, on judge_rationale; a visually-hidden aria-live=\"polite\" region announces after each resolved grade; a filed trace's three actions render aria-disabled with the locked caption; a 409 throws with the trace id, refetches, and renders the locked inline note — never a toast; the tally is read only from the (refetched) listing response, never incremented locally; busy state is Record<string, Grade> keyed by trace id."
    requirement: "WIRE-01"
    verification:
      - kind: other
        ref: "node -e structural script (Task 1's first verify, run this session): listing+grade routes referenced and no others, listbox/option roles, aria-selected, tabIndex, all four keys handled, modifier + form-control guards present, aria-live=\"polite\", vh class, voice class exactly once, 409 handled, no toast, no clickable non-interactive element, no local tally increment, opsFormat referenced, Record<string busy state — BENCH-PANE-OK"
        status: pass
      - kind: other
        ref: "node -e copy-verbatim script (Task 1's second verify, run this session) — all four locked strings present verbatim: BENCH-COPY-VERBATIM-OK; apps/api untouched: API-UNTOUCHED-OK"
        status: pass
      - kind: unit
        ref: "npx playwright test -c playwright.unit.config.ts (opsFormat.ts regression, the functions BenchPane calls: judgeVerdictToChip, gradeToChip) — 45 passed"
        status: pass
    human_judgment: true
    rationale: "No signed-in Clerk session or seeded control-DB job_events rows are available in this execution environment to render the listbox against real failing-trace data and visually confirm arrow-key focus movement, the enlarger's long-text wrap, and the conflict note's rendered position. Structural/type/copy/regression proof is complete; a rendered check against real data is recommended before user-facing review (23-09 owns the adversarial design review)."
  - id: D2
    description: "page.tsx mounts BenchPane in the bench section (no EmptyState left), passing the page's readiness condition and the shared setRegionError callback; the header comment no longer names any region as awaiting a backend; the two-pane CSS (.bench-panes/.bench-sheet/.bench-enlarger) provides a bounded, independently-scrolling sheet, a zero minimum width on both panes, and a responsive collapse below 900px, named distinctly from deploy/page.tsx's own .bench grid; the standing wiring gate exits zero with no flag, for the first time."
    requirement: "WIRE-01"
    verification:
      - kind: other
        ref: "node -e structural script (Task 2's second verify, run this session): BenchPane mounted, no EmptyState within the bench section's first 600 chars, min-width:0 present, overflow-y auto present, @media present, no .bench{ class-name collision, header comment clean of 'awaiting a backend' language, exactly one role=\"alert\" surface — BENCH-MOUNT-OK"
        status: pass
      - kind: other
        ref: "node scripts/check-ops-room-wiring.mjs (run this session, no flag) — check:ops-room-wiring: PASS, exits 0 — OPS-WIRING-GATE-GREEN-FIRST-TIME. --report confirms all 11/11 checks PASS including bench-traces-wired."
        status: pass
      - kind: e2e
        ref: "npx playwright test tests/overflow.spec.ts (run this session, real browser, real Next.js dev server in demo mode) — 32/33 passed; see Deviations for the one unrelated failure and its cause. /agents/demo-1 (the bench page) passed at all three viewports: desktop-1440 (13.6s), laptop-1280 (7.8s), tablet-900 (3.6s)."
        status: pass
      - kind: other
        ref: "git diff -U0 scope gate (run this session) — zero matches for gateBlocked|redTeamBlocked|openFindings|AdversaryPanel|LivePanel|RetrievalHealthPanel|PromptVersionPanel|cap-confirm|ledger\\. ; apps/api untouched — DIFF-SCOPE-AND-API-OK"
        status: pass
    human_judgment: true
    rationale: "The demo-mode overflow run has no real Clerk session (documented in playwright.config.ts's own header comment), so BenchPane's data query is disabled and the run only proves the page SHELL (with BenchPane's 'Fetching the bench…' placeholder line) does not overflow at the three widths — not a populated two-pane grid with real long customer/agent/judge text. This exact gap is already named as a Manual-Only Verification row in 23-VALIDATION.md ('populated tables do not overflow... needs populated data and therefore a session') and is not newly introduced by this plan; it is recorded here rather than implied as covered."

duration: ~65min (includes environment troubleshooting — see Deviations)
completed: 2026-08-03
status: complete
---

# Phase 23 Plan 08: The Bench — Roving-Listbox Contact Sheet, Enlarger, and the Three Grades Summary

**`BenchPane.tsx` builds the phase's one net-new UI pattern — a two-pane roving listbox with no analog anywhere in this codebase — wiring `GET /agents/{id}/traces?status=failing` and `POST .../grade` so an operator can list a failing production trace, read it in full beside its judge's reasoning, and file/hold/dismiss it by mouse or by guarded P/H/X keyboard shortcut; mounting it in `page.tsx` closes the flywheel end to end and flips the standing wiring gate to 11/11 PASS for the first time since it was written.**

## Performance

- **Duration:** ~65 min total. Task 1 commit at 13:38:54+02:00 (~21 min after the prior plan's close-out at 13:17:37), Task 2 commit at 14:20:39+02:00 (~42 min after Task 1). A meaningful share of Task 2's time was spent diagnosing and repairing two environment blockers unrelated to this plan's code — see Deviations.
- **Started:** ~2026-08-03T11:20:00Z (estimated, first tool call)
- **Completed:** 2026-08-03T12:20:39Z
- **Tasks:** 2/2 completed
- **Files modified:** 2 (1 created, 1 modified)

## Accomplishments

- **`BenchPane.tsx`** (419 lines, new): calls `GET /agents/{id}/traces?status=failing` for the listing and tally, and `POST /agents/{id}/traces/{trace_id}/grade` for the operator's grade — no other route. Built directly against `20-UI-SPEC.md §6.4.1`'s interaction contract since this region has no in-codebase precedent: the contact sheet is a `role="listbox"` of `role="option"` buttons with a roving tab index (0 for the selected option, -1 for the rest); Arrow Down/Up wrap, Home/End jump to the ends, and moving the selection moves real DOM focus with it (`optionRefs` + an imperative `.focus()` call, matching the ingest page's own roving-tab precedent). The three grade keys (P/H/X → filed/held/dismissed) act on the selected trace from anywhere in the region, guarded against any modifier key, focus in a form control or editable element, an already-filed selected trace, and — since this region ships no staged confirmation anywhere by the approved design contract — a documented, always-false `confirmationOpen` constant standing in for the fourth guard the plan's threat register names. Each option row shows the first line of the customer's turn, the judge's verdict via `opsFormat.judgeVerdictToChip`, and (only once graded) a neutral badge via `opsFormat.gradeToChip` — never a pass/fail colour on an operator's own decision. The enlarger shows the full customer turn, agent turn, and the judge's reasoning in the `.voice` italic treatment — used exactly once in this file, and nowhere else. A filed trace's three actions render `aria-disabled` (not natively `disabled`, so the control stays focusable and a keyboard/screen-reader user still reaches the locked caption explaining why) with `This trace has been filed. It cannot be re-graded.` verbatim. A 409 from a concurrent grade throws with the trace id attached, refetches, and shows `Someone already graded this trace.` as a transient, self-clearing per-trace note — never a toast, never a second error surface. A visually-hidden `aria-live="polite"` region announces a sentence naming the trace's position and the grade applied after every resolved grade. The tally shown always comes from the (re-fetched) listing response, never a local increment. Busy state is `Record<string, Grade>` keyed by trace id, so grading one trace never disables another's actions. Zero traces renders the locked `Nothing on the bench` / `No failing production traces right now. Every recent turn passed its judge.` empty state and nothing else.
- **`page.tsx`**: the bench section now mounts `BenchPane`, passing the page's existing readiness condition and the shared `setRegionError` callback — the sixth and last region to leave its empty-state placeholder. The header comment's closing sentence is rewritten to describe the bench as wired rather than outstanding. `PAGE_CSS` gains `.bench-panes`/`.bench-sheet`/`.bench-enlarger`: a two-column grid (bounded, independently-scrolling sheet at left; enlarger at right) with a zero minimum width on both panes so a long unbroken customer-turn string cannot force horizontal overflow, collapsing to one column below 900px — named distinctly from `deploy/page.tsx`'s own `.bench` grid, a different layout for a different page that never loads alongside this one.
- `check-ops-room-wiring.mjs` (no flag) now exits `0` — `check:ops-room-wiring: PASS -- every region calls its own Phase 21 endpoint and the console asserts nothing false about its own capabilities.` `--report` confirms all 11/11 checks `PASS`, `bench-traces-wired` included, for the first time since the gate was written in Wave 0.

## Task Commits

Each task was committed atomically, scoped to exactly its own file (`git commit -- <file>`):

1. **Task 1: The contact sheet, the enlarger, and the three grades** — `3c34a1e` (feat)
2. **Task 2: Mount the bench and give it its two-pane layout** — `1efed6b` (fix)

**Plan metadata:** commit hash recorded after this SUMMARY is committed (see below).

## Files Created/Modified

- `apps/admin/app/agents/[id]/components/BenchPane.tsx` — The bench region: net-new roving-listbox contact sheet, the enlarger, three grade actions with guarded keyboard shortcuts, per-trace busy/note state, the live announcement region.
- `apps/admin/app/agents/[id]/page.tsx` — Mounts `BenchPane`; corrects the header comment; adds the two-pane layout CSS.

## Decisions Made

See `key-decisions` in the frontmatter for the four load-bearing calls. In brief: the plan's fourth shortcut guard ("confirmation open") has nothing to check in this build since file/hold/dismiss are deliberately unstaged by the approved design contract — implemented as a named, commented, always-false constant rather than invented dead UI or a silently-dropped guard; the two-pane CSS class names are distinct from `deploy/page.tsx`'s own `.bench` grid by construction; the tally is always re-read from the listing query rather than the mutation's own response, making "never incremented locally" true by construction; and the live-announcement sentence is captured at click time (not inside the mutation's `onSuccess`) to avoid any staleness risk from an interleaved refetch.

## Deviations from Plan

### Auto-fixed (Rule 3 — blocking issues, both environmental, neither in this plan's own deliverable code)

**1. [Rule 3 - Blocker] Playwright's Chromium browser binary was not installed in this environment.**
- **Found during:** Task 2's third verify command, `npx playwright test tests/overflow.spec.ts`.
- **Issue:** All 33 tests failed identically with `Error: browserType.launch: Executable doesn't exist at ...chrome-headless-shell.exe` — including routes with no relationship to this plan's changes (`/`, `/sign-in`, `/agents`), confirming the cause was environmental (no browser binary), not a code regression.
- **Fix:** `npx playwright install chromium` — this downloads pinned browser binaries for `@playwright/test`, a devDependency already declared in `package.json`; it is not a package-manager install of a new or unverified package, so it is not excluded from Rule 3's auto-fix scope the way `npm install <pkg>` is.
- **Verification:** the browser installed cleanly (113.6 MiB); the subsequent full run of `overflow.spec.ts` actually launched and rendered pages.
- **Commit:** not a code change — no file in this repository was modified by this fix.

**2. [Rule 3 - Blocker] The machine's C: drive reached 0 bytes free mid-session, blocking `git commit` for Task 2.**
- **Found during:** staging Task 2's changes (`git add`), after the overflow-spec run and the browser install above.
- **Issue:** `git add` failed with `fatal: unable to write loose object file: No space left on device`. Confirmed independently via `df -h` (`116G 116G 0 100%`) and Windows' own `Get-PSDrive C` (`Free: 0`) — not a Git Bash reporting artifact. `npm cache clean --force`, attempted as a first remediation, itself failed with `npm error code ENOSPC` — the disk was too full even to write npm's own log file. The overwhelming majority of the 116 GB is unrelated to this project (this project's own build caches measured in the hundreds of megabytes, not gigabytes), so a broad search-and-delete across the machine was explicitly avoided as out of scope and risky — the responsible fix is scoped to artifacts this session fully understands the provenance and regenerability of.
- **Fix:** removed `apps/admin/.next` (the Next.js dev/build cache, 362 MB, fully regenerated by `next dev`/`next build`, not a tracked or source file) — freed the drive to 137 MB available, enough to stage and commit this plan's small diff.
- **Verification:** `git add` and `git commit` both succeeded immediately afterward; `npx tsc --noEmit`, `check-no-dusk-tokens.mjs`, and `check-ops-room-wiring.mjs` were all re-run after the cache removal and are unaffected (none depend on `.next`).
- **Files modified:** none beyond this plan's own two files — `.next` is a build artifact, never a git-tracked path.
- **Impact:** this frees disk space narrowly scoped to a regenerable cache within this project; it does not address the machine's underlying, much larger disk-space condition, which is outside this plan's scope and is flagged below for the operator's attention.

### Reported, not fixed (real, honestly incomplete verification — not a defect in this plan's deliverable)

**3. [Reported] The one full `overflow.spec.ts` run that completed produced 32/33 passed, not a clean 33/33, and a retry to confirm the one failure was flaky could not be completed.**
- **Found during:** Task 2's third verify command, after fixing deviation 1.
- **Real output:** `32 passed (6.3m)`, `1 failed`. The single failure: `[laptop-1280] › / has no horizontal overflow at this viewport` — `Error: page.waitForLoadState: Test timeout of 90000ms exceeded` at `overflow.spec.ts:28`. This is a load-state **timeout**, not a `scrollWidth`/`clientWidth` assertion failure — the test never reached the overflow check itself. The failing route is `/` (the marketing/landing page), which does not import `BenchPane.tsx`, does not reference `.bench-panes`/`.bench-sheet`/`.bench-enlarger`, and is untouched by this plan's diff.
- **The route this plan is actually responsible for passed cleanly at all three viewports in the same run:** `/agents/demo-1` — desktop-1440 (13.6s), laptop-1280 (7.8s), tablet-900 (3.6s), all `ok`.
- **Why not fully re-verified:** an isolated retry of the one failing test (`--project=laptop-1280 -g "^/ has no horizontal overflow"`) was attempted to distinguish "genuine regression" from "flake," but the retry itself failed before launching a browser: `npm error code ENOSPC` — the disk-space exhaustion in deviation 2 was discovered via this very retry attempt. After freeing space (deviation 2's fix), the full suite was not re-run a second time given the remaining disk headroom is narrow (137 MB) and a second 6+ minute, multi-worker, multi-browser-instance run risks repeating the same resource pressure for a route this plan does not touch.
- **Why this is recorded as an environment flake, not a defect:** the failing test is a network/compile-load timeout on an unrelated route, in a suite explicitly documented (`playwright.config.ts`'s own header comment) as running on "a 4 GB dev machine" with "Turbopack's on-demand per-route first-compile" cold-compile times "observed up to ~30s/route" — and the disk was independently confirmed to be under severe pressure during this exact run. Reported here rather than silently omitted or claimed clean, per this plan's own instruction to report real output, never a failing gate as passed.
- **Files modified:** none — no code change addresses this; it is an environment condition, not a code defect.

## Issues Encountered

See Deviations above — two environment blockers (missing browser binary, disk exhaustion) fixed within this plan's scope, and one honestly-reported incomplete re-verification (the `/` route's flaky timeout) that could not be cleanly re-confirmed without repeating a resource-intensive run against an already-strained disk. No blocking issue remains in this plan's own deliverable code: every structural, copy-verbatim, type-check, token-gate, wiring-gate, diff-scope, and pure-layer regression command printed its real, expected success output this session.

**Flag for the operator, outside this plan's scope:** the machine's C: drive was at 0 bytes free (116 GB, 100% used) during this session, confirmed via two independent tools. This plan's own fix (removing a 362 MB regenerable Next.js cache) freed only 137 MB — nowhere near enough headroom for comfortable further development, and the cause of the remaining ~115 GB of usage was not investigated (out of scope for a plan executor to search and delete arbitrary machine-wide files). Recommend running Windows' own Disk Cleanup or reviewing large directories outside this repository before the next session that needs to run a full browser-based Playwright suite.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- `BenchPane.tsx` and `page.tsx`'s mount are both complete and independently verified; `check-ops-room-wiring.mjs` exits `0` with no flag, and `--report` confirms all 11/11 checks `PASS` — the gate that was red from the moment it was written in Wave 0 is green for the first time.
- `npx tsc --noEmit` carries the single pre-existing, unrelated `tests/reduced-motion.spec.ts:18` error only (present since Phase 20, `7f64005`, out of scope, documented in `23-03-SUMMARY.md`/`23-05-SUMMARY.md`/`23-06-SUMMARY.md`/`23-07-SUMMARY.md`/`deferred-items.md`).
- Zero `.py` files touched by this plan's commits; `apps/api` confirmed untouched (`git status --porcelain apps/api` empty) both before and after this plan's two commits. The Python unit suite baseline (**1199 passed, 8 skipped, 0 failed**) is untouched by construction.
- The failure-triage flywheel is now reachable end to end from the console at the code/structural level: list a failing trace (`BenchPane` calls `GET .../traces?status=failing`), grade it filed (`POST .../grade`), and the promotion the grade route dispatches server-side (`traces.py:158-167`, unmodified) raises the born-in-production count the Judgement region already renders (23-05). The round trip itself — actually filing a trace against a live backend and watching the ORRERY tile rise — was not observed this session (no signed-in session / seeded control-DB data available), matching this phase's own `23-VALIDATION.md` Manual-Only Verifications table, which already names this exact round trip as requiring a live session.
- **Flag for 23-09 (adversarial design review):** the automated overflow check for `/agents/demo-1` in this session ran in demo mode with no real Clerk session, so `BenchPane`'s data query was disabled and the page rendered its "Fetching the bench…" placeholder, not a populated two-pane grid with real long text. The three-viewport overflow check therefore proves the page **shell** is clean, not a populated listbox + enlarger with a long customer turn, agent turn, or judge rationale — this exact gap is already a named Manual-Only Verification row in `23-VALIDATION.md`, not newly introduced here.
- No blockers for 23-09 — its scope (adversarial design review, `23-VALIDATION.md` fill, one observed gate sweep) does not overlap this plan's two files, and this plan's own files are fully committed.

---
*Phase: 23-wire-the-gotham-operations-room-to-the-phase-21-backends-and*
*Plan: 08*
*Completed: 2026-08-03*

## Self-Check: PASSED

- FOUND: `apps/admin/app/agents/[id]/components/BenchPane.tsx`
- FOUND: `apps/admin/app/agents/[id]/page.tsx` (BenchPane mounted, verified via structural script)
- FOUND: commit `3c34a1e` (Task 1)
- FOUND: commit `1efed6b` (Task 2)
