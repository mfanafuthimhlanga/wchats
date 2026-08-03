---
phase: 23
slug: wire-the-gotham-operations-room-to-the-phase-21-backends-and
# status lifecycle: draft (seeded by plan-phase) → validated (set by validate-phase §6)
status: draft
nyquist_compliant: true
wave_0_complete: true
created: 2026-08-02
---

# Phase 23 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `23-RESEARCH.md` § Validation Architecture and § Security Domain, and from
> `23-01-PLAN.md § Open Decisions Resolved` (OD-6 in particular) and
> `§ Source Verification Findings`.

**The rule this document is written against.** Phase 20 and Phase 21 each passed their own
verification while five integration defects sat between them. Nothing in this repository could
have caught any of them. So every row below has to answer one question honestly: *could this
gate have failed on the defect it is listed against?* A row that cannot is deleted, not
softened.

---

## Test Infrastructure

Two stacks, plus one package with none.

| Property | Backend (`apps/api`) | Console (`apps/admin`) |
|----------|----------------------|------------------------|
| **Framework** | pytest (existing, in the API virtual environment) | `@playwright/test` 1.61.1 (existing; **no unit framework, and this phase adds none**) |
| **Config file** | `apps/api/pyproject.toml` (byte-unchanged by this phase) | `playwright.config.ts` (shipped e2e, **untouched**) and `playwright.unit.config.ts` (**new, 23-03** — browserless, no dev server) |
| **Quick run command** | `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/unit/<module> -q` | `cd apps/admin && npx playwright test -c playwright.unit.config.ts` |
| **Full suite command** | `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/unit -q --ignore=tests/unit/test_chunking_service.py --ignore=tests/unit/test_docling_service.py` | `cd apps/admin && npx playwright test` (4 specs × 3 viewports) |
| **Estimated runtime** | ~15s targeted, ~3min full | ~3s browserless, ~6-10min full e2e (cold Turbopack on a 4 GB machine) |

**Static gates** (no framework, `node` only, modelled on the shipped `check-no-dusk-tokens.mjs`):

| Gate | Command | Owner |
|------|---------|-------|
| Retired-token gate | `cd apps/admin && node scripts/check-no-dusk-tokens.mjs` | pre-existing (Phase 20) |
| Ops-room reachability + honesty | `cd apps/admin && node scripts/check-ops-room-wiring.mjs` (add `--report` for per-check status) | **new, 23-03** |
| Type check | `cd apps/admin && npx tsc --noEmit` | pre-existing |
| Widget bundle budget | `cd apps/widget && npm run build && node scripts/check-size.mjs` (ceiling 20480 bytes gzipped; measured 8094 at phase start) | pre-existing (UI2-06) |

**`apps/widget` has no test framework at all** — no test script, no runner, no assertion
library. `23-RESEARCH.md` recommends against introducing one for a single feature and this
phase agrees. What backstops it instead, and this is the whole of it: a network-stubbed
assertion over `api.js` (which imports nothing and is therefore importable in bare `node`),
a structural seam check comparing the field name the widget handler reads against the key the
Celery task emits, the backend's own emit tests, the build-time size gate, and one manual row
below. That is genuinely less coverage than the console has, and the manual row says so
rather than implying otherwise.

---

## Sampling Rate

- **After every task commit:** the exact `<automated>` command(s) in that task's own verify
  block. Backend tasks run their targeted pytest module; console tasks run `npx tsc --noEmit`,
  `node scripts/check-no-dusk-tokens.mjs`, the browserless spec, and the wiring gate in
  `--report` mode asserting their own check has flipped.
- **After every wave:** the full suite for whichever stack that wave touched. Wave 1 →
  backend full suite plus the console static gates. Wave 2 → both. Waves 3-5 → console static
  gates plus the browserless spec. Wave 6 → everything.
- **Before `/gsd-verify-work 23`:** the whole sweep in `23-09` Task 3, with every number
  observed and recorded rather than asserted.
