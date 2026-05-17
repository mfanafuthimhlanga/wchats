---
phase: 04-reasoning-engine-widget
reviewed: 2026-05-17T00:00:00Z
depth: standard
files_reviewed: 11
files_reviewed_list:
  - apps/api/app/core/clerk_jwt.py
  - apps/api/app/api/deps.py
  - apps/api/app/api/v1/webhooks.py
  - apps/api/app/models/tenant.py
  - apps/api/app/core/config.py
  - apps/api/app/main.py
  - apps/api/alembic/versions/0005_tenant_clerk_user_id.py
  - apps/api/tests/unit/test_clerk_jwt.py
  - apps/admin/middleware.ts
  - apps/admin/app/agents/[id]/soul/page.tsx
  - apps/admin/app/layout.tsx
findings:
  critical: 4
  warning: 5
  info: 3
  total: 12
status: issues_found
---

# Phase 04: Code Review Report

**Reviewed:** 2026-05-17T00:00:00Z
**Depth:** standard
**Files Reviewed:** 11
**Status:** issues_found

## Summary

This review covers the Clerk JWT authentication integration (M4.1): server-side JWT
verification, dual-auth dependency injection, Svix webhook handling, the self-healing
`/me/provision` endpoint, the Alembic migration that adds `clerk_user_id`, the Next.js
admin middleware, the soul editor page, and the root layout.

The cryptographic foundations are sound: RS256 via JWKS, argon2id for API key hashing,
HMAC-prefix O(1) lookup, and Svix signature gating. The most serious issues are a
hardcoded weak default for `JWT_SECRET`, a silent webhook bypass when
`CLERK_WEBHOOK_SIGNING_SECRET` is empty, a null-dereference in the race-condition
recovery path of `/me/provision`, and a missing `await` on the initial `fetch` in the
soul editor page that silently drops load errors.

---

## Critical Issues

### CR-01: Hardcoded `JWT_SECRET` default allows unsigned widget tokens in production

**File:** `apps/api/app/core/config.py:67`
**Issue:** `JWT_SECRET` has a default value of `"dev-secret-change-in-production"`. If an
operator forgets to set this environment variable — easy to miss on a fresh deployment —
the production instance will sign and accept widget session JWTs with a publicly known
secret. Any attacker who reads this source file can forge valid widget tokens.
The problem is structural: `pydantic-settings` treats fields with defaults as optional, so
no startup error is raised.

**Fix:** Remove the default so pydantic-settings raises `ValidationError` on startup when
the variable is missing, identical to how `ANTHROPIC_API_KEY` and `ADMIN_KEY` are handled:

```python
# Before (dangerous):
JWT_SECRET: str = "dev-secret-change-in-production"

# After (fail-closed):
JWT_SECRET: str  # required; no default — must be set in every deployment environment
```

If a dev-only default is truly needed, guard it with an environment check:
```python
JWT_SECRET: str = Field(default="", validate_default=True)

@field_validator("JWT_SECRET")
@classmethod
def _jwt_secret_required(cls, v: str, info) -> str:
    if not v:
        raise ValueError("JWT_SECRET must be set")
    return v
```

---

### CR-02: Empty `CLERK_WEBHOOK_SIGNING_SECRET` default silently bypasses webhook signature verification

**File:** `apps/api/app/core/config.py:71`
**Issue:** `CLERK_WEBHOOK_SIGNING_SECRET` defaults to `""`. The `svix` library's `Webhook("")`
constructor raises a `RuntimeError` (not `WebhookVerificationError`) when given an empty
string. The `except WebhookVerificationError` block in `webhooks.py` does **not** catch
`RuntimeError`, so the exception propagates uncaught, resulting in an HTTP 500 rather
than a 400. The real threat: if an operator sets the secret to any non-empty string that
does NOT match Clerk's actual secret, all Svix-signed requests fail with 400 as intended.
But with `""` the error mode is a 500, which can mask deployment misconfiguration and is
also a DoS vector — any unauthenticated POST to `/webhooks/clerk` will produce a 500.
Worse: if the svix library ever changes to accept `""` and skip verification, this becomes
a complete authentication bypass.

