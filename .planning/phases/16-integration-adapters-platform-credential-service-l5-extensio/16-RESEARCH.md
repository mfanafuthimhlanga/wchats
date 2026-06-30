# Phase 16: Integration Adapters + Platform Credential Service (L5 Extension) — Research

**Researched:** 2026-06-30
**Domain:** Python provider SDK adapters + HKDF-based per-tenant credential encryption + in-memory credential handles
**Confidence:** HIGH (architecture), MEDIUM (provider SDK specifics), LOW (Calendly Scheduling API)

---

<user_constraints>
## User Constraints (from ROADMAP Phase 16 + ADR-0002 — authoritative decision record)

### Locked Decisions
- **Call provider SDKs directly behind our typed tools** — NOT provider MCP servers or vendor "agent toolkits" (e.g. Stripe Agent Toolkit). The narrow, hand-curated tool set is both the security boundary (preserves L1–L3: Actor validator + capability envelope + audit) and the context-efficiency win. Provider MCP/toolkit hands raw provider operations to the LLM, bypassing Actor + capability envelope + audit.
- **Stripe (INT-05):** Refunds API (`issue_refund`), Subscriptions API (`update_subscription`), Checkout Session / Payment Link for `place_order` (NO raw card handling). Pass the TXN-02 client idempotency key straight to Stripe's native `Idempotency-Key`. Provision a per-tenant **Stripe Restricted API Key** scoped to only the enabled skills — defense-in-depth at the Stripe layer mirroring the L2 envelope.
- **Shopify / WooCommerce / Calendly:** same pattern — provider SDK/REST behind typed tools, with scope-restricted credentials. (Stripe Agent Toolkit / MCP kept only as a reference for which ops to expose.)
- **INT-01:** `integration_credentials` is a **tenant-DB** table (alembic_tenant migration, NOT control DB). Fernet-encrypted BYTEA, key derived from platform master key + tenant ID.
- **INT-02:** Credential service returns short-lived in-memory handles — no agent code path reads the table or constructs SQL.

### Claude's Discretion
- Exact HKDF parameters (salt encoding, info bytes) — HKDF with SHA-256 is standard; recommended configuration in research.
- Whether WooCommerce adapter uses `WooCommerce` PyPI package vs direct `httpx` — research recommendation included.
- Exact shape of `integration_credentials` table columns (provider_type, enabled_skills, currency_code, etc.).
- How `get_adapter()` dispatches to the right concrete adapter class.
- Calendly: one-time scheduling link vs direct invitee creation (Scheduling API) — noted constraints.

### Deferred Ideas (OUT OF SCOPE)
- Multi-currency support (INT-07 explicitly: single currency per tenant, configured at deploy time only).
- Provider MCP servers or vendor agent toolkits as the agent-facing layer.
- Phase 17 identity verification (separate phase).
- Phase 18 blast-radius gate and capability admin UI.
- A2A/MCP provisioning surface (v1.2).
- `update_customer_record` adapter implementation (no explicit INT requirement; stub remains).
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| INT-01 | `integration_credentials` tenant-DB table — Fernet-encrypted BYTEA, key derived from platform master key + tenant ID; never exposed to agent code | HKDF derivation pattern, alembic_tenant migration, `PLATFORM_CREDENTIAL_KEY` settings field |
| INT-02 | Platform credential service resolves a credential to a short-lived in-memory handle at tool-execution time; no agent code path reads the table or constructs SQL | `CredentialHandle` dataclass pattern, `get_adapter_for_skill()` async function, dispatcher step 6 change |
| INT-03 | Shopify adapter (place/cancel order, issue refund) behind the tool contract | ShopifyAPI 12.7.0 + GraphQL mutations: `orderCreate`, `orderCancel`, `refundCreate` |
| INT-04 | WooCommerce adapter (same operations) | `WooCommerce` 3.0.0 package or httpx; endpoints: `POST /orders`, `PUT /orders/{id}`, `POST /orders/{id}/refunds` |
| INT-05 | Stripe adapter (issue refund, update subscription; + Checkout for place_order) | `stripe` 15.3.0; Refund.create, Subscription.modify, checkout.Session.create; `idempotency_key=` parameter |
| INT-06 | Calendly adapter (book slot) | httpx + Bearer PAT; `POST https://api.calendly.com/invitees` (Scheduling API — paid plan required) |
| INT-07 | Single-currency per tenant, configured at deploy time | `currency_code TEXT` column in `integration_credentials`; validated at adapter call time |
</phase_requirements>

---

## Summary

Phase 16 wires the six transactional tools built in Phase 14 to real external providers. The three components it must build are: (1) a per-tenant credential storage table in the tenant DB with HKDF-derived per-tenant Fernet encryption, (2) a stateless credential service that resolves an encrypted credential to a short-lived in-memory handle at tool-call time (invisible to the LLM), and (3) four concrete provider adapters that implement the existing `ProviderAdapter` ABC using the real provider SDKs.

The Phase 14 dispatcher (`tools.py`) calls `get_adapter(agent_id)` at step 6. Phase 16 changes this single call to `await get_adapter_for_skill(skill, agent_id, conn_str)`, which looks up the configured provider for the agent, fetches and decrypts the credential from the tenant DB, and returns the appropriate concrete adapter holding a `CredentialHandle`. The credential handle has a redacted `__repr__` and goes out of scope as soon as the adapter method returns. No credential ever appears in tool schemas, agent context, Celery task args, audit rows, or logs.

The security invariant "credential is never visible to agent code" is enforced architecturally: the tool input schemas (already locked in Phase 14) have no credential fields, the credential service is not callable from any agent-facing code path, and the `CredentialHandle` wrapper prevents accidental serialization.

