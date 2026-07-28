---
phase: 22-owner-capability-control-pending-confirmation-resolution-clo
verified: 2026-07-28T00:00:00Z
status: human_needed
score: 2/4 must-haves verified
behavior_unverified: 2
overrides_applied: 0
gaps: []
behavior_unverified_items:
  - truth: "SC2 — an approver can approve or reject a pending_confirmations row; on approval the mutating action executes exactly once (concurrency/atomicity guarantee) and the row is marked resolved; on rejection and on expiry it never executes"
    test: "Run tests/integration/test_act07_resolve_live.py against a real local PostgreSQL server (INTEGRATION_TESTS_ENABLED=1 pytest -m integration -q -s), and/or drive two concurrent resolve requests against a real database row."
    expected: "Exactly one adapter invocation is observed by the test itself (not inferred from a mock), exactly one tool_calls_audit row carries actor_decision='approved_by_human', a second concurrent resolve is refused by the database's own atomic UPDATE ... WHERE resolved_at IS NULL claim (not by application-level state), and a confirmation approved before an owner tightened a ceiling is denied at execution against the live row."
    why_human: "The 'exactly once' guarantee under concurrent resolution is a property of real database transactional atomicity. Every existing test mocks db.execute() and scripts its return value; the test suite's own docstring states 'a mocked DB boundary can't exercise a live claim/predicate.' No test in this codebase — unit or integration — has ever been executed against a real PostgreSQL instance. The authored integration module (tests/integration/test_act07_resolve_live.py, 586 lines) has never run."
  - truth: "SC4 — VER-01 SC2 is re-run by a genuinely un-briefed non-technical tester end to end (signup → enable skills → deploy → refund → order → approve → reject) and its [failed — blocked] disposition in 19-UAT.md is replaced by an observed result"
    test: "Bring up the local process stack (PostgreSQL, Redis, API, Celery worker, admin console) and hand docs/guides/owner-capability-guide.md to a genuinely un-briefed non-technical tester; run the nine-step script in 22-UAT.md item 1's how: block."
    expected: "The tester completes signup through deploy, enables issue_refund and place_order from the deploy screen with no database action, drives a refund and a Shopify order, and resolves one approval and one rejection from the Pending confirmations queue — all unaided."
    why_human: "This is by definition a live human observation with a non-technical tester; it cannot be automated or inferred from source. It has never been performed against the shipped build — 22-UAT.md item 1 records the run as deferred (no PostgreSQL server installed on this machine, no un-briefed tester available), not passed."
human_verification:
  - test: "Run tests/integration/test_act07_resolve_live.py against a real local PostgreSQL server and transcribe the five required figures (adapter invocation count, resolved row state, audit row count, duplicate-resolve outcome, denial error string)."
    expected: "Exactly one adapter call, exactly one audit row with actor_decision='approved_by_human', the duplicate resolve refused at the database level, and the tightened-ceiling test denies execution against the live row — all observed directly, not inferred."
    why_human: "Real-database atomicity and concurrency cannot be proven by a mocked test boundary; this is the load-bearing evidence for SC2's 'executes exactly once' claim and has never been produced."
  - test: "Hand docs/guides/owner-capability-guide.md to a genuinely un-briefed non-technical tester and run the nine-step script in 22-UAT.md item 1's how: block, against a live local stack."
    expected: "Signup through deploy with no code edit; both skills turned on from the deploy screen with no database action; a refund and a Shopify order completed or reaching approval; one approval and one rejection resolved from the queue with the row transitioning correctly."
    why_human: "VER-01 SC2's own criterion text requires this to be an observed live-tester result, not a code-level inference; it has never been performed against the shipped build."
  - test: "View the deploy page's Pending confirmations queue with a free-text field of at least 300 characters, and at 900/1280/1440px viewport widths."
    expected: "The long field wraps within its column with no truncation or page-width overflow; the queue collapses to a single column at the narrow breakpoint with no horizontal overflow at any width."
    why_human: "Visual rendering and responsive layout cannot be verified from source alone; this check has never been performed against the shipped build (22-UAT.md item 3, deferred)."
---

# Phase 22: Owner Capability Control + Pending-Confirmation Resolution Verification Report

