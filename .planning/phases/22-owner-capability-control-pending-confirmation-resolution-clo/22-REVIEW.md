---
phase: 22-owner-capability-control-pending-confirmation-resolution-clo
reviewed: 2026-08-01T00:00:00Z
depth: standard
files_reviewed: 17
files_reviewed_list:
  - apps/admin/app/agents/[id]/deploy/page.tsx
  - apps/api/app/api/v1/pending_confirmations.py
  - apps/api/app/main.py
  - apps/api/app/schemas/pending_confirmation.py
  - apps/api/app/services/capability_service.py
  - apps/api/app/services/transactional/confirmation_resolution.py
  - apps/api/app/services/transactional/schemas.py
  - apps/api/app/services/transactional/tools.py
  - apps/api/app/worker/celery_app.py
  - apps/api/app/worker/tasks/runtime/confirmations.py
  - apps/api/tests/integration/test_act07_resolve_live.py
  - apps/api/tests/unit/test_capability_routes.py
  - apps/api/tests/unit/test_capability_service.py
  - apps/api/tests/unit/test_confirmation_resolution.py
  - apps/api/tests/unit/test_pending_confirmation_routes.py
  - apps/api/tests/unit/test_transactional_tools.py
  - docs/guides/owner-capability-guide.md
findings:
  critical: 2
  warning: 7
  info: 4
  total: 13
status: issues_found
---

# Phase 22: Code Review Report

**Reviewed:** 2026-08-01T00:00:00Z
**Depth:** standard
**Files Reviewed:** 17
**Status:** issues_found

## Summary

Phase 22 closes CAP-05 (the `enabled` tighten-only lockout) cleanly — that fix is small, well-tested,
and correct. ACT-07's resolution path (`confirmation_resolution.py`, the two `pending_confirmations`
routes, and the Celery task) is careful about the things its own docstrings worry about: the ordering
of capability/idempotency/rate re-checks against the live envelope is correct, the atomic claim's SQL
is sound on inspection, IDOR is handled identically to existing precedent, and the admin UI's
`idempotency_key` exclusion (the known-lead PII concern) is real and effective — I traced every
consumption site of `row.arguments` and found exactly one, correctly filtered. That specific known
lead is **refuted**: no code path in `deploy/page.tsx` prints a row's `idempotency_key` or dumps
`arguments` wholesale.

But two things this phase's own stated design goals depend on do not hold under inspection:

1. The resolver's crash-safety argument ("a Celery redelivery finds replay/in_progress, never
   executes twice") is only true if task redelivery happens within the idempotency reservation's
   120-second lease. Nothing in this codebase keeps that promise — the Celery/Redis broker
   configuration this same phase's files touch (`celery_app.py`) sets `visibility_timeout=3600`,
   30x longer than the lease. A worker crash between a successful (irreversible) provider call and
   the audit/finalize write that follows it can result in either a permanently stuck reservation or
   a genuine double execution, depending on how fast the broker notices the worker is gone. See CR-01.

2. `confirm_action_tool` — a shipped, agent-callable tool — writes rows this phase's own generic
   resolver cannot execute. Every row it creates has `arguments = {"action_reference": ...}`, but the
   resolver re-validates against the full six-field Input model for whatever skill the row names,
   which always fails. An approver approving one of these rows gets a silent, opaque "invalid"
   outcome, and the queue UI renders "undefined" fields for it. Test coverage for this exact,
   real-world row shape does not exist — the tests that claim to cover it use a row shape
   (`skill="confirm_action"` literally) that cannot occur in production. See CR-02.

Six further warnings and four info items are below — several concern the missing-dedup path for
`require_human`-created rows (a genuinely false code comment), the queue's lack of any polling so
"Awaiting execution" can go stale indefinitely, an unguarded post-commit dispatch call whose failure
mode is actively disguised as "someone else already handled this," and test-quality notes about which
assertions are load-bearing proofs versus decorative canaries.

The second known lead (the `capability_service.py:311` comment) is **confirmed false as literally
written**, but the underlying safety claim holds: `red_team_probe.py` sets `enabled: True` in an
in-memory Python constant only, and no code under `apps/api/app/` ever issues an `INSERT`/`UPDATE`
against a real `capability_envelopes` row using it. The comment should still be fixed — see WR-05.

