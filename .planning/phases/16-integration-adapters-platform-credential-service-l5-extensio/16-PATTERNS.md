# Phase 16: Integration Adapters + Platform Credential Service — Pattern Map

**Mapped:** 2026-06-30
**Files analyzed:** 10 new/modified files
**Analogs found:** 10 / 10

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `apps/api/app/services/transactional/credential_service.py` | service | request-response | `apps/api/app/core/security.py` | role-match (same crypto primitives, Fernet pattern) |
| `apps/api/app/services/transactional/provider_adapter.py` | service | request-response | itself (MODIFY) | exact — add `get_adapter_for_skill()` async factory replacing `get_adapter()` |
| `apps/api/app/services/transactional/adapters/__init__.py` | config | — | `apps/api/app/services/transactional/__init__.py` | exact (empty package init) |
| `apps/api/app/services/transactional/adapters/stripe_adapter.py` | service | request-response | `apps/api/app/services/transactional/provider_adapter.py` (StubProviderAdapter) | role-match (same ABC subclass pattern + asyncio.to_thread) |
| `apps/api/app/services/transactional/adapters/shopify_adapter.py` | service | request-response | same as above | role-match |
| `apps/api/app/services/transactional/adapters/woocommerce_adapter.py` | service | request-response | same as above | role-match |
| `apps/api/app/services/transactional/adapters/calendly_adapter.py` | service | request-response | `apps/api/app/services/actor_seam.py` (_fetch_history httpx/asyncio pattern) | partial-match (async httpx pattern) |
| `apps/api/alembic_tenant/versions/0007_integration_credentials.py` | migration | CRUD | `apps/api/alembic_tenant/versions/0006_red_team_runs_status.py` | exact (same DDL style, IF NOT EXISTS guards, op.execute) |
| `apps/api/app/core/config.py` | config | — | itself (MODIFY) | exact — add `PLATFORM_CREDENTIAL_KEY: str` field after `NEON_ENCRYPTION_KEY` |
| `apps/api/app/services/transactional/tools.py` | service | request-response | itself (MODIFY) | exact — one-line change at line 384 |
| `apps/api/app/services/agent_tools.py` | service | request-response | itself (MODIFY) | exact — add `_tenant_id_var` alongside existing ContextVars |

---

## Pattern Assignments

---

### `apps/api/app/services/transactional/credential_service.py` (service, request-response)

**Analog:** `apps/api/app/core/security.py`

**Imports pattern** (`security.py` lines 19–26):
```python
from cryptography.fernet import Fernet
from app.core.config import settings
```

New file needs to add:
```python
import asyncio
import base64
import json
import psycopg2
import structlog
from dataclasses import dataclass
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from app.core.config import settings
```

**Existing Fernet pattern** (`security.py` lines 40–67):
```python
def _get_fernet() -> Fernet:
    """Build a Fernet instance from NEON_ENCRYPTION_KEY."""
    key_bytes: bytes = settings.NEON_ENCRYPTION_KEY.encode()
    return Fernet(key_bytes)

def fernet_encrypt(plaintext: str) -> bytes:
    return _get_fernet().encrypt(plaintext.encode())

def fernet_decrypt(ciphertext: bytes) -> str:
    return _get_fernet().decrypt(ciphertext).decode()
```

**New HKDF derivation pattern** (mirrors `_get_fernet()` but per-tenant):
```python
def _derive_tenant_fernet(platform_master_key: bytes, tenant_id: str) -> Fernet:
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=tenant_id.encode("utf-8"),
        info=b"wchats-integration-credential",
    )
    derived_key = hkdf.derive(platform_master_key)
    fernet_key = base64.urlsafe_b64encode(derived_key)
    return Fernet(fernet_key)
    # CRITICAL: create a fresh HKDF instance per call — HKDF.derive() is single-use
```

**CredentialHandle pattern** (new, no direct analog; mirrors security docstring style):
```python
@dataclass(frozen=True)
class CredentialHandle:
    _raw: str

    def use(self) -> str:
        return self._raw

    def __repr__(self) -> str:
        return "<CredentialHandle:redacted>"

    __str__ = __repr__
```