- **Max feedback latency:** ~20 seconds for a task commit (targeted pytest or the browserless
  spec plus a type check). The full console e2e suite is the only slow gate and it runs once
  per wave, not per task.

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 23-01-01 | 01 | 1 | WIRE-05 | T-23-GA-01 / T-23-GA-04 | Only the assistant message id crosses to the browser; the user message id never does, and no second id is minted between the row insert and the emit | unit (AST + source shape) | `cd apps/api && ./.venv/Scripts/python.exe -c "<emit-shape gate, 23-01 T1>"` — superseded by an AST-accurate re-check; see reconciliation note below the table | ✅ (`agent.py`) | ✅ green |
| 23-01-02 | 01 | 1 | WIRE-05 | T-23-GA-03 | No patch site can hand a mock object into the emitted payload; the emit field and the correct local are each proven by a mutation observed red | unit | `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/unit/test_agent_task.py -q` | ✅ (repaired, not created — finding F-1: **eight** sites, not seven) | ✅ green |
| 23-02-01 | 02 | 1 | WIRE-04 | T-23-GB-01 / T-23-GB-03 / T-23-GB-05 | Only open findings are returned; severity ranks explicitly so a critical is never buried last; no probe text or connection string is logged | unit (SQL + source shape) | `cd apps/api && ./.venv/Scripts/python.exe -c "<Gap-B shape gate, 23-02 T1>"` | ✅ (`redteam_programme_service.py`) | ✅ green |
| 23-02-02 | 02 | 1 | WIRE-04 | T-23-GB-02 / T-23-GB-04 | A correlation miss still yields a containable finding; a lexical severity sort is proven to break the ordering test | unit | `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/unit/test_redteam_programme.py -q` | ✅ (repaired, not created — finding F-7: three existing tests break and nothing upstream said so) | ✅ green |
| 23-03-01 | 03 | 1 | WIRE-01, WIRE-02, WIRE-04 | T-23-VAL-03 / T-23-VAL-04 | A sentinel never becomes a number; a measured zero never becomes an absence; the gate derivation over an open-findings list is provable before any component exists | unit (browserless) | `cd apps/admin && npx playwright test -c playwright.unit.config.ts` | ❌ **Wave 0 — created by this task** | ✅ green |
| 23-03-02 | 03 | 1 | WIRE-01, WIRE-03 | T-23-VAL-01 / T-23-VAL-02 | The gate cannot be satisfied by a comment and cannot scan itself; it is observed both firing and staying silent | static | `cd apps/admin && node scripts/check-ops-room-wiring.mjs --report` | ❌ **Wave 0 — created by this task** | ✅ green |
| 23-04-01 | 04 | 2 | WIRE-05 | T-23-WF-03 / T-23-WF-05 / T-23-WF-06 | The request body carries exactly the schema's fields and no customer text; a 204 answer is never parsed; a message with no id renders no control | unit (network-stubbed module import) | `cd apps/widget && node --input-type=module -e "<transport gate, 23-04 T1>"` | ❌ **Wave 0 — created by this task** | ✅ green |
| 23-04-02 | 04 | 2 | WIRE-05 | T-23-WF-01 / T-23-WF-02 / T-23-WF-04 | The field the handler reads is character-identical to the key the task emits; at most two submissions per message; the bundle stays under its ceiling | structural + build gate | `cd apps/widget && npm run build && node scripts/check-size.mjs` | ✅ (`check-size.mjs`) | ✅ green |
| 23-05-01 | 05 | 2 | WIRE-01 | T-23-UI-01 / T-23-UI-04 / T-23-UI-06 | Every metric checks its sentinel before formatting; the cost is dollars; no local error surface | static + browserless | `cd apps/admin && npx tsc --noEmit && npx playwright test -c playwright.unit.config.ts` | ❌ W0 (23-03) | ✅ green |
| 23-05-02 | 05 | 2 | WIRE-01, WIRE-03 | T-23-UI-02 / T-23-UI-07 | Two sentinel spellings are checked by two predicates, neither of which recognises the other's literal; one chip, no fourth hue | static + browserless | `cd apps/admin && npx tsc --noEmit && node scripts/check-no-dusk-tokens.mjs && npx playwright test -c playwright.unit.config.ts` — the plan's own row-count assertion undercounted; see reconciliation note below the table | ❌ W0 (23-03) | ✅ green |
| 23-05-03 | 05 | 2 | **WIRE-02 (half 1 — the field is read)** | T-23-UI-03 | The response type declares the ledger as a sibling, the query returns it, and the untracked treatment is gone from the Judgement channel block | static | `cd apps/admin && node scripts/check-ops-room-wiring.mjs --report` (Judgement check flips to PASS) | ❌ W0 (23-03) | ✅ green |
| 23-05-03 | 05 | 2 | **WIRE-02 (half 2 — a zero is a zero)** | T-23-VAL-04 | A literal zero from a real measurement renders as a zero, never as an absence message | unit (browserless) | `cd apps/admin && npx playwright test -c playwright.unit.config.ts` (zero cases) | ❌ W0 (23-03) | ✅ green |
| 23-06-01 | 06 | 3 | WIRE-01, WIRE-03, WIRE-04 | T-23-ADV-02 / T-23-ADV-04 / T-23-ADV-06 / T-23-ADV-07 | Every contain control stages; the panel never fetches the runs endpoint; busy state is per finding; no toast and no optimistic removal | static + copy gate | `cd apps/admin && node -e "<Adversary shape + locked-copy gates, 23-06 T1>"` — the plan's own column-count assertion undercounted; see reconciliation note below the table | ❌ W0 (23-03) | ✅ green |
| 23-06-02 | 06 | 3 | **WIRE-04 (the stale-verdict fix)** | T-23-ADV-01 | The page references neither the per-run blocked flag nor the per-run findings snapshot; the gate input is the live open-findings derivation | static + browserless | `cd apps/admin && node -e "<gate-recompute gate, 23-06 T2>" && npx playwright test -c playwright.unit.config.ts` — the plan's own whole-file future-release check was unscoped; see reconciliation note below the table | ❌ W0 (23-03) | ✅ green |
| 23-07-01 | 07 | 4 | WIRE-01, WIRE-03 | T-23-PRM-01 / T-23-PRM-02 / T-23-PRM-03 / T-23-PRM-07 | Both live actions stage; the "nothing is deleted" clause is verbatim; unchanged fields render; no diff package | static + copy gate | `cd apps/admin && node -e "<prompt shape + locked-copy gates, 23-07 T1>"` — the plan's own column-count assertion undercounted, a third instance; see reconciliation note below the table | ❌ W0 (23-03) | ✅ green |
| 23-07-02 | 07 | 4 | WIRE-03 | — | All three false claims are gone and the phrase behind them appears nowhere under the app directory | static | `cd apps/admin && node scripts/check-ops-room-wiring.mjs --report` (every honesty check PASS) | ❌ W0 (23-03) | ✅ green |
| 23-08-01 | 08 | 5 | WIRE-01 | T-23-BCH-01 / T-23-BCH-03 / T-23-BCH-04 / T-23-BCH-06 / T-23-BCH-07 | All four shortcut guards present; the tally comes from a response; the grade badge is neutral; a polite live region announces every grade | static + copy gate | `cd apps/admin && node -e "<bench shape + locked-copy gates, 23-08 T1>"` | ❌ W0 (23-03) | ✅ green |
| 23-08-02 | 08 | 5 | **WIRE-01 (all six regions)** | — | The standing wiring gate exits zero with no flag, for the first time since it was written | static + rendered | `cd apps/admin && node scripts/check-ops-room-wiring.mjs && npx playwright test tests/overflow.spec.ts` | ❌ W0 (23-03) | ✅ green |
| 23-09-01 | 09 | 6 | WIRE-01..05 | T-23-GATE-02 / T-23-GATE-03 / T-23-GATE-05 | Every design-review finding fixed or declined with a reason; every gate still green after the fixes; pixel findings distinguished from code findings | rendered + static | `cd apps/admin && npx playwright test tests/overflow.spec.ts tests/a11y.spec.ts` | ✅ | ✅ green |
| 23-09-02 | 09 | 6 | WIRE-01..05 | T-23-GATE-04 / T-23-GATE-06 | This document reconciled against what actually ran; no row left naming a gate that could not catch its own defect; both follow-ups recorded | static | `node -e "<contract-filled gate, 23-09 T2>"` (run this session — `VALIDATION-CONTRACT-FILLED-OK rows=21`) | ✅ (this file) | ✅ green |
| 23-09-03 | 09 | 6 | WIRE-01..05 | T-23-GATE-01 / T-23-SC | Every recorded number is observed; the API manifest and both migration trees clean; no dependency added to either front-end package | full sweep | see `23-09` Task 3's four verify commands (run this session — see the observed-sweep table in the Validation Sign-Off section) | ✅ | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

