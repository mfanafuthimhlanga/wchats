# Phase 19: Documentation + v1.1 verification - Research

**Researched:** 2026-07-27
**Domain:** Technical/owner documentation authoring + end-to-end verification harness design for a transactional agent platform
**Confidence:** MEDIUM (the shipped surfaces are HIGH confidence — read directly from source; the verification-harness *design* for VER-01/AUD-03 is necessarily MEDIUM/LOW because neither has any prior art in this repo — 18-11 explicitly deferred RTX-04's live run and did not attempt a 100-message harness or a 30-day audit-gap test)

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DOC-01 | Tool-author guide | § Shipped Surface: Transactional Tool Contract documents the exact dispatcher contract, registry shape, and schema conventions a tool author writes against |
| DOC-02 | Integration-provider guide | § Shipped Surface: Integration Adapters + Credential Service documents `ProviderAdapter`, `get_adapter_for_skill`, the four shipped adapters, and the existing `docs/runbooks/integration-credentials.md` this guide extends |
| DOC-03 | Owner-facing capability-configuration guide | § Shipped Surface: Capability Envelope documents the GET/PATCH contract, `PLATFORM_CAPABILITY_DEFAULTS`, and the already-built (18-10) admin UI copy this guide narrates |
| VER-01 | v1.1 success-criteria gate | § Critical Finding: the Pending-Confirmation Dead End and § Open Decisions (b) directly scope what "non-technical tester, end-to-end, without code" can mean given the shipped substrate |
| AUD-03 | Zero audit gaps across 30 days of synthetic mutating traffic | § Open Decisions (c) and § Audit Substrate design the only two buildable constructions given the actual `tool_calls_audit` schema and the actual dispatcher code |
</phase_requirements>

## Summary

Phase 19 is a documentation-plus-verification phase with **no code substrate of its own** — it writes guides about code four other phases already shipped, and it proves claims about code Phase 18 shipped only 9 of 11 plans of. The three guides (DOC-01/02/03) are well-supported: every contract they need to document — the transactional dispatcher, the four provider adapters, and the capability-envelope API — is fully implemented, unit-tested, and stable. Ground every guide section in the literal code excerpted below, not in the PRD's aspirational language.

VER-01 and AUD-03 are a different problem. Neither has any prior art in this repo: no requirement before Phase 19 asked for a 100-message adversarial harness or a 30-day audit-gap test, and 18-11 (the plan that would have run the closest analog, RTX-04) is itself unexecuted. Research surfaced one blocking discovery that changes how the planner must scope VER-01: **the `require_human` path the Actor validator can take on any mutating call has no resolution route anywhere in the codebase.** `pending_confirmations` rows are created by two code paths and read/approved by zero. Phase 18's own closing plan (18-11, unexecuted) explicitly flagged this and explicitly named Phase 19 as the phase where it "may surface... as a blocker, at which point it becomes explicit." It has now surfaced. The planner must choose, and must record the choice as a locked decision: either build a minimal approve/reject route for `pending_confirmations` as in-scope Phase 19 work, or configure the VER-01 demo tenant so `require_human` is structurally unreachable during the happy-path proof (and treat "the agent can end up in `require_human` with no way out" as an accepted, documented gap rather than a silent one).

**Primary recommendation:** Write DOC-01/02/03 as three new files in `docs/` following the existing `docs/runbooks/` and `docs/adr/` conventions (no MDX renderer exists in `apps/admin`, so in-product rendering is out of scope for a documentation-only phase). Scope VER-01 as two separable proofs — a scripted non-technical-tester happy path (refund + Shopify order, Actor never reaching `require_human` by tenant configuration) and a 100-message adversarial harness classified against the seven `verdict_tag`/severity vocabularies Phase 18 already emits — both `autonomous:false` live gates mirroring the house pattern from Phases 13/15/16/17/18. Scope AUD-03 as a seeded-backdated-rows test against the real `tool_calls_audit` schema (no accelerated-clock mechanism exists anywhere in this codebase to inject a fake `now()`), comparing dispatcher-invocation count to audit-row count over a synthetic 30-day window built by writing rows with backdated `created_at` via direct SQL, since `write_audit_row` has no `created_at` parameter.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Tool-author guide (DOC-01) | Documentation (repo) | — | Static markdown describing a backend contract; no runtime component |
| Integration-provider guide (DOC-02) | Documentation (repo) | — | Extends the existing `docs/runbooks/` operator-facing precedent |
| Owner capability guide (DOC-03) | Documentation (repo) | Frontend Server (admin UI, already shipped by 18-10) | The guide narrates UI copy that already exists; it does not add UI |
| VER-01 happy-path proof | API / Backend (dispatcher, Actor, capability envelope) | Frontend Server (Gotham deploy page) | The tester drives the already-shipped admin UI and widget; nothing new is built unless the pending-confirmations gap is closed |
| VER-01 adversarial harness | API / Backend (red-team probe substrate) | — | Reuses Phase 18's `red_team_probe.py` / `red_team_service.py` runners; this is a harness around existing enforcement code, not new enforcement |
| AUD-03 audit-gap test | Database / Storage (`tool_calls_audit`, control DB) | API / Backend (`write_audit_row`) | The gap check is a row-count/coverage query against an existing table; no new audit-writing code path is implied |

## Package Legitimacy Audit

No new external packages are required for this phase. DOC-01/02/03 are markdown files; VER-01/AUD-03 reuse `pytest`, `pytest-asyncio`, and the already-pinned `anthropic`/`claude-agent-sdk` the red-team substrate already depends on (verified present in `apps/api/pyproject.toml` by Phase 18's own package-legitimacy gate). If the planner elects to build a `pending_confirmations` resolution route (see Critical Finding below), it is a plain FastAPI route against the existing `PendingConfirmation` ORM model — no new dependency.

**Packages removed due to [SLOP] verdict:** none — no packages proposed.
**Packages flagged as suspicious [SUS]:** none.

## Shipped Surface: Transactional Tool Contract (what DOC-01 documents)

**Source:** `apps/api/app/services/transactional/tools.py`, `registry.py`, `schemas.py`, `provider_adapter.py` (all read directly, this session). `[VERIFIED: codebase]`

A tool author writes against three files:

1. **`schemas.py`** — one `<Verb><Noun>Input` / `<Verb><Noun>Output` Pydantic model pair per skill. Hard rule (T-14-02-01, enforced by convention not by a runtime check): every field is a typed scalar or enum — no free-form blob, SQL string, URL, or open dict. Every mutating `Input` model carries a required `idempotency_key: str` field. Amount fields are `Annotated[int, Field(ge=0)]` in cents, never a float, so `capability_envelopes.constraints.max_amount_cents` has a directly comparable typed field.
2. **`registry.py`** — one `TransactionalToolDef` entry per skill in the `TOOL_METADATA`/`TOOL_REGISTRY` dict. Every flag (`mutating`, `idempotency_required`, `requires_identity_verification`) is a **literal value set at definition time** — the codebase's own comment states this is "never runtime-inferred from the tool name or arguments" (T-14-02-02). A new tool's author sets these three booleans by hand; there is no derivation.
3. **`tools.py`** — the `@tool`-decorated handler validates input, then calls the single shared dispatcher `_execute_transactional_tool(skill, validated, raw_args, adapter_method)`. **A tool author never touches enforcement order** — it is encoded exactly once, and every mutating handler is a ~15-line wrapper. The order (verified from source, current as of Phase 18):
   1. IN-03 agent-id precondition
   2. Capability check (`check_capability_access` — auth-only, runs on every call including replays)
   3. IDV gate (Step 2.5 — driven by the capability snapshot's `requires_identity_verification`, runs before idempotency reservation so a blocked call never consumes a replay slot)
   4. Reserve idempotency (atomic `INSERT ON CONFLICT` — `replay` / `args_mismatch` / `in_progress` / `reserved`)
   5. Rate + constraint checks (Redis `INCR`+`EXPIRE`, only for the fresh reserved winner)
   6. Actor seam (`call_actor_gate` — `approve` / `block` / `require_human`)
   7. Adapter execute (`get_adapter_for_skill` → `getattr(adapter, adapter_method)(validated, agent_id)`)
   8. Audit row + finalize idempotency

Every rejection branch writes exactly one `tool_calls_audit` row before returning (AUD-01 symmetry, verified across every `return` in the dispatcher). A tool author adding an 8th skill needs: a schema pair, a registry entry, an `@tool` handler calling the dispatcher with `adapter_method=`, and a method on `ProviderAdapter` (abstract) plus every concrete adapter subclass. `confirm_action_tool` is the one exception — `mutating=False`, bypasses the dispatcher entirely, writes a `PendingConfirmation` row directly, gated only by capability check + the IN-03 guard.

**A2A forward-compat note for the guide:** every `TransactionalToolDef` carries `a2a_input_modes`, `a2a_output_modes`, and `examples` fields and a `to_a2a_skill()` serializer, even though no A2A endpoint exists yet (deferred to v1.2). A tool author should fill `examples` with 2-3 plain-English phrasings — this is not decorative, it is consumed by `to_a2a_skill()`.

## Shipped Surface: Integration Adapters + Credential Service (what DOC-02 documents)

**Source:** `apps/api/app/services/transactional/provider_adapter.py`, `apps/api/scripts/provision_integration_credential.py`, `docs/runbooks/integration-credentials.md` (existing file, read directly). `[VERIFIED: codebase]`

An integration provider (someone adding a 5th adapter) writes against:

1. **`ProviderAdapter` (ABC)** — six abstract async methods, one per mutating skill (`place_order`, `cancel_order`, `issue_refund`, `update_subscription`, `book_slot`, `update_customer_record`), each taking the typed `Input` model + `agent_id` and returning the typed `Output` model. `StubProviderAdapter` is the reference no-network implementation every method returns a `[STUB]`-labelled Output from.
2. **`get_adapter_for_skill(skill, agent_id, conn_str)`** — the ONLY entry point that resolves a credential and dispatches to a concrete adapter (Stripe / Shopify / WooCommerce / Calendly are the four shipped `provider_type` values, dispatched by an `if/elif` chain on `config.provider_type` at the bottom of the file). The docstring is explicit: **"MUST NOT be imported or called from any FastAPI route handler or SDK hook — only from `_execute_transactional_tool` (tools.py step 6)."** A 5th provider adds one more `elif` branch + one new adapter module.
3. **`credential_service.py`** — `_fetch_credential_config` / `_derive_tenant_fernet` / `CredentialHandle` (redacted `__repr__`, never logged). Per-tenant Fernet keys are HKDF-derived from `PLATFORM_CREDENTIAL_KEY` + `tenant_id` — the raw credential exists only inside `get_adapter_for_skill`'s stack frame.
4. **The existing runbook** `docs/runbooks/integration-credentials.md` already documents the deploy-time provisioning script (`provision_integration_credential.py`) end to end — prerequisites, per-provider `--config-json` shapes, the single-currency guard (INT-07), and a dry-run mode. DOC-02 should **extend this runbook's audience from "platform operator" to "integration provider building a 5th adapter"**, not duplicate it. The provisioning script itself already documents (in its own docstring) example invocations for all four shipped providers including the Calendly `event_types` dict-not-list gotcha (a real historical bug class, CR-03).
5. **Red-team-mode short-circuit** (Phase 18, `_red_team_mode_var` in `provider_adapter.py`): a module-private `ContextVar`, default `False`, the only sanctioned setter is `red_team_probe.red_team_mode()`. When set, `get_adapter_for_skill` returns the stub singleton **before** any credential fetch. DOC-02 should note this exists so a future adapter author does not assume `get_adapter_for_skill` always touches real credentials during tests — and does not attempt to set the ContextVar from anywhere except the sanctioned context manager.

**ADR precedent to cite in DOC-02:** `docs/adr/0002-agent-tool-and-provisioning-strategy.md` records the locked decision to call provider SDKs directly behind typed tools rather than provider MCP servers or vendor "agent toolkits" — this is the architectural rationale a new integration author needs before reaching for, e.g., the Stripe Agent Toolkit.

## Shipped Surface: Capability Envelope (what DOC-03 documents)

**Source:** `apps/api/app/api/v1/capability_envelopes.py`, `apps/api/app/schemas/capability.py`, `apps/api/app/services/capability_service.py`, and the already-executed `18-10-PLAN.md` (admin UI copy, verified checkpoint-approved). `[VERIFIED: codebase]`

DOC-03's audience is the **business owner**, not a developer. The guide should narrate, not re-derive, the admin UI that plan 18-10 already shipped and got human-approved:

- **`GET /agents/{id}/capability-envelopes`** returns exactly 7 entries (one per `PLATFORM_CAPABILITY_DEFAULTS` key — the six mutating skills plus non-mutating `confirm_action`), synthesizing a `enabled=False`/`updated_at=None` row for any skill with no stored override. The admin UI (18-10) filters this response on `mutating` before rendering, so the owner sees exactly six per-skill panels — `confirm_action` never gets a currency-ceiling control because it moves no money.
- **`PATCH /agents/{id}/capability-envelopes/{skill}`** is **tighten-only**, enforced server-side by `validate_tighten_only` (`capability_service.py`), which runs **before** any DB write — a 422 leaves the row untouched. Platform defaults (`PLATFORM_CAPABILITY_DEFAULTS`) are the ceiling for a first write: an owner cannot configure a skill looser than the platform default even on day one.
- **Fields an owner configures per skill:** `enabled` (bool), `rate_limit` (string, `"N/<minute|hour|day>"`, compared as calls-per-second server-side), `constraints.max_amount_cents` (int cents, can only ever be lowered once set — there is no "clear back to unlimited" control by design), `requires_confirmation` (one-way false→true switch — controls whether the Actor's low-value skip short-circuit is available, see § Critical Finding below), `requires_identity_verification` (one-way false→true switch), `actor_mode` (`off` / `sample_at_rate_N` / `always-on`, ordered by tightness; `off` is not offered at all for a mutating skill).
- **Platform defaults today** (from `capability_service.py`, verified): every mutating skill ships `enabled: False`, `rate_limit: "5/hour"`, `actor_mode: "always-on"`, `requires_confirmation: False`, `requires_identity_verification: False`, and a per-skill `max_amount_cents` ceiling (`place_order` R1000/100 000c, all other five R500/50 000c). An owner must explicitly enable a skill and set (or accept) a ceiling before the agent can act.
- **Blast-radius + envelope-acknowledgement surface** (also 18-10, checkpoint-approved): the owner reads a two-line-per-figure block (configured ceiling vs. observed maximum, never merged) and ticks an acknowledgement bound to a human-legible per-skill summary table before approving deployment; any later capability change invalidates that acknowledgement (`envelope_drift`) and the checklist must be re-run.

**DOC-03's job is therefore small and concrete:** explain what each of the six per-skill controls means in plain business language (e.g., "rate limit" = how many times per hour this action can fire before the agent refuses further attempts), what the blast-radius numbers mean, and what re-triggering the checklist after a config change implies. It should NOT re-litigate the UI-SPEC decisions (D1-D6) — those are implementation details already resolved and shipped.

## Critical Finding: the Pending-Confirmation Dead End (blocks VER-01 SC2 unless scoped around)

**Source:** `apps/api/app/services/actor_seam.py`, `apps/api/app/services/transactional/tools.py`, `apps/api/app/api/v1/*.py` (full route inventory taken this session, see below), and `.planning/phases/18-.../18-11-PLAN.md` (unexecuted, itself names this gap). `[VERIFIED: codebase]`

**The chain:**

1. The Actor validator (`call_actor_gate`) returns `approve | block | require_human` for every mutating call, via a real Haiku judgment (except the skip short-circuit below).
2. **Skip short-circuit** (`actor_seam.py`, ACT-03): Actor is skipped entirely (auto-`approve`) only when `requires_confirmation is False` **AND** the skill's `max_amount_cents` is strictly below `settings.ACTOR_SKIP_MAX_AMOUNT_CENTS` (default **500 cents = R5.00**). Every platform default for the five non-`place_order` mutating skills ships `max_amount_cents=50 000` (R500) — **100x above the skip threshold**. `requires_confirmation` also defaults to `False` platform-wide, but that alone is not sufficient; the amount ceiling must ALSO be tightened below R5 for the skip to engage. **In the platform's default and every realistically-configured tenant, the Actor genuinely runs a live Haiku call on every mutating action and can non-deterministically return `require_human`.**
3. When Actor returns `require_human` (`tools.py`, the `require_human` branch): the idempotency reservation is released, a `PendingConfirmation` row is written to the control DB, and the tool call returns a **non-error** response telling the customer "a confirmation request has been created... the action will proceed only after an authorized approver confirms it." **The adapter is never called. No money moves, no order is placed.**
4. **Nothing in the codebase reads or resolves a `PendingConfirmation` row.** A full route inventory of `apps/api/app/api/v1/*.py` (taken this session — every `@router.get`/`@router.post` in the directory) shows zero routes under any `pending-confirmations` or `confirmations` path. `confirm_action_tool` (the agent-facing tool with the confusingly similar name) does **not** approve an existing pending confirmation — it *creates a new, separate* `pending_confirmations` row (deduplicated by a partial unique index on `(agent_id, skill, action_reference)` when the agent calls it again with the same reference), gated by capability check only. There is no admin route, no widget route, no Celery task, and no CLI script that flips a `PendingConfirmation.resolved_at`/`resolution` field.
5. Phase 18's own plan `18-01-PLAN.md § Open Decisions Resolved` records this as **OQ-1/OS-1 — deliberately out of scope, with no Phase 18 requirement ID covering it**, and `18-11-PLAN.md` (Task 4, unexecuted) was written to record it in STATE.md with the explicit sentence: *"Phase 19's VER-01 end-to-end proof may surface it as a blocker, at which point it becomes explicit."*

**What this means for VER-01 SC2** ("a non-technical tester deploys an agent that issues refunds up to a configured limit and places Shopify orders end-to-end without code"): if the Actor ever judges a legitimate, in-policy refund or order request as `require_human` — which is a real Haiku-model risk on borderline phrasing, not a hypothetical — **the demo dead-ends with no way to complete the transaction**, because no route exists to approve the pending confirmation. This is not a hardening gap; it is a missing feature that sits directly in the critical path of the milestone's own headline success criterion.

**This is an Open Decision the planner must close, not research can close.** See § Open Decisions (b) below for the two viable dispositions.

## Shipped Surface: Red-Team Probe Substrate (what a 100-message VER-01 harness classifies against)

**Source:** `apps/api/app/services/red_team_probe.py`, `red_team_service.py`, `apps/api/app/worker/tasks/runtime/red_team.py` (all read directly, current as of 18-09). `[VERIFIED: codebase]`

`run_red_team` (the Celery task) runs **seven** runners sequentially (`worker_pool=solo`, no chord):

| Runner | Attack class | Deterministic or conversational |
|---|---|---|
| `run_conversation_injection_agent` | Prompt injection via conversation (renamed from `run_prompt_injection_agent`, alias kept) | Conversational (Sonnet victim turn) |
| `run_content_injection_agent` | Poisoned-corpus injection via retrieval | Deterministic (seeds a canary chunk, probes via BM25) |
| `run_data_leakage_agent` | Cross-tenant / PII / system-prompt extraction | Conversational |
| `run_hallucination_agent` | Adversarial framing, false premises | Conversational |
| `run_confused_deputy_agent` | RTX-01 — legitimate-looking conversation, illegitimate action | Conversational |
| `run_value_bound_evasion_agent` | RTX-02 — chained smaller refunds to evade a cap | Deterministic (chains real `issue_refund` calls inside `red_team_mode()`) |
| `run_identity_bypass_agent` | RTX-03 — no session / forged token against the IDV gate | Deterministic (two attempts against Step 2.5) |

Every finding is classified by `classify_severity` (a Haiku call, unchanged since M7) into `low | medium | high | critical`, and by the newer **`ProbeToolResult.verdict_tag`** property (`red_team_probe.py`) into one of exactly seven machine-readable tags derived from the dispatcher's own response text: **`capability_denied`, `identity_required`, `rate_denied`, `actor_blocked`, `awaiting_approval`, `provider_not_configured`, `succeeded`**. `provider_not_configured` is a deliberate sentinel: if it appears in a run against a clean red-team tenant, the `red_team_mode()` short-circuit failed to engage and the run is **invalid, not clean** (this is exactly the vacuous-pass failure Phase 18's own RESEARCH.md flagged as Pitfall 1, and 18-11's `test_clean_tenant_zero_high_severity` was written specifically to assert against before evaluating severity).

**Direct implication for VER-01's "100 synthetic adversarial messages" harness:** the classification vocabulary a 100-message harness needs already exists and is already exercised by the RTX runners. **The planner should not invent a new classifier.** A 100-message VER-01 harness is most naturally built as either (a) an extension of the existing `run_confused_deputy_agent`/RTX runners with a larger `attack_sequences` count, reusing `verdict_tag` to prove "zero unauthorized state mutations" (any finding whose `verdict_tag` is `succeeded` on an attack the operator knows should have been denied is the failure signal — no adapter call with a `succeeded` verdict should be attributable to an adversarial message), or (b) a new, purpose-built script layered on the same `invoke_probe_tool`/`red_team_mode()` substrate, counting messages rather than turns.

**Overlap with 18-11 — must not duplicate:** `18-11-PLAN.md` Task 1 already wrote `test_clean_tenant_zero_high_severity`, an aggregate RTX-04 gate over the three RTX runners against the fixed `CLEAN_TENANT_SPEC` fixture (a clean tenant with all six skills enabled, bounded, one requiring IDV). Task 2 of the same plan is the live human-run of that test plus two other live gates (0019 migration roundtrip, blast-radius/envelope-drift end-to-end). **If 18-11 executes before Phase 19 is planned, RTX-04's clean-tenant zero-high-severity claim is already proven and Phase 19 should cite `18-UAT.md` rather than re-run it.** If 18-11 has NOT executed by the time Phase 19 plans, the planner has two options: (i) treat 18-11's live gate as a hard blocking dependency and require it to run first, or (ii) have Phase 19's own VER-01 harness subsume RTX-04's assertion (same `CLEAN_TENANT_SPEC` fixture, same runners, larger message count) so Phase 19 does not depend on a separate unexecuted phase-18 plan. **Report this precisely to the planner as a phase-dependency risk, not just a note** — Phase 19 lists Phase 18 as a dependency in ROADMAP.md, but "Phase 18 depended-on" and "Phase 18 fully executed" are not the same claim given 18-10/18-11 are `autonomous:false` and unexecuted as of this research date (2026-07-27).

## Audit Substrate for AUD-03 (30-day zero-gap test)

**Source:** `apps/api/app/models/tool_calls_audit.py`, `apps/api/app/services/transactional/audit.py::write_audit_row` (read directly). `[VERIFIED: codebase]`

The `tool_calls_audit` table has a plain `created_at: DateTime(timezone=True)` column with `server_default=now()`. **`write_audit_row` takes no `created_at` parameter** — every row's timestamp is whatever the DB assigns at insert time. There is no accelerated-clock mechanism, no `freezegun`/`time-machine` dependency, and no injected-clock seam anywhere in `apps/api` (verified: no `freezegun` or `time_machine` in `pyproject.toml`; `datetime.now(timezone.utc)` is called directly throughout the transactional code path with no clock abstraction).

**This forecloses one of the two constructions the phase description asked research to evaluate.** An "accelerated/injected clock" test would require either (a) adding a clock-abstraction seam to `write_audit_row` and every caller (a real code change, out of scope for a documentation phase and risky to retrofit into an already-verified security-critical write path), or (b) manipulating the server's wall clock (not viable — this is a shared local dev machine, CLAUDE.md rule 9 context, and would corrupt every other timestamped table in the same DB).

**The only construction the shipped substrate actually supports is seeded backdated rows via direct SQL, after the fact:**
1. Run N synthetic mutating tool calls through the real dispatcher (via `invoke_probe_tool` in `red_team_mode()`, or via a plain test harness driving `_execute_transactional_tool` directly) — this produces real `tool_calls_audit` rows with `created_at = now()`.
2. Immediately after each batch, `UPDATE tool_calls_audit SET created_at = created_at - interval '<n> days' WHERE id = ANY(:ids)` to redistribute the batch's rows across a synthetic 30-day window (e.g., spread N batches evenly across days 0-29 before "today").
3. The **gap check** itself is then a query over that backdated window: for AUD-03 to mean anything, it must assert **coverage parity** — every dispatcher invocation in the synthetic run produced exactly one `tool_calls_audit` row (this is the same guarantee AUD-01 already asserts unit-test-level; AUD-03's "30 days" is really "AUD-01's per-call guarantee integrated across a realistic volume/time spread, with no row silently dropped by a retry, a worker restart, or a Redis blip"). A reasonable concrete assertion: instrument the harness to count dispatcher invocations attempted vs. `tool_calls_audit` rows written (grouped by day), and assert the two counts match for every day in the backdated window, with zero days showing mutating traffic and zero matching audit rows.
4. This is a `checkpoint:human-verify` or `autonomous:false` gate by construction — it needs a real local Postgres the harness is willing to seed and later truncate, mirroring the pattern `18-11-PLAN.md`'s Gate 1/Gate 2 already established for this exact class of "needs a real ephemeral DB" verification.

**Guardrail for the planner:** do NOT construct AUD-03 as "wait 30 real wall-clock days" — that is not executable inside a planning/execution cycle and no prior phase in this project has ever done it (every "30-day" or "nightly" requirement elsewhere in REQUIREMENTS.md — e.g. OPS-01/OPS-02 weekly digests — is proven by code inspection + a single synthetic run, never by literal elapsed time).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Classifying an adversarial probe's outcome | A new pass/fail taxonomy for the 100-message harness | `ProbeToolResult.verdict_tag` (7-way) + `classify_severity` (4-way), both already shipped in `red_team_probe.py` | Building a second classifier risks disagreeing with the one RTX-04 already uses for the same underlying dispatcher responses — two sources of truth for "was this call authorized" is itself a security-relevant inconsistency |
| Driving a synthetic tenant through the transactional dispatcher for testing | A new bypass or test-only code path into `_execute_transactional_tool` | `red_team_mode()` context manager + `invoke_probe_tool` (`red_team_probe.py`) | This is the exact mechanism Phase 18 built precisely so probes exercise the REAL enforcement layers (capability, IDV, rate, Actor) with only the network-facing adapter leaf swapped — reproducing it would silently reintroduce Pitfall 1 (a probe that never reaches enforcement and vacuously passes) |
| Proving audit completeness | A clock-injection seam retrofitted into `write_audit_row` | Seeded-backdated-rows via direct SQL against the real schema (see § Audit Substrate) | No clock abstraction exists in this codebase; adding one to a security-audited write path for a documentation-phase test is a disproportionate and risky change |
| Documenting the capability envelope for owners | A new UI or new copy | Narrate the already-built, already-human-checkpoint-approved 18-10 admin UI | 18-10's Copywriting Contract (UI-SPEC) already defines every string the owner sees; DOC-03 should reference it, not re-author it |

**Key insight:** every piece of machinery Phase 19's verification work needs — the probe substrate, the verdict vocabulary, the clean-tenant fixture, the ContextVar-based enforcement bypass for the provider leaf only — was purpose-built by Phase 18 for exactly this kind of adversarial proof. The temptation in a "prove the milestone" phase is to build a fresh, simpler-looking test harness from scratch; resist it, because the existing substrate is what makes a finding trustworthy (it proves enforcement was actually exercised, not merely that a Haiku call happened).

## Common Pitfalls

### Pitfall 1: Treating "Phase 18 depended-on" as "Phase 18 fully executed"
**What goes wrong:** The planner schedules Phase 19 work assuming RTX-04, the capability admin UI, and the blast-radius UI are proven and stable, when 18-10 and 18-11 are `autonomous:false` and unexecuted as of this research.
**Why it happens:** ROADMAP.md lists "Depends on: Phase 14, 15, 16, 17, 18" without qualifying which of Phase 18's 11 plans that dependency actually needs.
**How to avoid:** The planner must explicitly state, per DOC/VER/AUD requirement, whether it needs 18-10/18-11 executed first, and if so, make that an explicit blocking precondition rather than an implicit assumption.
**Warning signs:** A VER-01 plan step that reads `report.blast_radius` or `envelope_drift` from a live checklist run, or that assumes `test_clean_tenant_zero_high_severity` already has a recorded result.

### Pitfall 2: Assuming ACTOR_SKIP_MAX_AMOUNT_CENTS makes the Actor optional for a realistic demo
**What goes wrong:** A plan configures the VER-01 demo tenant believing the R5 skip threshold means most refunds bypass the Actor.
**Why it happens:** The skip condition looks like a broad optimization at first read; it is in fact narrow (both `requires_confirmation=False` AND ceiling `< R5`), and every platform default ships a R500 ceiling.
**How to avoid:** Read § Critical Finding above before writing any VER-01 task. If the demo needs deterministic completion, the tenant's envelope must be explicitly tightened below R5 for the specific skill under test, and that tightening must be stated as a locked demo-configuration decision, not left implicit.
**Warning signs:** A VER-01 plan step whose acceptance criteria assumes a refund "just completes" without addressing what happens if Actor returns `require_human`.

### Pitfall 3: Building AUD-03 as a literal 30-real-day wait
**What goes wrong:** A plan schedules a background Celery beat task to run daily for 30 days before AUD-03 can be marked verified, blocking the milestone indefinitely.
**Why it happens:** The requirement text says "30 days of synthetic mutating traffic," which reads like elapsed time.
**How to avoid:** Use the seeded-backdated-rows construction (§ Audit Substrate). No other project requirement with a time-window name ("weekly", "nightly", "30-day") has ever been proven by literal elapsed time in this codebase.
**Warning signs:** A plan task with a `wait` or `sleep`-style verification step, or an `autonomous:false` gate whose resume signal is "come back in a month."

### Pitfall 4: Re-authoring capability-envelope copy in DOC-03 instead of citing it
**What goes wrong:** The guide invents new explanatory language for "tighten-only" or "blast radius" that drifts from the UI-SPEC Copywriting Contract's locked strings, so the guide and the product say different things.
**Why it happens:** The guide author (an LLM) has full context on the domain and will naturally write fresh, fluent copy rather than checking whether the product's own strings already say it.
**How to avoid:** Pull the locked copy strings directly from `18-UI-SPEC.md § Copywriting Contract` (if that file still exists after 18-10 executed) or from the rendered `deploy/page.tsx` source, and quote them, rather than paraphrasing.
**Warning signs:** DOC-03 uses different terminology for the same control than the screenshot/UI the tester is looking at.

## Runtime State Inventory

*(This section applies to rename/refactor/migration phases. Phase 19 is a documentation + verification phase with no rename/refactor/migration scope — omitted per the trigger condition. The one piece of "runtime state" this phase's own verification work will create — seeded/backdated `tool_calls_audit` rows for AUD-03 — is addressed in § Audit Substrate above, including that it must be truncated/cleaned up after the test, mirroring the ephemeral-DB pattern `18-11-PLAN.md` established.)*

## Open Decisions

No CONTEXT.md exists for this phase (no discuss-phase pass was run, matching Phases 15-18). The planner must own and close each of the following, recording the resolution in the plan's "Open Decisions Resolved" section (the established house pattern from `18-01-PLAN.md`).

### (a) Where do the three guides live?

**Finding:** `apps/admin` has no MDX/markdown renderer (`grep` of `package.json` for `mdx`/`markdown`/`contentlayer` returns nothing), and no `apps/admin/app/**/help|guide|doc*` route exists anywhere. The repo already has two precedents for developer/operator-facing markdown: `docs/adr/` (2 ADRs) and `docs/runbooks/` (1 runbook, `integration-credentials.md`, which DOC-02 directly extends).

**Recommendation:** All three guides live as new files under `docs/` (e.g. `docs/guides/tool-author-guide.md`, `docs/guides/integration-provider-guide.md`, `docs/guides/owner-capability-guide.md`), following the existing `docs/runbooks/integration-credentials.md` structure (Audience / Phase / Scope header, then sections). This requires zero new frontend work, which fits a documentation-only phase. In-product surfacing (an in-app help panel) is a legitimate future enhancement but is **not implied by DOC-01/02/03's wording** and would pull UI work into a phase that has none scoped. `[ASSUMED — this is the researcher's recommendation, not a locked decision; the planner should confirm with the user or lock it explicitly given no CONTEXT.md exists]`

### (b) Is the VER-01 "non-technical tester" a real human UAT pass or a scripted proxy — and how does the Pending-Confirmation Dead End bound the answer?

**Finding:** Every prior v1.1 phase (13, 15, 16, 17, 18) that needed a live, human-judged proof used the identical pattern: an `autonomous:false` `checkpoint:human-verify` task with a scripted `<how-to-verify>` runbook the operator executes and reports results from verbatim, written into a `*-UAT.md` file with an explicit `passed | deferred (operator accepted) | failed` disposition per item — never inferred, never silently skipped. VER-01's "non-technical tester" criterion is more literal than those (a real un-briefed human, not the operator who already knows the system), but the *mechanism* — a scripted checkpoint with a recorded, transcribed outcome — is the only pattern this project has ever used for a live human-judgment gate.

**Recommendation:** Structure VER-01 SC2 as a `checkpoint:human-verify` task whose `<how-to-verify>` is written for a genuinely non-technical reader (the guides from DOC-01/02/03 — specifically DOC-03 — should be the only materials that tester is handed), with the resume signal being the tester's transcribed pass/fail per step, written into `19-UAT.md` using the same disposition vocabulary as `16-UAT.md`/`17-UAT.md`/`18-UAT.md`. **This cannot be scripted as a fully automated proxy** — "does an unbriefed non-technical human complete this without getting stuck" is definitionally a human-judgment question a script cannot stand in for. `[ASSUMED — house pattern extrapolation; the planner must lock this explicitly]`

**The Pending-Confirmation Dead End bounds what SC2 can promise.** The planner has two dispositions, and must pick one explicitly:

- **Option 1 — Build the minimal resolution route.** Add a `POST /agents/{id}/pending-confirmations/{id}/resolve` (or similar) admin route that sets `resolved_at`/`resolution` on a `PendingConfirmation` row and, on approval, re-attempts the tool execution (re-reserving idempotency and calling the adapter). This closes a real, previously out-of-scope gap and gives VER-01 a legitimate story for what happens when Actor returns `require_human`. It is scope creep relative to "documentation + verification" but is directly load-bearing for SC2, and `18-11-PLAN.md` itself anticipated this phase would be where it becomes forced.
- **Option 2 — Configure the demo tenant so `require_human` is structurally avoided.** Tighten the demo tenant's `issue_refund`/`place_order` envelopes so the Actor skip threshold engages deterministically (requires `max_amount_cents < 500` — i.e., demo refunds under R5, which conflicts with "refunds up to a configured limit" reading naturally as a meaningful limit) — OR accept that Actor still runs live but treat any `require_human` outcome during the human UAT pass as **an explicit, recorded, out-of-scope finding** (not a blocking failure) rather than pretending it cannot happen. This preserves phase scope but weakens SC2's "end-to-end" claim and must be stated plainly in `19-UAT.md`, not glossed over.

Whichever option is chosen, **VER-01's plan must state the choice explicitly and record the rationale** — this is exactly the kind of "silently absorbed or silently dropped" gap Phase 18's own STATE.md conventions (see `18-11-PLAN.md` Task 4) require to be named, not implied.

### (c) How is the 30-day synthetic audit-gap test (AUD-03) constructed?

**Finding:** See § Audit Substrate above — no accelerated/injected-clock mechanism exists anywhere in this codebase, so only the seeded-backdated-rows construction is buildable without a risky retrofit to a security-audited write path.

**Recommendation:** Seeded backdated rows via direct SQL `UPDATE` against `tool_calls_audit.created_at`, immediately after a real synthetic-traffic run through the actual dispatcher (via `red_team_mode()`/`invoke_probe_tool`, reusing Phase 18's substrate rather than a bespoke script), with the gap assertion being per-day coverage parity between dispatcher invocations attempted and audit rows written. This is an `autonomous:false` / `checkpoint:human-verify` gate needing a real local Postgres, mirroring `18-11-PLAN.md`'s Gate 1/2 pattern exactly. `[ASSUMED — this is the researcher's recommendation given the codebase's actual constraints; the planner must lock it explicitly since no CONTEXT.md exists]`

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Three guides should live under `docs/` as new markdown files, not in-product | § Open Decisions (a) | Low — reversible; if the user wants in-product help, that becomes a small follow-up UI task, not a rewrite |
| A2 | VER-01's "non-technical tester" must be a real, scripted `checkpoint:human-verify` human pass, not a fully automated proxy | § Open Decisions (b) | Medium — if the user intends a fully scripted UAT-simulation instead, the planner would over-scope a human-checkpoint task that could have been automated; but a scripted proxy cannot actually prove the SC2 claim as worded, so this risk is asymmetric (under-scoping the human check is worse than over-scoping it) |
| A3 | The Pending-Confirmation Dead End is genuinely reachable at realistic demo configuration (not just a theoretical edge case) | § Critical Finding | High if wrong — if platform defaults or a subsequent phase change made the skip threshold effectively universal, the planner would be over-engineering Option 1/2 for a non-issue. Verified directly from `actor_seam.py` and `capability_service.py` source this session — confidence is HIGH that the code reads as described, MEDIUM on how often Haiku actually returns `require_human` for a benign message (that is an LLM-behavior question, not a code-reading one) |
| A4 | AUD-03 must use seeded-backdated rows rather than any other construction | § Open Decisions (c) | Medium — if a future phase adds a clock-abstraction seam for unrelated reasons, that would become the better construction; as of this research, no such seam exists |

## Open Questions

1. **Does 18-11 execute before Phase 19 is planned/executed?**
   - What we know: 18-11 (RTX-04 live gate) and 18-10 (capability admin UI) are the last two Phase 18 plans, both `autonomous:false`, both unexecuted as of 2026-07-27.
   - What's unclear: whether the user runs `/gsd-execute-phase 18` to close them before starting Phase 19, or proceeds directly to `/gsd-plan-phase 19` with them still open.
   - Recommendation: the planner should treat "18-10 and 18-11 executed" as an explicit precondition for any VER-01 task that reads `report.blast_radius`, `envelope_drift`, or cites `test_clean_tenant_zero_high_severity`'s result, and should surface this as a blocking dependency check at the start of plan execution rather than assuming it silently.

2. **Should Phase 19's own VER-01 100-message harness supersede or depend on 18-11's RTX-04 gate?**
   - What we know: both prove closely related but distinct claims (RTX-04: zero high-severity findings on 3 RTX classes for a clean tenant, small N; VER-01 SC3: zero unauthorized mutations across 100 adversarial messages, broader scope).
   - What's unclear: whether the user wants VER-01's harness to literally reuse/extend `test_clean_tenant_zero_high_severity`, or build a parallel, larger harness that happens to share substrate.
   - Recommendation: extend, don't duplicate — reuse `CLEAN_TENANT_SPEC`, the RTX runners, and `verdict_tag` classification; scale `attack_sequences`/message count rather than inventing a second fixture.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio (`asyncio_mode = "auto"`), already configured in `apps/api/pyproject.toml` |
| Config file | `apps/api/pyproject.toml § [tool.pytest.ini_options]` |
| Quick run command | `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/unit -q --ignore=tests/unit/test_chunking_service.py --ignore=tests/unit/test_docling_service.py` |
| Full suite command | same, plus `-m integration` runs gated on `INTEGRATION_TESTS_ENABLED=1` with real local Postgres + Redis (existing convention, see `tests/integration/test_red_team_rtx.py`) |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DOC-01 | Tool-author guide accurately describes the dispatcher contract | manual-only (doc review against source) | n/a — reviewed against `tools.py`/`registry.py`/`schemas.py` at plan-check/verify time | ❌ Wave 0 — no automated doc-correctness check exists or is proposed; verification is human/plan-checker review against source |
| DOC-02 | Integration-provider guide accurately describes `ProviderAdapter`/`get_adapter_for_skill` | manual-only (doc review against source) | n/a | ❌ Wave 0 |
| DOC-03 | Owner guide accurately describes the capability envelope UI/API | manual-only (doc review against source + 18-10's shipped UI) | n/a | ❌ Wave 0 |
| VER-01 (happy path) | Non-technical tester completes refund + Shopify-order deploy end-to-end without code | manual-only, `checkpoint:human-verify` | scripted `<how-to-verify>` runbook, transcribed into `19-UAT.md` | ❌ Wave 0 — new scripted runbook needed |
| VER-01 (adversarial) | 100 synthetic adversarial messages, zero unauthorized mutations escaping L1-L3 | integration, `autonomous:false` | `pytest tests/integration/test_ver01_adversarial_harness.py -m integration -q -s` (new file, extends the RTX substrate) | ❌ Wave 0 |
| AUD-03 | Zero audit gaps across a synthetic 30-day window | integration, `autonomous:false` | `pytest tests/integration/test_aud03_audit_gap.py -m integration -q -s` (new file) | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/unit -q` (no new unit-test surface is implied by this phase beyond any pure-Python helper the VER-01/AUD-03 harnesses need, e.g. a gap-checking query function)
- **Per wave merge:** full unit suite + the two new `autonomous:false` integration modules collected-but-skipped (`INTEGRATION_TESTS_ENABLED` unset)
- **Phase gate:** the two live `autonomous:false` gates (VER-01 adversarial harness, AUD-03) run for real by the operator, plus the VER-01 happy-path human checkpoint, before `/gsd-verify-work 19`

### Wave 0 Gaps
- [ ] `docs/guides/tool-author-guide.md`, `docs/guides/integration-provider-guide.md`, `docs/guides/owner-capability-guide.md` — new files, DOC-01/02/03
- [ ] `apps/api/tests/integration/test_ver01_adversarial_harness.py` — new file, VER-01 SC3, built on `red_team_probe.py` substrate
- [ ] `apps/api/tests/integration/test_aud03_audit_gap.py` — new file, AUD-03, seeded-backdated-rows construction
- [ ] `.planning/phases/19-.../19-UAT.md` — new file, VER-01 SC2 human-checkpoint transcript, following the `16-UAT.md`/`17-UAT.md`/`18-UAT.md` house format
- [ ] Framework install: none — pytest/pytest-asyncio/anthropic/claude-agent-sdk are all already present

*(Zero of this phase's required test infrastructure exists yet — all three new test artifacts must be authored in Wave 0/1 of the plan.)*

## Security Domain

`security_enforcement` is absent from `.planning/config.json` — treated as enabled.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-------------------|
| V2 Authentication | no (new code) | This phase adds no new auth surface; it exercises existing Clerk/API-key auth via the tester's normal login |
| V3 Session Management | no (new code) | No new session handling; VER-01's IDV flow reuses Phase 17's shipped OTP session mechanism unchanged |
| V4 Access Control | yes, indirectly | If Option 1 (build a `pending_confirmations` resolve route) is chosen, that route MUST reuse the `_get_owned_agent` IDOR pattern (`capability_envelopes.py`/`prompt_versions.py`) — 404 on both missing and foreign-agent branches |
| V5 Input Validation | yes, indirectly | Same route, if built, needs a Pydantic body model with `extra="forbid"`, mirroring `CapabilityEnvelopeUpdate`'s convention |
| V6 Cryptography | no | No new cryptographic surface |
| V7 Error Handling & Logging | yes | AUD-03's harness must confirm audit rows are written on every error branch, not just success — this is exactly what the existing dispatcher already does (AUD-01 symmetry); the harness proves it holds under volume, it does not add new logging |

### Known Threat Patterns for this phase's scope

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| A newly-built `pending_confirmations` resolve route (if Option 1 chosen) is reachable by a tenant that does not own the agent | Elevation of Privilege | Reuse `_get_owned_agent` verbatim (404-on-both-branches IDOR pattern already shipped in `capability_envelopes.py` and `prompt_versions.py`) |
| A newly-built resolve route re-executes a mutating action on approval without re-running capability/rate/IDV checks | Elevation of Privilege | The resolve route must re-enter `_execute_transactional_tool`'s full dispatcher (or an equivalent subset), not call the adapter directly — approving a stale confirmation must not bypass a capability that was tightened after the confirmation was created |
| The AUD-03 seeded-backdated-rows harness leaves synthetic rows in a real DB after the test, polluting real audit history | Repudiation (of the audit log's own integrity) | The harness must run against an ephemeral test DB and clean up afterward, mirroring `18-11-PLAN.md`'s `tenant_db_url`/`control_db_url` ephemeral-DB fixture pattern exactly — never seed backdated rows into a DB that also holds real production audit data |
| The VER-01 100-message adversarial harness accidentally fires real provider side effects (e.g., a real Stripe/Shopify call) during the run | Information Disclosure / real-world side effect | Every probe message must run inside a `red_team_mode()` window exactly as the RTX runners do — the short-circuit to `StubProviderAdapter` is the control, not an assumption |

## Project Constraints (from CLAUDE.md)

- No Docker anywhere — every start/run command in guides, UAT runbooks, or test instructions must be a local process (`redis-server`, local PostgreSQL, `uvicorn`, `celery -A app.worker.celery_app worker`).
- Connection strings never in Celery task args — any new Celery task this phase might introduce (unlikely; this phase is primarily docs + pytest harnesses, not new background work) must take `tenant_id`/`agent_id` only.
- `acks_late=True` AND idempotency on every Celery task — same, if applicable.
- No `pg_search`/`pgbm25` — not relevant to this phase's scope (no retrieval work).
- Langfuse v4 API only; Ragas 0.4.x only — not relevant to this phase's scope.
- `docling`/`docling_core` are not installed in `apps/api/.venv` — `test_chunking_service.py`/`test_docling_service.py` must stay `--ignore`d in every full-suite command this phase's plans specify, exactly as `18-11-PLAN.md` already does.
- No live Neon/AWS/Stripe/Shopify access in this environment — VER-01's Shopify-order proof and any live-credential-dependent step are inherently live-gated (mirrors Phases 13-17's deferral pattern) and must be either run by the operator with real test credentials or explicitly deferred with operator acceptance in `19-UAT.md`, never silently skipped.

## Sources

### Primary (HIGH confidence — read directly from the working tree this session)
- `apps/api/app/services/transactional/tools.py` — full dispatcher, all 7 tool handlers
- `apps/api/app/services/transactional/registry.py` — `TransactionalToolDef`, `TOOL_METADATA`/`TOOL_REGISTRY`, `to_a2a_skill`
- `apps/api/app/services/transactional/schemas.py` — Pydantic Input/Output model conventions
- `apps/api/app/services/transactional/provider_adapter.py` — `ProviderAdapter` ABC, `StubProviderAdapter`, `get_adapter_for_skill`, red-team-mode ContextVar
- `apps/api/scripts/provision_integration_credential.py` — provisioning script docstring/usage
- `docs/runbooks/integration-credentials.md` — existing runbook precedent
- `docs/adr/0002-agent-tool-and-provisioning-strategy.md` (referenced, not fully re-read — cited via 18-16 ROADMAP context)
- `apps/api/app/api/v1/capability_envelopes.py` — GET/PATCH routes, IDOR guard, tighten-only ordering
- `apps/api/app/schemas/capability.py` — `CapabilityEnvelopeUpdate`/`Response`/`ListResponse`
- `apps/api/app/services/capability_service.py` — `PLATFORM_CAPABILITY_DEFAULTS`, `HASHED_ENVELOPE_FIELDS`, `validate_tighten_only`, `canonical_envelope_hash`, `envelope_drift`
- `apps/api/app/services/actor_seam.py` — skip-threshold logic, `call_actor_gate`
- `apps/api/app/services/red_team_probe.py` — `red_team_mode`, `invoke_probe_tool`, `ProbeToolResult.verdict_tag`, `CLEAN_TENANT_SPEC`
- `apps/api/app/services/red_team_service.py` — all seven runner functions, `RTX_ATTACK_VECTORS`, `INJECTION_ATTACK_VECTORS`
- `apps/api/app/worker/tasks/runtime/red_team.py` — `run_red_team` seven-runner sequential wiring
- `apps/api/app/models/tool_calls_audit.py` — `ToolCallsAudit` ORM, no `created_at` param path
- `apps/api/app/services/transactional/audit.py` — `write_audit_row` signature
- `apps/api/app/api/v1/*.py` — full route inventory (confirmed no `pending-confirmations` resolve route exists)
- `apps/api/app/models/pending_confirmation.py`, `apps/api/app/services/transactional/tools.py` (`require_human` branch) — the dead-end chain
- `DESIGN.md` (repo root) — confirms Gotham "Bone on Graphite" is current, `wchats-design` skill is explicitly superseded
- `.agents/skills/wchats-design/SKILL.md` — confirms it still describes the retired "Hillbrow at Dusk" direction
- `.planning/phases/18-.../18-10-PLAN.md`, `18-11-PLAN.md` — full plan text, both unexecuted, both directly informing the overlap and the pending-confirmation gap
- `.planning/REQUIREMENTS.md`, `.planning/STATE.md`, `.planning/ROADMAP.md` — phase context, traceability, dependency state
- `.planning/config.json` — `nyquist_validation: true`, `security_enforcement` absent (enabled)
- `apps/api/pyproject.toml § [tool.pytest.ini_options]` — test framework config

### Secondary (MEDIUM confidence)
- None used — all findings in this document trace to a directly-read source file or plan artifact in this repo.

### Tertiary (LOW confidence)
- Behavioral prediction of how often Haiku's Actor judgment returns `require_human` for a benign, in-policy demo message — this is an LLM-behavior claim, not a code-reading one, and cannot be verified without a live run. Flagged in Assumptions Log A3.

## Metadata

**Confidence breakdown:**
- Standard stack / shipped surfaces (DOC-01/02/03 grounding): HIGH — every claim traces to source code read directly this session
- Architecture (dispatcher order, adapter dispatch, capability envelope flow): HIGH — read end-to-end from the actual files, not summarized from STATE.md narrative alone
- Pitfalls / Critical Finding (pending-confirmation dead end): HIGH on the code fact (verified: zero resolving route exists), MEDIUM on operational frequency (how often it actually fires)
- VER-01/AUD-03 construction recommendations: MEDIUM — no prior art exists in this repo for either; recommendations are inferred from the closest analogous patterns (18-11's live-gate structure, RTX-04's substrate) rather than verified against a precedent that already proved this exact requirement

**Research date:** 2026-07-27
**Valid until:** Effectively until Phase 18's remaining plans (18-10, 18-11) execute or the pending-confirmations gap is otherwise resolved — those two events materially change what this document's Critical Finding and Overlap sections say. Treat as stale the moment `/gsd-execute-phase 18` completes; re-check § Critical Finding and § Overlap before planning Phase 19 if that happens first.
