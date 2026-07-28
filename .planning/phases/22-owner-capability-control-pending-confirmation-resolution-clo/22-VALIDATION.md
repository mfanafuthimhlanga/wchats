---
phase: 22
slug: owner-capability-control-pending-confirmation-resolution-close-ver-01-s-two-structural-blockers
# status lifecycle: draft (seeded by plan-phase) → validated (set by validate-phase §6)
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-07-28
---

# Phase 22 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `22-RESEARCH.md` § Validation Architecture and § Security Domain.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + `pytest-asyncio` (`asyncio_mode = "auto"`), already pinned |
| **Config file** | `apps/api/pyproject.toml` `[tool.pytest.ini_options]` — markers `integration` / `e2e` |
| **Quick run command** | `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/unit/<touched_file>.py -x -q` |
| **Full suite command** | `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/unit -q --ignore=tests/unit/test_chunking_service.py --ignore=tests/unit/test_docling_service.py` |
| **Live gate command** | `INTEGRATION_TESTS_ENABLED=1 pytest tests/integration/<file>.py -m integration -q -s` — requires local Postgres + Redis |
| **Estimated runtime** | ~120s full unit suite; route modules ~100s each (cold `import app.main`) |

**Baseline as of 2026-07-28: 1136 passed, 8 skipped, 0 failed.** Any red result
during Phase 22 is attributable to Phase 22 — there is no pre-existing-failure alibi.
Establish this by running the full suite before Wave 0.

### This phase's distinguishing validation problem

Phase 22 is the **inverse** of Phase 19. Phase 19 wrote prose and tests about code
it did not touch; Phase 22 writes production code in the two most
security-sensitive positions in the system — the capability comparator that
decides what an owner may authorize, and a bypass seam inside the mutating-tool
dispatcher. Two consequences follow.

**First, the CAP-05 fix is a deletion, and deletions are the hardest changes to
test.** Removing ~6 lines from `validate_tighten_only`'s `enabled` branch is
trivially easy to over-apply: the same function guards five other fields, and a
slip that widens `max_amount_cents` or `actor_mode` would hand an owner authority
the platform never intended, silently, with every existing test still green
because those tests assert the *rejection* paths that would still exist. The
validation obligation is therefore **not** "prove enabling works" — that is the
easy half. It is "prove the other five dimensions are bit-for-bit as strict as
they were," which requires the full existing comparator suite to run unmodified
except for the single `[enabled]` case.

**Second, ACT-07's correctness claim is mostly about what must NOT happen.**
The adapter must fire exactly once on approval, never on rejection, never after
expiry, never twice under concurrent resolve, and never against an envelope the
owner has since tightened. Tests that assert presence are cheap; the load-bearing
tests here assert absence, and absence tests are the ones that silently pass when
mis-wired. Every "never" below needs a test that has been demonstrated to fail
when the guard is removed — not merely a test that passes today.

### Genuine environment constraints (these are real)

| Constraint | Effect on validation |
|---|---|
| **No PostgreSQL server exists on this machine**; `CONTROL_DB_URL` is a live Neon **production** endpoint | Every DB-backed proof is an `autonomous:false` operator gate. Do not propose running anything against `CONTROL_DB_URL` — it holds real data, and a resolve-route test executes adapter calls. Mirrors the Phase 13/15/16/17/18/19 deferral pattern. |
| `tests/unit/test_chunking_service.py` + `test_docling_service.py` cannot collect — `docling`/`docling_core` absent | Both stay `--ignore`d in every full-suite command. Untouched by Phase 22. |
| `EMBEDDING_PROVIDER` defaults to `"bedrock"` with no AWS access | Only relevant if a test reaches a retrieval path. Pin to `voyage` or mock the boundary. |
| Real `claude_agent_sdk` vs fake-SDK bootstrap is import-order dependent | Any test invoking an `@tool`-decorated function must resolve it via a `_fn()`-style helper (`getattr(t, "handler", t)`), never call the decorated object directly. Relevant to any dispatcher-adjacent test. |
| 4 GB Windows box, no Docker (CLAUDE.md rule 9) | Local processes only in every command, runbook, and docstring. Cold `import app.main` costs ~100s — budget for it, do not read slowness as a hang. |
| Control-DB migration head is Phase 18's `0019` | If ACT-07 adds a column, the new migration is `0020` and its up/down roundtrip is an `autonomous:false` live gate. Research's OD-(c) says no new column is strictly required — if the planner adds one anyway, that decision must be recorded, not incidental. |