**asyncio.to_thread pattern for psycopg2** (`actor_seam.py` lines 108–124):
```python
def _sync_fetch() -> list[dict]:
    conn = psycopg2.connect(conn_str, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT ... FROM ... WHERE ...", (param,))
            rows = cur.fetchall()
    finally:
        conn.close()
    return rows

return await asyncio.to_thread(_sync_fetch)
```
Copy this pattern exactly for `_fetch_credential_config()`.

**Error handling pattern** (`actor_seam.py` lines 191–197):
```python
try:
    history_rows = await _fetch_history(conn_str, conversation_id)
except Exception as exc:  # noqa: BLE001
    log.warning("actor_gate.history_fetch_failed", agent_id=agent_id, error=str(exc))
```

---

### `apps/api/app/services/transactional/provider_adapter.py` (MODIFY)

**Analog:** itself — add `get_adapter_for_skill()` async function; keep all existing code.

**Current `get_adapter` signature** (lines 164–171):
```python
def get_adapter(agent_id: str | None = None) -> ProviderAdapter:
    """Phase 14: always returns the stub singleton."""
    return _STUB_ADAPTER
```

**Replacement pattern** (NEW async function added below `get_adapter`; `get_adapter` may remain for test backward compat):
```python
from app.services.transactional.credential_service import (
    CredentialHandle,
    CredentialDecryptionError,
    ProviderNotConfiguredError,
    get_adapter_for_skill,   # or define inline here
)
```
The function signature from RESEARCH.md (Pattern 3):
```python
async def get_adapter_for_skill(
    skill: str,
    agent_id: str,
    conn_str: str,
) -> ProviderAdapter:
    ...
```

**Import additions** (add at top of file after existing imports):
```python
from app.services.transactional.adapters.stripe_adapter import StripeAdapter
from app.services.transactional.adapters.shopify_adapter import ShopifyAdapter
from app.services.transactional.adapters.woocommerce_adapter import WooCommerceAdapter
from app.services.transactional.adapters.calendly_adapter import CalendlyAdapter
```

---

### `apps/api/app/services/transactional/adapters/stripe_adapter.py` (NEW, service, request-response)

**Analog:** `apps/api/app/services/transactional/provider_adapter.py` (StubProviderAdapter, lines 81–171)

**Imports pattern** (mirror StubProviderAdapter imports + new SDK):
```python
from __future__ import annotations
import asyncio
import stripe
import structlog
from app.services.transactional.provider_adapter import ProviderAdapter
from app.services.transactional.credential_service import CredentialHandle
from app.services.transactional.schemas import (
    IssueRefundInput, IssueRefundOutput,
    PlaceOrderInput, PlaceOrderOutput,
    UpdateSubscriptionInput, UpdateSubscriptionOutput,
    BookSlotInput, BookSlotOutput,
    CancelOrderInput, CancelOrderOutput,
    UpdateCustomerRecordInput, UpdateCustomerRecordOutput,
)
```

**Core pattern** — abc subclass + `__init__` with handle (mirrors StubProviderAdapter structure, lines 81–100):
```python
class StripeAdapter(ProviderAdapter):
    def __init__(self, handle: CredentialHandle, currency_code: str) -> None:
        self._handle = handle
        self._currency_code = currency_code.lower()

    async def issue_refund(self, args: IssueRefundInput, agent_id: str) -> IssueRefundOutput:
        def _sync() -> stripe.Refund:
            client = stripe.StripeClient(self._handle.use())
            return client.v1.refunds.create(
                {"charge": args.order_id, "amount": args.refund_amount_cents,
                 "reason": "requested_by_customer", "currency": self._currency_code},
                idempotency_key=args.idempotency_key,  # TXN-02 → Stripe Idempotency-Key
            )
        refund = await asyncio.to_thread(_sync)
        return IssueRefundOutput(
            refund_id=refund.id, status="refunded",
            message=f"Refund {refund.id} issued for {args.refund_amount_cents} cents.",
        )
```

