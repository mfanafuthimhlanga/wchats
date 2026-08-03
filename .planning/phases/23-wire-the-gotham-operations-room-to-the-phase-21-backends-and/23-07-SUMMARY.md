---
phase: 23-wire-the-gotham-operations-room-to-the-phase-21-backends-and
plan: 07
subsystem: ui
tags: [nextjs, react-query, typescript, gotham, sentinel-honesty, ops-room, staged-confirm]

requires:
  - phase: 23-06
    provides: "the .cap-confirm* staged-confirm styles ported into page.tsx's own style block (OD-3), and the setRegionError(region, message) shared error-surface callback (23-05) this plan wires into independently"
provides:
  - "PromptVersionPanel.tsx — the prompt region as a component: a four-column version ledger, a two-side comparison over all four soul fields (unchanged fields shown, list fields rendered per entry), and staged canary/rollback actions keyed per version"
  - "page.tsx's prompt section mounted on real data, its header comment corrected, and the third and last false capability claim deleted from the tree"
affects: [23-08]

tech-stack:
  added: []
  patterns:
    - "A per-version sub-component (VersionActions) keeps its own `staged`/`percent` local state, mirroring PendingConfirmationRow and AdversaryPanel's FindingContain exactly — only busy/note state is lifted to the parent, keyed by version identifier, so two versions' actions never share state."
    - "A single busy-state Record maps version id to WHICH action is in flight ('canary' | 'rollback'), not just a boolean — the row reads its own key to decide both whether it's disabled and which in-flight label to show, without a second map."

key-files:
  created:
    - apps/admin/app/agents/[id]/components/PromptVersionPanel.tsx
  modified:
    - apps/admin/app/agents/[id]/page.tsx

key-decisions:
  - "The version ledger stays a pure 4-column read-only table (Version / Label / Canary / Created), with the canary-input-plus-actions living in a separate per-version list below it — mirroring AdversaryPanel's own split between its read-only coverage ledger and its separate per-finding contain-action list, rather than cramming actions into a 5th ledger column the design contract never specifies."
  - "Busy state is a single Record<string, 'canary' | 'rollback'> keyed by version id, not two separate boolean maps — a version can only have one of its two actions in flight at a time in practice, and this shape lets each row's resting buttons show the correct action-specific in-flight label ('Setting…' / 'Rolling back…') from one lookup instead of two."
  - "The canary percent input is bounded three ways, not one: the native min={0}/max=\"100\" HTML attributes, a clampPercent() function run on every onChange so an out-of-range value can never even be typed into state, and the server's own Pydantic Field(ge=0, le=100) as the actual authority. The client bound is a courtesy, never the control, per the plan's own T-23-PRM-04 disposition."
  - "The comparison's list-valued fields (soul_do_list, soul_donot_list) render as one <div> per entry inside the .well block rather than a newline-joined string — .well is applied to plain <div>/<code> elements elsewhere in this codebase (deploy/page.tsx's embed snippet, DocumentDetailModal's chunk text), neither of which sets `white-space: pre`, so a joined '\\n' string would collapse to one line and silently reproduce the exact misreading (a reordered entry looking like a rewrite) the plan's own action text calls out by name."
  - "The header-comment edit in page.tsx touches only the one sentence naming which regions remain unwired, leaving every neighbouring line (which names AdversaryPanel/LivePanel/RetrievalHealthPanel/onOpenFindingsChange) byte-identical — satisfying the diff-scope gate by construction rather than by accident."

patterns-established:
  - "A region whose read-only summary table and its per-item live actions are visually and structurally separate (ledger above, action list below) rather than combined into one wide table — the shape AdversaryPanel established and this plan reuses for a second region."

requirements-completed: [WIRE-01, WIRE-03]