**Phase Goal:** Make a transactional agent deployable and completable by a non-technical owner — give the owner a way to turn a capability on, and give an approver a way to resolve a `require_human` confirmation so the action actually executes.
**Verified:** 2026-07-28
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (Roadmap Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | SC1 — An owner can enable a previously-disabled skill through the shipped admin UI, with no direct database action, and the tighten-only guarantee still holds on every other field and dimension | ✓ VERIFIED (code/route level) | `validate_tighten_only`'s `enabled` branch confirmed by direct source read to be a bare `pass` with no `default_entry` reference; five sibling branches (`rate_limit`, `constraints.max_amount_cents`, `requires_confirmation`, `requires_identity_verification`, `actor_mode`) confirmed byte-for-byte unchanged; `PLATFORM_CAPABILITY_DEFAULTS` confirmed to still ship `enabled: False` for all seven entries. Pinned tests re-run directly by this verifier: 34/34 pass (`test_confirmation_resolution.py` + `test_pending_confirmation_routes.py`); full CAP-05 test set re-run separately, green. Admin UI checkbox logic (`deploy/page.tsx`) confirmed by source read: no permanent lock, staged confirmation only when the agent `isDeployed`. No live human UI walkthrough was performed (see human verification below) — this is code-level, not observed. |
| 2 | SC2 — An approver can approve or reject a `pending_confirmations` row; on approval the mutating action executes exactly once and the row is marked resolved; on rejection and on expiry it never executes | ⚠️ PRESENT_BEHAVIOR_UNVERIFIED | Sequential application logic (reject never enqueues, expired row forced to `resolution='expired'` and never enqueued, commit precedes dispatch, resolver never references `call_actor_gate` or an identity-session check) is directly source-verified and unit-tested — this verifier re-ran the relevant tests and they pass. However, the "exactly once" claim under **concurrent** resolution is a real-database atomicity property. Every existing test mocks `db.execute()`; `test_second_resolve_returns_409_and_never_enqueues` simulates the already-resolved case by scripting the mock to return no row — it does not exercise real transactional atomicity. The test module's own docstring states this explicitly: "a mocked DB boundary can't exercise a live claim/predicate." The one test that would prove this (`tests/integration/test_act07_resolve_live.py`) has never been executed — no PostgreSQL server exists on this machine. Routed to human verification. |
| 3 | SC3 — An approval created before an owner tightened a capability cannot execute against the looser envelope it was created under | ✓ VERIFIED (code level) | Confirmed by direct source read of `confirmation_resolution.py`: step 3 (`check_capability_access`) and step 6 (`apply_rate_and_constraint_checks`) both read the **live** `capability_envelopes` row, never a `capability_snapshot` stored on the original confirmation. This is a deterministic read-and-compare, not a concurrency guarantee, so a mocked-DB test legitimately proves it: `TestLiveEnvelope::test_tightened_ceiling_denies_execution` and `test_disabled_skill_denies_execution` re-run directly by this verifier, both pass. Unlike SC2, this claim does not depend on real-database atomicity. |
| 4 | SC4 — VER-01 SC2 is re-run and its `[failed — blocked]` disposition in `19-UAT.md` is replaced by an observed result | ⚠️ PRESENT_BEHAVIOR_UNVERIFIED | `22-UAT.md` item 1 confirms this was attempted and deferred, not run: no genuinely un-briefed non-technical tester was available, and no local PostgreSQL server is installed on this machine (verified detail: stale `postgresql-x64-17` service pointing at a deleted binary, orphaned `C:\Program Files\PostgreSQL\18\data\` with no `bin\`, nothing on PATH, nothing listening on 5432-5435). `19-UAT.md` item 1's disposition was amended in place from `[failed — blocked]` to a dated note stating both structural causes are closed in code but the criterion moved to "unproven," not to "passed." Routed to human verification — SC4's own text requires an observed result, and none exists. |

**Score:** 2/4 truths verified (2 present + wired, behavior/observation-unverified)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `apps/api/app/services/capability_service.py` | `validate_tighten_only`'s `enabled` branch with the platform-default gate removed | ✓ VERIFIED | Confirmed by direct read: bare `pass` under `if "enabled" in proposed:`, no `default_entry`, no `_reject(`. Five sibling branches confirmed unchanged. |
| `apps/api/app/services/transactional/confirmation_resolution.py` | The narrow resolver, skipping only the Actor seam and IDV gate | ✓ VERIFIED | Confirmed by direct read: no `call_actor_gate`, no identity-session check; re-checks live capability + rate/constraint state; keyword-only parameter contract (`agent_id`, `conn_str` explicit, no ContextVar reads). |
| `apps/api/app/api/v1/pending_confirmations.py` | Queue read + atomic resolve claim, commit before dispatch | ✓ VERIFIED | Confirmed by direct read: single `UPDATE ... WHERE ... resolved_at IS NULL ... RETURNING`, `await db.commit()` precedes the `.delay(...)` dispatch, execution-outcome lookup carries `actor_decision = 'approved_by_human'` discriminator. Route registered in `main.py:165`. |
| `apps/api/app/worker/tasks/runtime/confirmations.py` | `runtime`-queue Celery task, `acks_late=True`, takes only `confirmation_id` | ✓ VERIFIED | Confirmed by direct read: `@celery_app.task(bind=True, acks_late=True, ..., queue="runtime")`, single positional arg `confirmation_id`; `conn_str` decrypted inside the task body from `agent.neon_connection_string`, never passed as an arg. Idempotency delegated to the resolver's own fresh reservation (CLAUDE.md rule 5 satisfied: `acks_late=True` AND idempotency, not conflated). Task registered in `celery_app.py:114`. |
| `apps/admin/app/agents/[id]/deploy/page.tsx` | Unlocked Enabled control + Pending confirmations queue | ✓ VERIFIED (source-level) | Confirmed by direct read: `pendingEnabled`/`isDeployed` staged-confirm logic, `PendingConfirmationRow`/`PendingConfirmationsSection`/`resolveConfirmation`/`formatRelative`/`confirmationHeadline` all present and wired to `pendingConfirmationsQuery`/`resolveConfirmation` mutation. Four adversarial-review fix commits (`e5951f5`, `b1e2c22`, `a165a32`, `b66d9d4`) confirmed present in `git log`. No live browser render was checked by this verifier (routed to human verification for the two held-out visual checks). |
| `docs/guides/owner-capability-guide.md` | Corrected to state enabling is now possible; documents the approval queue | ✓ VERIFIED | Confirmed by direct read: `### Enabled` section and new `## When an action needs your approval` section present; explicitly states "approving a request authorises the action. It does not verify the customer's identity" (P3 prohibition honored). |
| `apps/api/tests/integration/test_act07_resolve_live.py` | Live-DB proof module, `INTEGRATION_TESTS_ENABLED`-gated | ✓ EXISTS, ✗ NEVER EXECUTED | 586 lines, confirmed on disk. Collects cleanly per SUMMARY claims (not independently re-verified by this agent since it requires `INTEGRATION_TESTS_ENABLED=1` and a live PostgreSQL server, neither available in this environment). This is the artifact whose non-execution drives the SC2/SC4 human-verification routing. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `capability_envelopes.py` (PATCH route) | `capability_service.py` | `patch_capability_envelope` calls `validate_tighten_only` before any ORM mutation | ✓ WIRED | Confirmed present in prior Phase 18 code, unchanged by this phase. |
| `pending_confirmations.py` (resolve route) | `worker/tasks/runtime/confirmations.py` | Local import + `.delay(str(claimed["id"]))`, dispatched only after `await db.commit()` and only when `resolution == "approved"` and `skill in SKILL_INPUT_MODELS` | ✓ WIRED | Confirmed by direct read of the exact commit-then-dispatch ordering (OD-6) and the dispatch guard. |
| `worker/tasks/runtime/confirmations.py` | `confirmation_resolution.execute_approved_confirmation` | `asyncio.run(execute_approved_confirmation(...))`, called with re-read row data + freshly decrypted `conn_str` | ✓ WIRED | Confirmed by direct read. |
| `deploy/page.tsx` (Enabled checkbox) | `PATCH /capability-envelopes/{skill}` | `onSave(envelope.skill, { enabled: checked })`, staged via a confirm step only when `isDeployed` | ✓ WIRED | Confirmed by direct read of the checkbox `onChange` handler and the staged-confirm block. |
| `deploy/page.tsx` (Pending confirmations queue) | `GET/POST /pending-confirmations` | `pendingConfirmationsQuery` (useQuery) and `resolveConfirmation` (useMutation) | ✓ WIRED | Confirmed present by grep; not independently exercised against a live backend (routed to human verification for the two held-out visual checks). |

### Data-Flow Trace (Level 4)

Not applicable in the traditional dashboard sense — `pendingConfirmationsQuery` fetches from the real `GET /pending-confirmations` route, which itself performs a real `SELECT` against `pending_confirmations` (confirmed by direct read of the SQL text in `pending_confirmations.py:188-201`). No static/hardcoded fallback data found in the query path. Not independently exercised against a running backend by this verifier.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| CAP-05 comparator: `enabled` transitions both directions, five siblings unchanged | Direct source read + inline Python assertions (as specified in `22-01-PLAN.md`'s own `<verify>` block) | Branch is bare `pass`, no `default_entry`/`_reject(` reference; siblings unchanged; `PLATFORM_CAPABILITY_DEFAULTS` all `enabled: False` | ✓ PASS |
| ACT-07 resolver + route unit suite | `pytest tests/unit/test_confirmation_resolution.py tests/unit/test_pending_confirmation_routes.py -q` (re-run independently by this verifier) | `34 passed in 78.10s` | ✓ PASS |
| Eight named pinned tests (absence tests, live-envelope, atomic-claim, execution-outcome discriminator) | `pytest <8 explicit node ids> -v` (re-run independently by this verifier) | `8 passed in 71.71s` | ✓ PASS |
| Full unit suite | `pytest tests/unit -q --ignore=test_chunking_service.py --ignore=test_docling_service.py` (re-run independently by this verifier, in background) | `1179 passed, 8 skipped, 0 failed in 219.06s` — matches the orchestrator-supplied baseline exactly | ✓ PASS |
| No `0020` migration; `pyproject.toml` unchanged | `ls apps/api/alembic/versions/` (head still `0019`); `git diff --quiet -- apps/api/pyproject.toml` | Head is `0019_blast_radius_capability_v2.py`; pyproject unchanged | ✓ PASS |
| Six guard-removal demonstrations claimed across 22-01/22-02/22-03 | Six commit hashes cross-checked in `git log --oneline --all` | All six commits (`618d705`, `38d5d4f`, `45ad4c8`, `503eb08`, `69468fa`, `bbe6e40`) resolve; SUMMARYs carry the real red-then-green transcripts | ✓ PASS |
| Live-database ACT-07 proof | `INTEGRATION_TESTS_ENABLED=1 pytest tests/integration/test_act07_resolve_live.py -m integration -q -s` | Not run — no local PostgreSQL server available in this environment | ? SKIP (routed to human verification) |

### Probe Execution

No `scripts/*/tests/probe-*.sh` files found for this phase; no probes declared in PLAN/SUMMARY frontmatter. Skipped — not applicable to this phase.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| CAP-05 | 22-01, 22-04, 22-05, 22-06 | Owner-reachable path to enable a capability | ✓ SATISFIED (code level) | Comparator fix + UI unlock + doc correction, all directly source-verified. `REQUIREMENTS.md` ticks this with an accurate, hedged note ("the code-level capability is proven, the live human re-run is not"). |
| ACT-07 | 22-02, 22-03, 22-04, 22-05, 22-06 | Resolution path for `pending_confirmations` (approve/reject/expiry) | ⚠️ SATISFIED at unit level, NOT proven at live-database level | Resolver + routes + task all directly source-verified and unit-tested by this verifier. The "exactly once" concurrency guarantee — the core of the requirement's own text — has never been observed against a real database. `REQUIREMENTS.md`'s own note states this explicitly and does not overclaim. |
| VER-01 (referenced, not owned by this phase) | — | v1.1 success-criteria gate | ✗ NOT SATISFIED, correctly left unticked | `REQUIREMENTS.md` and `ROADMAP.md` both correctly leave VER-01 unticked; SC2 moved from `[failed — blocked]` to `[deferred — unproven]`, not to passed. |

No orphaned requirements found — CAP-05 and ACT-07 are the only two IDs given for this phase and both appear across the six plans' `requirements:` frontmatter.

### Anti-Patterns Found

No `TBD`, `FIXME`, `XXX`, `TODO`, `HACK`, or `PLACEHOLDER` markers found in any of the nine production files touched by this phase (`capability_service.py`, `transactional/{schemas,tools,confirmation_resolution}.py`, `api/v1/pending_confirmations.py`, `schemas/pending_confirmation.py`, `worker/tasks/runtime/confirmations.py`, `worker/celery_app.py`, `main.py`). No debt-marker gate violations.

### Human Verification Required

### 1. ACT-07's live-database gate

**Test:** Run `tests/integration/test_act07_resolve_live.py` against a real local PostgreSQL server (`INTEGRATION_TESTS_ENABLED=1 ./.venv/Scripts/python.exe -m pytest tests/integration/test_act07_resolve_live.py -m integration -q -s`), and transcribe the adapter invocation count, resolved row state, audit row count, duplicate-resolve outcome, and denial error string.
**Expected:** Exactly one adapter call, exactly one `tool_calls_audit` row carrying `actor_decision='approved_by_human'`, a second concurrent resolve refused by the database claim itself (not application logic), and a confirmation approved before a ceiling tightening denied at execution.
**Why human:** This is the only test in the codebase capable of proving real-database transactional atomicity — the core of SC2's "executes exactly once" claim. Every other test mocks the DB boundary. Never executed in this environment (no local PostgreSQL server installed).

### 2. VER-01 SC2 re-run with a non-technical tester

**Test:** Hand `docs/guides/owner-capability-guide.md` to a genuinely un-briefed non-technical tester and run the nine-step script in `22-UAT.md` item 1's `how:` block against a live local stack (PostgreSQL, Redis, API, Celery worker, admin console).
**Expected:** Signup through deploy with no code edit; both skills turned on from the deploy screen with no database action; a refund and a Shopify order completed or reaching approval; one approval and one rejection resolved from the queue.
**Why human:** This is SC4's own criterion — a live observation by a non-technical tester cannot be produced from source review or unit tests. Never performed against the shipped build (deferred in `22-UAT.md` item 1, for two independent reasons: no PostgreSQL server, no available tester).

### 3. Two held-out visual checks

**Test:** View a pending confirmation with a free-text field of at least 300 characters, and view the deploy page at 900/1280/1440px widths.
**Expected:** The long field wraps within its column with no truncation or page overflow; the queue collapses to a single column at the narrow breakpoint with no horizontal overflow.
**Why human:** Visual rendering cannot be verified from source; requires a live admin console against a live backend. Never performed (deferred in `22-UAT.md` item 3).

### Gaps Summary

No code-level gaps were found. Direct source verification of the two most security-sensitive changes in this phase — the `validate_tighten_only` comparator deletion (SC1) and the confirmation resolver (SC2/SC3) — confirms the claims in every SUMMARY.md this verifier checked against source: the `enabled` branch is a scoped, unmodified-sibling deletion; the resolver genuinely never references the Actor seam or an identity-session check; the atomic claim genuinely commits before dispatch; the execution-outcome lookup genuinely carries the `actor_decision` discriminator; no `0020` migration was added; `pyproject.toml` is unchanged. This verifier independently re-ran 34+8 targeted tests and the full 1179-test unit suite, all green, matching the orchestrator-supplied figures exactly.

What is missing is not a code defect but an unclosed observation: SC2's "exactly once" claim depends on real-database transactional atomicity that no test in this codebase — including the authored `test_act07_resolve_live.py` — has ever exercised, because no PostgreSQL server exists on this machine. SC4 explicitly requires a live non-technical-tester run that was attempted and deferred, not performed. The phase's own `ROADMAP.md` and `REQUIREMENTS.md` already state this honestly (SC1/SC3 "met, code-level"; SC2/SC4 "NOT marked met"), and this verification concurs with that self-assessment rather than inflating it. The phase goal — "make a transactional agent deployable and completable by a non-technical owner" — is therefore **code-proven but not yet observably achieved**: the two live gates that would demonstrate it (real-database exactly-once execution, an actual non-technical tester's run) remain open, environment-blocked items, not silently dropped ones.

---

_Verified: 2026-07-28_
_Verifier: Claude (gsd-verifier)_