**Stub fallback pattern for unimplemented methods** (StubProviderAdapter `update_customer_record`, line 144–154):
```python
async def update_customer_record(
    self, args: UpdateCustomerRecordInput, agent_id: str
) -> UpdateCustomerRecordOutput:
    raise NotImplementedError("update_customer_record not implemented for StripeAdapter (Phase 16 deferred)")
```

**Error handling:** Let exceptions propagate — `_execute_transactional_tool` at `tools.py` lines 391–413 catches all `Exception` from the adapter, releases idempotency, writes audit row, and returns `is_error`.

---

### `apps/api/app/services/transactional/adapters/shopify_adapter.py` (NEW, service, request-response)

**Analog:** Same as StripeAdapter — `provider_adapter.py` StubProviderAdapter + RESEARCH.md Pattern 4 / Code Examples.

**Imports pattern**:
```python
from __future__ import annotations
import asyncio
import shopify
import structlog
from app.services.transactional.provider_adapter import ProviderAdapter
from app.services.transactional.credential_service import CredentialHandle
from app.services.transactional.schemas import (...)
```

**Core pattern** — session-per-call (RESEARCH.md Code Examples, Shopify section):
```python
class ShopifyAdapter(ProviderAdapter):
    def __init__(self, handle: CredentialHandle, shop_url: str, currency_code: str) -> None:
        self._handle = handle
        self._shop_url = shop_url
        self._currency_code = currency_code
        self._api_version = "2025-04"

    def _make_session(self) -> None:
        session = shopify.Session(self._shop_url, self._api_version, self._handle.use())
        shopify.ShopifyResource.activate_session(session)

    async def issue_refund(self, args: IssueRefundInput, agent_id: str) -> IssueRefundOutput:
        def _sync():
            self._make_session()
            result = shopify.GraphQL().execute(mutation, variables={...})
            shopify.ShopifyResource.clear_session()
            return result
        result = await asyncio.to_thread(_sync)
        ...
```

**asyncio.to_thread pattern:** Same as actor_seam.py — wrap every sync SDK call.

---

### `apps/api/app/services/transactional/adapters/woocommerce_adapter.py` (NEW, service, request-response)

**Analog:** Same ProviderAdapter ABC subclass pattern.

**Imports pattern**:
```python
from __future__ import annotations
import asyncio
from woocommerce import API as WooCommerceAPI
import structlog
from app.services.transactional.provider_adapter import ProviderAdapter
from app.services.transactional.credential_service import CredentialHandle
from app.services.transactional.schemas import (...)
```

**Core pattern**:
```python
class WooCommerceAdapter(ProviderAdapter):
    def __init__(self, handle: CredentialHandle, site_url: str, currency_code: str) -> None:
        self._site_url = site_url
        self._currency_code = currency_code
        creds = json.loads(handle.use())  # {"consumer_key": "ck_...", "consumer_secret": "cs_..."}
        self._wcapi = WooCommerceAPI(
            url=self._site_url,          # must be HTTPS (Pitfall 5)
            consumer_key=creds["consumer_key"],
            consumer_secret=creds["consumer_secret"],
            version="wc/v3",
        )

    async def issue_refund(self, args: IssueRefundInput, agent_id: str) -> IssueRefundOutput:
        def _sync():
            return self._wcapi.post(f"orders/{args.order_id}/refunds",
                                    {"amount": str(args.refund_amount_cents / 100)}).json()
        result = await asyncio.to_thread(_sync)
        ...
```

---

### `apps/api/app/services/transactional/adapters/calendly_adapter.py` (NEW, service, request-response)

**Analog:** `apps/api/app/services/actor_seam.py` async httpx pattern (the file uses `asyncio.to_thread` for psycopg2, but CalendlyAdapter uses native async httpx — no `to_thread` needed).

**Imports pattern** (mirrors httpx usage already in the project):
```python
from __future__ import annotations
import httpx
import structlog
from app.services.transactional.provider_adapter import ProviderAdapter
from app.services.transactional.credential_service import CredentialHandle
from app.services.transactional.schemas import BookSlotInput, BookSlotOutput, ...
```