**Primary recommendation:** Implement `get_adapter_for_skill()` as the single credential-resolution entry point, changing only line 384 of `tools.py` (the `get_adapter` call). Introduce `PLATFORM_CREDENTIAL_KEY` as a new settings field alongside `NEON_ENCRYPTION_KEY`. Add `integration_credentials` as alembic_tenant migration 0007. Build four adapters as subclasses of the existing `ProviderAdapter` ABC.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Credential storage (INT-01) | Database / Storage (tenant DB) | — | Encrypted BYTEA in tenant Neon project; keyed per-tenant; never in control DB or agent context |
| Credential resolution (INT-02) | API / Backend (Celery runtime worker) | — | Runs inside the tool dispatcher, server-side only, never reaches the LLM layer |
| Provider adapter execution (INT-03–06) | API / Backend (Celery runtime worker) | External provider API | Adapter makes outbound SDK calls; result returned through typed Output schema |
| Idempotency key pass-through (INT-05) | API / Backend | External provider API (Stripe) | TXN-02 key flows from dispatcher into Stripe's native `Idempotency-Key` header |
| Single-currency enforcement (INT-07) | API / Backend | Deploy-time config | `currency_code` stored in `integration_credentials`; adapter validates before calling provider |

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `cryptography` | 48.0.0 (pinned, already installed) | HKDF key derivation + Fernet encryption | Already the project's crypto library; `HKDF` is in `cryptography.hazmat.primitives.kdf.hkdf`; `Fernet` is already used in `security.py` [VERIFIED: pyproject.toml] |
| `stripe` | 15.3.0 (upgrade from 11.4.1) | Stripe Refunds, Subscriptions, Checkout Sessions | Official Stripe Python SDK; `idempotency_key=` param maps directly to `Idempotency-Key` HTTP header [VERIFIED: PyPI registry + docs.stripe.com] |
| `ShopifyAPI` | 12.7.0 | Shopify GraphQL mutations (orderCreate, orderCancel, refundCreate) | Official Shopify Python SDK; supports GraphQL via `shopify.GraphQL().execute()`; last release Nov 2024 [VERIFIED: PyPI registry; GitHub: Shopify/shopify_python_api] |
| `WooCommerce` | 3.0.0 | WooCommerce REST API (orders, refunds) | Official WooCommerce Python wrapper; last release 2021 but stable; alternative is httpx with manual OAuth1 signing [VERIFIED: PyPI registry; GitHub: woocommerce/wc-api-python] [WARNING: SUS — last updated 2021. Verify before installing.] |
| `httpx` | 0.28.1 (already installed) | Calendly REST calls (no official SDK) | Already installed as a project dependency; use for Calendly `POST /invitees` [VERIFIED: pyproject.toml] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `asyncio.to_thread` | stdlib | Offload synchronous SDK calls to thread pool | All provider SDKs (stripe, ShopifyAPI) are synchronous; wrap every SDK call with `asyncio.to_thread` to avoid blocking the event loop |
| `base64.urlsafe_b64encode` | stdlib | Encode HKDF-derived bytes for Fernet | HKDF.derive() returns raw bytes; Fernet requires URL-safe base64-encoded 32-byte key |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `WooCommerce` 3.0.0 | `httpx` + manual OAuth1 (`requests-oauthlib`) | WooCommerce package is simpler; httpx approach gives more control over TLS/timeout. If `WooCommerce` package fails legitimacy gate, use httpx + `requests-oauthlib` OAuth1 signing |
| `ShopifyAPI` GraphQL | Shopify REST via httpx | REST deprecated for public apps (Feb 2025). For private/custom apps REST still works, but GraphQL is forward-compatible. Use GraphQL. |
| HKDF | PBKDF2HMAC | PBKDF2 is slow by design (password hashing); HKDF is the right choice for key derivation from an already-strong key material (not a password) |
| `MultiFernet` | Single `Fernet` | MultiFernet enables key rotation; can be added in Phase 18. Phase 16 uses single Fernet per the HKDF derivation. |

**Installation (net-new dependencies only):**
```bash
pip install "stripe==15.3.0" "ShopifyAPI==12.7.0" "WooCommerce==3.0.0"
```

Note: `cryptography`, `httpx`, and `asyncio` are already installed.

---

## Package Legitimacy Audit

| Package | Registry | Age | Downloads | Source Repo | Verdict | Disposition |
|---------|----------|-----|-----------|-------------|---------|-------------|
| `stripe` | PyPI | ~12 years | Not available via seam — confirmed via official docs | github.com/stripe/stripe-python | SUS (seam: unknown-downloads) | Approved — official Stripe corp repo; confirmed via stripe.com/docs |
| `ShopifyAPI` | PyPI | ~8 years | Not available via seam | github.com/Shopify/shopify_python_api | SUS (seam: unknown-downloads) | Approved — official Shopify org repo; confirmed via shopify.dev |
| `WooCommerce` | PyPI | 5+ years, last update 2021 | Not available via seam | github.com/woocommerce/wc-api-python | SUS (seam: unknown-downloads + stale) | Flagged — planner must add checkpoint:human-verify before install. If WooCommerce package install fails, fallback is httpx + requests-oauthlib |
| `httpx` | PyPI | 6+ years, actively maintained | Very high (already installed) | github.com/encode/httpx | OK (already in pyproject.toml) | Approved — already installed |

**Packages removed due to SLOP verdict:** none

**Packages flagged as suspicious (SUS):** `stripe`, `ShopifyAPI`, `WooCommerce` — all flagged because PyPI download counts are unavailable to the seam. All three are confirmed as official packages via their respective company GitHub organizations and official documentation. Planner must add a `checkpoint:human-verify` note citing the official source confirmation before each install.

*Note: `stripe` and `ShopifyAPI` are confirmed via official documentation ([CITED: docs.stripe.com], [CITED: shopify.dev]) and their official GitHub repos. `WooCommerce` confirmed via [CITED: pypi.org/project/WooCommerce/] and GitHub. Registry existence alone is not the only verification — official source confirmation is present for all three.*

---

## Architecture Patterns

### System Architecture Diagram

```
Tool call (from LLM via Agent SDK)
        │
        ▼
 _execute_transactional_tool() dispatcher (tools.py)
  Step 1: agent_id guard
  Step 2: capability envelope check (control DB)
  Step 3: reserve_idempotency (control DB, atomic INSERT)
  Step 4: rate + constraint checks (Redis)
  Step 5: call_actor_gate (Haiku judge)
        │
        ▼ decision == "approve"
  Step 6: [PHASE 16] get_adapter_for_skill(skill, agent_id, conn_str)
        │
        ├─→ fetch_integration_credentials(conn_str, skill)  ──→ tenant DB (Neon)
        │       [integration_credentials table]
        │       returns: (provider_type, encrypted_cred_bytes, currency_code)
        │
        ├─→ _derive_tenant_key(PLATFORM_CREDENTIAL_KEY, tenant_id)
        │       HKDF(SHA256, length=32, salt=tenant_id, info=b'wchats-cred')
        │       → Fernet key (in-memory only)
        │
        ├─→ fernet.decrypt(encrypted_cred_bytes)
        │       → CredentialHandle(raw_cred)  [in-memory, __repr__ redacted]
        │
        └─→ dispatch by provider_type:
               "stripe"    → StripeAdapter(handle, currency_code)
               "shopify"   → ShopifyAdapter(handle, shop_url, currency_code)
               "woocommerce" → WooCommerceAdapter(handle, site_url, currency_code)
               "calendly"  → CalendlyAdapter(handle)
                                    │
                                    ▼
                         adapter.issue_refund(args, agent_id)
                                    │
                         asyncio.to_thread(sync_sdk_call)
                                    │
                         External Provider API
                         (Stripe/Shopify/WooCommerce/Calendly)
                                    │
                         ProviderOutput (typed) ──→ audit row ──→ tool response
                         CredentialHandle goes out of scope (GC)
```

### Recommended Project Structure
```
apps/api/app/services/transactional/
├── provider_adapter.py      # MODIFY: ProviderAdapter ABC + get_adapter_for_skill() async function
├── credential_service.py    # NEW: CredentialHandle + HKDF derivation + fetch_integration_credentials
├── adapters/
│   ├── __init__.py          # NEW
│   ├── shopify_adapter.py   # NEW: ShopifyAdapter(ProviderAdapter)
│   ├── woocommerce_adapter.py  # NEW: WooCommerceAdapter(ProviderAdapter)
│   ├── stripe_adapter.py    # NEW: StripeAdapter(ProviderAdapter)
│   └── calendly_adapter.py  # NEW: CalendlyAdapter(ProviderAdapter)
apps/api/alembic_tenant/versions/
└── 0007_integration_credentials.py  # NEW: integration_credentials tenant-DB table
apps/api/app/core/config.py          # MODIFY: add PLATFORM_CREDENTIAL_KEY field
```