**Fix:** Make `CLERK_WEBHOOK_SIGNING_SECRET` required (no default) so the app refuses to
start without it:
```python
# Before:
CLERK_WEBHOOK_SIGNING_SECRET: str = ""

# After:
CLERK_WEBHOOK_SIGNING_SECRET: str  # required — must be set from Clerk dashboard
```

Additionally, add a guard at startup or in the webhook handler:
```python
if not settings.CLERK_WEBHOOK_SIGNING_SECRET:
    raise HTTPException(status_code=500, detail="Webhook signing secret not configured")
```

---

### CR-03: Null dereference in `/me/provision` race-condition recovery path

**File:** `apps/api/app/api/v1/webhooks.py:196-197`
**Issue:** When the `ON CONFLICT DO NOTHING` INSERT returns no row (a concurrent request
won the race), the code re-queries the database to find the pre-existing tenant and then
unconditionally calls `str(existing.id)`. However, `existing` can be `None` if the
concurrent request soft-deleted the tenant between the INSERT attempt and this re-query:

```python
# Line 196-197 — existing may be None here:
response.status_code = status.HTTP_200_OK
return {"status": "exists", "tenant_id": str(existing.id) if existing else "unknown"}
```

The `"unknown"` fallback on line 197 handles the None case for `tenant_id`, so there is no
hard crash. However the response status is set to `200 OK` and the body says `"exists"`
when the tenant was just deleted — the client receives misleading data and will proceed
as if provisioning succeeded when it did not. This is a logic error that can cause
downstream 404s when the client subsequently tries to use an `id` of `"unknown"`.

**Fix:** Return a 404 when the re-query finds no tenant, signaling to the caller that
provisioning failed and they should retry:
```python
if row is None:
    re_result = await db.execute(
        select(Tenant).where(
            Tenant.deleted_at.is_(None),
            Tenant.clerk_user_id == clerk_user_id,
        )
    )
    existing = re_result.scalars().first()
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail="Tenant not found after concurrent INSERT. Please retry.",
        )
    response.status_code = status.HTTP_200_OK
    return {"status": "exists", "tenant_id": str(existing.id)}
```

---

### CR-04: `fetch` result not awaited — initial agent load silently fails on token errors

**File:** `apps/admin/app/agents/[id]/soul/page.tsx:121-145`
**Issue:** `getToken()` is awaited (`const token = await getToken()`), but the `fetch()`
call that follows is **not** awaited — it is a floating promise:

```typescript
// Line 122-144: fetch() result is a floating Promise
const token = await getToken()
fetch(`${apiBase}/api/v1/agents/${id}`, {   // <-- not awaited
  headers: { 'Authorization': `Bearer ${token}` },
})
  .then((r) => { ... })
  .then((data: SoulData & ...) => { ... })
  .catch(console.error)
```

Because `fetch` is not awaited inside an `async` function, any network error or token
expiry is swallowed by `.catch(console.error)` — there is no user-visible error state.
More critically: if `getToken()` returns `null` (unauthenticated or session expired),
the request is sent with `Authorization: Bearer null`, the API returns 401, and the UI
silently renders with all fields blank and no indication that auth failed. The user sees
an empty form and can unknowingly submit a blank PATCH that clears all soul fields.