### A doc that this phase falsifies

`docs/guides/owner-capability-guide.md` currently states — correctly, as of Phase
19's CR-01 fix — that a disabled skill can never be turned on. **CAP-05 makes that
sentence false.** Correcting it is in scope for this phase, and the correction
must land in the same phase as the behavior change, not after it. A validation
sign-off that ships CAP-05 while leaving the guide asserting the opposite would
reintroduce, in one commit, exactly the defect class Phase 19's code review
raised as Critical.

---

## Sampling Rate

- **After every task commit:** run the specific unit file(s) that task touched.
  For the CAP-05 comparator change, that means the **entire**
  `test_capability_routes.py` class, not just the `enabled` case — the regression
  surface is the point.
- **After every plan wave:** full unit suite, plus the new gated integration
  module collected-but-skipped (`INTEGRATION_TESTS_ENABLED` unset) to prove it
  imports and collects cleanly.
- **Phase gate, before `/gsd-verify-work 22`:** full unit suite green at ≥1136,
  and each `autonomous:false` gate either run for real by the operator and
  recorded in `22-UAT.md`, or explicitly deferred with operator acceptance.
- **Max feedback latency:** 120 seconds (full unit suite).

---

## Per-Task Verification Map

Task IDs are assigned by the planner; this map binds each requirement to its
verifying command and its threat reference. The planner MUST carry every row into
PLAN.md task `<verify>` blocks and fill the Task ID / Plan / Wave columns.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 22-01-T2 | 22-01 | 1 | CAP-05 | — | `enabled: False → True` PATCH returns 200 and commits | unit | `pytest tests/unit/test_capability_routes.py -k enabled -x` | ✅ extend | ⬜ pending |
| 22-01-T2 | 22-01 | 1 | CAP-05 | T-22-CAP-01 | **Every other field's rejection is unchanged** — rate limit, `max_amount_cents`, `requires_confirmation`, `requires_identity_verification`, `actor_mode` all still reject loosening | unit (full class, unmodified) | `pytest tests/unit/test_capability_routes.py -x` | ✅ exists | ⬜ pending |
| 22-01-T2 | 22-01 | 1 | CAP-05 | T-22-CAP-02 | A first write still cannot exceed the platform default on any **bounded** dimension, even though `enabled` is now free | unit | `pytest tests/unit/test_capability_routes.py -k platform_default -x` | ✅ extend | ⬜ pending |
| 22-02-T3 | 22-02 | 1 | ACT-07 | — | Approving a `require_human` row executes the adapter **exactly once** and marks the row resolved | unit (mocked adapter) | `pytest tests/unit/test_confirmation_resolution.py -k approve -x` | ❌ W0 (new) | ⬜ pending |
| 22-03-T3 | 22-03 | 2 | ACT-07 | T-22-ACT-01 | Rejecting **never** calls the adapter | unit (route-level, reassigned — see note below the table) | `pytest tests/unit/test_pending_confirmation_routes.py -k reject_never -x` | ❌ W0 (new) | ⬜ pending |
| 22-03-T3 | 22-03 | 2 | ACT-07 | T-22-ACT-02 | An expired unresolved row **cannot** be approved after `expires_at` | unit (route-level, reassigned — see note below the table) | `pytest tests/unit/test_pending_confirmation_routes.py -k expired -x` | ❌ W0 (new) | ⬜ pending |
| 22-03-T3 | 22-03 | 2 | ACT-07 | T-22-ACT-03 | Concurrent resolve yields **one** adapter call — atomic `UPDATE ... WHERE resolved_at IS NULL ... RETURNING` claim | unit (route-level, reassigned — see note below the table) | `pytest tests/unit/test_pending_confirmation_routes.py -k second_resolve -x` | ❌ W0 (new) | ⬜ pending |
| 22-02-T3 | 22-02 | 1 | ACT-07 (SC3) | T-22-ACT-04 | A confirmation created before a tightening **cannot** execute against the pre-tightening ceiling — checks re-run against the **live** envelope, never the stored snapshot | unit | `pytest tests/unit/test_confirmation_resolution.py -k tighten -x` | ❌ W0 (new) | ⬜ pending |
| 22-02-T3 | 22-02 | 1 | ACT-07 | T-22-ACT-05 | The resolver **never** calls `call_actor_gate` — the loop this phase exists to avoid | unit (`inspect.getsource` absence, per the 18-05 `derive_blast_radius_warnings` precedent) | `pytest tests/unit/test_confirmation_resolution.py -k actor_gate -x` | ❌ W0 (new) | ⬜ pending |
| 22-02-T3 | 22-02 | 1 | ACT-07 | T-22-ACT-06 | Exactly one `tool_calls_audit` row is written on **every** resolver outcome — success and deny alike (AUD-01 symmetry at a NEW call site) | unit | `pytest tests/unit/test_confirmation_resolution.py -k audit -x` | ❌ W0 (new) | ⬜ pending |
| 22-03-T3 | 22-03 | 2 | ACT-07 | T-22-ACT-07 | Both new routes reject a foreign-agent id with 404 on **both** branches (`_get_owned_agent`, verbatim) | unit (route-level, `ASGITransport`, reassigned — see note below the table) | `pytest tests/unit/test_pending_confirmation_routes.py -k foreign_agent -x` | ❌ W0 (new) | ⬜ pending |
| 22-06-T2 | 22-06 | 5 | ACT-07 | — | Live proof: approval drives a real adapter call against a real control DB | integration, **`autonomous:false`** | `pytest tests/integration/test_act07_resolve_live.py -m integration -q -s` | ❌ W0 (new) | ⬜ pending |
| 22-05-T1 | 22-05 | 4 | CAP-05 + ACT-07 | — | `docs/guides/owner-capability-guide.md` no longer claims a skill cannot be enabled, and documents the approval queue | source-anchored review | grep the guide for the stale locked-enabled wording; diff against `capability_service.py` | ✅ exists (correct today, falsified by CAP-05) | ⬜ pending |
| 22-06-T1 | 22-06 | 5 | VER-01 SC2 | — | Owner enables the two skills through the shipped UI and completes a `require_human` approval end-to-end, without code | manual, `checkpoint:human-verify`, **`autonomous:false`** | scripted runbook re-using `19-UAT.md` item 1's steps, transcribed into `22-UAT.md` | ❌ W0 (new) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

