# Phase 22: Owner capability control + pending-confirmation resolution - Research

**Researched:** 2026-07-28
**Domain:** Authorization-lattice semantics (capability tighten-only) + a synchronous-dispatcher re-entry / bypass-seam design for a security-critical mutating-tool pipeline
**Confidence:** HIGH on every source-code claim (all read directly this session from the current working tree); MEDIUM on the specific architectural recommendations for ACT-07's bypass seam, since no prior art for "resolve a pending confirmation" exists anywhere in this codebase — the recommendations are derived from the closest shipped analog (`red_team_probe.py`'s out-of-band dispatcher re-entry) rather than verified against a precedent that already solved this exact problem.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CAP-05 | Owner-reachable path to enable a capability, tighten-only intact on every other field | § CAP-05 Design: What `enabled` Actually Is; § Code Examples (capability_service.py diff, page.tsx diff) |
| ACT-07 | Resolution path for `pending_confirmations` — approve/reject/expire, no dispatcher loop, no stale-envelope execution | § ACT-07 Design: The Resolver Seam; § Critical Finding: No Out-of-Band Identity for Re-Entry; § Critical Finding: FastAPI Never Does Work Inline |
</phase_requirements>

## Summary

Both blockers are real, both are narrow, and both have a single root cause worth naming up front: **the codebase's authorization primitives were built assuming every mutating-tool invocation happens inside a live agent turn**, with a human never in that loop except as the *customer*. CAP-05 and ACT-07 are the two places where a human *operator* (owner, approver) needs to reach into that pipeline from outside a live turn, and the existing machinery — `validate_tighten_only`'s platform-default ceiling, the dispatcher's ContextVar-sourced identity, the Actor seam's synchronous ordering — was never designed with that entry point in mind. Neither requirement needs new authentication, new database tables beyond one narrow migration, or a new package. Both are surgical: CAP-05 is a ~10-line change to one comparator branch plus deleting a UI lock; ACT-07 is a new route + a new Celery task that reuses shipped machinery (the `red_team_probe.py` out-of-band dispatcher-re-entry pattern, the `reserve_idempotency` atomic-claim idiom, the `_get_owned_agent` IDOR guard) rather than inventing new patterns.

**CAP-05's actual defect:** `validate_tighten_only`'s `enabled` branch treats `enabled` as a tightness dimension bound by `PLATFORM_CAPABILITY_DEFAULTS[skill]["enabled"]` — exactly like `max_amount_cents` is bound by a numeric ceiling. But every platform default ships `enabled: False`, so this ceiling is permanently zero and no skill can ever be turned on through the shipped PATCH route. Reading the six comparable fields side by side shows `enabled` does not actually belong in this lattice the way the others do: `requires_confirmation` and `requires_identity_verification` are one-way *safety* switches (False→True always allowed, no platform-default gate at all) — the mirror-image case of `enabled`, which is a *capability* switch, not a safety one. The two booleans that most resemble `enabled` are treated asymmetrically only because `enabled`'s True→False direction (disable) was correctly recognized as always-safe and left unconditional, while its False→True direction was — per the function's own docstring — deliberately punted to "a chosen consequence, not a surprise," anticipating exactly the phase now closing it. The fix is not "ship a different platform default" (that would auto-enable every mutating skill for every newly-provisioned agent, which is a materially worse security posture than today) — it is to stop gating `enabled`'s direction on the platform default at all, matching how the two adjacent boolean fields already behave, while leaving every other field's tighten-only comparison completely untouched.

**ACT-07's actual defect and the two obstacles that make it non-trivial:** nothing resolves a `pending_confirmations` row (confirmed again this session by direct source read — zero routes, zero Celery tasks, zero scripts). The obvious fix — a route that re-enters `_execute_transactional_tool` on approval — collides with two facts the phase brief already named plus a **third the brief did not name, which this research surfaces as the harder of the two real obstacles**: the dispatcher's identity (`agent_id`, `conn_str`, `conversation_id`, `verified_session_token`) is sourced entirely from `ContextVar`s populated once per live agent turn by `build_tool_server()` — there is no live agent turn when an admin clicks Approve hours later, and no live customer IDV session to re-verify even if one wanted to. The correct design is not "re-enter the full 8-step dispatcher" but a **purpose-built bypass path that re-runs only the checks whose live state can meaningfully change between confirmation-creation and approval** (capability enabled/disabled, rate limit, `max_amount_cents` ceiling, idempotency) and **skips the two steps a human approval structurally replaces or cannot re-supply** (the Actor seam — approval *is* the human verdict — and the IDV gate, already satisfied once at creation time, with no customer session available to re-check at resolution time). This must run inside Celery, not inline in a FastAPI route handler, per the codebase's own "FastAPI never does work inline" architecture principle and the existing `get_adapter_for_skill` docstring constraint that it is callable ONLY from `_execute_transactional_tool`.

