# Guide: Adding an Integration Provider — Part 2 (Runtime Adapter Resolution)

**Audience:** Engineers adding a fifth integration provider
**Phase:** 19 (documents shipped Phase 16/18 code)
**Scope:** Runtime adapter resolution and the `ProviderAdapter` contract.
Deploy-time credential provisioning is covered by
`docs/runbooks/integration-credentials.md` — this guide links to it and does not
repeat it.

---

## What the runbook already covers

`docs/runbooks/integration-credentials.md` is the deploy-time half of adding a
provider. This guide does not restate any of it — read it first:

- The full prerequisites table (Python version, `PLATFORM_CREDENTIAL_KEY`,
  `TENANT_DB_CONN_STR`, migration 0007, credential-file mode 600).
- The `provision_integration_credential.py` CLI, including a working `--dry-run`
  example.
- The per-provider (Stripe, Shopify, WooCommerce, Calendly) credential-file JSON
  shape, config-JSON shape, and a full provisioning command for each.
- `INT-07` single-currency enforcement, with the exact abort error text and the
  remediation steps.
- The Security Checklist table (grep-for-leaked-key command, file-mode check,
  key-type check).
- The Troubleshooting table (error string → cause → fix).

---

## The `ProviderAdapter` contract

`apps/api/app/services/transactional/provider_adapter.py` defines
`ProviderAdapter`, an `ABC` with six abstract async methods — one per mutating
transactional skill:

- `place_order(args: PlaceOrderInput, agent_id: str) -> PlaceOrderOutput`
- `cancel_order(args: CancelOrderInput, agent_id: str) -> CancelOrderOutput`
- `issue_refund(args: IssueRefundInput, agent_id: str) -> IssueRefundOutput`
- `update_subscription(args: UpdateSubscriptionInput, agent_id: str) -> UpdateSubscriptionOutput`
- `book_slot(args: BookSlotInput, agent_id: str) -> BookSlotOutput`
- `update_customer_record(args: UpdateCustomerRecordInput, agent_id: str) -> UpdateCustomerRecordOutput`

Each method takes the skill's typed `Input` model (see
`docs/guides/tool-author-guide.md` for the schema rules) plus `agent_id`, and
returns the skill's typed `Output` model — never a free-form dict.

`StubProviderAdapter` is the reference no-network implementation: it implements
all six methods and returns `[STUB]`-labelled outputs with no HTTP call and no
real side effect. It is both the Phase-14 offline default and, per Phase 18, the
adapter every red-team probe resolves to instead of a real provider (see
"Red-team mode" below). A fifth provider's adapter class subclasses
`ProviderAdapter` the same way `StubProviderAdapter` does.

---

## `get_adapter_for_skill` is the only credential-resolving entry point

`get_adapter_for_skill(skill, agent_id, conn_str)` is the single function that
fetches, decrypts, and dispatches to a concrete `ProviderAdapter`. Its docstring
states the constraint that governs where it may be called from:

**"MUST NOT be imported or called from any FastAPI route handler or SDK hook —
only from `_execute_transactional_tool` (tools.py step 6)."**

This is a real architectural boundary, not a style preference: a route handler
or SDK hook that called it directly would resolve and hold a decrypted
credential outside the dispatcher's enforcement order — bypassing the capability
check, the IDV gate, the idempotency reservation, the rate/constraint checks,
and the Actor seam that every other path to a provider action passes through
first.

The four shipped `provider_type` values — `stripe`, `shopify`, `woocommerce`,
`calendly` — are dispatched by a plain `if`/`elif` chain on
`config.provider_type` at the bottom of `get_adapter_for_skill`, after the
credential has been fetched and decrypted.

---

## Adding a fifth provider

1. **One new adapter module** under
   `apps/api/app/services/transactional/adapters/` implementing `ProviderAdapter`
   (all six abstract methods), following the pattern of the four shipped
   adapters — constructed from a `CredentialHandle` plus whatever provider
   config (e.g. a shop URL) the concrete provider needs.
2. **One new `elif` branch** in `get_adapter_for_skill`'s dispatch chain, keyed
   on the new provider's `provider_type` string, importing the new adapter class
   lazily (matching the existing four branches, which import their adapter
   classes inside the function body to avoid a circular import with
   `provider_adapter.py`).
3. **The `provider_type` string** the fifth branch matches on is exactly what
   `docs/runbooks/integration-credentials.md`'s provisioning script must be
   given via `--provider-type` when a tenant is provisioned for the new
   provider — the runbook covers that half; this guide only names the
   coupling.

---

## Runtime credential resolution

This is content the runbook does not cover — it documents deploy-time
provisioning, not what happens inside a live call.