**Reassignment note:** this draft originally routed the reject, expiry, concurrent-resolve, and
ownership assertions (T-22-ACT-01, T-22-ACT-02, T-22-ACT-03, T-22-ACT-07) to
`test_confirmation_resolution.py`, the resolver's own unit module. Plan 22-03 (task 3) instead put
all four in `tests/unit/test_pending_confirmation_routes.py`, because all four are route-level
facts — a 409 status code, a dispatch that did or did not happen, an ownership check that must
precede a body read — and asserting them below the route, against the resolver alone, would not
prove them: the resolver has no route to 409 from, no dispatch call to make or withhold, and no
agent-ownership guard of its own (`_get_owned_agent` lives in the route module). The route module
is authoritative for these four rows; the Automated Command column above reflects the plan as
executed, not the original draft.

**Sampling continuity check:** no three consecutive tasks may lack an automated
`<verify>`. This phase is well-placed for that — nearly every task is
unit-verifiable — so the only run at risk is the tail, where the doc correction
and the two `autonomous:false` gates cluster. Interleave the doc-correction task
with an automated one rather than stacking it against the manual gates.

**Absence tests must be demonstrated, not asserted.** Six of the rows above
(T-22-ACT-01 through T-22-ACT-05, and T-22-CAP-01) prove a *negative*. For each,
the plan must require the executor to confirm the test **fails when the guard is
removed**, the same way Phase 19's WR-03 guard was validated by reintroducing the
defect. A negative test that has never been seen to fail is indistinguishable
from a tautology.