`tools.py` requires only ONE change (step 6, line 384): `get_adapter(agent_id)` → `await get_adapter_for_skill(skill, agent_id, conn_str)`.

### Pattern 1: HKDF Per-Tenant Key Derivation (INT-01)

**What:** Derive a per-tenant Fernet key from a platform master key using HKDF.
**When to use:** Whenever the credential service decrypts an `integration_credentials` row.

```python
# Source: cryptography.io/en/latest/hazmat/primitives/key-derivation-functions/
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

def _derive_tenant_fernet(platform_master_key: bytes, tenant_id: str) -> Fernet:
    """Derive a per-tenant Fernet instance using HKDF.

    platform_master_key: raw bytes of PLATFORM_CREDENTIAL_KEY (decoded from URL-safe b64)
    tenant_id: the tenant UUID string (used as HKDF salt)
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=tenant_id.encode("utf-8"),  # tenant_id as salt = per-tenant uniqueness
        info=b"wchats-integration-credential",  # application-specific context
    )
    derived_key = hkdf.derive(platform_master_key)
    fernet_key = base64.urlsafe_b64encode(derived_key)
    return Fernet(fernet_key)
```

Key points:
- HKDF is for deriving from already-strong key material (not passwords). PBKDF2 would be wrong here.
- `salt=tenant_id.encode()` ensures each tenant gets a different derived key from the same master.
- `info=b"wchats-integration-credential"` prevents cross-purpose key reuse.
- `platform_master_key` must be 32+ bytes. Store as URL-safe b64 in `PLATFORM_CREDENTIAL_KEY` env var, same pattern as `NEON_ENCRYPTION_KEY`.
- `HKDF.derive()` is single-use (one call per Fernet instance creation) — do not store the Fernet instance.

### Pattern 2: CredentialHandle — In-Memory Secret Wrapper (INT-02)

**What:** Wrap a decrypted credential to prevent accidental logging/serialization.

```python
# credential_service.py
from dataclasses import dataclass

@dataclass(frozen=True)
class CredentialHandle:
    """In-memory wrapper for a decrypted provider credential.

    Intentionally non-serializable: __repr__ is redacted so the raw
    credential never appears in structlog output, exception tracebacks,
    or any JSON serialization path.

    Lifetime: created immediately before adapter call, discarded after
    the adapter method returns (garbage collected). Never stored in
    ContextVars, module state, or persistent storage.
    """
    _raw: str

    def use(self) -> str:
        """Return the raw credential for direct SDK initialization."""
        return self._raw

    def __repr__(self) -> str:
        return "<CredentialHandle:redacted>"

    __str__ = __repr__
```

### Pattern 3: get_adapter_for_skill — Credential Resolution Entry Point (INT-02)

**What:** Async factory that resolves the credential and returns the right adapter.
**When to use:** Called at dispatcher step 6, replacing `get_adapter(agent_id)`.

```python
# provider_adapter.py — replaces get_adapter()
async def get_adapter_for_skill(
    skill: str,
    agent_id: str,
    conn_str: str,
) -> ProviderAdapter:
    """Resolve credentials and return the correct ProviderAdapter for a skill.

    Called at step 6 of _execute_transactional_tool. The conn_str is already
    available via _conn_str_var (set by build_tool_server, never a task arg).

    Returns a provider adapter holding an in-memory CredentialHandle.
    The handle is discarded when the returned adapter goes out of scope.
    """
    # 1. Fetch encrypted credential + provider config from tenant DB
    config = await _fetch_credential_config(conn_str, skill)
    if config is None:
        raise ProviderNotConfiguredError(
            f"No integration credential configured for skill '{skill}'"
        )

    # 2. Derive per-tenant Fernet key (in-memory, discarded after decrypt)
    master_key_bytes = base64.urlsafe_b64decode(settings.PLATFORM_CREDENTIAL_KEY)
    fernet = _derive_tenant_fernet(master_key_bytes, config.tenant_id)

    # 3. Decrypt → CredentialHandle (raw credential in memory only)
    try:
        raw_cred = fernet.decrypt(config.encrypted_credential).decode("utf-8")
    except Exception:
        raise CredentialDecryptionError(
            f"Failed to decrypt credential for provider '{config.provider_type}'"
        )
    handle = CredentialHandle(_raw=raw_cred)

    # 4. Dispatch to concrete adapter
    if config.provider_type == "stripe":
        return StripeAdapter(handle=handle, currency_code=config.currency_code)
    elif config.provider_type == "shopify":
        return ShopifyAdapter(handle=handle, shop_url=config.shop_url, currency_code=config.currency_code)
    elif config.provider_type == "woocommerce":
        return WooCommerceAdapter(handle=handle, site_url=config.site_url, currency_code=config.currency_code)
    elif config.provider_type == "calendly":
        return CalendlyAdapter(handle=handle)
    else:
        raise ProviderNotConfiguredError(
            f"Unknown provider_type '{config.provider_type}'"
        )
```

### Pattern 4: StripeAdapter — Idempotency-Key Pass-Through (INT-05)

**What:** Pass TXN-02 client idempotency key to Stripe's native `Idempotency-Key` header.

```python
# Source: docs.stripe.com/api/idempotent_requests [CITED]
import stripe

class StripeAdapter(ProviderAdapter):
    def __init__(self, handle: CredentialHandle, currency_code: str) -> None:
        self._handle = handle
        self._currency_code = currency_code.lower()  # Stripe expects lowercase ISO-4217

    async def issue_refund(self, args: IssueRefundInput, agent_id: str) -> IssueRefundOutput:
        def _sync() -> stripe.Refund:
            client = stripe.StripeClient(self._handle.use())  # key used, never logged
            return client.v1.refunds.create(
                {
                    "charge": args.order_id,         # order_id = Stripe charge ID
                    "amount": args.refund_amount_cents,
                    "reason": "requested_by_customer",
                    "currency": self._currency_code,
                },
                idempotency_key=args.idempotency_key,  # TXN-02 → Stripe Idempotency-Key header
            )
        refund = await asyncio.to_thread(_sync)
        return IssueRefundOutput(
            refund_id=refund.id,
            status="refunded",
            message=f"Refund {refund.id} issued for {args.refund_amount_cents} cents.",
        )
```

### Pattern 5: tools.py Step 6 — The Only Required Change

```python
# tools.py _execute_transactional_tool() step 6 — before (Phase 14):
adapter = get_adapter(agent_id)

# tools.py _execute_transactional_tool() step 6 — after (Phase 16):
from app.services.transactional.provider_adapter import get_adapter_for_skill
try:
    adapter = await get_adapter_for_skill(skill, agent_id, conn_str)
except (ProviderNotConfiguredError, CredentialDecryptionError) as exc:
    await release_idempotency(agent_id, skill, validated.idempotency_key)
    await write_audit_row(
        ..., error=f"provider.not_configured:{exc}"
    )
    return {"content": [{"type": "text", "text": str(exc)}], "is_error": True}
```

This is the ONLY change to `tools.py`. The enforcement order (7 steps) is unchanged.

