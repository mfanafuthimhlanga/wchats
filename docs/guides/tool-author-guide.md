# Guide: Adding a Transactional Skill

**Audience:** Backend engineers adding a transactional tool
**Phase:** 19 (documents shipped Phase 14/16/17/18 code)
**Scope:** Adding an 8th skill to the existing dispatcher — not building a new dispatcher.

---

## Overview

W Chats currently ships 7 transactional skills — 6 mutating (`place_order`,
`cancel_order`, `issue_refund`, `update_subscription`, `book_slot`,
`update_customer_record`) plus 1 non-mutating (`confirm_action`). Every mutating
skill is a thin `@tool` wrapper that validates its Pydantic input, then delegates
to a single shared dispatcher, `_execute_transactional_tool`
(`apps/api/app/services/transactional/tools.py`). That dispatcher — not any
individual `@tool` handler — is where the platform's security guarantees live:
capability enforcement, identity verification, idempotency, rate limiting, the
Actor validator, and the audit log. This guide walks through what an 8th skill
needs to add across the four files that together define a transactional tool.

---

## The three files you write against

A new mutating skill touches three files:

- `apps/api/app/domain/transactional_schemas.py` — the typed `Input`/`Output`
  Pydantic pair for the skill.
- `apps/api/app/services/transactional/registry.py` — the `TransactionalToolDef`
  entry describing the skill's flags and A2A metadata.
- `apps/api/app/services/transactional/tools.py` — the `@tool`-decorated handler
  that validates input and delegates to `_execute_transactional_tool`.

A fourth file also gains a method for any skill that executes a real provider
action: `apps/api/app/services/transactional/provider_adapter.py`, where the new
skill needs an `@abstractmethod` on the `ProviderAdapter` ABC and an
implementation on every concrete adapter. That contract — the six existing
abstract methods, `StubProviderAdapter`, and `get_adapter_for_skill` — is covered
in full by `docs/guides/integration-provider-guide.md`; this guide only names it
here so the checklist below is complete.

---

## The enforcement order you never touch

`_execute_transactional_tool` encodes a fixed total order **exactly once**. Every
mutating `@tool` handler is a thin wrapper that validates its input model and then
calls this one dispatcher — it never reorders, re-implements, or partially inlines
the sequence. The eight steps, in the exact order the source banners in
`tools.py` use:

1. `IN-03 agent_id precondition`
2. `Capability check`
2.5. `IDV gate`
3. `Reserve idempotency`
4. `Rate + constraint checks`
5. `Actor seam`
6. `Adapter execute`
7. `Audit row + finalize`

Two adjacencies matter enough to call out explicitly:

**The IDV gate at Step 2.5 runs after the capability check (Step 2) and before
idempotency reservation (Step 3)**, so a call blocked for missing identity
verification never consumes an idempotency slot (T-17-21). If a call needs
verification and none is present, the dispatcher returns
`identity_verification.required` before `reserve_idempotency` is ever called —
the same `idempotency_key` remains reusable once the customer actually verifies.

**The capability check at Step 2 runs on every call, including replays**
(T-14-04-03). It is deliberately side-effect-free (no Redis `INCR`, no writes) so
it can run unconditionally ahead of the idempotency reservation without cost —
an agent, envelope, or skill that is disabled between the original call and a
retry is caught on the retry too.

Every rejection branch — capability denial, the **three** IDV block branches
at Step 2.5 (`identity_verification.required`, the fail-closed
`identity_verification.check_failed` branch that fires when
`check_verified_session` itself raises, and
`identity_verification.invalid_or_expired`), the `args_mismatch` branch, the
rate/constraint denial, the Actor `block` decision, and every other early
return in `_execute_transactional_tool` — writes exactly one `tool_calls_audit`
row before returning (AUD-01 symmetry). Replays and the benign `in_progress`
concurrent-duplicate branch are the only paths that do **not** write a row,
because they represent no new attempt at the underlying action.

Adding a skill means adding a new `@tool` handler that calls
`_execute_transactional_tool(skill, validated, args, adapter_method)` after its
own Pydantic validation — it never writes its own capability check, idempotency
reservation, or Actor call. The order above is not a convention to imitate; it
is enforced by the single dispatcher function every handler calls into.

---

## Schema rules

Every skill has exactly one `Input`/`Output` Pydantic pair in `schemas.py`.

**T-14-02-01** — every field on every schema is a typed scalar (`str`/`int`/`bool`)
or a typed enum. No field may be a free-form blob, SQL string, URL, or open dict.
This is what lets the dispatcher and the capability envelope reason about a call's
arguments without parsing prose.