## Critical Issues

### CR-01: Idempotency reservation lease (120s) is far shorter than the Celery redelivery window (3600s) — a worker crash mid-execution can double-charge or silently strand a real provider call

**File:** `apps/api/app/services/transactional/idempotency.py:92` (`_RESERVATION_LEASE_SECONDS: int = 120`)
**Also:** `apps/api/app/worker/celery_app.py:178` (`"visibility_timeout": 3600`), `apps/api/app/services/transactional/tools.py:181-235` (`_execute_adapter_and_audit`: adapter call at 182, `write_audit_row` at 222, `finalize_idempotency` at 235 — three separate steps, not atomic), `apps/api/app/worker/tasks/runtime/confirmations.py:17-23` (the docstring's safety claim) and `:47-54` (`max_retries=2, default_retry_delay=5` declared but never invoked via `self.retry()` anywhere in the task body — see WR-07)

**Issue:** `execute_approved_confirmation`'s idempotency reservation (`reserve_idempotency`) treats a
`pending` row as reclaimable — i.e. safe to re-execute the adapter against — once it is older than
120 seconds (`_RESERVATION_LEASE_SECONDS`). This module's own docstring, and the worker task's
docstring, assert this makes redelivery-after-crash safe: "a Celery redelivery of the resolve task
must find replay/in_progress, not execute twice." That is only true if redelivery happens within 120
seconds of the original reservation. Nothing enforces that. `task_acks_late=True` +
`task_reject_on_worker_lost=True` (celery_app.py:146-150) means a message is only guaranteed to
eventually come back to a live worker — the *when* is governed by the broker. In the fast path
(Celery detects the connection to a dying worker drop), redelivery may be quick. In the slow path
(a network partition, an orchestrator killing the container without a clean disconnect, or any
scenario the broker can't distinguish from "still working"), redelivery is bounded only by
`visibility_timeout=3600` — 30x the reservation lease.

**Concrete failure scenario:** A worker calls `getattr(adapter, adapter_method)(validated, agent_id)`
(tools.py:182) for `issue_refund`. The provider (Stripe/Shopify/etc.) processes the refund — real,
irreversible money movement — and returns success. Before `write_audit_row` (tools.py:222) or
`finalize_idempotency` (tools.py:235) run, the worker process is killed (OOM on the project's stated
4GB dev machine, a Fargate task eviction, etc.). The `tool_idempotency_keys` row is left in `pending`
status forever from this worker's perspective. Redelivery happens ~3600 seconds later (slow path).
`reserve_idempotency` finds the row `pending` and *stale* (>120s) and reclaims it, returning
`"reserved"` — the resolver calls the adapter **again**, issuing a second, real refund for the same
customer request. Alternatively, if redelivery happens promptly (<120s), `reserve_idempotency`
returns `"in_progress"` and the resolver exits without executing — but now the reservation is
permanently `pending` (nothing will ever call this confirmation again, since the
`pending_confirmations` row is already `resolved_at`-claimed and the route refuses a second resolve
with 409), so the real-world outcome of the original adapter call — which may itself have already
succeeded — can never be confirmed or reconciled by this system. Either branch is a genuine
correctness failure the review's own focus area (#2, concurrency/idempotency) asks about directly.

Note also that `max_retries=2` / `default_retry_delay=5` on the task decorator (confirmations.py:50-51)
imply a bounded, fast Celery-level retry, but no code path in the task body ever calls `self.retry()`
and the decorator carries no `autoretry_for=`. These settings do nothing for this task today (see
WR-07) — they do not provide the fast, bounded retry the docstring's safety argument would need.

**Fix:** Either (a) make the reservation lease safely exceed the broker's actual redelivery window
(or make the broker's redelivery window provably shorter than the lease — e.g. a dedicated,
short-`visibility_timeout` queue for this one task), or (b) record an unambiguous "adapter call
attempted, outcome unknown" marker *before* calling the adapter (not after), so a reclaim after a
crash can detect "we don't know if this ran" and refuse to blindly re-execute, surfacing the row for
manual reconciliation instead of either double-executing or silently stranding it. At minimum, either
wire `self.retry()` (with `autoretry_for`) so the retry story matches the declared `max_retries`, or
remove the now-misleading `max_retries=2, default_retry_delay=5` from the task decorator.

### CR-02: `confirm_action_tool`-created `pending_confirmations` rows can never execute through this phase's resolver — a shipped, agent-callable code path silently dead-ends

**File:** `apps/api/app/services/transactional/tools.py:896-905` (`confirm_action_tool` writes `arguments={"action_reference": validated.action_reference}`, with `skill=validated.skill` set to the *target* mutating skill, e.g. `"issue_refund"` — never the literal string `"confirm_action"`)
**Also:** `apps/api/app/api/v1/pending_confirmations.py:303` (`claimed["skill"] in SKILL_INPUT_MODELS` — true for `"issue_refund"`, so the row IS dispatched), `apps/api/app/services/transactional/confirmation_resolution.py:197-219` (Pydantic re-validation against the target skill's full Input model), `apps/admin/app/agents/[id]/deploy/page.tsx:324-390` (`CONFIRMATION_HEADLINES` templates read fields like `a.order_id`/`a.refund_amount_cents` that do not exist on this row's `arguments`)

**Issue:** There are two distinct writers of `pending_confirmations` rows: the Actor gate's
`require_human` verdict (tools.py:571-627, which stores the *full* validated tool arguments — e.g.
`idempotency_key`, `order_id`, `refund_amount_cents`, `reason` for `issue_refund`), and
`confirm_action_tool` (tools.py:812-968, an agent-callable tool in its own right — confirmed wired
into `build_tool_server`/allowed_tools by `test_agent_py_has_all_7_new_allowed_tools`), which stores
*only* `{"action_reference": <string>}`. Phase 22's resolver and resolve route treat both row shapes
identically: any approved row whose `skill` is a key of `SKILL_INPUT_MODELS` gets dispatched to
`resolve_approved_confirmation`, which re-validates `arguments` against that skill's full Input model.

**Concrete failure scenario:** An agent calls `confirm_action(skill="issue_refund",
action_reference="ref-1")`. A `pending_confirmations` row is created with `skill="issue_refund"`,
`arguments={"action_reference": "ref-1"}`. An approver sees this row in the queue, where it renders
via the `issue_refund` headline template (page.tsx:346-358) reading `a.refund_amount_cents` (absent →
"amount unavailable") and `a.order_id` (absent → literally the string `#undefined`). The approver
approves it anyway (or the amount-unreadable gate blocks Approve entirely — see IN-02). If approved,
the route dispatches the Celery task (since `"issue_refund" ∈ SKILL_INPUT_MODELS`).
`execute_approved_confirmation` calls `IssueRefundInput(**{"action_reference": "ref-1"})`, which
raises `ValidationError` (missing `idempotency_key`, `order_id`, `refund_amount_cents`, `reason`) →
outcome `"invalid"`, audit error `"confirmation.arguments_invalid"`. The customer's request — which
the agent explicitly and correctly flagged as needing human approval — silently never executes, and
the only trace is an opaque error code with no path to recovery through this UI.

**Test-coverage gap:** `test_confirmation_resolution.py:369-389`
(`TestNullTolerance::test_confirm_action_skill_is_not_executable`) and
`test_pending_confirmation_routes.py:296-310`
(`TestResolveRoute::test_confirm_action_row_resolves_without_enqueue`) both exercise
`skill="confirm_action"` **literally** as the row's `skill` field. That shape cannot occur in
production — `confirm_action_tool` always stores the *target* skill name in `skill`, never the
literal string `"confirm_action"` (tools.py:899). Both tests pass, but they test a scenario that
never happens, giving false confidence that this path is handled.

**Fix:** Either give `confirm_action`-originated rows a distinct resolution path that recovers the
full original arguments (e.g. by looking up the originating `require_human` audit row or tool call by
`action_reference`/`idempotency_key` before dispatching), or detect this row shape explicitly at
approve-time and reject it with a clear, actionable message instead of routing it through the generic
resolver as if it carried complete arguments. Add a test that seeds a row exactly the way
`confirm_action_tool` produces it (`skill` = a real mutating skill, `arguments` = `{"action_reference":
...}` only) and asserts the actual (current or fixed) behavior.

## Warnings

### WR-01: The `require_human` write path's duplicate-suppression comment is false — the unique index never applies to it

**File:** `apps/api/app/services/transactional/tools.py:590-601` (comment: "a duplicate require_human for the same (agent_id, skill, action) already has an unresolved pending row. Silent dedup — consistent with confirm_action_tool (T-14-08-05)")
**Also:** `apps/api/app/models/pending_confirmation.py:45-57` (repeats the same claim), `apps/api/alembic/versions/0016_pending_confirmations_dedup_index.py:15-24,74-78` (`uq_pending_confirmations_unresolved` is built on `(agent_id, skill, (arguments->>'action_reference'))`)

**Issue:** The `require_human` branch stores `arguments=raw_args` — the mutating tool's raw validated
arguments (e.g. `idempotency_key`, `order_id`, `refund_amount_cents`, `reason` for `issue_refund`).
None of the six mutating skills' Input models (`transactional/schemas.py`) has a field named
`action_reference`. So `arguments->>'action_reference'` evaluates to `NULL` for every
`require_human`-created row, and Postgres never treats two `NULL`s as equal for a unique index —
meaning the partial unique index this code's own comment credits with "silent dedup" **never fires**
for this write path. Only `confirm_action_tool`'s rows (which do set `action_reference`) are actually
deduplicated by it.

**Concrete failure scenario:** A customer repeats the same request (or an agent retries the same
turn, or an attacker scripts repeated identical requests) enough times to trigger `require_human`
repeatedly for the same logical action. Each call inserts a brand-new, undeduplicated
`pending_confirmations` row — unbounded growth, directly contradicting this code's own stated purpose
(T-14-08-05: "bound pending_confirmations to one per action"). The underlying idempotency table still
prevents the adapter from actually running twice if the *same* `idempotency_key` is reused (a second
approved duplicate resolves to a `"replay"` outcome with no new audit row — see IN-01), but the
approver's queue itself has no defense against clutter, and nothing in this phase caught or tested
this gap.

**Fix:** Extend the partial unique index (or add an application-level pre-insert check in the
`require_human` branch) to also dedupe on `(agent_id, skill, arguments->>'idempotency_key')` when
`action_reference` is absent, mirroring what `confirm_action_tool` already gets from the existing
index.

### WR-02: The approver's queue never polls for the real execution outcome — "Awaiting execution" can persist indefinitely with no automatic refresh

**File:** `apps/admin/app/agents/[id]/deploy/page.tsx:2116-2129` (`pendingConfirmationsQuery` — no `refetchInterval`)
**Contrast:** `apps/admin/app/agents/[id]/deploy/page.tsx:1999` (the checklist-runs query *does* poll: `refetchInterval: (query) => (query.state.data?.[0]?.status === 'running' ? 3000 : false)`)

**Issue:** After an approver clicks Approve, `resolveConfirmation`'s `onSuccess` invalidates
`['pending-confirmations', id]` exactly once (page.tsx:2183-2189). At that point the Celery task has
almost certainly not finished yet, so the row shows "Approved · Awaiting execution." Nothing then
re-queries this endpoint again unless the browser tab regains focus (TanStack Query's default
`refetchOnWindowFocus`) or the approver takes another action that happens to invalidate the same
query key. The docs guide (`docs/guides/owner-capability-guide.md:250-251`) promises "once you
confirm, the action runs once, in the background, shortly after you approve it" — but the UI gives
the approver no way to *see* that promise fulfilled short of manually reloading or refocusing the
page.

**Fix:** Add a bounded `refetchInterval` (matching the existing pattern at line 1999), e.g. poll every
few seconds while any row in the current data has `resolution === 'approved' && execution_outcome ===
null`.

### WR-03: A Celery dispatch failure after a committed approval is unguarded, and its retry is disguised as benign concurrent resolution

**File:** `apps/api/app/api/v1/pending_confirmations.py:298-307` (`await db.commit()` then an unguarded `resolve_approved_confirmation.delay(...)` — no try/except)
**Also:** `apps/admin/app/agents/[id]/deploy/page.tsx:2167-2180` (409 responses are always treated as benign: `"Someone already resolved this request."`)

**Issue:** This is the phase's own named, accepted residual (T-22-ACT-09: "the dispatch call itself
failing after a durable claim"), but the concrete failure mode it produces for the approver is worse
than the docstring's framing suggests. If `.delay()` raises (broker unreachable, network blip) after
`db.commit()` has already durably recorded `resolution='approved'`, the exception propagates
uncaught out of the route handler and FastAPI returns a 500. If the approver retries — a natural
reaction to a 500 — the atomic claim now finds `resolved_at` already set and returns 409. The UI
(page.tsx:2171) shows "Someone already resolved this request," which reads as "a colleague already
handled it." In reality it was the approver's own first attempt that succeeded at the claim but never
dispatched — the row is now stuck `approved`/unexecuted forever with no operator-visible signal, and
the reassuring 409 message actively discourages the approver from investigating further.

**Fix:** Wrap the `.delay()` call in try/except and record a distinguishable state (or emit an
operator-visible alert/log line distinct from the normal dispatch path) on failure, so a retry-after-
500 doesn't get silently absorbed into the "someone else already resolved it" UX branch.

### WR-04: SQL-text substring assertions in the mocked route tests are guard-removal canaries, not correctness proofs — and the only test that could prove correctness has never run

**File:** `apps/api/tests/unit/test_pending_confirmation_routes.py:249-250` (`"resolved_at IS NULL" in sent_query`), `:457-459` (`"NULLS LAST"`, `"id ASC"`, `"resolved_at DESC"` in the ordering query), `:496-498` (`"actor_decision"`, `"approved_by_human"` in the execution-outcome query)
**Also:** `apps/api/tests/integration/test_act07_resolve_live.py` (module docstring: "This module has never been run live in this environment — there is no local PostgreSQL server on this machine")

**Issue:** These assertions are explicitly and honestly documented by the test file's own docstring
(lines 41-48) as guard-removal canaries against a *literal* source-text regression, not as proof that
the SQL behaves correctly against a real database — a mocked `db.execute()` returns whatever the test
scripts regardless of what the SQL actually says. This is a reasonable, self-aware design choice, but
its counterpart — `test_act07_resolve_live.py`, the one test suite that runs the atomic claim, the
tightened-ceiling-after-approval race, and the double-resolve race against a *real* Postgres — has
never executed in this environment. The review's own focus area #2 (concurrency) and #3 (transaction
handling) are therefore **unverified by any test run**, not merely unverified by this review. The
review team should not read a passing unit-test suite as evidence that the atomic claim, the ordering
guarantee, or the execution-outcome discriminator are correct — only that their SQL text hasn't
accidentally been deleted.

**Fix:** No code change required here — this is a test-quality/process note. Running
`test_act07_resolve_live.py` against a real local Postgres (per CLAUDE.md rule 9, no Docker required)
before this phase is considered verified should be treated as a blocking step, not an optional one.

### WR-05: `capability_service.py:311`'s comment is false as written — `red_team_probe.py` sets `enabled: True` six times

**File:** `apps/api/app/services/capability_service.py:311-313` ("no code path in apps/api/app/ ever set enabled=True")
**Also:** `apps/api/app/services/red_team_probe.py:409,418,427,436,445,454` (`CLEAN_TENANT_ENVELOPES` entries, each `"enabled": True`)

**Issue:** Independently confirmed and refuted as asked: the comment's literal claim is false —
`red_team_probe.py` sets `enabled: True` six times in its `CLEAN_TENANT_ENVELOPES` fixture. Also
independently confirmed: this constant is never written to a real `capability_envelopes` row by any
code under `apps/api/app/` (no `INSERT INTO capability_envelopes` anywhere in the app tree) — it is
consumed only as an in-memory reference value by `red_team_service.py`'s deterministic RTX-02/RTX-03
runners (to read `max_amount_cents`, not to seed a live envelope), and by test fixtures that describe
a separately-seeded ephemeral/demo tenant DB. The underlying safety property (the CAP-05 change did
not newly create a way to flip `enabled=True` in production data) holds, matching the prior audit's
judgment. The comment itself should still be corrected — as written, it would mislead a future reader
performing exactly this kind of trace into believing `enabled=True` is unreachable anywhere in the
codebase, when it is reachable in a Python dict literal a grep for `"enabled": True` will find in
seconds.

**Fix:** Reword to something like "no code path in `apps/api/app/`, other than the red-team fixture
module's in-memory constant, ever writes `enabled=True` to a real `capability_envelopes` row."

### WR-06: The owner-facing docs guide overstates the "runs once" guarantee CR-01 shows is not unconditional

**File:** `docs/guides/owner-capability-guide.md:429-432` ("the background task that actually executes an approved action, once, only after the resolve claim above has committed")

**Issue:** This line is presented to a non-technical business owner as a settled fact about the
system's behavior. Per CR-01, "once" is not unconditionally true — it depends on Celery redelivery
happening within the 120-second idempotency reservation lease, which the shipped broker configuration
(`visibility_timeout=3600`) does not guarantee. This isn't a documentation-only issue (the code should
be fixed per CR-01), but the guide should not assert a stronger guarantee than the code actually
provides to the audience least equipped to notice the gap.

