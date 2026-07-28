---
status: deferred
phase: 22-owner-capability-control-pending-confirmation-resolution-clo
source: [22-01-SUMMARY.md, 22-02-SUMMARY.md, 22-03-SUMMARY.md, 22-04-SUMMARY.md, 22-05-SUMMARY.md]
started: 2026-07-28T20:43:36Z
updated: 2026-07-28T21:00:00Z
---

## Current Test

[none remain — items 1-3 each carry a final, dated deferral disposition, item 4 is a
written record. See `## Summary` and `## Gaps` at the foot of this file.]

## Tests

### 1. VER-01 SC2 re-run — a non-technical owner turns on `issue_refund`/`place_order` and completes signup -> deploy -> refund -> order -> approve -> reject, unaided, replacing `19-UAT.md` item 1's `[failed — blocked]` disposition

expected: |
  A person who cannot code completes signup through deployment, turns two
  disabled skills on from the deploy screen with no database action, drives a
  successful refund and a successful Shopify order using only the admin
  console and the widget, and resolves a pending confirmation (approve one,
  reject a second) from the deploy screen's queue. The tester is given no
  briefing beyond `docs/guides/owner-capability-guide.md` and receives no
  narration or assistance from the operator during the run.

  Per-step expected outcomes:
  1. Signup completes with no terminal step.
  2. Turning `issue_refund` on (ceiling 499 cents, rate limit 5/hour): the
     Enabled box is not locked; the tester can turn it on and the caption
     changes to the on-state wording.
  3. Turning `place_order` on (ceiling 20000 cents, rate limit 5/hour): same
     as step 2.
  4. Deploying through the checklist: the checklist passes and the agent goes
     live with no code edit.
  5. Toggling one already-enabled skill off then on again: turning off saves
     immediately; turning on now asks for a confirmation first, because the
     agent is live. The exact confirmation wording the tester saw must be
     recorded verbatim.
  6. A refund conversation at or under R4.99 (499 cents): completes
     deterministically via the Actor's low-value skip, with no approval wait.
     The 499-cent ceiling is imposed by the platform's Actor skip threshold,
     not chosen by the owner — recorded that way, as `19-UAT.md` item 1 does.
  7. A Shopify order conversation: either completes end to end, or reaches
     the awaiting-approval state.
  8. If step 7 reached awaiting-approval — the leg that dead-ended in the
     previous run — the tester finds the request under Pending confirmations,
     reads what it says the agent wants to do, and approves it: the row moves
     to approved, then to an executed outcome, and the order completes.
     Record what the row said at each stage, in the tester's own words.
  9. A second order driven to awaiting-approval and rejected: the row reads
     as rejected and the action never runs.

how: |
  **Operator preconditions — set up beforehand, never part of the tester's
  steps.** Local processes only, per CLAUDE.md rule 9:
  1. Start a local PostgreSQL server.
  2. Start local Redis: `redis-server`
  3. Start the API: `cd apps/api && ./.venv/Scripts/python.exe -m uvicorn app.main:app --reload`
  4. Start the worker: `cd apps/api && ./.venv/Scripts/python.exe -m celery -A app.worker.celery_app worker`
  5. Start the admin console: `cd apps/admin && pnpm dev`
  6. Provision a Shopify test-mode credential per `docs/runbooks/integration-credentials.md`.
  7. Provision a tenant and an agent with every mutating skill at the shipped
     platform default (disabled) — do **not** pre-seed any enabled row;
     turning the two skills on is part of the tester's own run.

  **The only document the tester is handed:** `docs/guides/owner-capability-guide.md`.
  No other briefing, no narration, no assistance during the run.

  **Tester steps 1-9** as listed under `expected:` above, run in order with no
  narration or assistance.

  **Two held-out visual checks** (`22-UI-SPEC.md § UI Considerations`),
  performed by the operator after the tester's run, are recorded separately
  in item 3 below, not here.

  **Three failure rules the operator applies rather than works around,
  carried forward verbatim from `19-UAT.md` item 1:**
  - Any step requiring a terminal, a raw HTTP call, or a code edit **fails
    the criterion** and is recorded as a failure, not routed around.
  - If no genuinely un-briefed non-technical tester is available, the item is
    **deferred** — never satisfied by the operator standing in for the
    tester.
  - If any step's observed behaviour differs from the guide's description,
    record the discrepancy verbatim. The guide being wrong is a finding
    about this phase, not a tester error.

  Transcribe the full result here, including the confirmation wording from
  step 5 and the tester's own words from step 8. Cross-reference:
  `19-UAT.md` item 1 carries the `[failed — blocked]` disposition this run
  replaces.