coverage:
  - id: D1
    description: "PromptVersionPanel.tsx calls all four prompt_versions endpoints (list, diff, canary, rollback) and no others; the version ledger has exactly four real columns (Version, Label, Canary, Created) with a real caption and the scroll wrapper, is never re-sorted client-side, and a null label renders a dash while a null or zero canary share both render 0% through opsFormat's renderCanaryPercent."
    requirement: "WIRE-01"
    verification:
      - kind: other
        ref: "node -e structural script (Task 1's first verify, run this session): all four routes referenced, all four soul fields named, opsFormat referenced, caption + scroll-x present, .well used, no diff/highlighting package, >=2 cap-confirm-q, >=2 autoFocus, Record<string busy state, no toast, share input bounded to 100 — all PASS except the LedgerColHead bare-identifier count (see Deviations)"
        status: pass
      - kind: other
        ref: "independent corrected-pattern proof this session: (s.match(/<LedgerColHead/g)||[]).length === 4"
        status: pass
      - kind: unit
        ref: "npx playwright test -c playwright.unit.config.ts (opsFormat.ts regression, the functions PromptVersionPanel calls, in particular renderCanaryPercent) — 45 passed, twice (after Task 1 and after Task 2)"
        status: pass
    human_judgment: true
    rationale: "No dev server, backend, or signed-in Clerk session is available in this execution environment to render the version ledger and comparison against a real /prompt-versions response and visually confirm column alignment, the empty state, and the compare-selector default. Structural/type/regression proof is complete; a rendered screenshot check is recommended before user-facing review (23-09 owns the adversarial design review)."
  - id: D2
    description: "The comparison calls GET .../prompt-versions/diff with two distinct version ids as named query parameters, renders all four soul fields including unchanged ones (each explicitly marked changed/unchanged, never omitted), and renders the two list-valued fields (soul_do_list, soul_donot_list) as one line per entry rather than a joined string, inside the existing .well code-block treatment with no diff-highlighting library introduced."
    requirement: "WIRE-01"
    verification:
      - kind: other
        ref: "node -e structural script (Task 1's first verify) confirmed all four soul_* field names present, .well present, no diff-match/jsdiff/diff2html/highlight.js/prismjs import — PASS"
        status: pass
      - kind: other
        ref: "manual read-through of PromptVersionPanel.tsx's renderFieldSide() confirming array values map to one <div> per entry (never Array.join), and SOUL_FIELD_ROWS iterates all four keys unconditionally so an unchanged field still renders its label with '· unchanged'"
        status: pass
    human_judgment: false
  - id: D3
    description: "Setting a canary share and rolling back are both staged behind the shipped .cap-confirm shape with the locked, verbatim copy (including the rollback's 'Nothing is deleted' clause), an autofocused primary described by its question via aria-describedby, and neither resting control invokes its mutation directly; both mutations invalidate the versions query on success (no optimistic list mutation) and busy/failure state is keyed per version identifier so one version's in-flight action never disables another's."
    requirement: "WIRE-01"
    verification:
      - kind: other
        ref: "node -e copy-verbatim script (Task 1's second verify, run this session) against 23-UI-SPEC.md's prompt rows — PROMPT-COPY-VERBATIM-OK, all nine invariant strings present"
        status: pass
      - kind: other
        ref: "manual read-through of VersionActions confirming both staged blocks use cap-confirm/cap-confirm-q/cap-confirm-actions, both primaries carry autoFocus + aria-describedby, resting canary/rollback buttons only call setStaged (never onSetCanary/onRollback directly), and Record<string, 'canary' | 'rollback'> is the sole busy-state shape"
        status: pass
    human_judgment: false
  - id: D4
    description: "page.tsx mounts PromptVersionPanel in the prompt section (replacing its EmptyState entirely, including the soul-editor link which now lives inside the component's own no-versions empty state), passes it the page's readiness condition and the shared setRegionError callback, and the false claim 'Version history, canary releases and rollback ship in a future release.' is gone from the tree — flipping check-ops-room-wiring.mjs's no-prompt-versions-future-claim, no-future-release-evasion-phrase, and prompt-versions-wired checks to PASS, leaving only bench-traces-wired (23-08) OPEN."
    requirement: "WIRE-03"
    verification:
      - kind: other
        ref: "node scripts/check-ops-room-wiring.mjs --report (run this session): 10/11 checks PASS, only bench-traces-wired OPEN — HONESTY-CHECKS-ALL-GREEN"
        status: pass
      - kind: other
        ref: "plan's literal Task 2 structural verify (node -e, run this session) — PromptVersionPanel mounted, no EmptyState within the prompt section's first 700 chars, zero 'future release' occurrences anywhere in page.tsx, exactly one role=\"alert\" surface — PROMPT-MOUNT-OK"
        status: pass
      - kind: other
        ref: "git diff -U0 scope gate (run this session) — zero matches for gateBlocked|redTeamBlocked|openFindings|AdversaryPanel|LivePanel|RetrievalHealthPanel|bench-h|cap-confirm — DIFF-SCOPE-OK"
        status: pass
    human_judgment: false

duration: ~15min
completed: 2026-08-03
status: complete
---

# Phase 23 Plan 07: The Prompt Region — Version Ledger, Comparison, and Staged Canary/Rollback Summary

**`PromptVersionPanel.tsx` wires all four `prompt_versions` endpoints (list, diff, canary, rollback) that Phase 21 shipped and this console never called, replacing the third and last "ships in a future release" false claim with a real version ledger, a four-field comparison that shows unchanged fields too, and two staged live actions — set canary, roll back — both carrying the locked copy that says the truth about what each one does.**

## Performance

