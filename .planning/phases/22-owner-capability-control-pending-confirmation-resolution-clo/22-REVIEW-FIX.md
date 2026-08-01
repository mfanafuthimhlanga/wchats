---
phase: 22-owner-capability-control-pending-confirmation-resolution-clo
fixed_at: 2026-08-02T00:00:00Z
review_path: .planning/phases/22-owner-capability-control-pending-confirmation-resolution-clo/22-REVIEW.md
iteration: 1
findings_in_scope: 9
fixed: 8
skipped: 1
status: partial
---

# Phase 22: Code Review Fix Report

**Fixed at:** 2026-08-02T00:00:00Z
**Source review:** .planning/phases/22-owner-capability-control-pending-confirmation-resolution-clo/22-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 9 (CR-01, CR-02, WR-01 through WR-07)
- Fixed: 8
- Skipped: 1 (WR-04 — no code change; needs a PostgreSQL server installed, which this machine lacks)

All work was done in an isolated git worktree/branch and fast-forwarded onto the branch this
agent started from. Full unit suite before any fix: **1179 passed, 8 skipped, 0 failed**. Full
unit suite after all fixes: **1191 passed, 8 skipped, 0 failed** (12 new tests, all additive —
no baseline test was deleted or weakened).

## Fixed Issues

### CR-01: Idempotency reservation lease (120s) far shorter than Celery's redelivery window (3600s)

**Files modified:** `apps/api/app/services/transactional/idempotency.py`,
`apps/api/app/services/transactional/tools.py`,
`apps/api/app/services/transactional/confirmation_resolution.py`,
`apps/api/tests/unit/test_tool_idempotency.py`,
`apps/api/tests/unit/test_transactional_tools.py`,
`apps/api/tests/unit/test_confirmation_resolution.py`
**Commit:** `8c1ea19`

**Applied fix:** Of the three options the finding named — (a) raise the 120s lease above the
broker's window, (b) lower the broker's `visibility_timeout`, (c) make the reclaim path refuse
to reclaim when an adapter call may already have happened — I took (c), the option that cannot
cause a duplicate irreversible provider call.

A reservation is now flipped from `status='pending'` to `status='in_flight'`
(`mark_reservation_in_flight`, called immediately before the adapter call in the shared
`_execute_adapter_and_audit` helper, so both the live-turn dispatcher and the confirmation
resolver get the fix from one implementation). `reserve_idempotency`'s stale-reclaim logic now
branches on this: a stale `'pending'` row (adapter never touched) is still safely reclaimed
exactly as before; a stale `'in_flight'` row (adapter call may already have run) is **never**
auto-reclaimed, regardless of staleness — it returns a new `Reservation(state="unknown")`
instead. Both call sites (the live-turn dispatcher and the resolver) deny and audit this state
(`error="idempotency.stranded_reservation"`), never execute, and never release the reservation
(releasing it would let a fresh reserve reclaim and re-execute a key that may already have hit
the adapter once). `finalize_idempotency`/`release_idempotency`'s WHERE clauses were widened to
also match `'in_flight'` so the normal success/synchronous-failure paths still work.

**What this costs (stated plainly, not hidden):** options (a) and (b) were rejected because both
are broker/queue-wide settings — `celery_app.py`'s `broker_transport_options` (including
`visibility_timeout`) apply to the `pipeline` queue too, not just this one task — and raising the
lease also means a genuinely stuck reservation on the **live-turn dispatcher path** (not just the
resolver) stays stuck longer, blocking a legitimate retry. Option (c) avoids both of those costs,
but has its own: a worker that crashes mid-adapter-call now leaves the row permanently in
`"unknown"` state, requiring a human to manually reconcile what actually happened before anything
retries. This is a real, deliberate trade — favoring "occasionally needs a human" over "might
silently double-charge a customer."