**Why WIRE-02 has two rows.** The defect had two halves and no single gate covers both. Half
one is that the response field was never read and a fixed string sat over it — a static gate
on the Judgement block catches that, and would have caught the original. Half two is the
inverse and is the one that will recur: a real zero rendered as an absence, which is a
pure-function fact and needs the browserless zero cases. A contract listing one row here would
claim coverage it does not have.

**Why the false-claims row includes a demonstration.** `23-03` Task 2 must observe the gate
firing on a literal outside a comment and staying silent on the same literal inside one.
Without the second observation the gate fires on this phase's own explanatory comments about
what it deleted, and the reflex fix for a noisy gate is to weaken it.

**Reconciliation note (23-09 Task 2) — six verify-script defects, all in the plans' own inline
checks, none in the delivered code.** Reading every SUMMARY this phase produced against this
document's own epigraph question — could this gate have failed on the defect it names? —
surfaced six cases where a task's own bespoke `<verify>` command could not have, and each was
independently caught and corrected by its own executor before that task's row could honestly
turn green. Recorded here because a reader of this table alone would not otherwise learn any
of this happened.

- **Docstring-collision false negatives (23-01 Task 1, 23-04 Task 2).** Two of `agent.py`'s own
  pre-existing docstrings contain the literal quoted phrase `"agent.response"` before the real
  `emit(...)` call does, so a naive `source.indexOf('"agent.response"')` anchors on prose and
  never reaches the real payload — the check fails identically whether the implementation is
  correct or broken. Both executors independently substituted an AST walk over the real
  `ast.Call`/`emit(...)` nodes, immune to docstring text, and confirmed the same claim the
  naive script was written to prove (`agent.response` carries four keys including `message_id`;
  `agent.escalated` carries three and never gains one). Neither `agent.py` docstring was edited
  — both are accurate, and out of scope for the plans that found the collision.