**Fix:** Await the fetch call and surface errors to the UI:
```typescript
useEffect(() => {
  const loadAgent = async () => {
    try {
      const token = await getToken()
      if (!token) {
        // Handle unauthenticated state — e.g., setLoadError('Not authenticated')
        return
      }
      const r = await fetch(`${apiBase}/api/v1/agents/${id}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data: SoulData & { status?: string } = await r.json()
      setName(data.name || '')
      // ... rest of field setting
    } catch (err) {
      // Set an error state so the UI can display it
      console.error(err)
      // e.g., setLoadError('Failed to load agent. Please refresh.')
    }
  }
  loadAgent()
}, [id, apiBase])
```

---

## Warnings

### WR-01: `api_key` column name collision — `api_key_hash` ORM alias masks a naming inconsistency

**File:** `apps/api/app/models/tenant.py:28-30`
**Issue:** The DB column is named `api_key` but the ORM attribute is `api_key_hash`. The
`webhooks.py` raw SQL INSERT (lines 90-101) writes to column `api_key` directly. Any
future developer writing a raw SQL query or examining DB dumps will see `api_key` and
assume it holds the raw key, potentially logging or exposing it. The column should be
renamed to `api_key_hash` in the database to match the ORM attribute intent, with a
migration, or the disparity should be prominently documented at the DB schema level.

This is also a latent bug risk: if someone adds a new raw SQL INSERT or SELECT and writes
`api_key` intending the hash, they will get the right column — but if they intend the raw
key they will silently store a hash. Both interpretations are plausible from the name.

**Fix:** Add a migration to rename the column:
```sql
ALTER TABLE tenants RENAME COLUMN api_key TO api_key_hash;
```
Update all raw SQL in `webhooks.py` accordingly.

---

### WR-02: `CLERK_WEBHOOK_SIGNING_SECRET` empty-string default causes `RuntimeError` not caught in webhook handler

**File:** `apps/api/app/api/v1/webhooks.py:65-71`
**Issue:** This is the handler-side counterpart to CR-02. Even if the config default is
fixed, the current exception handler only catches `WebhookVerificationError`:

```python
try:
    wh = Webhook(settings.CLERK_WEBHOOK_SIGNING_SECRET)
    evt = wh.verify(payload, headers)
except WebhookVerificationError:
    log.warning("clerk_webhook.signature_invalid")
    response.status_code = status.HTTP_400_BAD_REQUEST
    return
```

`Webhook("")` raises `RuntimeError("Invalid secret")` from the svix library, which
propagates uncaught all the way up to FastAPI's error handler, resulting in HTTP 500.
This is a defense-in-depth failure independent of whether the config default is fixed.

**Fix:** Broaden the exception catch to include `Exception` (or at minimum `RuntimeError`),
or add a startup validation guard:
```python
try:
    wh = Webhook(settings.CLERK_WEBHOOK_SIGNING_SECRET)
    evt = wh.verify(payload, headers)
except (WebhookVerificationError, Exception) as exc:
    log.warning("clerk_webhook.verification_failed", error=type(exc).__name__)
    response.status_code = status.HTTP_400_BAD_REQUEST
    return
```

---

### WR-03: `UNIQUE` constraint on `clerk_user_id` permits only one soft-deleted entry per user

**File:** `apps/api/alembic/versions/0005_tenant_clerk_user_id.py:19`
**Issue:** The migration adds `clerk_user_id TEXT UNIQUE` as an unconditional unique
constraint. When a user is soft-deleted (their row has `deleted_at` set, but the column
is NOT NULLed) and subsequently re-registers via Clerk, the `ON CONFLICT DO NOTHING` in
the webhook `user.created` handler will silently skip the INSERT because the
`clerk_user_id` already exists in the soft-deleted row. The user will be unable to
provision a new tenant and will receive HTTP 404 from `get_current_tenant` forever — even
though Clerk considers them a valid active user.

The ORM model (`tenant.py:39`) documents `clerk_user_id` as `nullable=True` but the
UNIQUE constraint includes NULL handling — in PostgreSQL, multiple NULLs are allowed by
a standard UNIQUE constraint, but a single non-NULL value is blocked by any existing row
with the same value regardless of `deleted_at`.

**Fix:** Use a partial unique index that only enforces uniqueness among non-deleted tenants:
```sql
-- Replace the unconditional UNIQUE column definition with:
ALTER TABLE tenants ADD COLUMN clerk_user_id TEXT;
CREATE UNIQUE INDEX tenants_clerk_user_id_active_uniq
    ON tenants(clerk_user_id)
    WHERE deleted_at IS NULL AND clerk_user_id IS NOT NULL;