### Anti-Patterns to Avoid

- **Credential in Celery task args:** Never pass the `CredentialHandle` or raw credential as a Celery task argument. The `agent_id` flows via ContextVar; the credential is fetched inside the adapter call at runtime. [CLAUDE.md rule: "Connection strings never in Celery task args"]
- **Module-level Stripe client:** Do NOT do `stripe.api_key = ...` at module level with the tenant key. Each adapter must initialize `stripe.StripeClient(handle.use())` inside the `asyncio.to_thread` call, so the key is localized to the sync call.
- **Storing the credential handle in a ContextVar:** `CredentialHandle` must go out of scope after the adapter method returns. Storing it in a ContextVar would make it persistent across turns.
- **Using Stripe Agent Toolkit or MCP:** ADR-0002 explicitly forbids this. It would bypass L1–L3 and dump the Stripe API surface into agent context.
- **REST API for Shopify new development:** Shopify REST is deprecated for all apps from February 2025. Use GraphQL mutations.
- **Logging the handle:** `structlog` will call `__repr__` on logged objects — `CredentialHandle.__repr__` is redacted. But never format it into a string explicitly: `log.info("cred", cred=str(handle))` is safe; `log.info(f"cred={handle.use()}")` is not.
- **Synchronous psycopg2 in async context:** `_fetch_credential_config` must use `asyncio.to_thread` (consistent with `idempotency.py`, `actor_seam.py` patterns).
- **Using `Fernet` without the HKDF step for tenant isolation:** If you reuse `NEON_ENCRYPTION_KEY` directly (the existing `fernet_encrypt`/`fernet_decrypt` in `security.py`), all tenants share the same key. INT-01 requires per-tenant key isolation via HKDF.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Symmetric encryption | Custom AES/XOR | `cryptography.fernet.Fernet` (already in project) | Fernet handles IV generation, HMAC authentication, and timestamp — 10+ edge cases |
| Per-tenant key derivation | Hash tenant_id + master_key | `cryptography.hazmat.primitives.kdf.hkdf.HKDF` (already installed) | HKDF correctly handles key stretching, domain separation, and side-channel resistance |
| Stripe API calls | httpx to Stripe REST | `stripe` SDK (`stripe.Refund.create`, `stripe.Subscription.modify`) | SDK handles TLS pinning, API versioning, error parsing, retry logic |
| Shopify order mutations | httpx GraphQL queries | `ShopifyAPI.GraphQL().execute(mutation)` | SDK handles session management, rate-limit headers, OAuth token refresh |
| OAuth1 signing for WooCommerce | Manual HMAC-SHA256 nonce | `WooCommerce` package (`wcapi = API(url, consumer_key, consumer_secret)`) | OAuth1 signing has >6 edge cases (nonce, timestamp, signature method, encoding) |
| Calendly bookings | Scraping Calendly UI | `httpx` + Calendly Scheduling API (`POST /invitees`) | Programmatic API; scraping is brittle and violates ToS |

**Key insight:** Every provider has non-trivial auth and error-handling; the SDKs encode years of fixes. The only case where httpx is the right choice is Calendly (no maintained Python SDK exists).

---

## Provider → Tool Mapping