- **Bare-identifier row/column-count regexes (23-05 Task 2, 23-06 Task 1, 23-07 Task 1) — the
  same bug, three times.** Each plan's own structural check counted occurrences of a bare
  component name (`LedgerRowHead`, `LedgerColHead`) instead of anchoring on the opening angle
  bracket (`/<LedgerColHead/`). A bare identifier matches the import statement once and then
  matches *both* the opening and closing tag of every real usage, so 12 real readings-ledger
  rows counted as 25, 5 real coverage columns counted as 11, and 4 real prompt-version columns
  counted as 9 — every one of these components was correct and every one of these checks was
  wrong. Each executor proved the true count with the corrected pattern (`/<TagName/g`) plus an
  independent `<tr>`-count or call-count cross-check, and none contorted a correct component to
  satisfy a broken regex. `.continue-here.md`'s own anti-pattern table already names this
  recurrence; this is the fourth confirmation, not a new instance, and no fifth has appeared
  since 23-08.
- **Unscoped whole-file phrase scan (23-06 Task 2).** A `/future release/i` check against the
  entire raw `page.tsx` necessarily fails while any sibling region's own false claim hasn't yet
  been deleted by its own, later plan — at the moment 23-06 ran, 23-07 had not yet landed, so
  the prompt region's claim was still present and the check failed for a reason outside 23-06's
  own scope. The phase's standing gate (`check-ops-room-wiring.mjs`) already scopes each claim
  check to its own literal and returned the correct `PASS` throughout; 23-06's inline duplicate
  was the only thing that fired wrongly, and it fired on a region 23-06's own diff-scope gate
  explicitly forbade it from touching.

None of these six is a defect in delivered code — every one is a defect in a one-off inline
verify script that existed for exactly one task's commit and is not itself a standing gate this
phase carries forward. The properties they were written to prove were all independently proven
by a corrected or substitute check in the same session, observed and recorded in each plan's own
SUMMARY, before that task's commit landed.

---

## Wave 0 Requirements

**This phase's wave 1 is its wave 0** — `23-03` builds the gates before the wiring they gate,
which is why every frontend plan from wave 2 onward has something concrete to assert against
on its first commit.

- [x] `apps/admin/app/agents/[id]/components/opsFormat.ts` — every sentinel check, formatter,
      verdict mapping and gate derivation as an exported pure function (23-03 T1).
- [x] `apps/admin/tests-unit/ops-format.spec.ts` + `apps/admin/playwright.unit.config.ts` —
      browserless assertions on the existing runner, deliberately outside the shipped e2e
      config's test directory so that suite cannot change as a side effect (23-03 T1).
- [x] `apps/admin/scripts/check-ops-room-wiring.mjs` — the standing reachability and honesty
      gate, modelled on `check-no-dusk-tokens.mjs`, red by design until 23-08 lands (23-03 T2).
- [x] `apps/api/tests/unit/test_agent_task.py` — **repaired, not created.** Eight bare patch
      sites need explicit return values (finding F-1; both `23-RESEARCH.md` and `23-PATTERNS.md`
      say seven in prose while listing eight line numbers).
