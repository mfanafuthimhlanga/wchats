---
phase: 16-integration-adapters-platform-credential-service-l5-extensio
reviewed: 2026-06-30T00:00:00Z
depth: standard
files_reviewed: 12
files_reviewed_list:
  - apps/api/app/services/transactional/credential_service.py
  - apps/api/app/services/transactional/provider_adapter.py
  - apps/api/app/services/transactional/tools.py
  - apps/api/app/services/transactional/adapters/stripe_adapter.py
  - apps/api/app/services/transactional/adapters/shopify_adapter.py
  - apps/api/app/services/transactional/adapters/woocommerce_adapter.py
  - apps/api/app/services/transactional/adapters/calendly_adapter.py
  - apps/api/app/services/agent_tools.py
  - apps/api/app/core/config.py
  - apps/api/app/worker/tasks/runtime/agent.py
  - apps/api/alembic_tenant/versions/0007_integration_credentials.py
  - apps/api/scripts/provision_integration_credential.py
findings:
  critical: 3
  warning: 8
  info: 3
  total: 14
status: issues_found
---

# Phase 16: Code Review Report

**Reviewed:** 2026-06-30
**Depth:** standard
**Files Reviewed:** 12
**Status:** issues_found

## Summary

This phase implements the platform credential service (HKDF + Fernet per-tenant encryption), four concrete provider adapters (Stripe, Shopify, WooCommerce, Calendly), and the `get_adapter_for_skill` dispatch path. The security invariants around credential isolation are largely well-implemented: `CredentialHandle.__repr__` suppression, no module-level API key storage, lazy per-call StripeClient construction, and the `_tenant_id_var` ContextVar flow are all correct.

Three blockers were found — two broken adapter API calls that would cause every real transaction to fail in ways that are difficult to detect (a $0 Shopify refund looks like a success, and a Stripe subscription update silently creates a duplicate item), and one unhandled exception path that permanently strands an idempotency reservation. Several warnings address PII exposure via error messages, a known Redis TLS bypass, and a provisioning-script documentation error that causes a runtime `AttributeError`.

---

## Critical Issues

### CR-01: Shopify `refundCreate` always issues a $0 refund

**File:** `apps/api/app/services/transactional/adapters/shopify_adapter.py:155`

**Issue:** `refundLineItems: []` is passed unconditionally to the `refundCreate` GraphQL mutation. Shopify derives the refund amount from `refundLineItems`, not from any top-level amount field. An empty `refundLineItems` list means the mutation creates a refund record with zero line items and a $0 amount. The mutation succeeds (returns a refund GID), the audit row is written with `status="refunded"`, but no money is returned to the customer.

The docstring for `issue_refund` says `refund_amount_cents` is "informational" because "Shopify derives amounts from refundLineItems" — this is accurate, but the consequence is that the implementation never actually requests any money back.

**Fix:** Populate `refundLineItems` from the order's line items by first querying the order, or use the `transactions` field for a payment-level refund. The minimal correct path for an arbitrary-amount refund is to use the `transactions` field referencing the original payment transaction and specifying the amount:

```python
variables = {
    "input": {
        "orderId": args.order_id,
        "currency": self._currency_code,
        "note": args.reason,
        "transactions": [
            {
                "orderId": args.order_id,
                "kind": "REFUND",
                "gateway": "shopify_payments",  # read from order.paymentGatewayNames
                "amount": f"{args.refund_amount_cents / 100:.2f}",
            }
        ],
    }
}
```

A simpler short-term alternative: the `RefundInput` type accepts `refundDuties` and `shipping` but for arbitrary-amount partial refunds the `transactions` path is the correct mechanism.

---

### CR-02: Stripe `update_subscription` adds a new subscription item instead of replacing the existing one

**File:** `apps/api/app/services/transactional/adapters/stripe_adapter.py:160-164`

**Issue:** The Stripe Subscriptions Update API (`POST /v1/subscriptions/{id}`) with `items: [{"price": new_plan}]` and no `id` field creates a NEW subscription item on top of the existing item(s). Per Stripe's documented behavior: items without an `id` are created; existing items are only modified when their `id` is provided. After `update_subscription` executes, the customer's subscription has two active price items — the old plan and the new plan — and they are charged for both.

Additionally, the return value of `asyncio.to_thread(_sync)` is discarded (line 166), so even if Stripe returns a validation error or a different subscription ID, the caller receives a synthetic "updated" response built from the input args.