**Core pattern** (RESEARCH.md Code Examples, Calendly section):
```python
CALENDLY_API_BASE = "https://api.calendly.com"

class CalendlyAdapter(ProviderAdapter):
    def __init__(self, handle: CredentialHandle) -> None:
        self._handle = handle

    async def book_slot(self, args: BookSlotInput, agent_id: str) -> BookSlotOutput:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{CALENDLY_API_BASE}/invitees",
                headers={"Authorization": f"Bearer {self._handle.use()}",
                         "Content-Type": "application/json"},
                json={"event_type": args.service_type,
                      "start_time": f"{args.preferred_date}T{args.preferred_time}:00Z",
                      "invitee": {"name": args.customer_name}},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
        return BookSlotOutput(
            booking_id=data["resource"]["uri"], status="confirmed",
            message=f"Booking confirmed. Join URL: {data['resource'].get('join_url', 'N/A')}",
        )
```

Note: Calendly uses native async httpx — no `asyncio.to_thread`. All other methods raise `NotImplementedError`.

---

### `apps/api/alembic_tenant/versions/0007_integration_credentials.py` (NEW, migration, CRUD)

**Analog:** `apps/api/alembic_tenant/versions/0006_red_team_runs_status.py` (exact — same DDL style, op.execute, IF NOT EXISTS)

**Full structure to copy** (0006, lines 1–47):
```python
"""Tenant DB v7 migration — integration_credentials table for per-tenant provider credentials.

Revision ID: 0007
Revises: 0006
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_integration_credentials_provider_type")
    op.execute("DROP TABLE IF EXISTS integration_credentials")
```

---

### `apps/api/app/core/config.py` (MODIFY)

**Analog:** itself — add one field after `NEON_ENCRYPTION_KEY`.

**Pattern to mirror** (`config.py` lines 30–32):
```python
# Base64url-encoded 32 bytes; kept as str because Fernet accepts str
NEON_ENCRYPTION_KEY: str
```

**Addition after line 32**:
```python
# Base64url-encoded 32 bytes; key material for HKDF per-tenant credential derivation (INT-01)
# Same encoding convention as NEON_ENCRYPTION_KEY. Set PLATFORM_CREDENTIAL_KEY in .env.
PLATFORM_CREDENTIAL_KEY: str
```

`__repr__` suppression at line 148 already covers all fields — no change needed there.

---

### `apps/api/app/services/transactional/tools.py` (MODIFY — one line at line 384)

**Analog:** itself.

**Before** (line 82 import + line 384 call):
```python
# line 82
from app.services.transactional.provider_adapter import get_adapter
# line 384
adapter = get_adapter(agent_id)
```

**After**:
```python
# line 82 — change import
from app.services.transactional.provider_adapter import get_adapter_for_skill
# line 384 — change call (dispatcher is already async def)
try:
    adapter = await get_adapter_for_skill(skill, agent_id, conn_str)
except Exception as exc:  # ProviderNotConfiguredError or CredentialDecryptionError
    await release_idempotency(agent_id, skill, validated.idempotency_key)
    await write_audit_row(
        agent_id=agent_id, conversation_id=conversation_id, skill=skill,
        arguments=raw_args, result=None, actor_decision=decision,
        actor_rationale=rationale, capability_snapshot=snapshot,
        latency_ms=None, error=f"provider.not_configured:{exc}",
    )
    return {"content": [{"type": "text", "text": str(exc)}], "is_error": True}
```

`conn_str` is already available as a local variable inside `_execute_transactional_tool` — search for the ContextVar read pattern in `agent_tools.py` line 255 (`conn_str = _conn_str_var.get()`). Verify `conn_str` is read into a local before line 384.

---

### `apps/api/app/services/agent_tools.py` (MODIFY — add `_tenant_id_var`)

**Analog:** itself — add one ContextVar after line 143 and one `.set()` call in `build_tool_server`.