**Guard-removal demonstration inventory.** Six demonstrations ran across this phase's plans, each
observed red-then-green (mutation applied, named test confirmed to fail, file restored from HEAD,
named test confirmed green again) and recorded in that plan's own SUMMARY:

| # | Guard | Plan | Mutation applied | Test(s) confirmed to go red |
|---|-------|------|-------------------|------------------------------|
| 1 | The ceiling guard (T-22-CAP-01) | 22-01 | `validate_tighten_only`'s `if proposed_max > current_max:` replaced with `if False:` | `test_capability_routes.py -k "max_amount or illegal_other_field"` |
| 2 | The resolver's rate-and-constraint call (T-22-ACT-04) | 22-02 | The resolver's `apply_rate_and_constraint_checks` call replaced with a hard-coded no-denial result | `TestLiveEnvelope::test_tightened_ceiling_denies_execution` |
| 3 | The Actor-symbol absence (T-22-ACT-05) | 22-02 | A line referencing `call_actor_gate` appended to the resolver module | `TestResolverAbsence::test_resolver_never_references_call_actor_gate` |
| 4 | The unconditional dispatch guard (T-22-ACT-01/02) | 22-03 | The resolve route's dispatch made unconditional instead of gated on `resolution == 'approved'` | `TestResolveRoute::test_reject_never_enqueues` and `test_expired_row_is_forced_to_expired_and_never_enqueues` |
| 5 | The atomic claim guard (T-22-ACT-03) | 22-03 | `resolved_at IS NULL ` dropped from the claim's `WHERE` clause | `TestResolveRoute::test_second_resolve_returns_409_and_never_enqueues` |
| 6 | The actor-decision predicate (execution-outcome lookup) | 22-03 | The `actor_decision = 'approved_by_human'` predicate dropped from the outcome lookup | `TestExecutionOutcome::test_outcome_ignores_the_original_require_human_audit_row` |

A reader of this document can confirm all six ran without opening 22-01/22-02/22-03 individually —
each plan's own SUMMARY carries the same red-then-green observation as its source of record.

---

## Wave 0 Requirements

- [ ] `apps/api/tests/unit/test_capability_routes.py` — **extend** (exists): flip the
      `[enabled]` parametrize case from reject to allow; add an explicit
      enable-a-disabled-skill case; keep every other case byte-identical
- [ ] `apps/api/tests/unit/test_confirmation_resolution.py` — **new file**, ACT-07
      core logic (approve / reject / expiry / concurrency / tighten / no-actor-gate /
      audit symmetry / IDOR)
- [ ] `apps/api/tests/integration/test_act07_resolve_live.py` — **new file**,
      ACT-07 live-DB proof, `INTEGRATION_TESTS_ENABLED`-gated, `autonomous:false`.
      Copy the ephemeral-DB fixture pattern from `tests/integration/test_red_team_rtx.py`
- [ ] `.planning/phases/22-.../22-UAT.md` — **new file**, VER-01 SC2 re-run
      transcript, following the `19-UAT.md` house format including its
      deferred-disposition shape