**Fix:** Resolve CR-01 first; if any residual risk window remains after that fix, describe it here
honestly (the guide already models this tone elsewhere, e.g. the rate-limit-floor gap under "Rate
limit").

### WR-07: The Celery task declares `max_retries=2, default_retry_delay=5` that nothing in its body ever uses

**File:** `apps/api/app/worker/tasks/runtime/confirmations.py:47-54`

**Issue:** `resolve_approved_confirmation` is decorated with `max_retries=2, default_retry_delay=5`,
but the task body never calls `self.retry(...)` and the decorator carries no `autoretry_for=`. These
two settings currently do nothing — the task either returns normally or lets an exception propagate
to failure, with no Celery-level retry ever triggered by them. This reads as configuration copied from
a template that intended bounded automatic retries and doesn't actually have them, which is
misleading both to a future maintainer and to the crash-safety argument this module's docstring makes
(see CR-01).

**Fix:** Either wire `self.retry()` for the specific transient-failure cases this task can
distinguish (e.g. a DB connectivity error, as opposed to a business-logic denial), or remove the two
unused kwargs so the decorator doesn't imply retry behavior that isn't there.

## Info

### IN-01: A shared audit row can be attributed to more than one `pending_confirmations` row when WR-01's duplicate rows exist

**File:** `apps/api/app/api/v1/pending_confirmations.py:90-142` (`_execution_outcome_for`)

**Issue:** `_execution_outcome_for` matches purely on `(agent_id, skill, idempotency_key,
actor_decision='approved_by_human')`, not on `confirmation_id`. This is deliberate and documented
(OD-3: no new column). It is also a direct consequence of WR-01: if two undeduplicated
`require_human` rows share the same `idempotency_key` (plausible if an agent retries with the same
key), and both get approved, the second one's resolver call returns `"replay"` and writes no audit
row of its own (`confirmation_resolution.py:259-266`) — so both confirmation rows' queue entries will
report the *first* row's audit outcome as their own "executed" status, which is accurate in effect
(the underlying action did execute) but can read as confusing/duplicated to an approver comparing two
rows that look identical. Not independently actionable beyond fixing WR-01.

### IN-02: `readCents`/`genericArgDetails` don't defensively floor negative numbers, relying entirely on backend validation

**File:** `apps/admin/app/agents/[id]/deploy/page.tsx:310-313` (`readCents`), `:535-545` (`genericArgDetails`)

**Issue:** `readCents` uses `Number.isFinite(n)` to decide "readable," which accepts negative numbers.
The six mutating skills' Pydantic models all enforce `Field(ge=0)` on cents fields, so a negative
value should be unreachable in practice — but the file's own comment at line 303 says `arguments` is
"a JSONB column with no runtime validation on read," which is exactly the posture that would call for
a defensive floor here too, given the surrounding code already goes out of its way to distinguish
"genuinely zero" from "not a number."

### IN-03: `PendingConfirmationResponse.resolution` is typed as a bare `str | None`, inconsistent with the file's own `Literal` convention

**File:** `apps/api/app/schemas/pending_confirmation.py:59`

**Issue:** Every other enum-like field in this file uses a tight `Literal` (`resolution:
Literal["approved", "rejected"]` on the request body at line 40; `execution_outcome:
Literal["executed", "not_executed"] | None` at line 60, three lines below the field in question). The
response's `resolution` field is a bare `str | None` even though the code that produces it (the SQL
`CASE` in `pending_confirmations.py`) only ever writes `'approved'`, `'rejected'`, or `'expired'`.
This is a minor internal inconsistency — Pydantic won't catch an unexpected value here the way it
would for `execution_outcome`.