Every mutating `Input` model carries a required `idempotency_key: str` field —
non-mutating `confirm_action` is the only exception (see below). Amount fields
(`amount_cents`, `refund_amount_cents`) are `Annotated[int, Field(ge=0)]` — integer
cents, never a float — which is what makes them directly comparable against
`capability_envelopes.constraints.max_amount_cents` at Step 4 without a unit
conversion or rounding step.

An 8th skill's `Input` model needs its own `idempotency_key: str` field (unless
it is non-mutating like `confirm_action`) and any amount field it carries must
follow the same `Annotated[int, Field(ge=0)]` cents convention if it is to be
checked against a `max_amount_cents` constraint.

---

## Registry rules

Every skill has exactly one `TransactionalToolDef` entry in `registry.py`'s
`TOOL_METADATA` dict (aliased as `TOOL_REGISTRY`).

**T-14-02-02** — `registry.py`'s own module docstring states the rule plainly:
`mutating`, `idempotency_required`, and `requires_identity_verification` are
"never runtime-inferred from the tool name or arguments." They are literal
values set by hand at definition time, on the `TransactionalToolDef` dataclass
instance for that skill — there is no derivation logic anywhere that infers them
from a call's shape.

`TransactionalToolDef` also carries three A2A/ACP forward-compatibility fields:
`a2a_input_modes`, `a2a_output_modes` (both default to `["text", "structured"]`),
and `examples`. `examples` must hold 2-3 plain-English phrasings on every
mutating skill, because `to_a2a_skill()` serialises the field directly into the
A2A v1.2 Agent Card skill dict it returns — an empty `examples` list is
permitted only for `confirm_action`, the one non-mutating skill in the registry.

**No A2A endpoint exists yet.** `to_a2a_skill()` produces metadata only — no
network call, no server — per the Plan-02 prohibition recorded in `registry.py`'s
own module docstring. Filling in `examples` and the two mode lists for an 8th
skill is forward-compatibility work for the v1.2 manifest serializer; it does
**not** make the agent externally callable today, and a reader should not treat
a populated `examples` list as evidence that an A2A surface is live.

---

## The one exception: `confirm_action`

`confirm_action` is the one skill in `TOOL_REGISTRY` with `mutating=False`. It
bypasses `_execute_transactional_tool` entirely — `confirm_action_tool` in
`tools.py` runs its own, much shorter path: IN-03 agent_id guard, then
`check_capability_access` (WR-05), then a direct write of a `pending_confirmations`
row via `get_sync_db()`. It takes no `idempotency_key` and calls no provider
adapter.

**Nothing in the codebase today reads or resolves a `pending_confirmations`
row.** The dispatcher's Actor seam (Step 5) can independently produce a
`require_human` decision that also writes a `pending_confirmations` row and
returns a non-error "awaiting approval" response to the agent — but no route,
task, or admin action currently transitions either kind of row out of its
unresolved state. A tool author must not design a new skill whose completion
depends on a `pending_confirmations` row being approved, because that approval
path does not exist yet.

---

## Adding an 8th skill: checklist

1. **Schema pair** in `schemas.py` — `<Verb><Noun>Input` / `<Verb><Noun>Output`,
   every field a typed scalar or enum (T-14-02-01), `idempotency_key: str`
   required on the Input model, any amount field as
   `Annotated[int, Field(ge=0)]` cents.
2. **Registry entry** in `registry.py` — a new `TransactionalToolDef` in
   `TOOL_METADATA` with `mutating`, `idempotency_required`, and
   `requires_identity_verification` set as literal values (T-14-02-02), 2-3
   `examples` phrasings.
3. **`@tool` handler** in `tools.py` — validate the new `Input` model, then call
   `_execute_transactional_tool(skill_name, validated, args, adapter_method=...)`.
   Register the resulting `SdkMcpTool` onto `TOOL_REGISTRY[skill_name].sdk_tool`
   alongside the existing seven, matching the pattern at the bottom of the file.
4. **One new abstract method** on `ProviderAdapter`
   (`provider_adapter.py`) plus a concrete implementation on **every** existing
   adapter, including `StubProviderAdapter` (which must return a
   `[STUB]`-labelled `Output`, no network call, per T-14-02-03). See
   `docs/guides/integration-provider-guide.md` for the full `ProviderAdapter`
   contract.
5. **`PLATFORM_CAPABILITY_DEFAULTS` entry** — a platform default row for the new
   skill so `GET /agents/{id}/capability-envelopes` returns a stable entry even
   for agents that have never had the skill configured.

---

## Related ADRs and References

- `docs/adr/0002-agent-tool-and-provisioning-strategy.md`
- `apps/api/app/services/transactional/tools.py`
- `apps/api/app/services/transactional/registry.py`
- `apps/api/app/domain/transactional_schemas.py`
- `apps/api/app/services/transactional/provider_adapter.py`
- `docs/guides/integration-provider-guide.md`