- [ ] Framework install: **none** — pytest / pytest-asyncio already pinned.
      `22-RESEARCH.md § Package Legitimacy Audit` proposes zero packages;
      `apps/api/pyproject.toml` must stay byte-unchanged

**The resolver's execution-context shim is a Wave 0 deliverable in its own right.**
Research established that `_execute_transactional_tool` sources `agent_id`,
`conn_str`, `conversation_id`, and `verified_session_token` from `ContextVar`s
populated per agent turn by `build_tool_server()`, and that a resolver runs
entirely outside that lifecycle. Whatever stands in for those values must be a
named, tested artifact — not incidental setup inside a route handler.

**OD-5 closed:** the resolver's execution-context shim resolved to an explicit, keyword-only
parameter contract (`execute_approved_confirmation(*, confirmation_id, agent_id, skill, arguments,
conn_str)`), not ContextVar seeding — a resolver has no per-turn context to seed a ContextVar from
in the first place. `conversation_id` is minted fresh inside the resolver (`uuid4()`), informational
on the audit row only. The named, tested artifact this decision produced is the resolver's
keyword-only signature itself, plus the three `TestResolverAbsence` source-absence assertions
(`test_resolver_never_references_call_actor_gate`, `test_resolver_never_references_identity_verification`,
`test_resolver_reads_no_dispatcher_contextvar`) in `apps/api/tests/unit/test_confirmation_resolution.py`.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Owner enables a skill through the shipped UI | CAP-05 / VER-01 SC2 | The claim is that a **non-technical** person succeeds unaided; automating it asserts the opposite of what is being proven | Hand the tester only `docs/guides/owner-capability-guide.md` (as corrected by this phase). Any step requiring a terminal, curl, or SQL fails the criterion and is recorded as such. |
| Approval queue is comprehensible to a non-developer | ACT-07 | Whether a pending confirmation reads as an actionable decision rather than a log row is a judgement, not an assertion | Confirm the queue states what action is awaiting approval, its amount, who requested it, and when it expires — in business language, before any identifier. Must honour the GOTHAM "Bone on Graphite" contract in `DESIGN.md` (verdict-only colour, no decorative hue). |
| Live adapter call on approval | ACT-07 | Needs a real control DB and a real provider credential | Operator runs the gated integration module; records adapter invocation count (must be exactly 1) and the resolved row state in `22-UAT.md`. |
| ~~Control migration `0020` up/down roundtrip~~ | ~~ACT-07~~ | — | **Struck.** This row's own instruction said to skip it entirely if OD-(c) closed as "no new column" — it did: OD-3 (`22-01-PLAN.md § Open Decisions Resolved`) closed the execution-outcome gap with a read-time lookup against the existing `tool_calls_audit` row instead of a new column, so no `0020` migration ships in this phase and there is nothing to roundtrip. |
| VER-01 SC2 re-run | VER-01 | The disposition being replaced is itself a human observation | Re-run `19-UAT.md` item 1's original script end to end. Replace its `[failed — blocked]` with the observed result and cross-reference from `22-UAT.md`. |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or a named Wave 0 dependency
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all ❌ references above
- [ ] No watch-mode flags
- [ ] Feedback latency < 120s
- [ ] Full unit suite green at ≥1136 passed before Wave 0 begins (baseline lock)
- [ ] **Every absence test (T-22-ACT-01..05, T-22-CAP-01) demonstrated to fail with its guard removed**
- [ ] The full `test_capability_routes.py` class runs unmodified except the single `[enabled]` case
- [ ] The resolver provably never references `call_actor_gate`
- [ ] Exactly one `tool_calls_audit` row on every resolver outcome, deny included
- [ ] `docs/guides/owner-capability-guide.md` corrected in the same phase as CAP-05
- [ ] No new dependency (`apps/api/pyproject.toml` byte-unchanged)
- [ ] Each `autonomous:false` gate either run and recorded, or deferred with explicit operator acceptance in `22-UAT.md` — never silently skipped
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