### IN-04: Tests claiming to cover `confirm_action` rows use a row shape that cannot occur in production

**File:** `apps/api/tests/unit/test_confirmation_resolution.py:369-389`, `apps/api/tests/unit/test_pending_confirmation_routes.py:296-310`

**Issue:** Restated from CR-02's evidence, listed separately here as a standalone test-quality note:
both tests construct a row with `skill="confirm_action"` literally. In production, `confirm_action_tool`
always stores the *target* mutating skill's name in `skill` (tools.py:899) — a row's `skill` field is
never the literal string `"confirm_action"`. These tests pass today and will keep passing regardless
of whether CR-02 is ever fixed, because they don't exercise the real code path.

---

## Orchestrator Verification (2026-08-01)

Both Critical findings were independently re-traced against source by the orchestrator before this
report was committed, because this phase's own checkpoint names "subagent claims taken at face value"
as a blocking anti-pattern. Neither was taken on trust.

**CR-01 — CONFIRMED.** `idempotency.py:92` `_RESERVATION_LEASE_SECONDS = 120`;
`celery_app.py:178` `"visibility_timeout": 3600`, with `task_acks_late=True` (`:146`) and
`task_reject_on_worker_lost=True` (`:150`). The reclaim path at `idempotency.py:250-259` is real: a
`status='pending'` row older than the lease is reclaimed by `UPDATE ... WHERE ... reserved_at <
:threshold RETURNING id`, after which the caller proceeds to the adapter. So a worker that dies
between a successful irreversible provider call and its finalize write leaves a `pending` row that a
redelivery reclaims and re-executes. The 120s lease predates this phase (Phase 14/15 substrate), but
**Phase 22 is what put an irreversible provider call behind a redelivering Celery task** — the
live-turn dispatcher has no redelivery semantics. The exposure is new even though the constant is not.