**Pattern to copy** (`agent_tools.py` lines 139–144):
```python
_conn_str_var: ContextVar[str] = ContextVar("conn_str", default="")
_agent_id_var: ContextVar[str] = ContextVar("agent_id", default="")
_agent_name_var: ContextVar[str] = ContextVar("agent_name", default="")
_strategy_var: ContextVar[RetrievalStrategy | None] = ContextVar("strategy", default=None)
_conversation_id_var: ContextVar[str] = ContextVar("conversation_id", default="")
_notify_fn_var: ContextVar = ContextVar("notify_fn", default=None)
```

**Addition after line 143**:
```python
_tenant_id_var: ContextVar[str] = ContextVar("tenant_id", default="")
```

**`build_tool_server` addition** (after line 606 `_agent_id_var.set(agent_id)`):
```python
_tenant_id_var.set(tenant_id)
```
`build_tool_server` signature must also gain `tenant_id: str` parameter — planner should check callers (Celery task body in `worker.py`) to ensure `tenant_id` is passed in.

---

## Shared Patterns

### asyncio.to_thread for Sync SDK Calls
**Source:** `apps/api/app/services/actor_seam.py` lines 108–124
**Apply to:** StripeAdapter, ShopifyAdapter, WooCommerceAdapter (all sync SDKs)
```python
def _sync() -> <return_type>:
    # synchronous SDK call here
    ...

result = await asyncio.to_thread(_sync)
```
Calendly adapter is exempt — uses native async httpx.

### Fernet Encrypt/Decrypt Convention
**Source:** `apps/api/app/core/security.py` lines 40–67
**Apply to:** `credential_service.py`
- Ciphertext stored as `BYTEA`, returned as `bytes` from `fernet.encrypt()`.
- `fernet.decrypt(ciphertext).decode("utf-8")` → plaintext `str`.
- Raises `cryptography.fernet.InvalidToken` on tampered/wrong-key input — let it propagate to caller.

### ContextVar Read-Before-Thread Pattern
**Source:** `apps/api/app/services/agent_tools.py` lines 133–136 (comment) + line 255
**Apply to:** `credential_service._fetch_credential_config()` — read `_tenant_id_var.get()` into a local variable in the async function body before passing to the `asyncio.to_thread` closure.

### structlog Logger Convention
**Source:** `apps/api/app/services/actor_seam.py` line 50
**Apply to:** All new service files
```python
log = structlog.get_logger(__name__)
```

### Error response shape from dispatcher
**Source:** `apps/api/app/services/transactional/tools.py` lines 413–418
**Apply to:** `tools.py` step 6 error handling for `ProviderNotConfiguredError`/`CredentialDecryptionError`
```python
return {
    "content": [{"type": "text", "text": f"Tool execution failed: {error_str}. Please try again."}],
    "is_error": True,
}
```

---

## Test File Analogs

| New Test File | Role | Closest Analog |
|---------------|------|----------------|
| `tests/unit/test_credential_service.py` | unit test | `tests/unit/test_actor_seam.py` (if exists) or any existing unit test using `pytest-asyncio` + mocking |
| `tests/unit/test_stripe_adapter.py` | unit test | same — mock `stripe.StripeClient` with `unittest.mock.patch` |
| `tests/unit/test_shopify_adapter.py` | unit test | same — mock `shopify.GraphQL` |
| `tests/unit/test_woocommerce_adapter.py` | unit test | same — mock `WooCommerceAPI` |
| `tests/unit/test_calendly_adapter.py` | unit test | same — mock `httpx.AsyncClient` via `pytest-httpx` or `unittest.mock` |
| `tests/integration/test_integration_e2e.py` | integration test | existing integration tests under `tests/integration/` |

Test structure mirrors the `asyncio_mode = "auto"` pattern in `pyproject.toml` — all test functions are plain `async def test_*` without explicit `@pytest.mark.asyncio`.

---

## No Analog Found

All files have analogs. No gaps.

---

## Metadata

**Analog search scope:** `apps/api/app/services/`, `apps/api/app/core/`, `apps/api/alembic_tenant/versions/`
**Files read:** 7 source files
**Pattern extraction date:** 2026-06-30