**Fix:** Retrieve the current subscription first to obtain the existing item's `id`, then replace it:

```python
def _sync() -> stripe.Subscription:
    client = stripe.StripeClient(json.loads(self._handle.use())["api_key"])
    # Fetch to get the existing item ID
    sub = client.v1.subscriptions.retrieve(args.subscription_id)
    existing_item_id = sub.items.data[0].id if sub.items.data else None
    if existing_item_id is None:
        raise ValueError(
            f"Subscription {args.subscription_id} has no items to update."
        )
    return client.v1.subscriptions.update(
        args.subscription_id,
        {"items": [{"id": existing_item_id, "price": args.new_plan}]},
        {"idempotency_key": args.idempotency_key},
    )

updated_sub = await asyncio.to_thread(_sync)
```

Return `updated_sub.id` in the output rather than the input arg to confirm the server-side state.

---

### CR-03: `KeyError` from missing `shop_url`/`site_url` escapes the idempotency handler and leaves the reservation permanently stuck

**File:** `apps/api/app/services/transactional/provider_adapter.py:283` and `:292`; `apps/api/app/services/transactional/tools.py:391-413`

**Issue:** In `get_adapter_for_skill`, Shopify dispatch accesses `config.config_data["shop_url"]` and WooCommerce accesses `config.config_data["site_url"]` using bare dictionary indexing. If a tenant's `integration_credentials` row was provisioned without these keys (misconfiguration, a wrong provider type assigned to the wrong row, or a bug in the provisioning script), these lines raise `KeyError`.

In `tools.py`, the call site wraps `get_adapter_for_skill` in:

```python
try:
    adapter = await get_adapter_for_skill(skill, agent_id, conn_str)
except (ProviderNotConfiguredError, CredentialDecryptionError) as exc:
    await release_idempotency(...)
    ...
    return {...}
start_ms = int(time.time() * 1000)

try:
    result_obj = await getattr(adapter, adapter_method)(...)
    ...
except Exception as exc:
    await release_idempotency(...)  # this block is never reached
```

`KeyError` is not a subclass of `ProviderNotConfiguredError` or `CredentialDecryptionError`. It escapes the first `except` clause. It also escapes the second `except Exception` block because that block is a separate `try` statement entered only after `adapter` is assigned. The `KeyError` propagates all the way out of `_execute_transactional_tool` without calling `release_idempotency`, leaving the reservation in the `"reserved"` or `"in_progress"` state until it expires.

**Fix:** Either catch `KeyError` alongside the existing exceptions, or use `.get()` with a fallback that raises `ProviderNotConfiguredError`:

```python
# In provider_adapter.py, Shopify dispatch:
shop_url = config.config_data.get("shop_url")
if not shop_url:
    raise ProviderNotConfiguredError(
        f"Shopify integration_credentials row is missing 'shop_url' in config_data."
    )
return ShopifyAdapter(handle=handle, shop_url=shop_url, currency_code=config.currency_code)

# Same pattern for WooCommerce:
site_url = config.config_data.get("site_url")
if not site_url:
    raise ProviderNotConfiguredError(
        f"WooCommerce integration_credentials row is missing 'site_url' in config_data."
    )
```

This converts the `KeyError` into a `ProviderNotConfiguredError`, which the existing handler in `tools.py` catches and properly releases the idempotency reservation.

---

## Warnings

### WR-01: Stripe Refunds API — `"currency"` is not a valid parameter for charge-based refunds

**File:** `apps/api/app/services/transactional/adapters/stripe_adapter.py:119-122`

**Issue:** The `refunds.create` payload includes `"currency": self._currency_code`. For standard charge-based refunds (created via `"charge": args.order_id`), the Stripe API does not accept a `currency` field — the refund currency is determined by the original charge and cannot be overridden. Stripe returns `400 Bad Request` with `"unknown parameter: currency"` for this combination. This means every Stripe refund attempt fails.

The `currency` field is only accepted for certain payment-method-specific refund types (e.g., refunds originating from bank-debit `charge_credit` objects). For the general charge ID path used here, it is invalid.

**Fix:** Remove `"currency"` from the refunds payload:

```python
return client.v1.refunds.create(
    {
        "charge": args.order_id,
        "amount": args.refund_amount_cents,
        "reason": "requested_by_customer",
        # currency omitted — fixed by the original charge (INT-07 still enforced by
        # validating the charge belongs to this tenant before calling)
    },
    {"idempotency_key": args.idempotency_key},
)
```