**CR-02 — CONFIRMED, and it falsifies a documented invariant.** `confirm_action_tool` writes
`skill=validated.skill` (`tools.py:899`) — the *target* mutating skill — with
`arguments={"action_reference": ...}` only (`tools.py:900`). The Actor's `require_human` branch by
contrast writes `arguments=raw_args`, the full validated dict (`tools.py:577`). The reviewer's
two-writer analysis is exactly right.

The consequence not named in the finding: **`STATE.md` records the opposite as a closed design
decision.** It states the task is dispatched "only when the claimed resolution is `approved` AND the
claimed skill is a key of `SKILL_INPUT_MODELS` — a `confirm_action` row resolves but never
dispatches." That invariant does not hold. A `confirm_action`-created row carries `skill="issue_refund"`
(or any other target), which **is** a key of `SKILL_INPUT_MODELS`, so it dispatches. The gate was
written against an assumed row shape that the shipped writer never produces. `STATE.md` and
`22-03-SUMMARY.md` both need correcting alongside the code fix — this is the fourth instance in this
project of a planning document asserting a property the source does not have.

Mitigating, and the reason CR-02 is a dead-end rather than a breach: the failure is **fail-closed**.
Re-validation raises `ValidationError`, one audit row is written with
`error="confirmation.arguments_invalid"`, and no adapter is ever reached
(`confirmation_resolution.py:197-219`). Nothing executes with incomplete arguments. The harm is a
silently stranded customer request plus a misleading UI — `_execution_outcome_for` returns
`(None, None, None)` when `idempotency_key` is absent (`pending_confirmations.py:121-122`), which these
rows never have, so the queue renders "Approved · Awaiting execution" **forever** for an action that
already definitively failed.

**Known lead #1 (PII leakage via `HIDDEN_ARG_KEY`) — REFUTED by the reviewer, consistent with the
orchestrator's own trace during `/gsd-secure-phase`.** `row.arguments` has one consumption site and
`genericArgDetails` (`page.tsx:535-545`) filters the key before render. The security audit's Finding 1
stands unchanged: the mitigation is correct but has **no automated regression gate**, and CR-02 now
gives a second reason to add render-level tests for this file.

**Known lead #2 — CONFIRMED false as written**, filed by the reviewer as WR-05, matching
`22-SECURITY.md § Findings` Finding 2. Benign: the `enabled: True` values never reach a real
`capability_envelopes` row.

---

_Reviewed: 2026-08-01T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
_Critical findings independently verified against source by the orchestrator._