- [x] `apps/api/tests/unit/test_redteam_programme.py` — **repaired, not created.** Three
      existing tests break on a fourth query and one breaks twice, on an exact three-key dict
      equality (finding F-7; **no upstream document flags this at all**).
- [ ] No framework install. Both stacks' runners already exist; `23-RESEARCH.md § Package
      Legitimacy Audit` records the phase as adding zero dependencies, so no legitimacy
      checkpoint is required and both manifests are asserted unchanged phase-wide.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Contain a critical finding and watch the room reopen | WIRE-04 (success criterion 3) | Needs a signed-in Clerk session against a live backend with an agent carrying an open critical finding. **OD-6 declined the demo-mode / token short-circuit that would have automated it**, because it would put an authentication branch into production code to satisfy a test. The derivation half is automated (23-03's gate assertions); this is the round trip only. | Sign in, open the operations room for an agent with an open critical finding. Confirm the gatebar reads shut and the room carries the blocked tint. Click **Contain** on the finding, read the staged question, confirm it names the deployment-block consequence, click **Yes, contain**. Confirm without reloading: the finding leaves the list, the critical severity tile decrements, the gatebar reads open, and the room clears. Then confirm a *second* agent with a blocking checklist recommendation stays shut, proving the combination was not bypassed. |
| Customer rates a reply in the widget | WIRE-05 (success criterion 4) | `apps/widget` has no test framework and this phase deliberately did not add one for a single feature (`23-RESEARCH.md § Wave 0 Gaps`). The transport, the body shape and the seam are automated; the rendered interaction is not. | Load the widget against a running API, send a message, wait for the reply. Confirm the thumbs pair renders below the citations and not on the user's own bubble. Click thumbs down: confirm it fills to the oxblood accent, no toast appears, and the score row fades in. Pick a score. Confirm a `message_feedback` row exists for each request. Then confirm the degrade path by serving a reply whose payload carries no message id and confirming **no control renders at all** — not a disabled one. |
| Long text wraps rather than truncates; populated tables do not overflow | WIRE-01 (`23-UI-SPEC.md` UI Considerations, both 🧪 backstop rows) | The shipped overflow specification checks the three widths against the page **shell**. It cannot reach a twelve-row readings ledger, a coverage table, a long judge rationale or an adversarial transcript, because those need populated data and therefore a session. This row states that gap rather than letting the passing shell check imply coverage it does not have. | At 1440, 1280 and 900 pixels wide, with real data: confirm no horizontal scrollbar on the document body in any region; confirm the readings ledger and the coverage table scroll inside their own wrapper rather than widening the page; confirm a long customer turn, agent turn and judge rationale wrap in the enlarger with nothing clipped; confirm a long probe transcript wraps in a finding row. |

**Environment constraint, stated specifically rather than generally:** rows 1 and 2 need the
local stack up, and `22-06-SUMMARY.md` records that **no PostgreSQL server is installed on
this machine** — the Windows service registration is stale and points at a deleted binary,
nothing is listening on 5432-5435, and the live Neon endpoint in `CONTROL_DB_URL` is
production and is not an acceptable substitute. Redis runs and the API key is present; neither
is the blocker. If these rows are deferred, they are recorded as deferred with that cause and
a date, never silently skipped.

**Reconciled 2026-08-04 (23-09 Task 2):** the constraint above was re-checked, not assumed —
still true; no PostgreSQL server is reachable on this machine in this session either. Rows 1
and 2 remain deferred for that stated cause. Row 3 (long-text wrapping, populated-table
overflow) was independently exercised this phase: `tests/overflow.spec.ts` and
`tests/a11y.spec.ts` both ran this session (66/66 passed, 23-09 Task 1) against the console
**shell** at all three widths, exactly as this row's own text already said the shipped
specification can reach — it still cannot reach a populated readings ledger, coverage table,
or enlarger, for the same session-dependency reason as rows 1 and 2. No row's disposition
changed; all three remain manual-only, deferred with the causes above, dated today.

---

## Deliberate Follow-Ups (not closed by this phase)

| Item | Why it was left | What would close it |
|------|-----------------|---------------------|
| Browser-level render specification with `page.route()` fixture interception | OD-6. It requires an authentication short-circuit in production code plus a fixture layer with zero precedent in this repository. The defects it would uniquely cover are the two manual rows above; the defects it was proposed for (WIRE-01/02/03) are covered more cheaply and more reliably by a static gate and a pure-function gate. | A session-seeding approach that does not put a test branch in production auth — a seeded Clerk test session or a dedicated harness user — then fixture-backed specs for the six regions. |
| A duplicate feedback row per message, and the thumbs-down rate it weights twice | OD-7. `metrics_service.py:92-99` computes that rate over all feedback rows, so a message rated and then scored counts twice. The approved UI-SPEC locks the two-request interaction and a planner does not redesign an approved contract; the correct fix is a uniqueness constraint plus an upsert, which is a migration, and this phase's own out-of-scope line forbids one. The satisfaction average is unaffected (line 95 filters the null-score row). | Tenant migration adding `UNIQUE (message_id)` to `message_feedback`, plus `ON CONFLICT DO UPDATE` in `_insert_message_feedback_sync`. |
| ~~Requirement-identifier collision: this phase's ids are already Phase 16's~~ **CLOSED 2026-08-02** | OD-8 / finding F-3. The planner correctly declined to renumber identifiers two committed documents already used, and logged it. The orchestrator then renamed `INT-01..INT-05` → `WIRE-01..WIRE-05` across every Phase 23 artifact and the Phase 23 sections of ROADMAP/STATE/audit, before execution. `WIRE-` was verified unused repo-wide first; Phase 16's `INT-01..06` were verified intact after. No open risk remains for THIS phase. | — |
| Pre-existing `OPS-01..06` collision (M10/Phase 10 vs Phase 21) | Untouched and still open — a different register, out of scope here, and named in `v1.2-MILESTONE-AUDIT.md` as a standing generator of wrong requirement ticks. | A `REQUIREMENTS.md` correction pass that namespaces one of the two OPS registers. |
| `evals.py`'s response-shape docstring omits the ledger key it returns | Finding F-6. A file no plan in this phase owns, and the out-of-scope line is explicit. Recorded because it is WIRE-02's defect one layer down and plausibly contributed to it: a frontend author reading the route's documentation would never learn the field exists. | One docstring line in `apps/api/app/api/v1/evals.py:120-121`. |
| `eval/page.tsx` has the same unguarded nested-read pattern UI-6 fixed in `page.tsx:560`, at five call sites | Surfaced by 23-09's adversarial review, task 1. `run.aggregate_scores[c.key]`/`s.scores.faithfulness` are read with no optional chaining at `eval/page.tsx:172,184,289,291,385` — the identical defect class as the operations-room bug that blanked all six regions, on a route that renders the same Judgement data one level down. `eval/page.tsx` is not in this task's eight-file scope (`.planning/.../23-09-PLAN.md <scope>` names the six region components, `page.tsx`, and the two widget files only) and this plan does not widen scope to fix it. | Add optional chaining (`res.scores?.faithfulness`, `run.aggregate_scores?.[c.key]`) at the five named lines — the same one-line pattern already applied in `page.tsx`. |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 20s per task commit
- [x] Every row's named gate could have failed on the defect it is listed against — six exceptions found and corrected, recorded in the reconciliation note above (23-09 Task 2)
- [x] `nyquist_compliant: true` set in frontmatter

**Approval: ticked 2026-08-04 (23-09 Task 3), against the observed sweep below — every number is a
transcript, not a claim.**

| Gate | Observed |
|---|---|
| Backend unit suite (`--ignore` chunking/docling) | 1199 passed, 8 skipped, 0 failed (baseline 1191; stable at 1199 since 23-01/23-02 landed their own new tests; no regression) |
| Two targeted backend modules (`test_agent_task.py`, `test_redteam_programme.py`) | 30 passed |
| Console type check (`npx tsc --noEmit`) | 1 pre-existing error only (`tests/reduced-motion.spec.ts:18`, unrelated, present since Phase 20 commit `7f64005`), 0 new |
| Retired-token gate | PASS — no retired dusk/skyline/amber-console markers |
| Standing wiring gate, no flag | PASS — 11/11, exits 0 |
| Browserless pure-function spec | 45 passed |
| Full shipped e2e suite, all 4 specs, 3 viewports | 113 passed, 0 failed |
| Widget build + size gate | 8968 / 20480 bytes gzipped |
| API manifest (`apps/api/pyproject.toml`) | byte-unchanged |
| Both migration trees (`alembic`, `alembic_tenant`) | clean, `git status --porcelain` empty |
| Dependency diff, both front-end packages, whole phase | unchanged, compared against `5bd9e9e` (the commit before Phase 23 wave 1 began, not a fixed relative offset) |