```
The ORM `unique=True` on line 39 of `tenant.py` must also be removed to avoid Alembic
attempting to enforce it at the ORM level.

---

### WR-04: `getToken()` can return `null` in the save handler — sends `Authorization: Bearer null`

**File:** `apps/admin/app/agents/[id]/soul/page.tsx:194`
**Issue:** In `handleSave`, `getToken()` can return `null` (no active session). The token
is interpolated directly into the Authorization header without a null check:

```typescript
const token = await getToken()   // may be null
const res = await fetch(`${apiBase}/api/v1/agents/${id}`, {
  method: 'PATCH',
  headers: {
    'Authorization': `Bearer ${token}`,   // "Bearer null" if token is null
    'Content-Type': 'application/json',
  },
  ...
})
```

The API will respond with 401, which sets `saveStatus = 'error'` — so there is no
data-loss risk. However, the error message shown to the user ("Save failed — check API
key and connection") is misleading when the actual cause is session expiry.

**Fix:** Check for null token before the fetch and surface an appropriate error:
```typescript
const token = await getToken()
if (!token) {
  setSaveStatus('error')
  // Optionally redirect to sign-in
  return
}
```

---

### WR-05: `_get_jwks_client` is cached with `lru_cache` but `PyJWKClient` internal key cache is mutable — cache invalidation is impossible at runtime

**File:** `apps/api/app/core/clerk_jwt.py:21-24`
**Issue:** `lru_cache(maxsize=1)` caches the `PyJWKClient` instance for the lifetime of
the Python process. While this is documented as intentional, `lru_cache` on a module-
level function means the cache cannot be cleared without restarting the process or calling
`_get_jwks_client.cache_clear()`. If Clerk rotates signing keys (JWKS key rotation), the
`PyJWKClient` will attempt to fetch new keys via its internal `lifespan` mechanism, but
only if a request arrives with a `kid` not in the cache. This is the documented "refresh
on unknown kid" path and is largely fine.

The actual risk: in testing, `lru_cache` means `_get_jwks_client` is mocked at the wrong
level in tests CLERK-01 and CLERK-02. The tests patch `app.core.clerk_jwt._get_jwks_client`
**after** the function has already been cached (if any prior test or import triggered it).
If test isolation breaks, the mock may not take effect, causing tests to make real JWKS
HTTP requests.

**Fix:** Expose a cache-clear helper for testing and document it:
```python
def _clear_jwks_cache() -> None:
    """Clear the JWKS client singleton. Test use only."""
    _get_jwks_client.cache_clear()
```
Also ensure tests call `_get_jwks_client.cache_clear()` in their setup or use
`importlib.reload`.

---

## Info

### IN-01: Imports inside function body in `/me/provision`

**File:** `apps/api/app/api/v1/webhooks.py:149-150`
**Issue:** `from sqlalchemy import select` and `from app.models.tenant import Tenant` are
imported inside the `provision_me` function body. Both are already imported at the module
level in `deps.py` and used throughout the project at module scope. This is inconsistent,
adds latency on the first call (small but non-zero), and obscures the module's
dependencies.

**Fix:** Move both imports to the top of `webhooks.py`:
```python
from sqlalchemy import select, text
from app.models.tenant import Tenant
```

---

### IN-02: `@pytest.mark.anyio` used with `asyncio_mode = "auto"` — redundant marker

**File:** `apps/api/tests/unit/test_clerk_jwt.py:63,103,148,200,228`
**Issue:** `pyproject.toml` sets `asyncio_mode = "auto"` for pytest-asyncio, which means
all `async def` test functions are automatically collected as async tests without needing
any marker. The `@pytest.mark.anyio` markers in the test file use the anyio test runner
instead of pytest-asyncio, creating a dependency on two separate async test libraries.
The `conftest.py` defines an `anyio_backend` fixture that returns `"asyncio"`, which
mitigates the risk of backend mismatch, but the mixed-marker pattern is confusing and
could cause silent failures if the anyio dependency is ever removed.

**Fix:** Use `@pytest.mark.asyncio` (or no marker with `asyncio_mode = "auto"`) consistently:
```python
# Remove @pytest.mark.anyio and rely on asyncio_mode = "auto":
async def test_get_current_tenant_jwt_path():
    ...
```

---

### IN-03: `console.error` used as `.catch` handler — suppresses errors silently in production

**File:** `apps/admin/app/agents/[id]/soul/page.tsx:144`
**Issue:** `.catch(console.error)` is the only error handler on the initial agent load
fetch chain. In production builds, `console.error` output may be suppressed or not
surfaced to users. This means network errors, 401s, and 404s on page load are invisible
to users. This overlaps with CR-04 but is called out separately because even after
adding an `await`, the error handling strategy should use component state rather than
console logging.

**Fix:** As described in CR-04: replace `.catch(console.error)` with state-driven error
handling that renders an error message to the user.

---

_Reviewed: 2026-05-17T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