- **Duration:** ~15 min (commits 2 min 29 sec apart at 13:09:14+02:00 and 13:11:43+02:00 local time; total includes reading all context/read_first sources and the full plan-level verification loop)
- **Started:** ~2026-08-03T11:05:00Z (estimated, first tool call)
- **Completed:** 2026-08-03T11:11:43Z
- **Tasks:** 2/2 completed
- **Files modified:** 2 (1 created, 1 modified)

## Accomplishments

- **`PromptVersionPanel.tsx`** (540 lines, new): calls `GET /agents/{id}/prompt-versions` for the ledger, `GET /agents/{id}/prompt-versions/diff?a=&b=` for the comparison, and `POST .../canary` / `POST .../rollback` for the two live actions — no other route. The version ledger (four columns: Version, Label, Canary, Created) renders in server order with zero client-side re-sort; a null label renders a dash and a null-or-zero canary share both render `0%` through `opsFormat.renderCanaryPercent`, the same shared pure renderer AdversaryPanel and the rest of this phase's regions already use. The comparison defaults to newest-vs-previous, is enabled only once two distinct versions are chosen, and renders all four soul fields — including unchanged ones, each explicitly labelled `· changed` or `· unchanged` — inside the existing `.well` code-block treatment; the two list-valued fields (`soul_do_list`, `soul_donot_list`) render one `<div>` per entry rather than a joined string, so a single reordered line can never misread as a full rewrite. Setting a canary share and rolling back are both per-version actions staged behind the house `.cap-confirm` shape with this phase's locked copy verbatim, including the rollback's load-bearing "Nothing is deleted" clause; busy state is a `Record<string, 'canary' | 'rollback'>` keyed by version id, so one version's in-flight action never disables another's, and both mutations invalidate the versions query on success rather than optimistically mutating the list.
- **`page.tsx`**: the prompt section now mounts `PromptVersionPanel`, passing the page's existing readiness condition and the shared `setRegionError` callback — replacing the `EmptyState` that carried the third and last false capability claim, whose "Edit in the soul editor" link now lives inside the component's own no-versions empty state (the only state in which it's the right next action). The header comment's closing sentence is corrected to stop naming the prompt region as outstanding; every neighbouring line (naming `AdversaryPanel`/`LivePanel`/`RetrievalHealthPanel`/`onOpenFindingsChange`) is untouched.
- `check-ops-room-wiring.mjs --report` now shows 10 of 11 checks `PASS` — `no-prompt-versions-future-claim`, `no-future-release-evasion-phrase` (the class-level check, since this was the last of the three claims), and `prompt-versions-wired` all flip this plan. Only `bench-traces-wired` (23-08's) remains `OPEN`.

## Task Commits

Each task was committed atomically, scoped to exactly its own file (`git commit -- <file>`):

1. **Task 1: The version list, the comparison, and the two staged live actions** — `aa39ac2` (feat)
2. **Task 2: Mount the region and remove the last false claim** — `af46949` (fix)

**Plan metadata:** commit hash recorded after this SUMMARY is committed (see below).

## Files Created/Modified

- `apps/admin/app/agents/[id]/components/PromptVersionPanel.tsx` — The prompt region: version ledger, two-side comparison, staged canary/rollback actions, per-version busy/note state.
- `apps/admin/app/agents/[id]/page.tsx` — Mounts `PromptVersionPanel`; deletes the third false capability claim; corrects the header comment.

## Decisions Made

See `key-decisions` in the frontmatter for the five load-bearing calls. In brief: the ledger stays a pure 4-column read-only table with actions in a separate per-version list below it (mirroring AdversaryPanel's own coverage-ledger/contain-list split); busy state is one `Record` mapping version id to which action is in flight, not two booleans; the canary input is bounded three independent ways (native HTML attrs, a client-side clamp function, and the server's real Pydantic bound as the actual authority); list-valued diff fields render as separate `<div>` children rather than a newline-joined string, since `.well` is applied to plain block elements elsewhere in this codebase that do not preserve `white-space: pre`; and the header-comment edit touches only the one outstanding-regions sentence, leaving every neighbouring line byte-identical to satisfy the diff-scope gate by construction.

## Deviations from Plan

### Reported, not fixed (verify-script bug, not a defect in this plan's code)

**1. [Verify-script bug] Task 1's version-column count regex is a bare identifier, not angle-bracket-anchored — `/LedgerColHead/g` should have been `/<LedgerColHead/g`.**
- **Found during:** Task 1's first `<verify>` block, the `node -e` structural script.
- **Issue:** The script asserts `(s.match(/LedgerColHead/g)||[]).length === 4`. This bare-identifier pattern matches the import statement (`{ LedgerCell, LedgerColHead, LedgerRowHead }`, 1 hit) plus both the opening and closing tag of every `<LedgerColHead>…</LedgerColHead>` JSX usage (2 hits per column, since `</LedgerColHead>` also contains the substring `LedgerColHead`). For 4 real columns written in this codebase's own established open/close JSX convention (matching `AdversaryPanel.tsx`'s coverage ledger and the Judgement ledger in `page.tsx`), the true literal count is `1 + 4×2 = 9`, confirmed by running the check (`Error: expected 4 version columns, found 9`).
- **Why this is a script bug, not a code bug:** this is the exact same class of defect `23-05-SUMMARY.md` documented for `RetrievalHealthPanel.tsx`'s `LedgerRowHead` count and `23-06-SUMMARY.md` documented for `AdversaryPanel.tsx`'s own `LedgerColHead` count — the third instance of this identical bug shape in this phase, on the same identifier `23-06` already flagged. `23-06-SUMMARY.md`'s own "Next Phase Readiness" section named this exact risk: "if a future plan copies this pattern for its own row-count checks, use `/<TagName/g`... not the bare identifier."
- **Independent proof the true column count is 4:** `(s.match(/<LedgerColHead/g)||[]).length` returns exactly `4` (Version, Label, Canary, Created) — confirmed this session.
- **What was NOT done:** the component was not restructured (e.g. self-closing `<LedgerColHead label="…" />`, which would still land on 5, not 4, because of the unavoidable import-statement hit) to chase a number a correctly-written check would never have demanded. It uses the same open/close `<LedgerColHead>` convention already shipped in `AdversaryPanel.tsx` and the Judgement ledger.
- **Files modified:** none — this is a defect in `23-07-PLAN.md`'s own verify text, which this executor does not edit.
- **Verification:** every other assertion in the same verify block passes cleanly when run individually; the corrected-pattern count, the copy-verbatim script, the structural mount script, the wiring gate, and the `npx playwright test` regression suite (45 passed, run twice) all confirm the deliverable is correct.

### Auto-fixed / handled inline

None beyond the design decisions already documented under "Decisions Made" above — no bugs, missing functionality, or blockers required a Rule 1/2/3 fix distinct from those judgment calls.

---

**Total deviations:** 1 reported-not-fixed (verify-script bug, not in this plan's own deliverable code).
**Impact on plan:** Zero impact on the actual deliverable. Every qualitative acceptance criterion (four real columns, real caption, scroll wrapper, no re-sort, dash/zero-percent sentinel handling, four-field comparison with unchanged fields shown, list fields per-entry, no diff/highlighting package, two staged confirmations with locked copy verbatim, per-version busy state, no toast, no local error surface, bounded canary input, one mounted component with no empty state, one page error surface, all three false claims gone) is met and independently proven.

## Issues Encountered

See Deviations above. No blocking issues — every one of this plan's own verify commands printed its expected success token by the end of execution: `PROMPT-PANEL-OK` (minus the one documented script-bug assertion, independently proven correct), `PROMPT-COPY-VERBATIM-OK`, `HONESTY-CHECKS-ALL-GREEN`, `PROMPT-MOUNT-OK`, `DIFF-SCOPE-OK`, and 45/45 pure-layer tests passing both before and after Task 2.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- `PromptVersionPanel.tsx` and `page.tsx`'s mount are both complete and independently verified; `check-ops-room-wiring.mjs --report` shows 10/11 checks `PASS`, with only `bench-traces-wired` correctly `OPEN` for 23-08.
- `npx tsc --noEmit` carries the single pre-existing, unrelated `tests/reduced-motion.spec.ts:18` error only (present since Phase 20, `7f64005`, out of scope, documented in `23-03-SUMMARY.md`/`23-05-SUMMARY.md`/`23-06-SUMMARY.md`/`deferred-items.md`).
- Zero `.py` files touched by this plan's commits (`git diff --stat aa39ac2~1..HEAD -- "*.py"` empty); the Python unit suite was re-run this session and stayed at the documented baseline — **1199 passed, 8 skipped, 0 failed**.
- **Flag for whoever next reads a Ledger-based `<verify>` script in this phase:** this is the third documented instance of the same bare-identifier miscount (`LedgerRowHead` in 23-05, `LedgerColHead` in 23-06, and now `LedgerColHead` again here in 23-07). Any future check copying this shape should use `/<TagName/g` (opening-angle-bracket-prefixed) from the start.
- No blockers for 23-08 — its `files_modified` does not overlap this plan's two files, and this plan's own files are fully committed.

---
*Phase: 23-wire-the-gotham-operations-room-to-the-phase-21-backends-and*
*Plan: 07*
*Completed: 2026-08-03*

## Self-Check: PASSED

- FOUND: `apps/admin/app/agents/[id]/components/PromptVersionPanel.tsx`
- FOUND: `apps/admin/app/agents/[id]/page.tsx` (PromptVersionPanel mounted, verified via structural script)
- FOUND: commit `aa39ac2` (Task 1)
- FOUND: commit `af46949` (Task 2)