**Verified:** 157/157 targeted unit tests pass immediately after this commit (idempotency,
transactional tools, confirmation resolution, pending-confirmation routes); full suite unaffected.
New tests cover: `mark_reservation_in_flight`'s SQL shape, the `in_flight` recent→`in_progress`
and stale→`unknown` reserve_idempotency branches, that a stale `'pending'` row is still reclaimed
(the in_flight protection doesn't over-reach), and that both the dispatcher and the resolver deny
+ audit + never execute on `"unknown"`.

### WR-01: `require_human` write path's duplicate-suppression comment was false

**Files modified:** `apps/api/app/services/transactional/tools.py`,
`apps/api/tests/unit/test_transactional_tools.py`
**Commit:** `77b1562`

**Applied fix:** `uq_pending_confirmations_unresolved` (migration 0016) is keyed on
`arguments->>'action_reference'`, which a `require_human` row never has — so it never dedupes
this write path, contrary to the code's own comment. Per the finding's Fix section
("extend the index... **or** add an application-level pre-insert check"), and since extending the
index requires a migration (`apps/api/alembic/` is explicitly byte-unchanged this phase per the
verification constraints), I added the no-migration alternative: a `SELECT`-before-`INSERT`
existence check scoped to `(agent_id, skill, arguments->>'idempotency_key', resolved_at IS NULL)`.
A hit reuses the existing unresolved confirmation id instead of inserting a duplicate. This is
best-effort, not atomic (documented in the code as a real, narrower race window than the
DB-enforced index's own guarantee) — a genuinely simultaneous pair of `require_human` calls for
the same key could both pass the check before either commits. It bounds the common case (retries)
without a schema change. Also corrected the branch's own comment, which repeated the same false
"silent dedup, consistent with confirm_action_tool" claim this finding traces to its root cause.

**Verified:** 158/158 targeted unit tests pass. New test seeds an existing unresolved row and
asserts the second `require_human` call does not insert (`session.add`/`session.commit` never
called) and reuses the existing row's id in its response text.

### CR-02: `confirm_action_tool`-created rows dispatched to a resolver that could never execute them

**Files modified:** `apps/api/app/api/v1/pending_confirmations.py`,
`apps/api/tests/unit/test_pending_confirmation_routes.py`, `apps/admin/app/agents/[id]/deploy/page.tsx`,
`.planning/STATE.md`, `.planning/phases/22-owner-capability-control-pending-confirmation-resolution-clo/22-03-SUMMARY.md`
**Commit:** `d6a56ba`

**Applied fix:** exactly the conservative option the design guidance specified — detect the row
shape and refuse it at approve-time, rather than building an argument-recovery mechanism (an
explicit non-goal). `_is_confirm_action_shaped()` checks for `"action_reference" in arguments` —
an exact, unambiguous discriminator, since no mutating skill's Input model ever defines that
field. When a claimed, approved row's skill is a key of `SKILL_INPUT_MODELS` **and** matches this
shape, the route no longer dispatches it: it writes one `tool_calls_audit` row
(`error="confirmation.incomplete_arguments"`) and raises `HTTPException(422)` with an actionable
message. The claim itself still commits — the approver's decision is durably recorded — only the
Celery dispatch is skipped. Outcome remains fail-closed throughout: no adapter call ever happens
with incomplete arguments, exactly as it was before this fix (the resolver's own re-validation
already prevented that) — what changed is that the failure is now immediate and explained instead
of a silent, opaque "invalid" outcome discovered later.

The admin queue's `resolveConfirmation.onError` handler only surfaced an inline note for the 409
"someone already resolved this" case — any other error (including this new 422) was silently
swallowed (no toast, no note, `savingConfirmations` just cleared with nothing to show for it),
which would have made the new "clear, actionable message" invisible to the approver. Generalized
the condition to show the note for any error carrying a `confirmationId`, reusing the existing
mechanism rather than building a new one.

**Documentation (same commit, per the explicit exception in the task instructions):** `STATE.md`
and `22-03-SUMMARY.md` both recorded "a `confirm_action` row resolves but never dispatches" as if
it covered every row `confirm_action_tool` produces. False — only a row whose *literal* `skill`
field is the string `"confirm_action"` was excluded by the old dispatch condition, and no code
path ever writes that literal value (`confirm_action_tool` always stores the *target* skill).
Corrected both documents in this commit, following this project's 22-05 precedent for landing a
behavior-falsifying doc correction alongside the code change that falsifies it.

**IN-04 (Info, out of scope but directly connected):** the tests this finding cited
(`test_confirmation_resolution.py::test_confirm_action_skill_is_not_executable`,
`test_pending_confirmation_routes.py::test_confirm_action_row_resolves_without_enqueue`) both use
`skill="confirm_action"` **literally** — a shape that is unreachable in production, but is a
*different* shape from the one CR-02 fixes (a row whose `skill` IS a real mutating skill). I
traced both: neither test's assertion became false as a result of this fix (the literal-skill
guard in `execute_approved_confirmation` and the `skill in SKILL_INPUT_MODELS` check in the route
are both untouched), so **no test update was required** — this fix's own new route test
(`test_confirm_action_shaped_mutating_row_refused_with_422_never_enqueued`) is the test the
CR-02 finding's own Fix section explicitly asked for and IN-04 says the suite was missing.

**Verified:** 18/18 → 19/19 `test_pending_confirmation_routes.py` tests pass, including the new
row-shape test seeding exactly what `confirm_action_tool` produces
(`skill="issue_refund"`, `arguments={"action_reference": "ref-1"}`). Frontend: `npx tsc --noEmit`
in `apps/admin` shows no new errors — the one pre-existing error
(`tests/reduced-motion.spec.ts`) is confirmed present on `main` before this phase's changes too.

### WR-02: The approver's queue never polled for the real execution outcome

**Files modified:** `apps/admin/app/agents/[id]/deploy/page.tsx`
**Commit:** `5420eb5`

**Applied fix:** added a `refetchInterval` to `pendingConfirmationsQuery`, mirroring the existing
conditional-refetch pattern the checklist-runs query already uses. Polls every 3s only while at
least one row is `resolution === 'approved'` with `execution_outcome === null`, so it stops
polling once every in-flight approval has settled.

**Verified:** `npx tsc --noEmit` shows no new errors (same pre-existing, unrelated error as
before).

### WR-03: Unguarded Celery dispatch failure after a committed approval

**Files modified:** `apps/api/app/api/v1/pending_confirmations.py`,
`apps/api/tests/unit/test_pending_confirmation_routes.py`
**Commit:** `b7708d0`

**Applied fix:** wrapped `.delay()` in try/except. On failure: log at ERROR (distinct from the
normal `.ok` info log), and write one `tool_calls_audit` row with
`actor_decision="approved_by_human"` — the same discriminator the resolver itself uses on a real
execution — and `error="confirmation.dispatch_failed:{exc}"`. This makes the failure surface
through the **existing** OD-3 `_execution_outcome_for` lookup the GET queue already reads
("Not executed" instead of an indefinite "Awaiting execution"), reusing established
infrastructure rather than adding a new mechanism.

**Residual, stated honestly, not hidden:** the retry-time 409 response text itself is unchanged
("Someone already resolved this request"). The 409 branch has no way to know *why* the row is
already resolved without a second lookup keyed off the confirmation id, which `tool_calls_audit`
does not carry (OD-3's own no-new-column constraint). Disambiguating that specific message would
need its own design pass; this fix closes the harm the finding actually names — no
operator-visible signal — not the retry response's wording.

**Verified:** 19/19 tests pass, including a new test asserting a `.delay()` `RuntimeError` does
not 500 (still 200, claim already committed) and writes the expected audit row.

### WR-05: `capability_service.py:311`'s comment was false as written

**Files modified:** `apps/api/app/services/capability_service.py`
**Commit:** `5f9d623`

**Applied fix:** reworded "no code path in `apps/api/app/` ever set `enabled=True`" to name the
one exception (`red_team_probe.py`'s in-memory `CLEAN_TENANT_ENVELOPES` fixture, never written to
a real `capability_envelopes` row), matching the reviewer's own confirmed-safe-but-false-as-written
finding exactly. No behavior change — comment only.

**Verified:** 42/42 `test_capability_service.py` + `test_capability_routes.py` tests pass.

### WR-06: The owner-facing docs guide overstated the "runs once" guarantee

**Files modified:** `docs/guides/owner-capability-guide.md`
**Commit:** `45d5740`

**Applied fix:** per the finding's own instruction ("resolve CR-01 first; if any residual risk
window remains after that fix, describe it here honestly"), CR-01 landed first in this same fix
pass. Added a plain-language paragraph describing the residual crash-recovery edge case (a human
occasionally needs to check a request the system could not safely auto-retry), matching the tone
the guide's own "Rate limit" section already models. Updated the "How this is enforced" bullet
list and References to point at `idempotency.py`'s `mark_reservation_in_flight` /
`reserve_idempotency`, which is what actually makes "runs once" true post-fix.

**Verified:** Tier 3 (no syntax checker for `.md`; re-read only). No test references this file
(grepped `tests/` for `"owner-capability-guide"` — zero hits).

### WR-07: Celery task declared unused `max_retries`/`default_retry_delay`

**Files modified:** `apps/api/app/worker/tasks/runtime/confirmations.py`
**Commit:** `48b1db2`

**Applied fix:** took the "remove the misleading kwargs" branch of the finding's either/or Fix,
not the "wire `self.retry()`" branch — distinguishing which failures inside
`execute_approved_confirmation` are safely retriable (a transient DB blip) from which are not (a
business-logic denial that would just be denied identically) is a design decision this fix pass
is not making unilaterally, and is documented as such in the task's new docstring paragraph.
`bind=True` is kept — `self` remains a harmless parameter even with no retry call.

**Verified:** grepped `tests/` for both the literal decorator kwargs and the task's dotted path —
no test asserted `max_retries`/`default_retry_delay` on this specific task (only
`test_pending_confirmation_routes.py` and `test_act07_resolve_live.py` reference it, both via the
dispatch-target patch string). Syntax-checked via `ast.parse`.

## Skipped Issues

### WR-04: SQL-text substring assertions are guard-removal canaries, not correctness proofs — the one test that could prove correctness has never run live

**File:** `apps/api/tests/unit/test_pending_confirmation_routes.py:249-250,457-459,496-498`
(mocked SQL-text assertions); `apps/api/tests/integration/test_act07_resolve_live.py` (the
never-run live-Postgres suite)

**Reason:** The finding's own Fix section states plainly: **"No code change required here — this
is a test-quality/process note."** The action it asks for is running
`test_act07_resolve_live.py` against a real local Postgres (per CLAUDE.md rule 9, no Docker) as a
blocking verification step, not a code change I can make.

Starting a system service is outside a code-fixer's mandate — it is an environment setup /
operator action with side effects beyond this phase's source files. I did not attempt it.

> **Orchestrator correction (2026-08-02).** The rationale originally recorded here stated that
> "PostgreSQL 17 is installed locally (`C:\Program Files\PostgreSQL\17\bin`)" and merely
> **stopped**. That is false, and it was verified false rather than assumed: the
> `postgresql-x64-17` service does report `Status: Stopped`, but its registered `ImagePath` is
> `"C:\Program Files\PostgreSQL\17\bin\pg_ctl.exe"`, and `Test-Path` on that binary returns
> **False** — the directory does not exist. Nothing is listening on 5432-5435. It is a **stale
> service registration for an uninstalled PostgreSQL**, exactly as `22-UAT.md` and
> `22-06-SUMMARY.md` already established on 2026-07-28. Starting the service would fail; there is
> nothing to start. The skip verdict is unchanged and correct — only its stated cause was wrong.
> The closing condition is therefore **install a PostgreSQL server**, not "start the service."

This is documented as a **skip**, not silently dropped: the concurrency/transaction-handling
claims in `22-REVIEW.md`'s own focus areas #2 and #3 remain **unverified by any test run in this
environment**, and CR-01's fix to `idempotency.py` — the exact module this live suite would
exercise — has not itself been proven against a real Postgres. Running
`test_act07_resolve_live.py` against a real local Postgres before this phase is considered fully
verified should be treated as a blocking step for a human operator, per the finding's own words.

---

_Fixed: 2026-08-02T00:00:00Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