result: [deferred — 2026-07-28, operator accepted the deferral after reviewing the
checkpoint, for **two independent reasons**, both recorded because a generic
"environment unavailable" would lose real information:

(1) No genuinely un-briefed non-technical tester was available for this run.

(2) The local process stack could not be brought up because **no PostgreSQL
server is installed on this machine**. The `postgresql-x64-17` Windows
service is a stale registration whose `ImagePath` points at
`C:\Program Files\PostgreSQL\17\bin\pg_ctl.exe`, a path that no longer
exists; `C:\Program Files\PostgreSQL\18\` holds only an orphaned `data\`
directory with no `bin\`; none of `psql`, `postgres`, `pg_ctl`, or `initdb`
is on PATH or anywhere under Program Files, LOCALAPPDATA, scoop, or
chocolatey; nothing listens on ports 5432-5435. Confirmed, not assumed:
`redis-server` **is** running (`redis-cli ping` -> `PONG`) and
`ANTHROPIC_API_KEY` **is** present in `apps/api/.env` — neither is a
blocker, stated explicitly so a future reader does not chase the wrong
precondition.

**What remains true despite the deferral:** both of VER-01 SC2's structural
blockers are closed in code and unit-proven. CAP-05's comparator change
landed with a diff-scope gate showing zero sibling-branch touches
(`22-01-SUMMARY.md`). ACT-07's resolver provably references neither
`call_actor_gate` nor any identity-session check, and re-checks the live
envelope rather than the stored snapshot (`22-02-SUMMARY.md`). The claim
commits before dispatch (OD-6, `22-03-SUMMARY.md`). The full unit suite went
1136 -> 1179 passed, 8 skipped, 0 failed, with six guard-removal
demonstrations observed red-then-green across `22-01`/`22-02`/`22-03`. The
admin UI additionally passed an adversarial design review whose four
findings were all fixed (`e5951f5`, `b1e2c22`, `a165a32`, `b66d9d4`). **This
run has never been observed against the shipped build, so this is an
unproven criterion, not a pass and not a repeat of the previous run's
`[failed — blocked]` finding** — the causes that produced that finding are
themselves closed; only the re-run to confirm it end to end is outstanding.

Follow-up: install a local PostgreSQL server, then run the nine-step tester
script in this item's `how:` block exactly as written.]

### 2. ACT-07's live-database gate — the adapter fires exactly once against a real local PostgreSQL database

expected: |
  `apps/api/tests/integration/test_act07_resolve_live.py` asserts against
  real database state that an approved confirmation executes exactly once,
  that a second resolve is refused by the database claim rather than by
  application logic, and that a confirmation approved before an owner
  tightened a ceiling is denied at execution. The adapter is reached through
  the shipped stub short-circuit, so no real provider credential is used and
  no real money moves. A pass recorded without the five figures below is not
  an acceptable record.

how: |
  Preconditions, local processes only: a local PostgreSQL server reachable
  at the test admin-connection defaults, and a local `redis-server` running.
  Do **not** point anything at the configured production control database.

  ```
  cd apps/api
  INTEGRATION_TESTS_ENABLED=1 ./.venv/Scripts/python.exe -m pytest tests/integration/test_act07_resolve_live.py -m integration -q -s
  ```

  Transcribe into this item, from the run itself rather than from the exit
  code:
  - the adapter invocation count observed by the first test (must be exactly 1);
  - the resolved row's resolution value and its resolved-at timestamp;
  - the number of audit rows carrying the human-approval actor decision (must be exactly 1);
  - the second test's outcome for the duplicate resolve;
  - the third test's audit error string, quoted in full.

  If no local PostgreSQL server exists, defer the item and name what was
  checked to establish that, in the same evidentiary style `19-UAT.md` items
  2 and 3 use. State plainly here that the gate was never pointed at the
  configured production control database.

result: [deferred — 2026-07-28, operator accepted the deferral after reviewing the
checkpoint. Same confirmed cause as item 1: **no PostgreSQL server is
installed on this machine** (stale `postgresql-x64-17` service pointing at a
deleted binary; an orphaned `C:\Program Files\PostgreSQL\18\data\` with no
`bin\`; nothing on PATH; nothing listening on 5432-5435). `redis-server` is
running and `ANTHROPIC_API_KEY` is present — neither is the blocker.

**The harness has never been executed against a live database, so this
result is unobserved, not a pass.** `CONTROL_DB_URL` is configured to a live
Neon production endpoint and is explicitly **not** an acceptable substitute:
this gate resolves real `pending_confirmations` rows and executes real
adapter calls (through the stub short-circuit) against whatever database it
is pointed at — running it against production would mutate live tenant
data. The gate was never pointed at the production control database; no run
was attempted against it.

**What remains true despite the deferral:** the module
(`apps/api/tests/integration/test_act07_resolve_live.py`, 586 lines) is
authored, collects cleanly, and all three of its tests are unit-companioned
— `test_confirmation_resolution.py`'s 18 mocked-boundary tests already prove
the resolver's exactly-once logic, its refusal of a re-approved-then-
tightened ceiling, and its source-level absence of `call_actor_gate` and any
identity-session check at the unit level. Only the live-database execution
path itself — the actual adapter invocation count, the actual claimed row,
the actual audit rows — is unproven.

Follow-up: install a local PostgreSQL server, then run
`cd apps/api && INTEGRATION_TESTS_ENABLED=1 ./.venv/Scripts/python.exe -m pytest tests/integration/test_act07_resolve_live.py -m integration -q -s`
and transcribe the five required figures from the run's own output.]

### 3. Two held-out visual checks — free-text wrapping and narrow-viewport overflow on the deploy page's Pending confirmations queue

expected: |
  A pending confirmation whose free-text field (a refund reason, a shipping
  address, or a new field value) is at least 300 characters wraps within its
  column and neither truncates the text nor pushes the page sideways. The
  deploy page at 900, 1280 and 1440 pixels wide collapses the queue to a
  single column at the narrow breakpoint and produces no horizontal overflow
  at any of the three widths (`22-UI-SPEC.md § UI Considerations`).

how: |
  Performed by the operator after the tester's run in item 1:
  - Create a pending confirmation whose free-text field is at least 300
    characters. Confirm the row wraps within its column and neither
    truncates the text nor pushes the page sideways.
  - View the deploy page at 900, 1280 and 1440 pixels wide. Confirm the
    queue collapses to a single column at the narrow breakpoint and produces
    no horizontal overflow at any of the three widths.

  Record the observed behaviour at each of the three viewport widths, and
  the observed wrapping behaviour for the long free-text field.

result: [deferred — 2026-07-28, operator accepted the deferral. Both held-out checks
need the admin console (`apps/admin`, `pnpm dev`) running against a live
backend (API + a real capability/pending-confirmations dataset) so a real
row and a real deploy page can be rendered — the same local-process stack
item 1 could not bring up, for the same confirmed cause (no local
PostgreSQL server). Neither check has been performed against the shipped
build.

Follow-up: once item 1's precondition stack is up, create a pending
confirmation with a free-text field of at least 300 characters and view the
deploy page at 900, 1280 and 1440 pixels wide, exactly as this item's `how:`
block describes.]

### 4. Accepted decisions, residual risks, and deliberate scope exclusions (record only, no run)

expected: |
  This item is a written record, not a test. It carries the six open
  decisions this phase closed, the two accepted residual risks
  (`T-22-ACT-08`, `T-22-ACT-09`), and the one deliberate scope exclusion
  (`OD-2`, no expiry sweep). Each entry states what was decided, why, and
  what would reopen it, following the shape `19-UAT.md` item 4 uses for
  `T-19-04`.

how: |
  No run — this item documents structural facts surfaced during Phase 22
  planning and execution that must not be silently dropped. Content is
  drawn from `22-01-PLAN.md § Open Decisions Resolved`:

  - **OD-1 — Identity verification is NOT re-checked at resolution.**
    Resolved: skip the IDV gate at resolution, explicitly, and accept the
    residual as a named threat (`T-22-ACT-08`, severity medium, disposition
    accept). The customer's identity was verified when the request was
    originally made; it is not re-verified when an approver clicks Approve
    hours later. Bounded by ordering (IDV precedes Actor in
    `_execute_transactional_tool`, so every `require_human` row for an
    IDV-requiring skill was created by a call that held a valid verified
    session at creation time — the gap is staleness, never absence) and by
    window (`_CONFIRM_TTL_HOURS = 24` bounds the staleness window; an
    expired row is refused at resolve). Mechanically enforced: a
    source-absence test asserts `confirmation_resolution.py` never
    references `check_verified_session`. Reopens if a future change
    re-adds an IDV call — or a synthetic session — at resolution time.
  - **OD-2 — Expiry is lazy only; no sweep task ships in this phase.**
    Resolved: enforce expiry lazily inside the resolve route's atomic claim;
    no Celery sweep is built. This is strictly stronger than a sweep for
    correctness (an unresolved expired row never executes by construction,
    even if a sweep never ran) but leaves a `pipeline`-queue sweep marking
    expired-and-unresolved rows `resolution='expired'` as a deliberate,
    named follow-up rather than a silent omission. Deferred to a future
    phase; logged to `STATE.md` by this plan.
  - **OD-3 — Execution outcome is a read-time lookup against
    `tool_calls_audit`; there is no `0020` migration.** Resolved: the GET
    queue route computes `execution_outcome` for `resolution='approved'`
    rows by reading the resolver's own audit row (keyed on
    `actor_decision="approved_by_human"`), rather than adding a new column.
    Chosen because the audit row is already a complete record by
    construction (one row per terminal outcome), the join key is precise,
    and no live control DB exists in this environment to prove a `0020`
    up/down roundtrip. Reopens if a future denormalized column is proposed
    that could drift from the audit trail.
  - **OD-4 — The queue lives on the Deploy page.** Confirmed, not
    overturned: a new "Pending confirmations" section on the existing
    Deploy page, not a new route. The Gotham ops room is a fixed,
    parity-tested six-region contract this phase had no reason to reopen.
  - **OD-5 (planner-surfaced) — the resolver's execution-context shim is an
    explicit parameter contract, not ContextVar seeding.** Resolved:
    `agent_id` and `conn_str` are threaded as explicit function parameters;
    no ContextVars are seeded. Tested by a source-absence assertion that
    `confirmation_resolution.py` references none of `_agent_id_var`,
    `_conn_str_var`, `_conversation_id_var`, `_verified_session_token_var`.
  - **OD-6 (planner-surfaced) — commit the claim BEFORE enqueueing,
    overturning `22-PATTERNS.md`'s ordering.** Resolved: commit first,
    enqueue second. A task dispatched before the claim commits could be
    picked up by a worker reading the row in its pre-claim state. The
    accepted residual is the enqueue itself failing after a durable claim
    (`T-22-ACT-09`, disposition accept) — a dispatch-after-claim window,
    deliberately accepted rather than closed with a two-phase commit or an
    outbox pattern.

  Two accepted residual risks carried forward: `T-22-ACT-08` (identity
  verification not re-checked at resolution, OD-1) and `T-22-ACT-09` (a
  dispatch failing after a durable claim, OD-6). One deliberate scope
  exclusion: `OD-2` (no expiry sweep).

result: [recorded]

## Summary

total: 4
passed: 0
issues: 0
pending: 0
skipped: 0
blocked: 0
deferred: 3

Note: item 4 ("Accepted decisions, residual risks, and deliberate scope
exclusions") is a written record, not a scored test — its own `expected:`
states this explicitly — so it is not counted in the buckets above. The
four items are: 3 deferred (items 1-3), 1 recorded (item 4). 3 + 1 = 4 =
`total`.

## Gaps

- Items 1-3 above (VER-01 SC2 re-run, ACT-07 live-database gate, the two
  held-out visual checks) each require real local infrastructure this
  execution environment does not provide, or a genuinely un-briefed
  non-technical tester who was not available. Confirmed rather than
  assumed: `redis-server` is running and `ANTHROPIC_API_KEY` is present —
  **there is no PostgreSQL server installed on this machine** (a stale
  Windows service registration pointing at a deleted binary, an orphaned
  data directory with no `bin\`, nothing on PATH, nothing listening on
  5432-5435). Deferred by the operator on 2026-07-28 after reviewing the
  checkpoint. Neither the tester run nor the live-database harness has ever
  been executed against the shipped build — both results are unobserved,
  not passes. This mirrors the Phase 13 AWS live-gate deferral
  (`13-08..11`), the Phase 14 live-DB deferral (`14-UAT.md` items 1-3), the
  Phase 15 ACT-06 latency deferral (`15-03-SUMMARY.md`), the Phase 16
  live-Stripe deferral (`16-UAT.md`), and Phase 19's own VER-01 SC3 / AUD-03
  deferrals (`19-UAT.md` items 2-3). All deferred items are recorded in UAT
  files so `/gsd-verify-work` surfaces them.
- Unlike Phase 19's item 1 (recorded **failed**, because its two causes were
  missing product capabilities that provisioning could not close), this
  phase's item 1 is recorded **deferred** — a deliberate distinction. Both
  of the causes Phase 19 found (`validate_tighten_only` rejecting every
  enable transition; `T-19-04`'s unresolved `require_human` branch) are now
  closed in code by CAP-05 (`22-01`) and ACT-07 (`22-02`/`22-03`) and are
  unit-proven. What remains unobserved is narrower and purely environmental:
  a live end-to-end run by an actual tester against a real database, which
  installing PostgreSQL and finding a tester would close without any further
  code change. See `19-UAT.md` item 1's amendment for the full cross-reference.
- CAP-05 and ACT-07 are ticked complete in `REQUIREMENTS.md` on this
  evidence — they shipped, are reachable end-to-end by a non-technical owner
  through the shipped UI with no terminal/curl/SQL, and are unit-proven.
  **VER-01 itself stays unticked**: SC2's disposition changes from
  `[failed — blocked]` to `[deferred — unproven, both causes closed]`, and
  SC3 remains deferred exactly as Phase 19 left it. No requirement in this
  phase is ticked on the strength of a gate that was not observed.