---

### WR-02: Shopify session management is not thread-local — cross-tenant credential bleed in concurrent workers

**File:** `apps/api/app/services/transactional/adapters/shopify_adapter.py:106-121`

**Issue:** `shopify.ShopifyResource.activate_session(session)` stores the active session in a class-level attribute in older versions of the ShopifyAPI Python library. If the library version in use does not use `threading.local()`, concurrent `asyncio.to_thread(_sync)` calls from different tenant requests racing inside the same Celery worker process will overwrite each other's session. Thread 1 calls `_make_session()` (sets session for tenant A), Thread 2 calls `_make_session()` (overwrites with tenant B's token), and Thread 1 then calls `shopify.GraphQL().execute()` using tenant B's access token.

The `try/finally` pattern correctly clears the session afterwards, but the window between `_make_session()` and `shopify.GraphQL().execute()` is a race condition under concurrent thread execution.

**Fix:** Either use the ShopifyAPI library's per-request session context (if available in the version used), or wrap the entire `_make_session() → execute → _clear_session()` sequence in a `threading.Lock()` to prevent concurrent session mutations. A per-call HTTP client approach (passing the token directly in each request header via the library's `with shopify.Session(...)` context manager) eliminates the shared state entirely:

```python
def _sync() -> str:
    token = json.loads(self._handle.use())["access_token"]
    with shopify.Session.temp(self._shop_url, self._api_version, token):
        return shopify.GraphQL().execute(mutation, variables=variables)
    # ShopifyAPI >= 8.x supports Session.temp() as a thread-safe alternative
```

Verify the installed ShopifyAPI version uses `threading.local()` before treating this as mitigated.

---

### WR-03: Raw provider exception message returned to agent and written to audit rows — PII exposure risk

**File:** `apps/api/app/services/transactional/tools.py:421-447`

**Issue:** When the adapter call raises any exception, `error_str = str(exc)` is both:
1. Logged at `ERROR` level via structlog: `log.error("transactional_tool.adapter_error", error=error_str)`
2. Returned to the Claude agent in the tool response text: `f"Tool execution failed: {error_str}. Please try again."`

Stripe exceptions (`stripe.StripeError`) include the Stripe API response body in their string representation, which can contain customer identifiers, charge amounts, card bank codes, or billing details. Shopify `RuntimeError` messages include the full `userErrors` array returned by the GraphQL API. These error strings flowing into the agent's context window constitute a PII/sensitive-data exposure surface — the agent may include them in its response text to the end user.

**Fix:** Return a generic message to the agent; log the full error internally with a correlation ID:

```python
except Exception as exc:  # noqa: BLE001
    latency_ms = int(time.time() * 1000) - start_ms
    # Log full detail internally only — never return to agent
    log.error(
        "transactional_tool.adapter_error",
        agent_id=agent_id,
        skill=skill,
        error=repr(exc),  # repr gives class name without necessarily leaking all fields
    )
    await release_idempotency(agent_id, skill, validated.idempotency_key)
    await write_audit_row(
        ...
        error=f"adapter_error:{type(exc).__name__}",  # class name only, not full message
    )
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    "The transactional action could not be completed due to a provider error. "
                    "Please try again or contact support if the issue persists."
                ),
            }
        ],
        "is_error": True,
    }
```

---

### WR-04: Redis TLS uses `ssl.CERT_NONE` unconditionally, bypassing `settings.REDIS_TLS_INSECURE`

**File:** `apps/api/app/worker/tasks/runtime/agent.py:77-79`; `apps/api/app/services/agent_tools.py:72-83`

**Issue:** Both module-level Redis clients use the pattern:

```python
_ssl_opts: dict = {"ssl_cert_reqs": ssl.CERT_NONE} if _url_clean.startswith("rediss://") else {}
```

This unconditionally disables TLS certificate verification for `rediss://` connections, regardless of `settings.REDIS_TLS_INSECURE`. The `enforcement.py` Redis client correctly gates `ssl.CERT_NONE` behind `settings.REDIS_TLS_INSECURE=True` and emits a warning log; these two clients do not.

`enforcement.py:94` acknowledges this as a "separate pre-existing issue" out of that plan's scope. It is in-scope for this phase review.

**Fix:** Apply the same guarded pattern used in `enforcement.py`:

```python
_ssl_opts: dict = {}
if _url_clean.startswith("rediss://"):
    if settings.REDIS_TLS_INSECURE:
        _ssl_opts = {"ssl_cert_reqs": ssl.CERT_NONE}
    else:
        _ssl_opts = {
            "ssl_cert_reqs": ssl.CERT_REQUIRED,
            "ssl_check_hostname": True,
        }
```

---

### WR-05: All credential DB fetch exceptions silently return `None` — transient errors indistinguishable from "not configured"

**File:** `apps/api/app/services/transactional/credential_service.py:209-217`

**Issue:** The outer `except Exception` in `_fetch_credential_config` catches every exception from the `asyncio.to_thread` call (connection timeouts, auth failures, network errors, query errors) and returns `None`. The caller (`get_adapter_for_skill`) treats `None` as "no row found" and raises `ProviderNotConfiguredError`. A transient Neon DB timeout during a credential lookup results in the same user-visible error as a genuinely unconfigured provider, with no way for the operator to distinguish them in the logs.

The log event `credential_service.fetch_failed` is emitted, but the error is still surfaced to the agent as "No integration credential configured for skill X", which is misleading.

**Fix:** Differentiate the error type before returning `None`, or re-raise a distinct exception type so the caller can choose the error message:

```python
except psycopg2.OperationalError as exc:
    log.error(
        "credential_service.db_connection_failed",
        skill=skill,
        error=str(exc),
    )
    return None  # could also raise a CredentialFetchError to give distinct message
except Exception as exc:  # noqa: BLE001
    log.warning(
        "credential_service.fetch_failed",
        skill=skill,
        error=str(exc),
    )
    return None
```

At minimum, promote connection errors to `log.error` so they surface in alerting.

---

### WR-06: Calendly `event_types` provisioning example uses list format; `CalendlyAdapter` expects a dict — causes `AttributeError` at runtime

**File:** `apps/api/scripts/provision_integration_credential.py:48`

**Issue:** The script's docstring example for Calendly shows:

```
--config-json '{"event_types": ["uuid-1", "uuid-2"]}'
```

`CalendlyAdapter.__init__` reads `config_data["event_types"]` and stores it in `self._event_types: dict`. When `book_slot` calls `self._event_types.get(args.service_type)`, a list object raises `AttributeError: 'list' object has no attribute 'get'`. The `AttributeError` escapes the `except (ProviderNotConfiguredError, CredentialDecryptionError)` handler in `tools.py` and is only caught by the broad `except Exception` at the adapter-execution level — the idempotency reservation IS released in that path (unlike CR-03), but the error message to the agent is unhelpful and the root cause is invisible.

The correct format for `event_types` is a dict mapping service_type label to Calendly event URI:

```
{"event_types": {"consultation": "https://api.calendly.com/event_types/UUID"}}
```

**Fix:** Correct the docstring example:

```python
# Calendly:
python scripts/provision_integration_credential.py \
    --tenant-id "..." \
    --provider-type calendly \
    --credential-file /secure/calendly_creds.json \
    --currency-code usd \
    --enabled-skills book_slot \
    --config-json '{"event_types": {"consultation": "https://api.calendly.com/event_types/<UUID>", "demo": "https://api.calendly.com/event_types/<UUID2>"}}'
```

Additionally, add a structural validation in `CalendlyAdapter.__init__` to raise `ValueError` if `event_types` is not a `dict`:

```python
if not isinstance(self._event_types, dict):
    raise ValueError(
        f"CalendlyAdapter: config_data['event_types'] must be a dict "
        f"({{label: uri}}), got {type(self._event_types).__name__!r}."
    )
```

---

### WR-07: Stripe `place_order` integer division discards remainder cents

**File:** `apps/api/app/services/transactional/adapters/stripe_adapter.py:204-205`

**Issue:** `unit_amount = args.amount_cents // quantity` uses integer (floor) division. For `amount_cents=100, quantity=3`, `unit_amount=33` and Stripe charges `3 × 33 = 99` cents instead of the requested 100 cents. The customer is undercharged by up to `quantity - 1` cents on every order where the total is not evenly divisible by the quantity. This also means the checkout session total never exactly matches `args.amount_cents`, causing reconciliation mismatches.

**Fix:** Add the remainder to the first unit:

```python
quantity = max(args.quantity, 1)
base_unit = args.amount_cents // quantity
remainder = args.amount_cents % quantity
# Add remainder to first line item to match exact total
line_items = []
for i in range(quantity):
    line_items.append({
        "price_data": {
            "currency": self._currency_code,
            "product_data": {"name": args.product_id},
            "unit_amount": base_unit + (remainder if i == 0 else 0),
        },
        "quantity": 1,
    })
```

Or — simpler and more accurate — use a single line item with the total `amount_cents` and `quantity=1` to avoid the division entirely if the product is sold as a bundle:

```python
line_items = [{
    "price_data": {
        "currency": self._currency_code,
        "product_data": {"name": f"{args.quantity}x {args.product_id}"},
        "unit_amount": args.amount_cents,
    },
    "quantity": 1,
}]
```

---

### WR-08: `LIMIT 1` without `ORDER BY` in credential lookup — non-deterministic adapter selection

**File:** `apps/api/app/services/transactional/credential_service.py:182-190`

**Issue:** `_fetch_credential_config` queries `integration_credentials WHERE enabled_skills @> %s LIMIT 1` with no `ORDER BY`. If a tenant DB has more than one row whose `enabled_skills` contains the requested skill (e.g., a Shopify row and a WooCommerce row both claim `"issue_refund"`), PostgreSQL returns whichever row it encounters first in the physical scan order. This is non-deterministic across VACUUM cycles, index rebuilds, and concurrent writes. A tenant provisioning error could silently flip which adapter handles a skill between requests.

**Fix:** Add an `ORDER BY created_at ASC` (first-provisioned wins) or `ORDER BY provider_type ASC` (alphabetical determinism) to make the selection stable:

```sql
SELECT provider_type, credential_data, config_data, currency_code
FROM integration_credentials
WHERE enabled_skills @> %s::jsonb
ORDER BY created_at ASC
LIMIT 1
```

A longer-term fix is to add a partial unique index at the DB level preventing duplicate skill assignments, but an explicit `ORDER BY` is an immediate improvement.

---

## Info

### IN-01: `_WooOAuth1Auth` stores credentials as plain string fields with no `__repr__` suppression

**File:** `apps/api/app/services/transactional/adapters/woocommerce_adapter.py:97-100`

**Issue:** `_WooOAuth1Auth.__init__` assigns `self._consumer_key = consumer_key` and `self._consumer_secret = consumer_secret` as plain `str` attributes. Unlike `CredentialHandle`, `_WooOAuth1Auth` has no `__repr__` override. If the auth object is accidentally repr'd in a log or exception traceback, the credentials are exposed. The `WooCommerceAdapter` stores this as `self._auth`, so any repr of the adapter also exposes the auth object.

**Fix:** Add `__repr__` suppression to `_WooOAuth1Auth`, mirroring `CredentialHandle`:

```python
def __repr__(self) -> str:
    return "<_WooOAuth1Auth:redacted>"

__str__ = __repr__
```

---

### IN-02: `except Exception` discards the original exception chain in `get_adapter_for_skill`

**File:** `apps/api/app/services/transactional/provider_adapter.py:264-266`

**Issue:** The Fernet decryption failure handler:

```python
except Exception:  # noqa: BLE001
    raise CredentialDecryptionError(
        f"Failed to decrypt credential for provider '{config.provider_type}'"
    )
```

The original exception (e.g., `cryptography.fernet.InvalidToken`) is not chained, making it harder to diagnose whether the failure was a wrong key, tampered ciphertext, or encoding error.

**Fix:**

```python
except Exception as exc:  # noqa: BLE001
    raise CredentialDecryptionError(
        f"Failed to decrypt credential for provider '{config.provider_type}'"
    ) from exc
```

---

### IN-03: No GIN index on `enabled_skills JSONB` in the tenant migration

**File:** `apps/api/alembic_tenant/versions/0007_integration_credentials.py`

**Issue:** The `_fetch_credential_config` query uses `WHERE enabled_skills @> %s::jsonb` (JSONB containment). Without a GIN index on `enabled_skills`, this does a full sequential scan of `integration_credentials` on every tool call. The table is expected to be small (a few rows per tenant) so this is not a correctness issue, but adding the index now avoids a follow-up migration later.

**Fix:** Add to `upgrade()`:

```sql
CREATE INDEX IF NOT EXISTS ix_integration_credentials_skills
    ON integration_credentials USING GIN (enabled_skills);
```

---

_Reviewed: 2026-06-30_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