Each concrete adapter only implements the methods its provider supports. Unsupported methods raise `NotImplementedError` (the dispatcher's `except Exception` catches this and returns `is_error`).

| Tool / Skill | ShopifyAdapter | WooCommerceAdapter | StripeAdapter | CalendlyAdapter |
|-------------|---------------|-------------------|--------------|----------------|
| `place_order` | `orderCreate` mutation | `POST /orders` | `checkout.Session.create` (mode=payment) | — |
| `cancel_order` | `orderCancel` mutation | `PUT /orders/{id}` (status=cancelled) | — | — |
| `issue_refund` | `refundCreate` mutation | `POST /orders/{id}/refunds` | `Refund.create` | — |
| `update_subscription` | — | — | `Subscription.modify` | — |
| `book_slot` | — | — | — | `POST /invitees` |
| `update_customer_record` | Stub (Phase 16 defers) | Stub (Phase 16 defers) | Stub (Phase 16 defers) | — |

The `update_customer_record` tool is not in INT-01 through INT-07. It remains a `[STUB]` in Phase 16 (StubProviderAdapter pattern).

---

## Critical Architecture Decision: integration_credentials Table Location

**INT-01 says: "tenant-DB table"** — this means the `alembic_tenant/` migration chain (currently at 0006), NOT the control DB (`alembic/` chain at 0016).

**Why tenant DB (not control DB):**
- Phase 14 tables (`capability_envelopes`, `tool_calls_audit`, `pending_confirmations`, `tool_idempotency_keys`) are in the **control DB** because they are platform-level records across all agents.
- `integration_credentials` is per-agent sensitive data (each agent's store has its own credentials). It belongs in the agent's isolated Neon tenant DB.
- The `conn_str` (tenant DB connection) is already available to the dispatcher via `_conn_str_var.get()` — no new connection infrastructure needed.
- Neon per-project isolation means a compromised control DB cannot expose tenant provider keys.

**Migration chain:** Add `0007_integration_credentials.py` in `alembic_tenant/versions/`.

```sql
-- alembic_tenant/versions/0007_integration_credentials.py
CREATE TABLE integration_credentials (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_type   TEXT NOT NULL,          -- 'stripe' | 'shopify' | 'woocommerce' | 'calendly'
    credential_data BYTEA NOT NULL,         -- Fernet-encrypted JSON blob (per-tenant key)
    config_data     JSONB NOT NULL DEFAULT '{}',  -- provider-specific config (shop_url, site_url, etc.)
    currency_code   TEXT NOT NULL DEFAULT 'USD',  -- INT-07 single currency per tenant
    enabled_skills  JSONB NOT NULL DEFAULT '[]',  -- which skills this credential permits
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);
-- One credential per provider_type per agent DB (enforced at application layer)
CREATE INDEX ix_integration_credentials_provider_type
    ON integration_credentials (provider_type);
```

The `credential_data` BYTEA is a Fernet-encrypted JSON blob. Example plaintext:
- Stripe: `{"api_key": "rk_live_..."}`
- Shopify: `{"access_token": "shpat_..."}`
- WooCommerce: `{"consumer_key": "ck_...", "consumer_secret": "cs_..."}`
- Calendly: `{"personal_access_token": "eyJ..."}`

---

## Critical Architecture Decision: tenant_id for HKDF Salt

The `HKDF` salt is `tenant_id` — but which `tenant_id`? The agent model (`agents` table in control DB) has `tenant_id: UUID`. The dispatcher has `agent_id` but not `tenant_id` directly.

**Resolution:** The `_fetch_credential_config()` function fetches the credential from the tenant DB. To derive the per-tenant Fernet key, the service needs the `tenant_id`. Options:
1. Store `tenant_id` in the tenant DB itself (e.g., in a `tenant_config` single-row table) — clean but requires a 0007 or 0008 migration.
2. Pass `tenant_id` through the ContextVar alongside `agent_id` — `_tenant_id_var` would need to be set in `build_tool_server`.
3. Look up `tenant_id` from the control DB by `agent_id` — adds a cross-DB query.

**Recommended approach (Option 2):** Add `_tenant_id_var` alongside existing `_agent_id_var`, `_conversation_id_var`, `_conn_str_var` in `agent_tools.py`. The `build_tool_server` function already sets these ContextVars at session start. Add `tenant_id` to that initialization. This keeps the credential derivation self-contained in the credential service.

---

## INT-07: Single Currency Enforcement

The `currency_code` column in `integration_credentials` is the per-tenant currency set at deploy time. The adapter enforces it:

```python
# In StripeAdapter.issue_refund():
if args.refund_amount_cents is not None:
    # currency_code is always the configured one — never from tool args
    response = client.v1.refunds.create(
        {"charge": args.order_id, "amount": args.refund_amount_cents,
         "currency": self._currency_code}  # from integration_credentials.currency_code
    )
```

Multi-currency support is **OUT OF SCOPE** per INT-07. The M8 pre-deploy checklist should include a "confirm currency" step. The planner should note this as a deploy-time configuration item, not a runtime enforcement item.

---

## Common Pitfalls

### Pitfall 1: HKDF is Single-Use Per Call
**What goes wrong:** Developer creates an HKDF instance once at module level and calls `.derive()` multiple times — `cryptography` raises `AlreadyFinalized` on the second call.
**Why it happens:** HKDF's `derive()` finalizes the internal state.
**How to avoid:** Always create a fresh `HKDF(...)` instance inside `_derive_tenant_fernet()`. Never cache the HKDF instance.
**Warning signs:** `cryptography.exceptions.AlreadyFinalized` exception at second tenant credential fetch.

### Pitfall 2: StripeClient vs stripe.api_key (Module-Level)
**What goes wrong:** Setting `stripe.api_key` at module level or at adapter init time in a multi-tenant worker — second tenant's call uses first tenant's key.
**Why it happens:** The old stripe SDK pattern sets a global `stripe.api_key` which is shared across threads.
**How to avoid:** Always use `stripe.StripeClient(api_key)` pattern (v15.x) inside each `asyncio.to_thread` call. Never use module-level `stripe.api_key` with per-tenant keys.
**Warning signs:** Refunds appearing on the wrong tenant's Stripe account.

### Pitfall 3: Synchronous SDK Calls on Event Loop
**What goes wrong:** Calling `stripe.Refund.create()` or `shopify.GraphQL().execute()` directly in an `async` function without `asyncio.to_thread` — blocks the Celery worker event loop.
**Why it happens:** All provider SDKs (stripe, ShopifyAPI) are synchronous Python.
**How to avoid:** Always wrap sync SDK calls: `result = await asyncio.to_thread(lambda: stripe_client.v1.refunds.create(...))`
**Warning signs:** Other async tools in the same worker appear to hang during provider calls.

### Pitfall 4: ContextVar Not Propagated to asyncio.to_thread
**What goes wrong:** `_tenant_id_var.get()` inside a `to_thread` callback returns `None` — ContextVars ARE propagated to `asyncio.to_thread` in Python 3.7+ but NOT to `ThreadPoolExecutor` if used directly.
**Why it happens:** `asyncio.to_thread()` uses the current context snapshot at call time — this is correct. Direct `loop.run_in_executor()` does NOT copy context.
**How to avoid:** Always use `asyncio.to_thread()` (not `loop.run_in_executor`) for synchronous calls that need ContextVar values. The existing codebase (idempotency.py, actor_seam.py) correctly uses `asyncio.to_thread`.
**Warning signs:** `LookupError: <ContextVar ... has no value>` inside thread callbacks.

### Pitfall 5: WooCommerce Package OAuth1 vs HTTPS
**What goes wrong:** WooCommerce API returns 401 Unauthorized when used with HTTP (not HTTPS) — the OAuth1 signing is required for HTTP but HTTPS uses basic auth.
**Why it happens:** The `WooCommerce` package uses different auth schemes based on whether the URL is HTTP or HTTPS.
**How to avoid:** Always configure WooCommerce with HTTPS (`url="https://..."`) and basic auth (consumer_key, consumer_secret). Never deploy with HTTP.
**Warning signs:** 401 responses from WooCommerce API calls.

### Pitfall 6: Shopify API Version Pin
**What goes wrong:** Shopify returns 404 or deprecation error when using an old API version string.
**Why it happens:** The ShopifyAPI Python library requires an explicit API version string (`"2024-07"` or `"2025-04"`).
**How to avoid:** Pin the API version in `integration_credentials.config_data` (e.g., `{"api_version": "2025-04"}`). Upgrade the version when Shopify EOLs it.
**Warning signs:** Shopify returning HTTP 404 on GraphQL mutations, or deprecation headers in responses.

### Pitfall 7: Calendly Scheduling API — Paid Plan Requirement
**What goes wrong:** Calendly API returns 403 Forbidden on `POST /invitees` even with a valid PAT.
**Why it happens:** The Calendly Scheduling API (which creates event invitees programmatically without UI) requires a **paid Calendly plan**. Free plans cannot use it.
**How to avoid:** Document this in the deploy-time checklist. Provide a fallback: `GET /event_types/{uuid}/scheduling_url` returns a URL that can be sent to the customer if programmatic booking is unavailable.
**Warning signs:** 403 on `POST /invitees` even with a valid Authorization header.

### Pitfall 8: `get_adapter` is Currently Synchronous — Must Become Async
**What goes wrong:** Changing `get_adapter(agent_id)` to an async function `get_adapter_for_skill(...)` without updating the `tools.py` caller to `await` it.
**Why it happens:** Phase 14 `get_adapter()` is sync (just returns `_STUB_ADAPTER`). Phase 16 needs DB I/O, hence must be async.
**How to avoid:** The single change to `tools.py` is: `adapter = get_adapter(agent_id)` → `adapter = await get_adapter_for_skill(skill, agent_id, conn_str)`. The dispatcher is already `async def`.

### Pitfall 9: Phase 14 LANDMINE — call_actor_gate is NOT an SDK Pre-Tool Hook
**What goes wrong:** Trying to put credential resolution into the Agent SDK `pre_tool_use` hook.
**Why it happens:** Phase 14 explicitly established that hooks cannot access Python ContextVars or the control DB.
**How to avoid:** Credential resolution belongs in the dispatcher (step 6), never in SDK hooks. This is already architecturally enforced by the existing code structure.

---

## Code Examples

### Integration Credentials — Tenant DB Schema

```python
# alembic_tenant/versions/0007_integration_credentials.py
def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS integration_credentials (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            provider_type   TEXT NOT NULL,
            credential_data BYTEA NOT NULL,
            config_data     JSONB NOT NULL DEFAULT '{}',
            currency_code   TEXT NOT NULL DEFAULT 'USD',
            enabled_skills  JSONB NOT NULL DEFAULT '[]',
            created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_integration_credentials_provider_type
        ON integration_credentials (provider_type)
    """)
```

### Shopify GraphQL Adapter (INT-03)

```python
# Source: shopify.dev/docs/api/admin-graphql/latest/mutations/orderCreate [CITED]
import shopify

class ShopifyAdapter(ProviderAdapter):
    def __init__(self, handle: CredentialHandle, shop_url: str, currency_code: str) -> None:
        self._handle = handle
        self._shop_url = shop_url  # e.g. "mystore.myshopify.com"
        self._currency_code = currency_code
        self._api_version = "2025-04"

    def _make_session(self) -> shopify.Session:
        session = shopify.Session(self._shop_url, self._api_version, self._handle.use())
        shopify.ShopifyResource.activate_session(session)
        return session

    async def issue_refund(self, args: IssueRefundInput, agent_id: str) -> IssueRefundOutput:
        mutation = """
        mutation refundCreate($input: RefundInput!) {
          refundCreate(input: $input) {
            refund { id }
            userErrors { field message }
          }
        }
        """
        def _sync():
            self._make_session()
            result = shopify.GraphQL().execute(
                mutation,
                variables={"input": {"orderId": args.order_id, "currency": self._currency_code,
                                     "refundLineItems": [], "note": args.reason}}
            )
            shopify.ShopifyResource.clear_session()
            return result
        result = await asyncio.to_thread(_sync)
        # parse result JSON → IssueRefundOutput
        ...
```

### Calendly Adapter (INT-06)

```python
# Source: developer.calendly.com/api-docs (Scheduling API) [CITED: LOW confidence — Scheduling API docs require paid plan]
import httpx

CALENDLY_API_BASE = "https://api.calendly.com"

class CalendlyAdapter(ProviderAdapter):
    def __init__(self, handle: CredentialHandle) -> None:
        self._handle = handle

    async def book_slot(self, args: BookSlotInput, agent_id: str) -> BookSlotOutput:
        async with httpx.AsyncClient() as client:
            # The event_type_uuid is stored in integration_credentials.config_data
            response = await client.post(
                f"{CALENDLY_API_BASE}/invitees",
                headers={"Authorization": f"Bearer {self._handle.use()}",
                         "Content-Type": "application/json"},
                json={
                    "event_type": args.service_type,  # Calendly event type UUID
                    "start_time": f"{args.preferred_date}T{args.preferred_time}:00Z",
                    "invitee": {"name": args.customer_name},
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
        return BookSlotOutput(
            booking_id=data["resource"]["uri"],
            status="confirmed",
            message=f"Booking confirmed. Join URL: {data['resource'].get('join_url', 'N/A')}",
        )
```

Note: Calendly adapter uses `httpx` directly (async-native), so no `asyncio.to_thread` is needed.

### Settings — PLATFORM_CREDENTIAL_KEY Addition

```python
# app/core/config.py — add after NEON_ENCRYPTION_KEY:
# Base64url-encoded 32 bytes; key material for HKDF per-tenant credential derivation
PLATFORM_CREDENTIAL_KEY: str
# Note: keep __repr__ suppression already present — this field is covered by it
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `stripe.api_key = "sk_..."` (global) | `stripe.StripeClient("sk_...")` (per-call) | stripe-python v10+ | Required for multi-tenant workers; global key is not thread-safe |
| Shopify REST Admin API | Shopify GraphQL Admin API | Mandatory for public apps Feb 2025 | REST deprecated; GraphQL is the standard going forward |
| WooCommerce wc/v1, wc/v2 | wc/v3 | WordPress 5.0+ | v3 is stable; v1/v2 are legacy |
| OAuth PAT for Calendly simple cases | Scheduling API (paid plan required) | Calendly 2023 | Creates bookings without UI redirect; requires paid plan |
| Direct `psycopg2.connect()` per adapter call | `asyncio.to_thread(_sync_psycopg2_call)` | Established in Phase 14 | Consistent with existing patterns in idempotency.py, actor_seam.py |

**Deprecated/outdated:**
- `stripe.Refund.create()` (old module-level pattern): Replaced by `stripe.StripeClient(key).v1.refunds.create()`. The old pattern is still supported but not thread-safe for multi-tenant use.
- Shopify REST `/admin/api/{version}/orders.json`: Deprecated for public apps. Use GraphQL mutations.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Calendly Scheduling API endpoint is `POST /invitees` and requires a paid plan | Code Examples / Pitfall 7 | If endpoint changed or free plan now works, the adapter implementation stays the same; only the error behavior differs |
| A2 | WooCommerce `WooCommerce` 3.0.0 package (last updated 2021) is still compatible with wc/v3 API | Standard Stack | If package is broken, fallback is httpx + requests-oauthlib; plan should include a verify checkpoint |
| A3 | Shopify private/custom apps can still use ShopifyAPI 12.x with GraphQL mutations | Architecture | REST is deprecated for public apps but private apps still work; GraphQL is the recommended path regardless |
| A4 | `_tenant_id_var` does not currently exist in `agent_tools.py`; it needs to be added | Architecture Decision: HKDF Salt | If tenant_id is already available via another mechanism, the ContextVar addition may be unnecessary |
| A5 | Calendly `POST /invitees` requires `event_type_uri` (a Calendly event type UUID URL, not a service name) | Code Examples | BookSlotInput.service_type would need to be a Calendly event_type URI, not a human label — requires schema note |

---

## Open Questions

1. **`tenant_id` availability in the tool dispatcher**
   - What we know: `_agent_id_var`, `_conversation_id_var`, `_conn_str_var` are set by `build_tool_server` in `agent_tools.py`. `agent_id` → `tenant_id` mapping is in the control DB (`agents.tenant_id`).
   - What's unclear: Is `tenant_id` already available as a ContextVar, or does `get_adapter_for_skill()` need to do a control DB lookup for it?
   - Recommendation: Add `_tenant_id_var: ContextVar[str] = ContextVar("tenant_id_var", default="")` to `agent_tools.py` and set it in `build_tool_server` alongside the existing ContextVars. This is one line in `agent_tools.py` and one line in the `build_tool_server` setup.

2. **Calendly event_type mapping**
   - What we know: Calendly bookings need an `event_type` URI (a specific Calendly event URL, not a generic service type label like "consultation").
   - What's unclear: Does `BookSlotInput.service_type` need to be a Calendly event_type URI, or should the mapping live in `integration_credentials.config_data`?
   - Recommendation: Store a mapping in `config_data`: `{"event_types": {"consultation": "https://api.calendly.com/event_types/UUID"}}`. The adapter uses `args.service_type` as a key to look up the event_type URI.

3. **Stripe Restricted API Key provisioning flow**
   - What we know: Restricted keys are created manually in the Stripe dashboard and stored in `integration_credentials`.
   - What's unclear: Is there an admin UI step in Phase 16 (just storing a pre-created key) or must Phase 16 also build a credential management UI?
   - Recommendation: Phase 16 builds only the storage + adapter. Key provisioning is a deploy-time step documented in an admin runbook (not a UI). The Phase 18 capability admin UI can surface credential status.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `stripe` (PyPI) | INT-05 (StripeAdapter) | Partial — 11.4.1 installed | 11.4.1 (upgrade to 15.3.0 needed) | None — must upgrade |
| `ShopifyAPI` (PyPI) | INT-03 (ShopifyAdapter) | Not installed | — | httpx + manual GraphQL (more work) |
| `WooCommerce` (PyPI) | INT-04 (WooCommerceAdapter) | Not installed | — | httpx + requests-oauthlib OAuth1 signing |
| `httpx` (PyPI) | INT-06 (CalendlyAdapter) | Yes (0.28.1) | 0.28.1 | — |
| `cryptography` (PyPI) | INT-01 (HKDF derivation) | Yes (48.0.0) | 48.0.0 | — |
| Stripe test mode credentials | INT-05 integration test | Not verified | — | Use Stripe test mode keys (no cost) |
| Shopify dev store | INT-03 integration test | Not verified | — | Shopify provides free dev stores |
| WooCommerce test site | INT-04 integration test | Not verified | — | Local WordPress install or staging site |
| Calendly paid account | INT-06 integration test | Not verified | — | Mock the httpx call; flag Calendly test as env-gated |

**Missing dependencies with no fallback:**
- `stripe` upgrade from 11.4.1 → 15.3.0 is required (new StripeClient API)

**Missing dependencies with fallback:**
- `ShopifyAPI` — can use httpx + GraphQL directly if package fails legitimacy gate
- `WooCommerce` — can use httpx + requests-oauthlib for OAuth1 signing

---

## Validation Architecture

`nyquist_validation: true` — all INT requirements must be validated by automated tests.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (already configured in pyproject.toml) |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` — `asyncio_mode = "auto"` |
| Quick run command | `cd apps/api && pytest tests/unit/test_integration_adapters.py -x -q` |
| Full suite command | `cd apps/api && pytest tests/ -x -q` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| INT-01 | `integration_credentials` table has BYTEA column; Alembic migration applies | unit | `pytest tests/unit/test_credential_service.py::test_table_schema -x -q` | Wave 0 |
| INT-01 | Per-tenant Fernet key derivation: two different tenant_ids produce different Fernet keys | unit | `pytest tests/unit/test_credential_service.py::test_hkdf_per_tenant_isolation -x -q` | Wave 0 |
| INT-01 | Raw credential is not readable from control DB or tool schemas | unit | `pytest tests/unit/test_credential_service.py::test_credential_never_in_tool_schema -x -q` | Wave 0 |
| INT-02 | `CredentialHandle.__repr__` is redacted (does not leak raw credential) | unit | `pytest tests/unit/test_credential_service.py::test_handle_repr_redacted -x -q` | Wave 0 |
| INT-02 | `get_adapter_for_skill()` returns the correct adapter class for each provider_type | unit | `pytest tests/unit/test_credential_service.py::test_adapter_dispatch -x -q` | Wave 0 |
| INT-02 | No credential field appears in any of the 6 mutating tool input schemas | unit | `pytest tests/unit/test_transactional_contract.py::test_no_credential_in_schemas -x -q` | Wave 0 |
| INT-03 | ShopifyAdapter.issue_refund calls `refundCreate` GraphQL mutation with correct variables | unit (mock ShopifyAPI) | `pytest tests/unit/test_shopify_adapter.py::test_issue_refund_calls_refund_create -x -q` | Wave 0 |
| INT-03 | ShopifyAdapter.place_order calls `orderCreate` mutation | unit (mock ShopifyAPI) | `pytest tests/unit/test_shopify_adapter.py::test_place_order_calls_order_create -x -q` | Wave 0 |
| INT-03 | ShopifyAdapter.cancel_order calls `orderCancel` mutation | unit (mock ShopifyAPI) | `pytest tests/unit/test_shopify_adapter.py::test_cancel_order_calls_order_cancel -x -q` | Wave 0 |
| INT-04 | WooCommerceAdapter.issue_refund calls `POST /orders/{id}/refunds` | unit (mock httpx/woocommerce) | `pytest tests/unit/test_woocommerce_adapter.py::test_issue_refund -x -q` | Wave 0 |
| INT-05 | StripeAdapter.issue_refund passes idempotency_key to Stripe `Refund.create` | unit (mock stripe.StripeClient) | `pytest tests/unit/test_stripe_adapter.py::test_issue_refund_idempotency_key -x -q` | Wave 0 |
| INT-05 | StripeAdapter.update_subscription calls `Subscription.modify` with correct items | unit (mock stripe) | `pytest tests/unit/test_stripe_adapter.py::test_update_subscription -x -q` | Wave 0 |
| INT-05 | StripeAdapter.place_order creates `checkout.Session` with mode=payment | unit (mock stripe) | `pytest tests/unit/test_stripe_adapter.py::test_place_order_checkout_session -x -q` | Wave 0 |
| INT-06 | CalendlyAdapter.book_slot sends `POST /invitees` with correct payload | unit (mock httpx) | `pytest tests/unit/test_calendly_adapter.py::test_book_slot -x -q` | Wave 0 |
| INT-07 | StripeAdapter uses `currency_code` from credential config, not from tool args | unit | `pytest tests/unit/test_stripe_adapter.py::test_currency_from_config_not_args -x -q` | Wave 0 |
| E2E | Full dispatcher flow: reserve → actor approve → adapter (mocked) → audit → finalize | integration (INTEGRATION_TESTS_ENABLED=1) | `pytest tests/integration/test_integration_e2e.py -x -q -m integration` | Wave 0 |
| E2E-Stripe | Live Stripe test mode: issue_refund produces a real Stripe refund object | integration env-gated (STRIPE_TEST_MODE_ENABLED=1) | `pytest tests/integration/test_stripe_live.py -x -q -m integration` | Wave 0 |

### Credential-Never-Leaks Invariant (Critical Test)
```python
# tests/unit/test_credential_service.py
def test_handle_repr_redacted():
    handle = CredentialHandle(_raw="sk_live_secret_credential")
    assert "secret" not in repr(handle)
    assert "secret" not in str(handle)
    assert repr(handle) == "<CredentialHandle:redacted>"

def test_no_credential_in_tool_schema():
    """Assert that no mutating tool Input schema has a credential field."""
    for skill_name, tool_def in TOOL_REGISTRY.items():
        if not tool_def.mutating:
            continue
        # Reach into the SDK tool's input_schema
        schema = tool_def.sdk_tool.input_schema
        field_names = list(schema.get("properties", {}).keys())
        for forbidden in ["api_key", "credential", "secret", "password", "token", "access_token"]:
            assert forbidden not in field_names, \
                f"Credential field '{forbidden}' found in {skill_name} input schema"

def test_hkdf_per_tenant_isolation():
    master_key = b"\x00" * 32
    fernet_t1 = _derive_tenant_fernet(master_key, "tenant-aaa")
    fernet_t2 = _derive_tenant_fernet(master_key, "tenant-bbb")
    ciphertext = fernet_t1.encrypt(b"secret")
    with pytest.raises(Exception):  # cryptography.fernet.InvalidToken
        fernet_t2.decrypt(ciphertext)  # cannot decrypt with wrong tenant key
```

### Sampling Rate
- **Per task commit:** `cd apps/api && pytest tests/unit/test_integration_adapters.py tests/unit/test_credential_service.py -x -q`
- **Per wave merge:** `cd apps/api && pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd-verify-work 16`

### Wave 0 Gaps
- [ ] `apps/api/tests/unit/test_credential_service.py` — covers INT-01, INT-02 invariants
- [ ] `apps/api/tests/unit/test_shopify_adapter.py` — covers INT-03 (mocked ShopifyAPI)
- [ ] `apps/api/tests/unit/test_woocommerce_adapter.py` — covers INT-04 (mocked WooCommerce)
- [ ] `apps/api/tests/unit/test_stripe_adapter.py` — covers INT-05 (mocked stripe.StripeClient)
- [ ] `apps/api/tests/unit/test_calendly_adapter.py` — covers INT-06 (mocked httpx)
- [ ] `apps/api/tests/integration/test_integration_e2e.py` — full dispatcher flow with mocked adapters, env-gated

---

## Security Domain

Security enforcement is ACTIVE (`block_on: high` per SECURITY.md from Phase 15). ASVS Level 1.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | This phase doesn't touch auth endpoints |
| V3 Session Management | No | No new session management |
| V4 Access Control | Yes | Credential service inaccessible from agent code paths; capability envelope gate preserved in dispatcher |
| V5 Input Validation | Yes | Typed Pydantic schemas (already enforced from Phase 14) prevent injection into provider calls |
| V6 Cryptography | YES | HKDF + Fernet; `CredentialHandle.__repr__` redacted; no custom crypto |

### Threat Model for Phase 16

| Threat ID | Pattern | STRIDE | Standard Mitigation |
|-----------|---------|--------|---------------------|
| T-16-01 | Raw credential appears in structlog output | Information Disclosure | `CredentialHandle.__repr__` returns `"<CredentialHandle:redacted>"`. Never pass `handle.use()` to any logger. |
| T-16-02 | SSRF via adapter URL injection | Tampering | Adapter calls fixed provider endpoints (Stripe API, Shopify API). No user-controlled URLs in typed tool schemas. `shop_url` comes from `integration_credentials.config_data`, not from tool args. |
| T-16-03 | Over-scoped Stripe Restricted Key grants more than needed skills | Elevation of Privilege | Per-tenant Restricted API Key scoped to only enabled skills (mirrors L2 capability envelope). Document key scope → skill mapping in admin guide. |
| T-16-04 | Injection-to-action via provider call (confused deputy) | Elevation of Privilege | Capability envelope + Actor validator + typed schemas block this chain before `get_adapter_for_skill()` is called. The provider call is the last gate, not the first. |
| T-16-05 | Platform master key (PLATFORM_CREDENTIAL_KEY) compromised | Information Disclosure / Tampering | Key stored as env var only (never in DB); same security posture as `NEON_ENCRYPTION_KEY`. Rotation strategy: re-encrypt all `integration_credentials` rows with a new HKDF derivation (Phase 18 scope). |
| T-16-06 | Raw credential in Celery task args | Information Disclosure | Never pass credentials as Celery task arguments. `agent_id` flows via ContextVar; credential is fetched inside `get_adapter_for_skill()` at runtime. [CLAUDE.md rule reinforced] |
| T-16-07 | `CredentialHandle` serialized to JSON / stored in Redis | Information Disclosure | `CredentialHandle` is NOT JSON-serializable by design (frozen dataclass with no `__json__`). Never store in any ContextVar or Redis key. |
| T-16-08 | Replay idempotency key reuse exploits Stripe | Replay Attack | TXN-02 idempotency key is passed to Stripe's native `Idempotency-Key` header. Stripe treats the same key as a replay — returns the original response without re-executing. Both W Chats idempotency engine AND Stripe idempotency protect against replay. |
| T-16-09 | PII in tool_calls_audit.arguments (e.g., customer_email from PlaceOrderInput) | Information Disclosure | `audit.py` writes `raw_args` to the audit table. `PlaceOrderInput.customer_email` IS in `raw_args`. This is an existing posture (Phase 14). Phase 16 does not introduce new PII fields. L4 PII firewall (Phase 18) will address. |

### Key Security Constraint: CredentialHandle Must Never Escape
The `CredentialHandle` must be proved unreachable from agent-facing code paths:
1. Tool input schemas (Phase 14 locked) have no credential fields. [VERIFIED: schemas.py]
2. `get_adapter_for_skill()` is called only inside `_execute_transactional_tool()` (server-side dispatcher), never in a route handler or SDK hook.
3. `write_audit_row()` receives `raw_args` (the tool's typed arguments) and `result` (the adapter's typed output) — neither ever includes the credential handle.

---

## Sources

### Primary (MEDIUM confidence — confirmed via official registry)
- [PyPI: stripe 15.3.0](https://pypi.org/project/stripe/) — confirmed latest version, official Stripe SDK
- [PyPI: ShopifyAPI 12.7.0](https://pypi.org/project/ShopifyAPI/) — confirmed latest version, Shopify org repo
- [PyPI: WooCommerce 3.0.0](https://pypi.org/project/WooCommerce/) — confirmed latest version, WooCommerce org repo
- [Stripe API Docs: Create Refund](https://docs.stripe.com/api/refunds/create?lang=python) — Python SDK refund creation with idempotency key
- [Stripe API Docs: Update Subscription](https://docs.stripe.com/api/subscriptions/update?lang=python) — subscription modify pattern
- [Stripe API Docs: Create Checkout Session](https://docs.stripe.com/api/checkout/sessions/create?lang=python) — place_order via payment mode
- [Stripe API Docs: Idempotent Requests](https://docs.stripe.com/api/idempotent_requests) — idempotency_key parameter → Idempotency-Key header
- [WooCommerce REST API Docs](https://woocommerce.github.io/woocommerce-rest-api-docs/) — orders, refunds, authentication
- [Shopify Admin GraphQL: orderCreate](https://shopify.dev/docs/api/admin-graphql/latest/mutations/orderCreate) — GraphQL mutation
- [Shopify Admin GraphQL: orderCancel](https://shopify.dev/docs/api/admin-graphql/latest/mutations/ordercancel)
- [Shopify Admin GraphQL: refundCreate](https://shopify.dev/docs/api/admin-graphql/latest/mutations/refundCreate)

### Secondary (MEDIUM confidence — cryptography library docs)
- [cryptography.io: Key Derivation Functions](https://cryptography.io/en/latest/hazmat/primitives/key-derivation-functions/) — HKDF usage pattern
- [cryptography.io: Fernet](https://cryptography.io/en/latest/fernet/) — Fernet symmetric encryption

### Tertiary (LOW confidence — web search summary)
- [Calendly Developer Portal](https://developer.calendly.com/api-docs/p3ghrxrwbl8kqe-create-event-invitee) — Scheduling API create invitee endpoint (page content inaccessible; endpoint inferred from URL + community references)
- [Calendly Getting Started](https://developer.calendly.com/getting-started) — Base URL: `https://api.calendly.com`, Bearer token authentication

---

## Metadata

**Confidence breakdown:**
- Standard stack (stripe, ShopifyAPI): MEDIUM — confirmed via PyPI registry and official org GitHub repos; not verified via Context7 or official docs in this session
- Architecture (HKDF derivation, CredentialHandle, dispatcher step 6): HIGH — directly derived from existing codebase (security.py, tools.py, actor_seam.py) + cryptography library docs
- Pitfalls: HIGH — derived from existing Phase 14/15 landmines + provider-specific documentation
- Calendly Scheduling API: LOW — endpoint confirmed from URL pattern but actual request shape inferred

**Research date:** 2026-06-30
**Valid until:** 2026-07-30 (Stripe and Shopify APIs may version-bump; HKDF pattern is stable)