Per-tenant Fernet keys are `HKDF`-derived (`_derive_tenant_fernet` in
`credential_service.py`) from `PLATFORM_CREDENTIAL_KEY` (the platform master
key, an env var with no default — it must be set explicitly) plus the tenant's
`tenant_id` as the HKDF salt. A fresh `HKDF` instance is constructed on every
call, because `HKDF.derive()` is single-use per instance.

The raw decrypted credential exists only inside `get_adapter_for_skill`'s own
stack frame, for the duration of one call. It is immediately wrapped in a
`CredentialHandle` (`credential_service.py`) before being handed to the adapter
constructor. `CredentialHandle.__repr__`/`__str__` always return the literal
string `<CredentialHandle:redacted>` — that redaction is **load-bearing, not
cosmetic**: it is what keeps the raw credential out of structlog output,
exception tracebacks, and any accidental `str()`/`repr()` call on the handle
anywhere downstream. The only way to reach the raw value is `handle.use()`,
called exactly once, inside the adapter constructor, to initialize the
provider's SDK client.

**A resolved credential is never logged, never printed, never returned to the
agent, never written into an audit row, and never persisted anywhere by adapter
code.** This guide contains no example that prints, logs, or persists a
resolved credential — write your own adapter the same way: pass `handle` into
the provider SDK's client constructor and let the handle go out of scope, never
extracting the raw string into a variable that could be logged or serialized.

---

## Red-team mode: why `get_adapter_for_skill` sometimes never touches credentials

`provider_adapter.py` declares a module-private `ContextVar`,
`_red_team_mode_var`, defaulting to `False`. The **only** sanctioned setter is
`red_team_probe.red_team_mode()` (`apps/api/app/services/red_team_probe.py`), a
context manager that sets the flag on entry and resets it symmetrically on
exit — even if the wrapped probe body raises.

When the flag is set, `get_adapter_for_skill` returns the `StubProviderAdapter`
singleton immediately, **before** any credential fetch, HKDF derivation, or
Fernet operation. This lets Phase 18's red-team probes drive the real
dispatcher — the capability check, IDV gate, idempotency reservation, rate and
constraint checks, and Actor seam (Steps 1 through 5) all run unmodified — while
only the network-facing leaf (the provider call) is swapped for the stub. A
clean red-team tenant has zero `integration_credentials` rows, so without this
short-circuit every probe would abort at credential resolution with
`provider.not_configured` before any of those security layers could be
observed.

Because the flag is a `ContextVar` and not a module global, a probe running
inside a `red_team_mode()` window and a concurrent ordinary customer turn each
carry their own independent value — one cannot contaminate the other's adapter
resolution.

Two consequences for an adapter author:

- **Do not assume real credentials are always resolved during tests or probe
  runs.** An adapter that has side effects in its constructor beyond reading
  `handle.use()` could behave unexpectedly under the stub path.
- **Never set `_red_team_mode_var` from anywhere except
  `red_team_probe.red_team_mode()`.** It has no `settings` field and no
  environment variable — it must stay that way, because any other setter is a
  path by which an ordinary customer turn could accidentally skip real
  credential resolution.

---

## Architecture rationale

`docs/adr/0002-agent-tool-and-provisioning-strategy.md` records the locked
decision behind this whole design: deployed agents call external providers only
through W Chats' own narrow, typed tools — behind the capability envelope,
idempotency, and Actor validator layers — rather than by mounting a provider
MCP server or a vendor "agent toolkit" (e.g. the Stripe Agent Toolkit) directly
into the agent. A provider MCP/toolkit would hand raw provider operations to
the LLM, bypassing every enforcement layer this guide and
`docs/guides/tool-author-guide.md` describe, and would dump that provider's
entire API surface into the agent's context instead of the one typed action a
skill needs. Read this ADR before reaching for a provider's own SDK toolkit
when building a fifth adapter — the typed-tool-behind-the-dispatcher pattern is
not incidental, it is the security boundary.

---

## A note on the runbook's admin-UI stop-sign

`docs/runbooks/integration-credentials.md` closes with a note that credential
provisioning was, at Phase 16, deploy-time-only, and that a self-serve
credential and capability admin UI was planned for Phase 18. **Phase 18 shipped
the capability admin UI** — but it configures capability *envelopes* (rate
limits, ceilings, enabled/disabled skills), not *credentials*. The runbook's
stop-sign against building admin API endpoints for credential management still
stands: no route exists today that lets an operator provision or rotate a
provider credential through the admin UI. Do not read the Phase 18 capability
admin UI as having lifted that constraint.

---

## Related ADRs and References

- `docs/runbooks/integration-credentials.md`
- `docs/adr/0002-agent-tool-and-provisioning-strategy.md`
- `apps/api/app/services/transactional/provider_adapter.py`
- `apps/api/app/services/transactional/credential_service.py`
- `apps/api/app/services/red_team_probe.py`
- `docs/guides/tool-author-guide.md`