**Primary recommendation:** Ship CAP-05 as a single-branch edit to `capability_service.py` plus deleting the `enabledLocked` lock in `deploy/page.tsx` (already wired to `onSave`, no new frontend plumbing needed). Ship ACT-07 as two new routes (`GET`/`POST` on `/agents/{id}/pending-confirmations...`, `_get_owned_agent` IDOR pattern copied verbatim) whose `POST .../resolve` performs an atomic claim-then-dispatch: the route atomically claims the row (mirrors `reserve_idempotency`'s `INSERT...ON CONFLICT...RETURNING` idiom, applied here as `UPDATE...WHERE resolved_at IS NULL...RETURNING`) and, on approval, enqueues a new `runtime`-queue Celery task that re-enters a **subset** of the dispatcher (capability + rate/constraint checks against the LIVE envelope, fresh idempotency reservation, adapter call, audit row) built by extracting the dispatcher's steps 6-7 into a shared helper rather than duplicating adapter-call/audit-write logic. Both requirements are code-buildable with **zero new PostgreSQL access needed to author or unit-test them** — the live-DB roundtrip proof (a new control migration, if one is added for a `resolved_by` column) is, like every prior v1.1/v1.2 phase's live-DB step, an `autonomous:false` gate, since no PostgreSQL server exists on this machine and `CONTROL_DB_URL` points at live Neon production.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Capability-envelope `enabled` toggle (CAP-05) | API / Backend (`capability_service.py`, existing PATCH route) | Frontend Server (`deploy/page.tsx`, already wired) | Pure comparator-logic change; the frontend control already calls the right endpoint, only its `disabled` lock needs removing |
| Pending-confirmation resolve/reject (ACT-07) | API / Backend (new route, atomic claim) | — | Fast, DB-only operation; safe to do inline in the route (no network call to a provider here) |
| Pending-confirmation approved-execution (ACT-07) | API / Backend, but dispatched to Celery (`runtime` queue) | Database / Storage (fresh idempotency reservation, audit row) | The actual provider adapter call is exactly the class of long-running/network operation the codebase's own architecture principle keeps out of FastAPI request handlers |
| Approver queue UI (ACT-07) | Frontend Server (Gotham admin, `apps/admin`) | API / Backend (new GET route) | Read-only list view; no new client-side logic beyond an approve/reject action calling the new route |
| Expiry handling (ACT-07 / ACT-04's "expires otherwise") | API / Backend (lazy check at resolve time) | Optional: Celery `pipeline` queue (hygiene sweep) | The correctness guarantee ("expiry never executes") is satisfied by a lazy check inside the resolve path alone; a sweep is queue-hygiene only, not load-bearing |

## Package Legitimacy Audit

No new external packages are required for either CAP-05 or ACT-07. CAP-05 touches only existing first-party modules. ACT-07 reuses `pytest`/`pytest-asyncio` (already pinned), the existing `celery` app, and the ORM model that already exists (`PendingConfirmation`). If the planner elects to add a `resolved_by` column (see § Open Decisions), it is a plain Alembic migration against an existing table — no new dependency.

**Packages removed due to [SLOP] verdict:** none — no packages proposed.
**Packages flagged as suspicious [SUS]:** none.

## CAP-05 Design: What `enabled` Actually Is

**Source:** `apps/api/app/services/capability_service.py:239-385` (`validate_tighten_only`, read directly this session), `apps/api/app/api/v1/capability_envelopes.py:159-261` (the PATCH route), `apps/admin/app/agents/[id]/deploy/page.tsx:898,1126-1156` (the `enabledLocked` UI lock). `[VERIFIED: codebase]`

### The current comparator, field by field

| Field | Direction that is always legal | Direction gated by what |
|---|---|---|
| `enabled` | `True → False` | `False → True` gated on `default_entry.get("enabled", False)` — **always `False` for every shipped skill today** |
| `rate_limit` | any numerically-tighter value | looser value rejected relative to *current row*, no platform-default gate on the direction itself (the platform default only bounds the very first write, via the route's "first write baseline is platform default" logic) |
| `constraints.max_amount_cents` | any numerically-lower value | looser value rejected relative to *current row*, same first-write-baseline pattern |
| `requires_confirmation` | `False → True` (unconditional) | `True → False` rejected unconditionally — **no platform-default gate on either direction** |
| `requires_identity_verification` | `False → True` (unconditional) | `True → False` rejected unconditionally — **no platform-default gate on either direction** |
| `actor_mode` | any tighter ordinal | looser ordinal rejected relative to current row; `off` additionally forbidden for any mutating skill regardless of ordinal |

Five of six fields compare the **proposed value against the current row**, with the platform default entering only as the *first-write baseline* (`current` synthesized from the platform default when no row exists yet — `capability_envelopes.py:210-218`). `enabled` is the **only** field whose comparator checks the platform default on *every* write, not just the first one, and it is the only field where doing so makes the field permanently stuck at a single value platform-wide. `requires_confirmation`/`requires_identity_verification` are the closest structural analogs — both are booleans, both express "add or remove a safety control" — but neither is gated against a platform default at all; both simply allow the safety-increasing direction unconditionally. `enabled` is a *capability*-increasing direction, the semantic opposite of those two, which is exactly why treating it identically to them (unconditional in both directions) is the correct fix rather than an inconsistency: disabling a skill is always safe (matches `True→False` being unconditional today), and *enabling* a skill an owner explicitly requests is the direct analog of an owner explicitly requesting a safety control be added — both are owner-initiated, explicit, single-field actions that this comparator should not silently veto.

**Why "ship `PLATFORM_CAPABILITY_DEFAULTS[skill]["enabled"] = True`" is the wrong fix, considered and rejected:** flipping every platform default's `enabled` to `True` would make every newly-provisioned agent's six mutating skills live by default — the agent could act on money-moving skills before an owner has configured a single ceiling, rate limit, or reviewed anything. `capability_envelopes.enabled` already has `server_default=false` at the schema level specifically to be fail-closed (T-14-01-01, `capability_envelope.py:41-43`) — changing the *comparator's* referenced default would not change the schema default, so a never-configured agent would still start `enabled=False` at the DB layer, but the *first legal PATCH* would suddenly be able to set every field to its (now-permissive) ceiling in one shot, defeating the entire "review and tighten before you turn it on" workflow DOC-03's owner guide already documents. This option is rejected; do not propose it in planning.

### The recommended fix

Remove the platform-default gate from `enabled`'s `False → True` branch entirely — both directions become unconditional, exactly mirroring how `requires_confirmation`/`requires_identity_verification` already behave (just in the opposite direction, since `enabled` is a capability switch, not a safety switch). Concretely, in `validate_tighten_only`:

```python
# --- enabled --------------------------------------------------------
if "enabled" in proposed:
    # enabled is an owner-controlled authorization toggle (CAP-05
    # research), not a tightness dimension bound by platform defaults —
    # unlike max_amount_cents/rate_limit/actor_mode, it has no numeric or
    # ordinal "how much" to bound. The platform-default gate here made
    # every skill permanently un-enablable (every default ships
    # enabled=False) and is removed. Both directions are legal; every
    # OTHER field on this envelope remains governed by tighten-only
    # exactly as before this change — enabling a skill does not, by
    # itself, loosen any other field's value.
    pass
```

**Why this satisfies SC1 exactly as worded** ("the tighten-only guarantee still holds on every other field and dimension"): the five other branches are untouched. A PATCH body of `{"enabled": true}` alone changes only `current["enabled"]`; no other field is present in `proposed`, so no other comparator branch even runs. An owner cannot use this change to simultaneously loosen a ceiling, a rate limit, or turn off a confirmation/IDV requirement — each of those still requires its own field-specific PATCH, each still independently checked. Re-enabling a skill previously tightened and then disabled does not reset its other fields either — `enabled` toggling alone never mutates `rate_limit`/`constraints`/`requires_confirmation`/`requires_identity_verification`/`actor_mode`; those persist on the row across enable/disable cycles.

**The frontend change is smaller than it looks.** `deploy/page.tsx:898` computes `enabledLocked = envelope.enabled === false && envelope.platform_default.enabled === false` (currently always `true` whenever a skill is off, since every platform default ships `enabled: false`) and uses it at line 1137 (`disabled={enabledLocked}`) plus the caption at 1147-1151 ("Cannot re-enable - the platform default is off for this skill."). The checkbox's `onChange` handler (line 1140-1144) **already** calls `onSave(envelope.skill, { enabled: e.target.checked })` — the exact same mutation path every other field on this panel uses. No new API call, no new mutation hook, no new state needs to be wired. The only changes needed: remove the `disabled={enabledLocked}` prop (or redefine `enabledLocked` to never lock — recommend deleting the variable and the prop together, since after this fix nothing computes it) and collapse the caption ternary to drop the "cannot re-enable" branch (`envelope.enabled ? 'Enabled.' : 'Disabled.'`). This is a UI-SPEC-pass decision on exact copy, but the shape of the change is settled by this research.

**Existing test that will need updating, named precisely:** `apps/api/tests/unit/test_capability_routes.py::TestPatchCapabilityEnvelope::test_patch_rejects_each_loosening_field[enabled]` (`current_overrides={"enabled": False}`, `payload={"enabled": True}`, asserts `422`) currently encodes the *old*, soon-to-be-wrong behavior as a passing test. This parametrize case must be removed or inverted to assert `200` as part of this phase's own test changes — flagging it here so the plan does not treat a red test here as evidence of a regression.

## ACT-07 Design: The Resolver Seam

**Source:** `apps/api/app/services/transactional/tools.py` (full dispatcher + `require_human` branch, read directly), `apps/api/app/services/actor_seam.py`, `apps/api/app/services/agent_tools.py:727-800` (`build_tool_server`, the ContextVar factory), `apps/api/app/services/red_team_probe.py:271-360` (`invoke_probe_tool`, `_build_transactional_probe_fn` — the only existing precedent for driving the dispatcher outside a live SDK turn), `apps/api/app/services/transactional/idempotency.py` (`reserve_idempotency`'s atomic-claim idiom), `apps/api/app/services/transactional/provider_adapter.py:13-20` (the `get_adapter_for_skill` docstring constraint), `apps/api/app/models/pending_confirmation.py`, `apps/api/app/worker/tasks/pipeline/staleness.py` (Celery task shape precedent). `[VERIFIED: codebase]`

### The dispatcher's 8 steps and which ones a resolver can and cannot safely repeat

| Step | What it checks | Re-run on approval? | Why |
|---|---|---|---|
| 1. IN-03 agent_id precondition | `agent_id` is non-empty | Trivially yes (sourced from the stored row, not a ContextVar) | Not a real risk; the row carries `agent_id` directly |
| 2. Capability check | envelope exists + `enabled` | **Yes — this is the one SC3 is about** | If the owner disabled the skill after the confirmation was created, this must now deny |
| 2.5 IDV gate | `requires_identity_verification` + a live customer session token | **No — cannot be re-run** | The token is a per-turn `ContextVar` never persisted anywhere queryable by the resolver; IDV, if required, was already satisfied once, before the Actor ever ran (step 2.5 precedes step 5) |
| 3. Reserve idempotency | atomic claim on `(agent_id, skill, idempotency_key)` | **Yes, freshly** | The original reservation was explicitly *released* in the `require_human` branch (`tools.py`'s comment: "the action will NOT proceed... free the reservation so a later retry... can re-enter") — a fresh reservation is exactly how "executes exactly once" is enforced here, identically to every other path |
| 4. Rate + constraint checks | Redis rate window + `max_amount_cents` against the LIVE envelope | **Yes — this is the other half of SC3** | If the owner tightened the ceiling or rate limit after creation, this must now deny against the current, not the original, value |
| 5. Actor seam | Haiku judgment `approve\|block\|require_human` | **No — this is what the human approval replaces** | Re-invoking it is exactly the "approval loops rather than completes" failure mode both the phase brief and 19-RESEARCH.md name; the human's approve/reject decision is the terminal verdict for this call, not an input to a second automated verdict |
| 6. Adapter execute | the real provider call | **Yes** | This is the actual action being approved |
| 7. Audit row + finalize | `tool_calls_audit` row + idempotency finalize | **Yes** | AUD-01 symmetry must hold for this path exactly as every other path — a resolved-and-executed confirmation without an audit row would be a silent gap of the same shape as the one this phase is closing |

**This table is the actual "bypass seam."** It is not "skip enforcement and call the adapter" — it is "run every check whose live answer can change between creation and approval (steps 2, 3, 4, 6, 7), and skip only the two steps that either cannot be re-supplied (2.5, no session token available to an admin resolver) or would recreate the exact loop this phase exists to close (5, the Actor)." Presented this way, the design directly answers the phase brief's three explicit questions:

- **Where in the dispatcher order could a bypass sit without skipping capability/IDV/rate/idempotency checks?** It cannot skip IDV cleanly — no live session exists to check. The honest answer is: IDV is **not re-checked** on approval, and this must be a named, explicit decision (see § Open Decisions), not a silent gap. Capability, rate, and idempotency **are** re-checked.
- **How does the idempotency reservation interact with deferred execution?** The original reservation was released at `require_human` time specifically so a later attempt could re-enter; the resolver's Celery task performs a **fresh** `reserve_idempotency` call using the `idempotency_key` and recomputed `args_hash` from the row's stored `arguments` — this is the same mechanism, not a new one, and it is what makes "executes exactly once" true even under a double-click on Approve (the second attempt reads `"in_progress"` or `"replay"`, never re-executes).
- **Does SC3 need re-evaluation against the live envelope, comparison against a stored snapshot, or both?** **Re-evaluation against the live envelope**, not a stored-snapshot comparison. `tool_calls_audit.capability_snapshot` already stores the envelope at the *original* call time (written in the `require_human` branch itself, `tools.py:460-471`) — that snapshot is a historical record for audit purposes, not a live gate. SC3's wording — "cannot execute against the looser envelope it was created under" — is satisfied by re-running the exact same `check_capability_access` + `apply_rate_and_constraint_checks` functions the normal dispatcher already uses, against whatever the envelope *now* says, using the stored `arguments` as the proposed action. If the owner tightened the ceiling below the stored refund amount, `apply_rate_and_constraint_checks` denies it today with zero new logic.

### Critical Finding: No Out-of-Band Identity for Re-Entry

`_execute_transactional_tool` never receives `agent_id`/`conn_str`/`conversation_id`/`verified_session_token` as parameters — it reads all four from module-level `ContextVar`s (`app/services/agent_tools.py:160-181`) that are populated exactly once per live agent turn by `build_tool_server()`, called from inside the Celery `runtime` task body (`worker/tasks/runtime/agent.py:786`) before `asyncio.run()` starts the SDK turn. **A confirmation resolver runs completely outside that lifecycle** — there is no active turn, no SDK session, and (per the IDV finding above) no customer session token to supply even if one wanted to re-run step 2.5.

The one place in this codebase that already solves "invoke the real dispatcher from outside a live SDK turn" is `red_team_probe.py`'s `invoke_probe_tool`/`_build_transactional_probe_fn` (added in Phase 18 for the exact same reason: probes need to drive the real enforcement layers deterministically, not through a live conversational turn). Its pattern — call `build_tool_server(conn_str, agent_id, agent_name, strategy, conversation_id, notify_fn, tenant_id, verified_session_token, job_id)` to populate the ContextVars for the current async context, then invoke the tool handler directly — is directly reusable for the resolver's Celery task, with one difference: the red-team probe substrate calls the **full** `@tool`-decorated handler (which re-enters the whole dispatcher, IDV and Actor included, because a red-team probe is deliberately testing the whole chain). The resolver needs the **step 2/3/4/6/7-only** subset described above, which does not exist yet and must be extracted from `_execute_transactional_tool` as a new, smaller shared function rather than either (a) duplicating the adapter-call/audit-write logic by hand in a new module, or (b) calling the full dispatcher and accepting IDV re-checks will always fail for any skill that requires them (which would make ACT-07 non-functional for 5 of the 6 mutating skills — every one except `book_slot`, per `registry.py`'s `requires_identity_verification` flags — since there is no session token to supply).

**Recommended refactor, minimal:** extract `_execute_transactional_tool`'s steps 6 (adapter execute) and 7 (audit + finalize) into a private helper (e.g. `_execute_adapter_and_audit(skill, validated, raw_args, adapter_method, agent_id, conn_str, conversation_id, snapshot, decision, rationale)`), called both by the existing dispatcher (unchanged behavior, same call site) and by the new resolver task (after it independently runs steps 2 and 4 against the live envelope and step 3 fresh). This is a pure refactor of already-tested logic — no behavior change to the existing path — and avoids a second, drifting copy of "how to call an adapter and write an audit row," which `Don't Hand-Roll` below calls out explicitly.

### Critical Finding: FastAPI Never Does Work Inline

CLAUDE.md's architecture principle ("FastAPI never does work inline. All long-running operations go to Celery.") and `provider_adapter.py`'s own docstring ("`get_adapter_for_skill`... MUST NOT be imported or called from any FastAPI route handler or SDK hook — only from `_execute_transactional_tool`") together rule out performing the actual provider call inside the resolve route's request/response cycle. The recommended shape:

1. `POST /agents/{agent_id}/pending-confirmations/{confirmation_id}/resolve` (body: `{"resolution": "approved" | "rejected"}`) — **fast, DB-only, safe to run inline**:
   - `_get_owned_agent` IDOR guard (copied verbatim from `capability_envelopes.py`/`prompt_versions.py` — 404 on both missing-agent and foreign-agent branches).
   - Atomic claim: `UPDATE pending_confirmations SET resolved_at = now(), resolution = CASE WHEN expires_at < now() THEN 'expired' ELSE :requested_resolution END WHERE id = :id AND agent_id = :agent_id AND resolved_at IS NULL RETURNING *`. This is the `reserve_idempotency`/`reserve_pending_confirmation` idiom this codebase already uses elsewhere for "DB decides the single winner under concurrency" (CR-02's `INSERT...ON CONFLICT...RETURNING` pattern, here an `UPDATE...WHERE...RETURNING` claim instead since the row already exists). A `NULL` `RETURNING` result means the row was already resolved (double-click, or a second admin) or does not belong to this agent — 404/409, no further action, matching the house convention of "the DB decides, the app reflects."
   - Expiry is enforced **inside this same atomic statement** — a resolve attempt on an already-expired row is forced to `'expired'` regardless of what the caller requested, which is the lazy-evaluation construction this codebase already prefers for time-bounded semantics (19-RESEARCH.md's AUD-03 finding: no accelerated-clock mechanism exists anywhere in this codebase, so time-bounded correctness is always evaluated lazily against `now()`, never via injected/simulated time).
   - If the claimed resolution is `'rejected'` or `'expired'` → return `200` immediately. Nothing further happens; the action never executes (SC2's rejection/expiry half is satisfied by construction — an unresolved-or-non-approved row is never a Celery task's input).
   - If the claimed resolution is `'approved'` → enqueue a new Celery task (`runtime` queue, `acks_late=True`) with the `confirmation_id` (NOT `conn_str` — CLAUDE.md rule 4) as the sole argument.
2. The new Celery task (`resolve_approved_confirmation(confirmation_id: str)`):
   - Re-reads the (already-resolved) `PendingConfirmation` row and the owning `Agent` row from the control DB.
   - Decrypts `conn_str = fernet_decrypt(agent.neon_connection_string)` (the exact pattern `worker/tasks/runtime/agent.py:724` already uses).
   - Re-validates the stored `arguments` dict into the correct typed Input model for `skill` (a new, small `skill -> Input model` mapping is needed — no such mapping exists today; `tools.py`'s handlers each import their own model by name. A `SKILL_INPUT_MODELS: dict[str, type[BaseModel]]` dict in `schemas.py` or `registry.py` is the natural home).
   - Runs `check_capability_access(agent_id, skill)` — deny path writes an audit row and stops (SC3, capability half).
   - Runs `apply_rate_and_constraint_checks(agent_id, skill, snapshot, validated)` — deny path releases nothing (idempotency was never reserved yet at this point) and writes an audit row and stops (SC3, ceiling/rate half).
   - Runs `reserve_idempotency(agent_id, skill, validated.idempotency_key, args_hash)` — non-`"reserved"` states are handled exactly as the main dispatcher already handles them (replay/in_progress/args_mismatch).
   - Calls the shared `_execute_adapter_and_audit(...)` helper (see above) with `decision="approved_by_human"` (a new, distinct `actor_decision` value distinguishing this path from the Haiku-produced `"approve"` in every `tool_calls_audit` row it produces — this is a small, deliberate audit-fidelity choice: a human-approved execution should be visibly distinguishable from an Actor-approved one in the audit trail, not conflated).
   - Idempotent by construction: a Celery redelivery (per `acks_late=True`) re-enters `reserve_idempotency` with the same key and finds `"replay"` or `"in_progress"`, never double-executes — this is the exact durability guarantee `TXN-02`/`acks_late` already provides everywhere else in this codebase.

**One honestly-named residual risk, not fully closed by this design, flagged for the planner rather than silently resolved:** if the route's atomic claim commits (`resolved_at` stamped `'approved'`) but the Celery enqueue itself fails (e.g. Redis unreachable at that instant), the row is left in a durable "approved but never executed" state with no automatic retry path. This is a narrow window (the enqueue call is the very next line after a successful claim) and is the same category of risk every other Celery-dispatching route in this codebase already accepts implicitly (no route in `apps/api/app/api/v1/*.py` currently guards against its own `.delay()` call failing). Document this as an accepted, named gap rather than engineering around it — matching this project's own established convention (T-19-04, the R4.99 skip-threshold note) of naming a residual gap explicitly rather than either silently absorbing it or over-building for it.

## Standard Stack

No new libraries. Both requirements are implemented entirely with packages already pinned in `apps/api/pyproject.toml` (FastAPI, SQLAlchemy, Celery, structlog — all already imported by the exact files this phase touches).

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| A new `runtime`-queue Celery task for approved-execution | Executing the adapter call synchronously inside the resolve route | Rejected — violates the codebase's own "FastAPI never does work inline" principle and the `get_adapter_for_skill` docstring's explicit prohibition on route-handler call sites; a Stripe/Shopify network round-trip blocking an admin's approve-click request is also a worse UX than a queued, pollable/SSE-observable outcome |
| Re-running the full 8-step dispatcher via `build_tool_server` + the existing `@tool` handler | A purpose-built step-2/3/4/6/7-only helper | Rejected as the primary path — the full dispatcher re-runs the Actor (causing the exact loop this phase closes) and IDV (for which no session token is available at resolve time, making 5 of 6 skills permanently unresolvable) |
| Flip `PLATFORM_CAPABILITY_DEFAULTS[skill]["enabled"]` to `True` | Removing the platform-default gate on `enabled`'s comparator branch | Rejected — auto-enables every mutating skill for every new agent, a materially worse default posture than today |

## Architecture Patterns

### System Architecture Diagram

```
  Owner (admin UI)                    Approver (admin UI)
        |                                    |
        | PATCH .../capability-envelopes/    | POST .../pending-confirmations/
        |   {skill}  {"enabled": true}       |   {id}/resolve  {"resolution": "approved"}
        v                                    v
  ┌─────────────────────┐          ┌──────────────────────────────┐
  │ capability_envelopes │          │  pending_confirmations route  │
  │        .py           │          │        (NEW, ACT-07)          │
  │  validate_tighten_    │          │  1. _get_owned_agent (IDOR)   │
  │  only (CAP-05 fix:    │          │  2. atomic UPDATE...WHERE     │
  │  enabled unconditional│          │     resolved_at IS NULL       │
  │  both directions)     │          │     RETURNING (claim, DB      │
  └─────────┬─────────────┘          │     decides winner; expiry    │
            │ 200 / 422              │     forced lazily here too)   │
            v                        └───────────┬───────────────────┘
  capability_envelopes table                     │
  (row now enabled=true;               approved? │ rejected/expired -> 200, stop
   every OTHER field unchanged)                  v
                                       .delay(confirmation_id)  [runtime queue,
                                                                  acks_late=True]
                                                  │
                                                  v
                              ┌──────────────────────────────────────┐
                              │ resolve_approved_confirmation (NEW)   │
                              │ Celery task, tenant_id/agent_id-only  │
                              │  1. re-read row + Agent (control DB)  │
                              │  2. fernet_decrypt(conn_str)          │
                              │  3. re-validate stored args -> model  │
                              │  4. check_capability_access  (LIVE)   │
                              │  5. apply_rate_and_constraint_checks  │
                              │     (LIVE envelope -- SC3)            │
                              │  6. reserve_idempotency (FRESH claim) │
                              │  7. shared _execute_adapter_and_audit │
                              │     (extracted from steps 6-7 of the  │
                              │     live-turn dispatcher; NO Actor    │
                              │     call, NO IDV re-check)            │
                              └──────────────────────────────────────┘
                                                  │
                                                  v
                                   tool_calls_audit row (actor_decision=
                                   "approved_by_human") + finalize_idempotency

  ── For comparison, the EXISTING live-turn path (unchanged by this phase) ──
  Customer (widget) -> agent turn (Celery runtime, build_tool_server sets
  ContextVars) -> _execute_transactional_tool steps 1-7, including Actor
  (step 5) -> on require_human: release reservation, write pending_
  confirmations row, STOP (no execution) -- this is the row the flow above
  now knows how to resolve.
```

### Recommended Project Structure

No new top-level modules. New/changed files:

```
apps/api/app/
├── services/capability_service.py          # CAP-05: edit validate_tighten_only's enabled branch
├── services/transactional/
│   ├── tools.py                            # ACT-07: extract _execute_adapter_and_audit helper
│   ├── schemas.py                          # ACT-07: add SKILL_INPUT_MODELS mapping
│   └── confirmation_resolution.py          # ACT-07: NEW — the step-2/3/4/6/7-only re-entry function
├── api/v1/
│   └── pending_confirmations.py            # ACT-07: NEW — GET list + POST resolve routes
├── worker/tasks/runtime/
│   └── confirmations.py                    # ACT-07: NEW — resolve_approved_confirmation Celery task
└── alembic/versions/
    └── 0020_*.py                           # ACT-07: NEW, only if resolved_by (or similar) column is added

apps/admin/app/agents/[id]/deploy/page.tsx  # CAP-05: remove enabledLocked; ACT-07: confirmations queue UI
```

### Pattern 1: Atomic claim before dispatch (reused, not invented)

**What:** Claim a row via `UPDATE ... WHERE <not-yet-claimed> RETURNING *` so the database, not application logic, decides the single winner under concurrency.
**When to use:** Any "resolve this row exactly once" operation, including the resolve route's own claim and — already shipped — `reserve_idempotency`'s `INSERT ... ON CONFLICT ... RETURNING`.
**Example:**
```python
# Source: apps/api/app/services/transactional/idempotency.py (existing pattern this
# phase's resolve route mirrors, adapted from INSERT...ON CONFLICT to UPDATE...WHERE
# since the pending_confirmations row already exists by the time resolve is called)
claimed = db.execute(
    sa_text(
        "UPDATE pending_confirmations "
        "SET resolved_at = now(), "
        "    resolution = CASE WHEN expires_at < now() THEN 'expired' ELSE :res END "
        "WHERE id = :id AND agent_id = :agent_id AND resolved_at IS NULL "
        "RETURNING id, skill, arguments, resolution"
    ),
    {"id": confirmation_id, "agent_id": agent_id, "res": requested_resolution},
).mappings().first()
if claimed is None:
    raise HTTPException(status_code=409, detail="Already resolved or not found")
```

### Pattern 2: Out-of-band dispatcher entry via `build_tool_server` (existing, Phase 18)

**What:** Populate the dispatcher's ContextVars from a non-turn context, then call into dispatcher-adjacent logic.
**When to use:** Anywhere the real enforcement layers must run outside a live SDK conversation — red-team probes (shipped) and, with the step-2/3/4/6/7-only helper this phase adds, confirmation resolution.
**Example:**
```python
# Source: apps/api/app/services/red_team_probe.py:315-325 (existing pattern —
# ACT-07's Celery task reuses conn_str decryption + agent lookup the same way,
# but calls the NEW narrow helper, not the full @tool handler, since it must
# skip the Actor seam and cannot re-supply an IDV session token)
tool_server = build_tool_server(
    conn_str=conn_str,
    agent_id=str(agent.id),
    agent_name=agent.name,
    strategy=strategy,
    conversation_id=conversation_id,
    notify_fn=lambda reason, context: None,
    tenant_id=tenant_id,
    verified_session_token="",
    job_id="",
)
```

### Anti-Patterns to Avoid

- **Re-running the full `_execute_transactional_tool` dispatcher on approval:** re-triggers the Actor (loop) and the IDV gate (unresolvable — no session token available at resolve time). Use the narrow step-2/3/4/6/7 helper instead.
- **Comparing against the stored `capability_snapshot` instead of the live envelope:** the snapshot in `tool_calls_audit`/the original `require_human` audit row is a historical record, not a gate. SC3 requires re-evaluating against whatever the envelope says *now*.
- **Calling `get_adapter_for_skill` from the FastAPI route handler:** explicitly prohibited by the function's own docstring; the resolve route must only claim the row and enqueue, never touch the adapter.
- **A new comparator branch treating `enabled` as bound by `PLATFORM_CAPABILITY_DEFAULTS`:** this is the exact defect being fixed; do not reintroduce it under a different name (e.g. a "minimum enabled tier" concept).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Claiming a row exactly once under concurrency | A new locking primitive, `SELECT ... FOR UPDATE`, or an application-level mutex | `UPDATE ... WHERE resolved_at IS NULL RETURNING` (Pattern 1 above) | This codebase already has exactly one house idiom for "DB decides the single winner" (`reserve_idempotency`); a second, different-shaped idiom for the same problem is itself a maintenance and audit inconsistency |
| Calling the adapter + writing the audit row from the resolver | A second, hand-copied version of steps 6-7 in a new module | Extract `_execute_adapter_and_audit` from `_execute_transactional_tool` and call it from both places | Two independently-maintained copies of "how to call a provider adapter and audit it" is exactly the kind of drift that produces a security-relevant inconsistency later — one already-tested implementation, two callers |
| Driving the dispatcher from outside a live turn | A new dispatcher-entry mechanism | `build_tool_server()` (Pattern 2 above) — the exact mechanism Phase 18 built for `red_team_probe.py` | Reinventing this risks silently reproducing Pitfall 1 from 18-RESEARCH.md (a probe/task that never actually reaches enforcement and vacuously "succeeds") |
| Re-verifying identity at resolution time | A workaround that reconstructs or re-derives a session token for the resolver to "re-check" IDV | Skip IDV re-check explicitly (see § Open Decisions) — do not invent a substitute verification mechanism for an admin who is not the customer | IDV-05 requires the mutating tool be blocked until *the customer* holds a valid verified session — an admin approving on the customer's behalf is not a substitute customer session, and pretending otherwise (e.g. auto-issuing a synthetic "verified" session for the approval) would be a genuine security regression, not a workaround |

**Key insight:** every piece of machinery ACT-07 needs — the atomic-claim idiom, the out-of-band dispatcher-entry pattern, the fresh-idempotency-reservation contract, the fernet-decrypt-conn_str pattern — already exists in this codebase for a different purpose. The temptation in "build the missing resolver" is to write something new and self-contained; resist it, because reusing the existing idioms is what keeps this path's guarantees provably identical to the guarantees the rest of the transactional substrate already has.

## Common Pitfalls

### Pitfall 1: Treating "re-run the dispatcher" as "re-run all 8 steps"
**What goes wrong:** A plan task re-invokes the full `@tool` handler (e.g. `issue_refund_tool(args)`) from the resolver, expecting it to "just work" because it is the shipped, tested code path.
**Why it happens:** It is the path of least resistance — no new function to write.
**How to avoid:** Read § ACT-07 Design's step table before writing any resolver code. The Actor seam WILL fire again and produce `require_human` again (looping, not completing) unless the resolver calls a narrower helper that omits steps 2.5 and 5.
**Warning signs:** A resolver task that imports `call_actor_gate` at all, or that expects a `verified_session_token` parameter from anywhere other than "always empty/skipped."

### Pitfall 2: Comparing the approval against a stored snapshot instead of the live envelope
**What goes wrong:** A plan reads `tool_calls_audit.capability_snapshot` (or invents a new snapshot column on `pending_confirmations`) and compares the approval's arguments against *that*, believing it satisfies SC3.
**Why it happens:** A snapshot already exists (written at `require_human` time) and looks like the obvious thing to diff against.
**How to avoid:** SC3 is about the envelope that exists **now**, at approval time — re-run `check_capability_access`/`apply_rate_and_constraint_checks` against the live row, exactly as every other call does. The stored snapshot's job is audit history, not authorization.
**Warning signs:** A resolver whose deny logic reads any column from `tool_calls_audit` rather than a fresh `SELECT` against `capability_envelopes`.

### Pitfall 3: Assuming the enable-path fix needs a schema/migration change
**What goes wrong:** A plan proposes a new `capability_envelopes.owner_enabled_ceiling` column or similar, believing the platform-default gate needs a *new*, owner-controllable ceiling rather than being removed.
**Why it happens:** Every other tighten-only field's fix pattern in this codebase involves a ceiling; it is easy to pattern-match "add a ceiling" onto this problem too.
**How to avoid:** Re-read § CAP-05 Design. `enabled` has no "how much" to bound — it is binary. The fix is removing an inappropriate gate, not adding a new one. Zero new columns, zero new migrations for CAP-05.
**Warning signs:** Any CAP-05 task that touches `alembic/versions/` or `capability_envelope.py` (the ORM model) — neither should change for this requirement.

### Pitfall 4: Executing the adapter call inline in the resolve route
**What goes wrong:** A plan writes `POST .../resolve` to synchronously call `get_adapter_for_skill` and the provider method before returning a response, because it is simpler than wiring a Celery task.
**Why it happens:** Avoids the ceremony of a new task + `.delay()` + polling/SSE story on the frontend.
**How to avoid:** This directly violates both CLAUDE.md's "FastAPI never does work inline" and `get_adapter_for_skill`'s own docstring prohibition. Follow the two-step (route claims, task executes) design in § ACT-07 Design.
**Warning signs:** Any import of `provider_adapter` or `get_adapter_for_skill` inside `apps/api/app/api/v1/*.py`.

## Runtime State Inventory

*(This section applies to rename/refactor/migration phases. Phase 22 is neither a rename nor a refactor — it adds new capability to existing tables/routes. The one genuinely migration-adjacent question — whether a new control-DB migration is needed for a `resolved_by`-style column — is addressed as an explicit Open Decision below rather than assumed either way. Omitted otherwise per the trigger condition.)*

## Open Decisions

No CONTEXT.md exists for this phase (no discuss-phase pass was run, matching Phases 15-19 on this track). The planner must own and close each of the following, recording the resolution in the plan's "Open Decisions Resolved" section (the established house pattern from `18-01-PLAN.md`/`19-01-PLAN.md`).

### (a) Does the resolver re-check identity verification at all?

**Finding:** No mechanism exists for a resolver to re-verify the customer's identity — `verified_session_token` is a per-turn `ContextVar`, never persisted, and the approver is an admin, not the customer. IDV was already satisfied once (if required) before the original call reached the Actor's `require_human` verdict (step 2.5 precedes step 5).

**Recommendation:** Skip IDV re-checking on approval, explicitly and by design — the original call already passed it once, and there is no honest way to re-check it without either (a) inventing a synthetic "approved on the customer's behalf" session (a real security regression — approving the money-moving action is not the same act as verifying the customer's identity), or (b) requiring the customer to re-verify out-of-band before the approver can act (a materially larger scope addition, likely a v1.2 concern). `[ASSUMED — this is the researcher's recommendation; the planner must lock it explicitly]`

### (b) Is a periodic expiry-sweep Celery task in scope for this phase, or is the lazy check sufficient?

**Finding:** SC2's literal wording ("on rejection and on expiry it never executes") is fully satisfied by the lazy check inside the resolve route alone — an unresolved, expired row that nobody ever clicks Approve on never executes, by construction, with or without a sweep. A sweep's only value is queue-hygiene (the approver's UI should not show a 3-week-old row as "pending" when nobody will act on it) and, secondarily, marking it `resolved_at`/`resolution='expired'` so it stops appearing at all. `require_human`-created rows carry no `action_reference` (that field is unique to `confirm_action_tool`'s rows), so the partial unique index `uq_pending_confirmations_unresolved` does **not** block a retried action on an expired-but-unresolved `require_human` row — confirmed by reading the index definition and the `require_human` branch's `arguments=raw_args` (no `action_reference` key ever present).

**Recommendation:** Build the sweep as a small, optional addition (`pipeline` queue, mirrors `check_index_staleness`'s shape and rationale exactly — `acks_late=True`, no `tenant_id`/`conn_str` needed since `pending_confirmations` lives in the control DB), but treat it as non-blocking for SC2 — the lazy check is the load-bearing guarantee. If time-boxed, ship the lazy check first and the sweep second (or defer the sweep explicitly, recorded as an accepted gap, matching this project's established pattern of naming rather than silently absorbing scope cuts). `[ASSUMED — the researcher's recommendation given the codebase's actual constraints; the planner must lock it explicitly]`

### (c) Does `pending_confirmations` need a `resolved_by` (or similar) column, i.e. a new migration?

**Finding:** The current schema (`pending_confirmations`: `id, agent_id, skill, arguments, requested_at, expires_at, resolved_at, resolution`) has no column identifying *which* authenticated tenant/admin resolved a row. There is also no per-tenant admin/user identity concept in this codebase yet beyond the single API-key-authenticated tenant (`AUTH-02`/`AUTH-03` — team members and RBAC — are explicitly v2/out of scope per `REQUIREMENTS.md`), so "who approved" today can only ever mean "the authenticated tenant," which is already implicit in the IDOR-guarded route (only the owning tenant can reach this agent's confirmations at all).

**Recommendation:** No new column is strictly required for SC2/SC3 as worded — `resolution` (`'approved'|'rejected'|'expired'`) plus `resolved_at` already satisfies "the row is marked resolved." If the planner wants audit-trail parity with `tool_calls_audit.actor_decision`/`actor_rationale` (which this research recommends recording as `"approved_by_human"` in the *new* audit row the resolver writes, not on `pending_confirmations` itself), that is sufficient without a schema change. If a future requirement needs finer-grained "which admin" tracking, that is a v2/RBAC concern, not this phase's. **If the planner does choose to add a column, the new migration is `0020`** — confirmed the current control-DB head is `0019` (`apps/api/alembic/versions/0019_blast_radius_capability_v2.py`, `Revises: 0018`; no `0020` file exists in the directory listing taken this session). `[ASSUMED — the researcher's recommendation; the planner must lock it explicitly]`

### (d) Where does the approver-facing confirmations queue live in the admin UI?

**Finding:** The Gotham ops room (`apps/admin/app/agents/[id]/page.tsx`) is a **fixed six-region layout** (Live, Retrieval health, The bench, Judgement, Adversary, The prompt) per UI-SPEC S6.4, established and parity-tested in Phase 20. Adding a seventh region there would need to revise that fixed contract. The Deploy page (`apps/agents/[id]/deploy/page.tsx`) already hosts the money-moving capability panel (six `CapabilityZone`s) and the blast-radius/envelope-acknowledgement block — the same page an owner reviews before enabling a skill (CAP-05) is a natural home for the confirmations an *enabled* skill can generate.

**Recommendation:** Place the confirmations queue on the Deploy page, likely as a new labeled sub-block below or beside the capability panel, rather than as a seventh ops-room region — lower risk (no fixed-region contract to revise) and higher coherence (the two controls this phase adds are both about the same money-moving surface). This is properly a UI-SPEC-pass decision, not a research decision; flagged here only so the UI-SPEC pass that follows this research starts from a considered default rather than an unexamined one. `[ASSUMED — the researcher's recommendation; the UI-SPEC pass must confirm or override it]`

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Removing the platform-default gate on `enabled` (both directions unconditional) is the correct fix, not adding a new owner-controlled ceiling | § CAP-05 Design | Low — reversible; the comparator change is a single branch, easy to revisit if a future requirement wants a bounded "enable ceiling" concept |
| A2 | IDV should NOT be re-checked at resolution time | § Open Decisions (a) | Medium-high if wrong — if the product actually wants a fresh customer re-verification step before an approved action executes, this changes the resolver's shape materially (it would need to hold the row in a THIRD state — "approved, awaiting customer re-verification" — not modeled anywhere today) |
| A3 | A periodic expiry sweep is not load-bearing for SC2 | § Open Decisions (b) | Low — the lazy check is a strictly stronger guarantee (it holds even if the sweep never runs); if wrong, the fix is additive (build the sweep), not corrective |
| A4 | No new `pending_confirmations` column is required | § Open Decisions (c) | Low — additive if wrong; a follow-up migration is cheap and does not require reworking the resolve/execute flow |
| A5 | The confirmations queue belongs on the Deploy page, not a new ops-room region | § Open Decisions (d) | Low — a UI-only placement decision the UI-SPEC pass can override without touching backend design |
| A6 | `require_human`-created `pending_confirmations` rows never populate `action_reference`, so the partial unique index does not bound retries of the same action | § Open Decisions (b) | Medium if wrong — verified directly by reading `tools.py`'s `require_human` branch (`arguments=raw_args`, no `action_reference` key) and the six mutating tools' Pydantic Input schemas (none define an `action_reference` field); only `ConfirmActionInput` has one. High confidence this reading is correct, but it was not exercised by a live query this session |

## Open Questions

1. **What HTTP status should a resolve attempt on an already-resolved row return — 404, 409, or 200-idempotent?**
   - What we know: the atomic claim distinguishes "row exists but already resolved" from "row does not exist or belongs to another agent" only if the query is written to distinguish them (the `_get_owned_agent` 404-on-both-branches convention deliberately does NOT distinguish missing-vs-foreign for IDOR reasons, but that convention exists to prevent existence-leakage across tenants, not within a tenant's own already-resolved rows).
   - What's unclear: whether "you already resolved this" should be a loud 409 (this research's default assumption) or a quiet 200 (idempotent-looking) to a double-click from the SAME admin.
   - Recommendation: 409 for a genuinely already-resolved row (informative, not an IDOR leak since it only fires after the IDOR-guarded ownership check has already passed) and 404 only for the missing/foreign-agent case via `_get_owned_agent`, unchanged from the existing convention.

2. **Should the GET queue route return only `resolved_at IS NULL` rows by default, or all rows with a filter param?**
   - What we know: an approver's primary need is "what needs my attention now" (unresolved); a secondary need (audit/history) is "what did I already decide."
   - What's unclear: whether Phase 22's UI needs the history view at all, or whether that is deferred.
   - Recommendation: default to unresolved-only (matches the "queue" framing), with an optional `?status=all` or `?status=resolved` param left for the UI-SPEC pass to decide is in-scope.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio (`asyncio_mode = "auto"`), already configured |
| Config file | `apps/api/pyproject.toml § [tool.pytest.ini_options]` |
| Quick run command | `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/unit -q --ignore=tests/unit/test_chunking_service.py --ignore=tests/unit/test_docling_service.py` |
| Full suite command | same, plus `-m integration` gated on `INTEGRATION_TESTS_ENABLED=1` with real local Postgres + Redis |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CAP-05 | `enabled: False -> True` PATCH now returns 200 and commits | unit | `pytest tests/unit/test_capability_routes.py -k enabled -q` | ✅ existing file — the `[enabled]` case in `test_patch_rejects_each_loosening_field` must be changed to assert success, and a new `test_patch_allows_enabling_previously_disabled_skill` added |
| CAP-05 | Every OTHER field's tighten-only comparator is unchanged (regression) | unit | `pytest tests/unit/test_capability_routes.py -q` (full class) | ✅ existing file |
| ACT-07 (approve) | Approving a `require_human` row executes the adapter exactly once and marks the row resolved | unit (mocked adapter) + integration (`autonomous:false`, real control DB) | `pytest tests/unit/test_confirmation_resolution.py -q` (new); `pytest tests/integration/test_act07_resolve_live.py -m integration -q -s` (new) | ❌ Wave 0 — both new |
| ACT-07 (reject) | Rejecting never calls the adapter | unit | `pytest tests/unit/test_confirmation_resolution.py -k reject -q` (new) | ❌ Wave 0 |
| ACT-07 (expiry) | An expired, unresolved row cannot be approved after its `expires_at` | unit | `pytest tests/unit/test_confirmation_resolution.py -k expir -q` (new) | ❌ Wave 0 |
| ACT-07 (SC3) | A confirmation created before a tightened envelope cannot execute against the pre-tightening ceiling | unit (mocked capability read) | `pytest tests/unit/test_confirmation_resolution.py -k tighten -q` (new) | ❌ Wave 0 |
| ACT-07 (no loop) | The resolver never calls `call_actor_gate` | unit (assert-not-called / `inspect.getsource` absence, mirroring the `derive_blast_radius_warnings` regression-test pattern from 18-05) | `pytest tests/unit/test_confirmation_resolution.py -k actor_gate -q` (new) | ❌ Wave 0 |
| VER-01 SC2 re-run | End-to-end: owner enables `issue_refund`/`place_order` through the shipped UI, a `require_human` case is approved through the new queue and executes | manual, `checkpoint:human-verify` | scripted `<how-to-verify>` runbook against `19-UAT.md` item 1's original steps, re-transcribed | ❌ Wave 0 — new UAT record for Phase 22, referencing `19-UAT.md`'s original script |

### Sampling Rate
- **Per task commit:** `pytest tests/unit -q --ignore=tests/unit/test_chunking_service.py --ignore=tests/unit/test_docling_service.py`
- **Per wave merge:** full unit suite + the new `autonomous:false` integration module collected-but-skipped (`INTEGRATION_TESTS_ENABLED` unset)
- **Phase gate:** the live `autonomous:false` gate (ACT-07's real-adapter-call proof, and VER-01 SC2's re-run) run for real by the operator before `/gsd-verify-work 22`, mirroring the exact deferral pattern Phases 13/15/16/17/18/19 already established

### Wave 0 Gaps
- [ ] `apps/api/tests/unit/test_capability_routes.py` — update the `[enabled]` parametrize case (CAP-05)
- [ ] `apps/api/tests/unit/test_confirmation_resolution.py` — new file, ACT-07 core logic
- [ ] `apps/api/tests/integration/test_act07_resolve_live.py` — new file, ACT-07 live-DB proof (`autonomous:false`)
- [ ] `.planning/phases/22-.../22-UAT.md` — new file, VER-01 SC2 re-run transcript, following the `19-UAT.md` house format
- [ ] Framework install: none

## Security Domain

`security_enforcement` is absent from `.planning/config.json` — treated as enabled.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-------------------|
| V2 Authentication | No (new code) | Both new routes reuse `get_current_tenant` (existing X-API-Key/Bearer) — no new auth surface; there is no distinct "approver" role in this codebase (RBAC is v2/out of scope), so "approver" = "the authenticated tenant," identical to every other admin route |
| V3 Session Management | No (new code) | No new session handling introduced |
| V4 Access Control | Yes | The new resolve/list routes MUST reuse `_get_owned_agent` verbatim (404-on-both-branches IDOR pattern already shipped in `capability_envelopes.py`/`prompt_versions.py`) |
| V5 Input Validation | Yes | The resolve route's body needs a Pydantic model (`{"resolution": Literal["approved", "rejected"]}`) with `extra="forbid"`, mirroring `CapabilityEnvelopeUpdate`'s convention |
| V6 Cryptography | No | No new cryptographic surface; reuses the existing `fernet_decrypt` call for `conn_str` |
| V7 Error Handling & Logging | Yes | The resolver's approved-execution path must write exactly one `tool_calls_audit` row on every outcome (success or deny), matching AUD-01 symmetry — this is a NEW call site for that guarantee and needs its own test, not an assumption that reusing existing code makes it automatic |

### Known Threat Patterns for this phase's scope

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| The new resolve/list routes reachable by a tenant that does not own the agent | Elevation of Privilege | `_get_owned_agent`, verbatim, first statement in both routes |
| Double-approval / concurrent resolve produces two adapter calls | Tampering | Atomic `UPDATE...WHERE resolved_at IS NULL...RETURNING` claim (Pattern 1) — only one caller ever sees `"reserved"`-equivalent success; combined with a fresh `reserve_idempotency` claim inside the Celery task as a second, independent layer |
| An approval executes against a capability the owner tightened or disabled after creation | Elevation of Privilege | Re-run `check_capability_access`/`apply_rate_and_constraint_checks` against the LIVE envelope inside the Celery task — never against the stored `capability_snapshot` |
| A resolver re-invokes the Actor and creates an infinite/duplicate `require_human` loop | Denial of Service (of the feature itself) | The narrow step-2/3/4/6/7 helper never imports or calls `call_actor_gate` — regression-tested by an `inspect.getsource`-style absence check, mirroring the pattern `18-05`'s `derive_blast_radius_warnings` regression test already established for a different function |
| The `enabled` comparator fix accidentally also loosens the OTHER five fields | Elevation of Privilege | The five other branches are untouched code; a regression suite run (`test_capability_routes.py`'s full class, unmodified except the one `[enabled]` case) proves this |
| The approved-execution Celery task's enqueue fails after the route already claimed the row as `'approved'` | Repudiation (a claimed decision with no corresponding executed or denied outcome) | Named explicitly as an accepted residual risk (§ ACT-07 Design) rather than silently engineered around; not a blocking finding for this phase |

## Project Constraints (from CLAUDE.md)

- **Connection strings never in Celery task args (rule 4):** the new `resolve_approved_confirmation` task takes only `confirmation_id` (itself sufficient to look up `agent_id` from the already-claimed row); `conn_str` is decrypted at runtime inside the task via `fernet_decrypt(agent.neon_connection_string)`, exactly as `worker/tasks/runtime/agent.py` already does.
- **`acks_late=True` AND idempotency, both always (rule 5):** the new task sets `acks_late=True`; idempotency is provided by the fresh `reserve_idempotency` claim inside the task body, not by Celery's own redelivery semantics alone — a redelivered task re-enters `reserve_idempotency` and finds `"replay"`/`"in_progress"`, never double-executing.
- **No Docker (rule 9):** every command in this research and in the resulting UAT runbook is a local process (`redis-server`, local PostgreSQL, `uvicorn`, `celery -A app.worker.celery_app worker`).
- **No pg_search/pgbm25:** not relevant to this phase's scope (no retrieval work touched).
- **Langfuse v4 API only; Ragas 0.4.x only:** not relevant to this phase's scope.
- **`docling`/`docling_core` not installed:** `test_chunking_service.py`/`test_docling_service.py` must stay `--ignore`d in every full-suite command this phase's plans specify.
- **No live Postgres on this machine; `CONTROL_DB_URL` is live Neon production:** any live migration roundtrip (if a `0020` migration is added), the ACT-07 live-adapter integration test, and VER-01 SC2's re-run are all inherently `autonomous:false` operator gates — say so explicitly in every plan step that needs a real database, mirroring how Phases 13/15/16/17/18/19 handled theirs.

## UI Considerations

*(A UI-SPEC pass follows this research — the findings below are inputs to that pass, not a substitute for it.)*

The shipped admin console is the GOTHAM "Bone on Graphite" system (`apps/admin/app/globals.css`'s `--ch-1..4`/`data-gate` token set, confirmed current — the repo-root `DESIGN.md` itself records the older `wchats-design` skill's "Hillbrow at Dusk" direction as superseded). Ground every recommendation below in what `apps/admin` actually implements today, not the older skill.

### CAP-05 — the enable control

- The checkbox, label, and `onChange` wiring already exist and are correct (`deploy/page.tsx:1133-1144`). The only required change is removing the `disabled={enabledLocked}` lock and its caption branch ("Cannot re-enable - the platform default is off for this skill.").
- **What must the new, unlocked state communicate?** Once unlocked, ticking the box makes a real, immediate PATCH (matching every other control on this panel's pattern — no staged-confirm step exists for this checkbox today, unlike the numeric `rate_limit`/`max_amount_cents` fields, which use a stage-then-confirm pattern (`requestRate`/`pendingRate`/`confirmRate`) specifically because they are higher-friction, higher-consequence numeric writes). **Open question for the UI-SPEC pass:** should enabling a skill for the first time get the same staged-confirm treatment the numeric fields already have, given it is arguably the single highest-consequence flip on the panel (it is what makes every other control's ceiling actually reachable by the agent)? This research does not resolve it — flagged for the UI-SPEC pass.
- The caption's replacement copy needs to say something true and specific in both directions — e.g. "Enabled." / "Disabled. Turn this on to let the agent use this skill." — never re-introduce language implying a platform floor that no longer exists.
- `docs/guides/owner-capability-guide.md` (DOC-03, already shipped) currently narrates the OLD, locked behavior verbatim (its own text: "the checkbox is permanently disabled, with the caption 'Cannot re-enable...'" and "a skill is switched on today only by a direct database action outside the owner's control"). **This guide will become factually wrong the moment CAP-05 ships** and must be corrected in the same phase — not left stale the way `18-10`'s note briefly was. This is not itself a DOC requirement ID for Phase 22, but shipping CAP-05 without fixing DOC-03's now-false claim reintroduces exactly the class of defect Phase 19's own verification (CR-01/CR-02) found and fixed once already.

### ACT-07 — the approver's confirmation queue

- **Must show, per pending row:** skill (human label, reusing `SKILL_LABELS` from `deploy/page.tsx`), the proposed arguments in a readable (not raw-JSON) form, when it was requested, when it expires, and — once resolved — the resolution and when.
- **Must prevent:** approving a row whose envelope has since been tightened below the stored arguments (the backend denies this, per § ACT-07 Design — but the UI should surface *why* an approval attempt failed with the same specific reason string the backend returns, e.g. `capability.denial:max_amount_cents`, translated to plain language, not a generic "failed" toast) and double-submitting an approve/reject click while a request is in flight (the panel already has an established `aria-disabled`-during-save convention across every other control on this page — reuse it, do not invent a new busy-state pattern).
- **Empty state:** "No confirmations are waiting for your review." — matching the established `EmptyState`/`blast-note` voice already used elsewhere on this page for "nothing to report" states (e.g. the blast-radius block's "No transactional skill is enabled for this agent. There is no blast radius to report.").
- **Placement:** see § Open Decisions (d) — recommend the Deploy page rather than a new ops-room region, pending UI-SPEC confirmation.
- **Accessibility:** each row needs an accessible name distinguishing it from every other pending row (mirrors the `cap-${skill}-label` pattern already used for the six capability Zones) — a screen reader reading "Approve, button" six times with nothing distinguishing rows is the exact class of defect this page's own code comments (`Zone as="section" aria-labelledby=...`) already document having fixed once, for a different control.

## Code Examples

### CAP-05: the comparator fix
```python
# Source: apps/api/app/services/capability_service.py:307-313 (current, to be replaced)
if "enabled" in proposed:
    current_enabled = bool(current.get("enabled", False))
    proposed_enabled = bool(proposed["enabled"])
    if proposed_enabled and not current_enabled:
        if not default_entry.get("enabled", False):
            return _reject("loosen_enabled", "enabled")

# Replacement — both directions unconditional, matching requires_confirmation/
# requires_identity_verification's treatment of a boolean safety switch, but in
# the opposite (capability) direction. No other branch in this function changes.
# (enabled is intentionally not compared at all past this point — there is
# nothing further to check for a pure boolean toggle.)
```

### ACT-07: the atomic claim (route-level, safe to run inline)
```python
# Source: pattern adapted from apps/api/app/services/transactional/idempotency.py's
# reserve_idempotency (INSERT...ON CONFLICT...RETURNING), applied to an
# UPDATE...WHERE...RETURNING claim since the row already exists.
result = await db.execute(
    sa_text(
        "UPDATE pending_confirmations "
        "SET resolved_at = now(), "
        "    resolution = CASE WHEN expires_at IS NOT NULL AND expires_at < now() "
        "                       THEN 'expired' ELSE :requested END "
        "WHERE id = :id AND agent_id = :agent_id AND resolved_at IS NULL "
        "RETURNING id, skill, arguments, resolution"
    ),
    {"id": str(confirmation_id), "agent_id": str(agent_id), "requested": body.resolution},
)
claimed = result.mappings().first()
if claimed is None:
    raise HTTPException(status_code=409, detail="Already resolved or not found")
if claimed["resolution"] == "approved":
    resolve_approved_confirmation.delay(str(confirmation_id))  # runtime queue, acks_late=True
```

### ACT-07: conn_str decryption inside the Celery task (existing pattern, reused verbatim)
```python
# Source: apps/api/app/worker/tasks/runtime/agent.py:724 (existing, unchanged usage)
from app.core.security import fernet_decrypt
conn_str = fernet_decrypt(agent.neon_connection_string)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| `enabled` treated as a tightness dimension bound by a permanently-`False` platform default | `enabled` treated as a bidirectional owner-controlled authorization toggle, unconditional in both directions | This phase (CAP-05) | Unblocks VER-01 SC2's first structural cause; zero change to any other field's tighten-only semantics |
| `pending_confirmations` rows created by two paths, resolved by zero | A single resolve route + a narrow, Actor/IDV-skipping re-entry into a subset of the dispatcher | This phase (ACT-07) | Unblocks VER-01 SC2's second structural cause; closes threat `T-19-04` |

**Deprecated/outdated:** `docs/guides/owner-capability-guide.md`'s current "cannot re-enable" narration becomes stale the moment CAP-05 ships (see § UI Considerations) — must be corrected in this phase, not left for a future docs pass.

## Sources

### Primary (HIGH confidence — read directly from the working tree this session)
- `apps/api/app/services/capability_service.py` — full `validate_tighten_only`, `PLATFORM_CAPABILITY_DEFAULTS`, `parse_actor_mode`
- `apps/api/app/api/v1/capability_envelopes.py` — GET/PATCH routes, `_get_owned_agent` IDOR guard, first-write-baseline logic
- `apps/api/app/schemas/capability.py` — `CapabilityEnvelopeUpdate`/`Response`/`ListResponse`
- `apps/api/app/models/capability_envelope.py` — ORM model, `server_default=false`
- `apps/api/app/services/transactional/tools.py` — full dispatcher, all 7 handlers, the `require_human` branch, every audit-write site
- `apps/api/app/services/actor_seam.py` — `call_actor_gate`, the skip short-circuit
- `apps/api/app/services/transactional/enforcement.py` — `check_capability_access`, `apply_rate_and_constraint_checks`, `_parse_rate_limit`, the `ratelimit:{agent_id}:{skill}:{window_key}` Redis key shape
- `apps/api/app/services/transactional/idempotency.py` — `reserve_idempotency`'s atomic-claim idiom, `finalize_idempotency`, `release_idempotency`
- `apps/api/app/services/transactional/audit.py` — `write_audit_row` signature and contract
- `apps/api/app/services/transactional/provider_adapter.py` — `get_adapter_for_skill`'s docstring constraint, `ProviderAdapter` ABC
- `apps/api/app/services/transactional/registry.py` — `TransactionalToolDef`, `TOOL_REGISTRY`, per-skill `requires_identity_verification` flags
- `apps/api/app/services/agent_tools.py:727-800` — `build_tool_server`, every ContextVar it populates
- `apps/api/app/services/red_team_probe.py` — `invoke_probe_tool`, `_build_transactional_probe_fn` (the only existing out-of-band dispatcher-entry precedent)
- `apps/api/app/worker/tasks/runtime/agent.py` — `fernet_decrypt(agent.neon_connection_string)` pattern, `build_tool_server` call site
- `apps/api/app/worker/tasks/pipeline/staleness.py` — Celery task shape precedent (`acks_late=True`, no tenant secrets in args, `pipeline` vs `runtime` queue rationale)
- `apps/api/app/models/pending_confirmation.py` — full ORM model, the partial unique index definition
- `apps/api/alembic/versions/0019_blast_radius_capability_v2.py`, `0016_pending_confirmations_dedup_index.py` — migration chain confirming control-DB head is `0019`
- `apps/api/app/core/database.py` — confirms `get_sync_db`/`get_async_db` are both control-DB sessions (relevant to whether the resolver needs any tenant conn_str at the route layer — it does not)
- `apps/admin/app/agents/[id]/deploy/page.tsx` — the full capability panel, `enabledLocked`, the blast-radius/envelope-acknowledgement blocks, every copy string quoted
- `apps/admin/app/agents/[id]/page.tsx` — the fixed six-region ops-room layout (relevant to § Open Decisions (d))
- `apps/api/tests/unit/test_capability_routes.py` — the existing `test_patch_rejects_each_loosening_field[enabled]` case that must change
- `.planning/REQUIREMENTS.md`, `.planning/STATE.md`, `.planning/ROADMAP.md` — phase context, traceability, the Phase 19 gap-closure record
- `.planning/phases/19-documentation-v1-1-verification/19-VERIFICATION.md`, `19-UAT.md`, `19-RESEARCH.md` — the origin analysis this phase closes
- `.planning/config.json` — `nyquist_validation: true`, `security_enforcement` absent (enabled)
- `docs/guides/owner-capability-guide.md` — confirmed the currently-shipped, soon-stale "cannot re-enable" narration

### Secondary (MEDIUM confidence)
- None used — all findings in this document trace to a directly-read source file or planning artifact in this repo.

### Tertiary (LOW confidence)
- None — this phase's domain is entirely internal to the codebase; no external library or third-party behavior claim was needed.

## Metadata

**Confidence breakdown:**
- CAP-05's comparator analysis and fix: HIGH — every claim traces to source read directly this session; the fix is a deletion, not new logic, and its blast radius (five untouched branches) was verified by reading the whole function
- ACT-07's step-by-step dispatcher analysis: HIGH on what each step checks and its ordering (read directly from `tools.py`); MEDIUM on the specific resolver architecture recommended, since no prior art for "resolve a pending confirmation" exists in this codebase — the design is derived by analogy to `red_team_probe.py`'s out-of-band entry pattern and `idempotency.py`'s atomic-claim idiom, not verified against a precedent that already solved this exact problem
- The "no out-of-band identity" and "FastAPI never does work inline" findings: HIGH — both are direct, load-bearing facts confirmed by reading `agent_tools.py`'s ContextVar definitions and `provider_adapter.py`'s docstring, not inferred
- UI Considerations: MEDIUM — grounded in the actual shipped `deploy/page.tsx` and `page.tsx` source, but placement/staging recommendations are explicitly flagged as researcher defaults for the UI-SPEC pass to confirm, not locked decisions

**Research date:** 2026-07-28
**Valid until:** Effectively until CAP-05/ACT-07 ship or the underlying files change — re-check `capability_service.py`'s `validate_tighten_only` and `tools.py`'s dispatcher step order before planning if either is touched by an intervening change (neither is expected to be, since Phase 22 is the only phase currently scoped to touch them).
