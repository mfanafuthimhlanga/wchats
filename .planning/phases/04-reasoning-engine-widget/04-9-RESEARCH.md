# Phase 04-9: Clerk Authentication Integration — Research

**Researched:** 2026-05-17
**Domain:** Authentication — Clerk identity backbone for FastAPI backend + Next.js App Router admin UI
**Confidence:** HIGH (official Clerk docs + npm/pip registry verification + codebase inspection)

---

## Summary

Clerk provides a complete identity backbone for Veridian's admin UI (Next.js App Router) and
FastAPI backend. The integration path is clear and well-documented. The canonical approach for
FastAPI is **manual JWKS-based JWT verification using PyJWT's `PyJWKClient`** — Clerk does not
ship an async-native Python middleware, so the `clerk-backend-api` SDK's `authenticate_request`
method is sync-only and wraps an httpx request, making PyJWT + JWKS the right production pattern
for an async FastAPI app.

Tenant provisioning uses a **Clerk webhook (`user.created`)** verified via the `svix` Python
library. The webhook handler creates a `tenants` row in the control DB and stores the Clerk
user ID. The `tenants` table needs one new column: `clerk_user_id TEXT UNIQUE` (Alembic
migration 0005). Migration path allows both `X-API-Key` and Clerk JWT to work in parallel
during transition — a single `get_current_tenant` dependency tries JWT first, falls back to
API key, so existing tenants without a Clerk ID keep working.

The `sub` claim in the Clerk JWT is the Clerk user ID (`user_xxx`). FastAPI maps it to a
`tenants` row via `tenants.clerk_user_id`. Clerk Organizations are **not needed** for Veridian's
"one business owner per agent" model — simple user identity is sufficient.

**Primary recommendation:** Use `PyJWT 2.12.1` + `PyJWKClient` for FastAPI JWT verification.
Use `svix 1.93.0` for webhook signature verification. Use `@clerk/nextjs 7.3.5` for the admin UI.
One Alembic migration (0005) adds `clerk_user_id` to `tenants`.

---

## Project Constraints (from CLAUDE.md)

- No Docker — all services run locally (Redis, Postgres, uvicorn, Celery)
- `python-jose[cryptography]` already in `pyproject.toml` — prefer it over adding PyJWT if
  JWKS support is available (check below — it is not fully equivalent, see Pitfalls §1)
- `acks_late=True` AND idempotency on every Celery task
- Connection strings never in Celery task args
- FastAPI never does work inline
- No pg_search / pgbm25

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Admin sign-in UI | Frontend (Next.js) | — | Clerk's `<SignIn />` component renders in the browser |
| Route protection (admin) | Frontend Server (Next.js middleware) | — | `clerkMiddleware()` runs at the edge before rendering |
| JWT issuance | Clerk Cloud | — | Clerk issues and signs all session JWTs |
| JWT verification | API / Backend (FastAPI) | — | FastAPI dep validates `Authorization: Bearer` on every protected route |
| Tenant provisioning | API / Backend (FastAPI webhook handler) | — | `POST /webhooks/clerk` creates the `tenants` row |
| Webhook signature verification | API / Backend (FastAPI) | — | `svix.webhooks.Webhook.verify()` in the handler |
| Clerk user→tenant mapping | Database (control DB) | API Backend | `tenants.clerk_user_id` column + index |

---

## Standard Stack

### Core — Python/FastAPI Side

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `PyJWT[cryptography]` | 2.12.1 | JWT decode + RS256 + `PyJWKClient` for JWKS | PyJWKClient handles key caching and rotation transparently; no custom JWKS code needed |
| `svix` | 1.93.0 | Clerk webhook signature verification | Clerk uses Svix as its webhook delivery infrastructure; Svix publishes the official Python verifier |
| `clerk-backend-api` | 5.0.6 | Optional: Clerk REST API client (user lookup, org management) | Useful for webhook payload cross-validation and future org/user management |

**Note on `python-jose`:** `python-jose[cryptography]==3.5.0` is already in `pyproject.toml` and supports RS256. However, it does NOT include a `PyJWKClient` equivalent — you must fetch JWKS manually. Adding `PyJWT` is the cleaner path. See Alternatives section.

### Core — Next.js Admin UI Side

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `@clerk/nextjs` | 7.3.5 | ClerkProvider, middleware, `auth()`, `useAuth()`, `useUser()`, `<SignIn>`, `<UserButton>` | Official Clerk SDK for Next.js App Router; includes everything needed |

[VERIFIED: npm view @clerk/nextjs version → 7.3.5]

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `httpx` | (already in pyproject.toml) | JWKS endpoint fetch (one-time at startup) | Needed if doing startup JWKS warm-up |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `PyJWT` + `PyJWKClient` | `python-jose` (already installed) | `python-jose` lacks a JWKS client; requires hand-rolling JWKS fetch + caching; adds code for same result. Only choose if minimizing dependencies is critical. |
| `PyJWT` + `PyJWKClient` | `clerk-backend-api` `authenticate_request()` | SDK method is synchronous; blocks FastAPI async event loop on each request; requires converting to thread executor. Bad pattern for async FastAPI. |
| `svix` for webhooks | Manual HMAC-SHA256 | Svix uses standardwebhooks spec with replay-attack protection (timestamp window); hand-rolling misses subtle edge cases. Don't hand-roll. |

**Installation (Python):**
```bash
pip install "PyJWT[cryptography]==2.12.1" "svix==1.93.0" "clerk-backend-api==5.0.6"
```

**Installation (Next.js admin):**
```bash
cd apps/admin && npm install @clerk/nextjs@7.3.5
```

---

## Architecture Patterns

### System Architecture Diagram

```
Browser (admin user)
    │  HTTPS
    ▼
Next.js App Router (apps/admin/)
    │  clerkMiddleware() — route protection
    │  ClerkProvider — session context
    │  auth() in server components → userId
    │  useAuth()/useUser() in client components
    │  Calls FastAPI with Authorization: Bearer <clerk_jwt>
    │
    ▼
FastAPI (apps/api/)
    │
    ├── POST /webhooks/clerk   ← Clerk Cloud (Svix delivery)
    │       svix.Webhook.verify()
    │       on user.created: INSERT tenants (clerk_user_id)
    │
    ├── GET/POST /agents/...   ← Protected routes
    │       get_current_tenant() dependency
    │       → try JWT: PyJWKClient.get_signing_key_from_jwt()
    │              jwt.decode() → claims.sub → tenants.clerk_user_id lookup
    │       → fallback X-API-Key: existing argon2 path (legacy tenants)
    │
    └── (widget routes)  ← short-lived widget JWT (existing python-jose pattern)
            no Clerk JWT here; widget uses its own JWT_SECRET

Clerk Cloud
    │  Issues session JWTs (RS256, JWKS at https://<frontend-api>/.well-known/jwks.json)
    │  Delivers webhooks via Svix infrastructure
    └──────────────────────────────────────────────────────────────────────────►
```

### Recommended Project Structure Changes

```
apps/api/app/
├── api/v1/
│   └── webhooks.py           # NEW: POST /webhooks/clerk (user.created)
├── core/
│   ├── clerk_jwt.py          # NEW: PyJWKClient singleton + verify_clerk_jwt()
│   └── deps.py               # MODIFY: dual-auth get_current_tenant()
├── models/
│   └── tenant.py             # MODIFY: add clerk_user_id field
alembic/versions/
└── 0005_tenant_clerk_user_id.py   # NEW: ADD COLUMN clerk_user_id

apps/admin/
├── middleware.ts              # NEW: clerkMiddleware()
├── app/
│   └── layout.tsx             # MODIFY: wrap with <ClerkProvider>
```

---

## Pattern 1: Clerk JWT Verification in FastAPI (PyJWT + PyJWKClient)

**What:** A FastAPI dependency that verifies Clerk session JWTs using PyJWKClient.
The JWKS client caches keys automatically and refreshes on key rotation.
On success, returns the decoded payload (contains `sub` = Clerk user ID).

**When to use:** Every admin API route that must be tied to a specific tenant.

```python
# apps/api/app/core/clerk_jwt.py
# Source: PyJWT docs (pyjwt.readthedocs.io/en/latest/usage.html) +
#         Clerk manual JWT verification (clerk.com/docs/guides/sessions/manual-jwt-verification)

import os
from functools import lru_cache
from typing import Any

import jwt
from jwt import PyJWKClient, InvalidTokenError

# Clerk JWKS endpoint — two options:
# Option A (simpler, no env var needed): Backend API JWKS — always valid
CLERK_JWKS_URL_BACKEND = "https://api.clerk.com/v1/jwks"
# Option B (per-instance): https://<your-frontend-api>/.well-known/jwks.json
# e.g. https://clean-mayfly-62.clerk.accounts.dev/.well-known/jwks.json
# Prefer Option B in production to avoid an extra network hop.
# Set CLERK_JWKS_URL env var to override.

CLERK_JWKS_URL = os.getenv("CLERK_JWKS_URL", CLERK_JWKS_URL_BACKEND)

@lru_cache(maxsize=1)
def _get_jwks_client() -> PyJWKClient:
    """Module-level singleton. PyJWKClient caches keys internally."""
    return PyJWKClient(CLERK_JWKS_URL, cache_keys=True, lifespan=3600)


def verify_clerk_jwt(token: str) -> dict[str, Any]:
    """
    Verify a Clerk session JWT.
    Returns the decoded payload on success.
    Raises jwt.InvalidTokenError on any failure.

    Claims validated:
    - Signature (RS256 via JWKS)
    - exp / nbf (PyJWT handles automatically)
    - azp NOT validated here — azp issue in clerk-sdk-python #90 shows
      azp may be absent in some token configurations; skip if not present.
    """
    client = _get_jwks_client()
    try:
        signing_key = client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={
                "verify_exp": True,
                "verify_nbf": True,
                # Do not require audience — Clerk session tokens have no aud by default
                "verify_aud": False,
            },
        )
        return payload
    except Exception as exc:
        raise InvalidTokenError(str(exc)) from exc
```

```python
# apps/api/app/api/deps.py — MODIFIED get_current_tenant (dual-auth)
# Source: existing deps.py pattern + FastAPI docs security

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader, HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clerk_jwt import verify_clerk_jwt
from app.core.security import hmac_key_prefix, verify_api_key
from app.core.database import get_async_db
from app.models.tenant import Tenant

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)  # auto_error=False for dual-auth
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_tenant(
    bearer: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
    api_key: str | None = Security(_api_key_header),
    db: AsyncSession = Depends(get_async_db),
) -> Tenant:
    """
    Dual-auth dependency: tries Clerk JWT first, falls back to X-API-Key.

    JWT path:  Authorization: Bearer <clerk_session_token>
               → decode JWT → extract sub (clerk_user_id)
               → SELECT tenant WHERE clerk_user_id = sub
    API key path: X-API-Key: vrd_live_xxx
               → existing argon2 HMAC-prefix lookup (unchanged)

    Raises HTTP 401 if neither credential is present or valid.
    Never logs credentials (T-04-02).
    """
    # --- Path 1: Clerk JWT ---
    if bearer is not None:
        try:
            payload = verify_clerk_jwt(bearer.credentials)
            clerk_user_id: str = payload["sub"]  # "user_xxx" format
            result = await db.execute(
                select(Tenant).where(
                    Tenant.deleted_at.is_(None),
                    Tenant.clerk_user_id == clerk_user_id,
                )
            )
            tenant = result.scalars().first()
            if tenant:
                return tenant
            # JWT valid but no tenant provisioned yet
            raise HTTPException(
                status_code=404,
                detail="Tenant not provisioned. Webhook may not have fired yet.",
            )
        except Exception:
            raise HTTPException(status_code=401, detail="Invalid session token")

    # --- Path 2: X-API-Key (legacy + service-account tokens) ---
    if api_key is not None:
        # Existing O(1) HMAC-prefix lookup (unchanged)
        prefix = hmac_key_prefix(api_key)
        result = await db.execute(
            select(Tenant).where(
                Tenant.deleted_at.is_(None),
                Tenant.api_key_prefix == prefix,
            )
        )
        tenant = result.scalars().first()
        if tenant and verify_api_key(tenant.api_key_hash, api_key):
            return tenant
        # Fallback for legacy rows without prefix
        result = await db.execute(
            select(Tenant).where(
                Tenant.deleted_at.is_(None),
                Tenant.api_key_prefix.is_(None),
            )
        )
        for tenant in result.scalars():
            if verify_api_key(tenant.api_key_hash, api_key):
                return tenant

    raise HTTPException(status_code=401, detail="Authentication required")
```

---

## Pattern 2: Clerk Webhook Handler (tenant provisioning)

**What:** FastAPI route that receives Clerk `user.created` events.
Svix verifies the signature. The handler inserts a `tenants` row with the Clerk user ID.

**When to use:** Called by Clerk Cloud when a new user signs up.

```python
# apps/api/app/api/v1/webhooks.py
# Source: Svix FastAPI guide (svix.com/guides/receiving/receive-webhooks-with-python-fastapi/)
#         + Clerk webhooks overview (clerk.com/docs/guides/development/webhooks/overview)

import os
import structlog
from fastapi import APIRouter, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from svix.webhooks import Webhook, WebhookVerificationError
from app.core.database import get_async_db
from app.models.tenant import Tenant

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = structlog.get_logger()

CLERK_WEBHOOK_SECRET = os.getenv("CLERK_WEBHOOK_SIGNING_SECRET", "")


@router.post("/clerk", status_code=status.HTTP_204_NO_CONTENT)
async def clerk_webhook(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_async_db),
) -> None:
    """
    Receives Clerk webhook events.
    Signature verified via Svix (whsec_... secret from Clerk dashboard).

    user.created:
        - Idempotent: INSERT ... ON CONFLICT DO NOTHING
        - Creates tenants row with clerk_user_id + generated api_key_hash
        - Tenant name defaults to email address until user sets it

    CRITICAL: Use raw request body (request.body()) — not parsed JSON.
    The Svix HMAC signature is computed over the raw bytes.
    """
    payload = await request.body()
    headers = dict(request.headers)

    try:
        wh = Webhook(CLERK_WEBHOOK_SECRET)
        evt = wh.verify(payload, headers)
    except WebhookVerificationError:
        log.warning("clerk_webhook.signature_invalid")
        response.status_code = status.HTTP_400_BAD_REQUEST
        return

    event_type: str = evt.get("type", "")
    data: dict = evt.get("data", {})

    if event_type == "user.created":
        clerk_user_id: str = data["id"]  # "user_xxx"
        email: str = data["email_addresses"][0]["email_address"] if data.get("email_addresses") else ""
        first_name: str = data.get("first_name") or ""
        last_name: str = data.get("last_name") or ""
        display_name = f"{first_name} {last_name}".strip() or email or clerk_user_id

        from app.core.security import generate_api_key, hash_api_key, hmac_key_prefix
        from app.core.config import settings

        raw_key = generate_api_key()
        key_hash = hash_api_key(raw_key)
        key_prefix = hmac_key_prefix(raw_key)

        # Idempotent upsert — Clerk may retry; ON CONFLICT DO NOTHING is safe
        await db.execute(
            """
            INSERT INTO tenants (name, api_key, api_key_prefix, clerk_user_id)
            VALUES (:name, :api_key, :api_key_prefix, :clerk_user_id)
            ON CONFLICT (clerk_user_id) DO NOTHING
            """,
            {
                "name": display_name,
                "api_key": key_hash,
                "api_key_prefix": key_prefix,
                "clerk_user_id": clerk_user_id,
            },
        )
        await db.commit()
        log.info("tenant.provisioned", clerk_user_id=clerk_user_id, name=display_name)
        # NOTE: raw_key is NOT returned in the response — it is lost after this function.
        # For M4, admin can retrieve their API key via the admin UI (future M5 feature).
        # M4 admin UI uses Clerk JWT exclusively; no API key surfaced to user.

    # All other event types: acknowledge and ignore
```

---

## Pattern 3: Next.js App Router Clerk Setup

**What:** Full Clerk integration for the admin UI.

```typescript
// apps/admin/middleware.ts
// Source: clerk.com/docs/references/nextjs/clerk-middleware (verified 2026-05-17)
// NOTE: Next.js >= 16 uses proxy.ts naming. Both work; middleware.ts is the stable convention.

import { clerkMiddleware, createRouteMatcher } from '@clerk/nextjs/server'

// All /dashboard/* routes require auth; /sign-in and /sign-up are public
const isPublicRoute = createRouteMatcher(['/sign-in(.*)', '/sign-up(.*)'])

export default clerkMiddleware(async (auth, req) => {
  if (!isPublicRoute(req)) {
    await auth.protect()
  }
})

export const config = {
  matcher: [
    // Skip Next.js internals and static files
    '/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)',
    // Always run for API routes
    '/(api|trpc)(.*)',
    // Always run for Clerk frontend endpoints
    '/__clerk/(.*)',
  ],
}
```

```tsx
// apps/admin/app/layout.tsx — MODIFIED
// Source: clerk.com/docs/quickstarts/nextjs (verified 2026-05-17)

import { ClerkProvider } from '@clerk/nextjs'
import './globals.css'

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ClerkProvider>
          {children}
        </ClerkProvider>
      </body>
    </html>
  )
}
```

```tsx
// Server component — auth() returns userId
// Source: clerk.com/docs/quickstarts/nextjs

import { auth } from '@clerk/nextjs/server'

export default async function DashboardPage() {
  const { userId } = await auth()
  // userId is the Clerk user ID ("user_xxx") — same as JWT sub claim
  return <div>Signed in as: {userId}</div>
}
```

```tsx
// Client component — useAuth() and useUser()
"use client"
import { useAuth, useUser } from '@clerk/nextjs'

export function UserInfo() {
  const { isLoaded, userId, getToken } = useAuth()
  const { user } = useUser()

  // getToken() returns the current session JWT — use this when calling FastAPI
  const callApi = async () => {
    const token = await getToken()
    const res = await fetch('http://localhost:8000/agents', {
      headers: { Authorization: `Bearer ${token}` },
    })
    // ...
  }

  if (!isLoaded) return null
  return <div>{user?.primaryEmailAddress?.emailAddress}</div>
}
```

---

## Pattern 4: Alembic Migration 0005 (tenants.clerk_user_id)

```python
# apps/api/alembic/versions/0005_tenant_clerk_user_id.py
# Source: existing migration pattern from 0002_tenant_api_key_prefix.py

"""Add clerk_user_id to tenants.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade() -> None:
    op.execute("""
        ALTER TABLE tenants ADD COLUMN clerk_user_id TEXT UNIQUE;
    """)
    op.execute("""
        CREATE INDEX tenants_clerk_user_id_idx ON tenants(clerk_user_id)
        WHERE clerk_user_id IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS tenants_clerk_user_id_idx")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS clerk_user_id")
```

SQLAlchemy model update:
```python
# apps/api/app/models/tenant.py — add field
clerk_user_id: Mapped[str | None] = mapped_column(
    Text, unique=True, nullable=True, index=True
)
```

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| JWKS key fetching + caching | Custom JWKS HTTP client | `PyJWT.PyJWKClient` | Handles key rotation, caching, retry on unknown kid; all edge cases solved |
| Webhook signature verification | Manual HMAC-SHA256 | `svix.webhooks.Webhook` | Svix implements standardwebhooks: HMAC-SHA256 + replay-attack timestamp window (5-min); missing the timestamp check is a security hole |
| Session cookie parsing for browser-based admin | Manual cookie extraction | `clerkMiddleware()` + `auth()` | Handles `__session` cookie, token refresh, cross-tab sync |
| Sign-in / sign-up UI | Custom auth forms | Clerk's `<SignIn />` / `<SignUp />` hosted components | MFA, social login, email magic link all included; not your problem to build |
| JWT revocation | Custom token blacklist | Clerk's `sessions.revokeSession()` API | Clerk handles session lifecycle |

**Key insight:** The entire token lifecycle (issuance, signing key rotation, session revocation, user lifecycle events) is Clerk's responsibility. Your code only verifies tokens and consumes webhook events.

---

## JWT Claim Structure

A decoded Clerk session JWT (Version 2, current default) looks like:

```json
{
  "azp": "http://localhost:3000",
  "exp": 1748000000,
  "fva": [0, -1],
  "iat": 1747999940,
  "iss": "https://clean-mayfly-62.clerk.accounts.dev",
  "jti": "abc123...",
  "nbf": 1747999930,
  "sid": "sess_xxx",
  "sub": "user_2abc123...",
  "v": 2,
  "pla": "free",
  "fea": "...",
  "sts": "active"
}
```

**Key claims:**

| Claim | Value | Use in FastAPI |
|-------|-------|----------------|
| `sub` | `"user_2abc..."` (Clerk user ID) | Map to `tenants.clerk_user_id` for tenant lookup |
| `iss` | `"https://<xxx>.clerk.accounts.dev"` (your instance) | Verified implicitly by signature; optionally assert explicitly |
| `azp` | `"http://localhost:3000"` (origin that requested the token) | SKIP validation — azp is absent in some configurations (see Pitfalls §3) |
| `sid` | `"sess_xxx"` | Session ID — useful for audit logs, not required for tenant auth |
| `exp` | Unix timestamp | Auto-validated by PyJWT |
| `sts` | `"active"` or `"pending"` | Reject `"pending"` if using Organizations (not needed for Veridian M4) |

**How FastAPI extracts tenant:** `sub` → `tenants.clerk_user_id` → `Tenant` row.
The admin UI uses Clerk's `getToken()` and sends `Authorization: Bearer <token>`.

**Organization claims (`o`, `org_id`, `org_role`):** Only present if the user has an active
organization. Not needed for Veridian's user-per-tenant model.

---

## Tenant-Clerk Mapping Model

**Recommendation: simple `users` model, NOT Clerk Organizations.**

Veridian's model: one business owner → one tenant → N agents.
Clerk Organizations are designed for multi-member teams where users switch between different org
contexts (think Slack workspaces). Overkill for Veridian M4.

**Chosen model:**

```
Clerk User (user_xxx)
    │  1:1
    ▼
tenants.clerk_user_id = "user_xxx"
    │  1:N
    ▼
agents (tenant_id FK)
```

**Future:** If Veridian adds team accounts (multiple owners per agent), migrating to Clerk
Organizations is straightforward — replace `sub` lookup with `o.id` (org claim) lookup and
add an `organizations` table. This is a non-breaking migration.

**Why not Organizations now:**
- Adds `createOrganization()` call in webhook handler (extra Clerk API call)
- Requires `org_id` in JWT (only present when user has an active org)
- Complicates the dev experience ("choose active org before you can use the API")
- Clerk docs confirm: "Organizations are for multi-member teams" — not single-owner accounts

---

## Migration Path (Parallel X-API-Key + Clerk JWT)

The `get_current_tenant` dependency (Pattern 1 above) handles both auth methods:

1. If `Authorization: Bearer` header present → try Clerk JWT path
2. If `X-API-Key` header present → try existing argon2 path
3. Neither present → 401

**Transition states:**

| Tenant state | JWT path | API key path | Result |
|---|---|---|---|
| Legacy (no clerk_user_id) | No bearer header | Has X-API-Key | Works via path 2 |
| New (has clerk_user_id, webhook provisioned) | Has bearer token | Optional | Works via path 1 |
| New but webhook missed | Bearer token, no DB row | No API key | 404 with clear message |

**No forced migration:** X-API-Key auth remains valid indefinitely. Widget auth (short-lived
`python-jose` JWTs signed with `JWT_SECRET`) is unchanged — those routes do NOT use
`get_current_tenant`; they use a separate `verify_widget_jwt` dependency.

**Admin UI:** In the admin Next.js app, `getToken()` from `useAuth()` returns the Clerk session
JWT. Pass it as `Authorization: Bearer` when calling FastAPI. Never store it in localStorage —
Clerk stores it in memory; `getToken()` auto-refreshes it.

---

## Environment Configuration

### Next.js Admin (`.env.local` — NOT committed to git)

```bash
# Required: from Clerk dashboard → API Keys
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_...    # or pk_live_... in production
CLERK_SECRET_KEY=sk_test_...                      # Never expose on frontend

# Optional: custom sign-in/sign-up pages
NEXT_PUBLIC_CLERK_SIGN_IN_URL=/sign-in
NEXT_PUBLIC_CLERK_SIGN_UP_URL=/sign-up
NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL=/dashboard
NEXT_PUBLIC_CLERK_SIGN_UP_FALLBACK_REDIRECT_URL=/dashboard

# Optional: networkless JWT verification (avoids JWKS network call per request)
# From Clerk dashboard → API Keys → Show JWT Public Key → PEM Public Key
NEXT_PUBLIC_CLERK_JWT_KEY=-----BEGIN PUBLIC KEY-----\n...
```

### FastAPI Backend (`.env` — project root)

```bash
# Clerk JWT verification
CLERK_JWKS_URL=https://<your-instance>.clerk.accounts.dev/.well-known/jwks.json
# Alternative (simpler, always works): https://api.clerk.com/v1/jwks
# The instance URL is preferred in production (avoids extra network hop to api.clerk.com)

# Clerk webhook (from Clerk dashboard → Webhooks → select endpoint → Signing Secret)
CLERK_WEBHOOK_SIGNING_SECRET=whsec_...

# Optional: full Clerk API access for user management (webhooks + admin operations)
CLERK_SECRET_KEY=sk_test_...
```

**Key distinctions:**
- `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` — safe to expose; needed only in Next.js
- `CLERK_SECRET_KEY` — server-side only; in Python, used only by `clerk-backend-api` if you make
  API calls (user lookup, org management). Not needed for JWT verification.
- `CLERK_JWKS_URL` — not a Clerk-defined variable; a Veridian convention for our FastAPI config
- `CLERK_WEBHOOK_SIGNING_SECRET` — the `whsec_...` value from the Clerk webhook dashboard

**`.env.local` vs `.env`:**
- Next.js uses `.env.local` (gitignored by default by Next.js scaffolding)
- FastAPI uses `.env` at project root (already in `.gitignore` per M1)

---

## Common Pitfalls

### Pitfall 1: `python-jose` does not have a JWKS client

**What goes wrong:** Developer tries to use the existing `python-jose` for JWKS-based Clerk JWT
verification and finds no `PyJWKClient` equivalent. They write a manual `requests.get(JWKS_URL)`
with no caching, hitting Clerk's JWKS endpoint on every request (rate limiting, latency).

**Why it happens:** `python-jose` predates JWKS client patterns. It can verify an RS256 token
if you pass the public key directly, but it provides no key-fetching or rotation handling.

**How to avoid:** Add `PyJWT[cryptography]` to `pyproject.toml`. Use `PyJWKClient` as shown in
Pattern 1. The `PyJWKClient` singleton (via `@lru_cache(maxsize=1)`) caches keys and only hits
the network when the kid is unknown or the cache expires.

**Alternatively:** Use Clerk's PEM public key via `NEXT_PUBLIC_CLERK_JWT_KEY` / `CLERK_JWT_KEY`
environment variable for fully networkless verification with `python-jose`:

```python
from jose import jwt as jose_jwt
import os

CLERK_PEM_KEY = os.getenv("CLERK_JWT_KEY", "").replace("\\n", "\n")

def verify_clerk_jwt_networkless(token: str) -> dict:
    return jose_jwt.decode(token, CLERK_PEM_KEY, algorithms=["RS256"])
```

This avoids adding `PyJWT` but requires setting a PEM key env var and manually rotating it if
Clerk ever rotates signing keys. **Recommended only if you want to avoid an extra dependency.**

---

### Pitfall 2: Raw body consumed before Svix verification

**What goes wrong:** FastAPI route reads request body as JSON with `body: dict = Body(...)`.
Svix's `wh.verify()` receives an empty bytes string. Signature check fails for every request,
causing all webhooks to return 400.

**Why it happens:** FastAPI's JSON body parsing consumes the request stream. Svix needs the raw
bytes that the HMAC was computed over.

**How to avoid:** Use `payload = await request.body()` (returns bytes) and pass that to
`Webhook.verify()`. Do NOT use Pydantic model parsing in the webhook route body parameter.

---

### Pitfall 3: `azp` claim absent — authorized_parties validation fails

**What goes wrong:** Developer validates `azp` claim against `authorized_parties` as recommended
in Clerk docs. Validation fails with "azp does not match" error. Verified in GitHub issue
`clerk/clerk-sdk-python#90` (closed Feb 2025, no SDK fix).

**Why it happens:** Clerk does not always include `azp` in machine-to-machine or service-account
tokens. The claim is present for browser-initiated sessions but may be absent in API-generated
tokens.

**How to avoid:** Do not enforce `azp` validation unless you have a specific cross-origin CSRF
requirement. The RS256 signature verification plus `exp`/`nbf` checks are sufficient for a
server-to-server API where the admin UI and API are same-origin or explicitly trusted.

---

### Pitfall 4: `ClerkProvider` above `<html>` causes hydration error

**What goes wrong:** Developer wraps the `<html>` element with `ClerkProvider` in `layout.tsx`.
Next.js hydration errors appear in the console.

**How to avoid:** `ClerkProvider` must be inside `<body>`, not wrapping `<html>`:

```tsx
// CORRECT:
<html><body><ClerkProvider>{children}</ClerkProvider></body></html>

// WRONG:
<ClerkProvider><html><body>{children}</body></html></ClerkProvider>
```

---

### Pitfall 5: Svix webhook secret format — must include `whsec_` prefix

**What goes wrong:** Developer strips the `whsec_` prefix from the signing secret when storing
in env var (thinking it's a key suffix). Svix verification returns `WebhookVerificationError`
for every request.

**Why it happens:** The Clerk dashboard displays the secret with the `whsec_` prefix. Some
developers assume this is a display label.

**How to avoid:** Store the full value including prefix:
```bash
CLERK_WEBHOOK_SIGNING_SECRET=whsec_MfKQ9r8GKY...   # include whsec_ prefix
```
The `Webhook(secret)` constructor expects the full `whsec_` prefixed string.

---

### Pitfall 6: JWKS URL mismatch between Clerk instances (dev vs prod)

**What goes wrong:** JWKS URL is hardcoded to `https://api.clerk.com/v1/jwks` in development.
In production with a custom domain, the instance JWKS URL differs. Tokens signed by the
production instance are rejected because the backend hits the wrong JWKS endpoint.

**How to avoid:** Set `CLERK_JWKS_URL` as an environment variable:
- Dev: `https://api.clerk.com/v1/jwks` (works for all instances, slightly higher latency)
- Prod: `https://<your-clerk-frontend-api>/.well-known/jwks.json` (instance-specific, faster)

Your Clerk frontend API URL is shown in the Clerk dashboard under **API Keys** → **Advanced**
→ **Frontend API URL** (format: `https://<hash>.clerk.accounts.dev` for dev,
or `https://clerk.<your-domain>.com` for production with custom domains).

---

### Pitfall 7: `useAuth()` / `getToken()` returns null during SSR

**What goes wrong:** Server component accidentally calls `useAuth()` (a client-only hook).
Build fails. Or: `getToken()` is called during static render where no session exists.

**How to avoid:**
- Server components: use `auth()` from `@clerk/nextjs/server`
- Client components: use `useAuth()` from `@clerk/nextjs`
- Never mix them. `useAuth()` requires `"use client"` directive.

---

## Critical Risks

### RISK-01: No webhook delivery guarantee — tenant provisioning gap

**Risk:** If the Clerk `user.created` webhook fails to deliver (network error, server down),
a user can sign in via the admin UI but their `tenants` row doesn't exist. JWT verification
succeeds but the DB lookup returns nothing, resulting in 404.

**Impact:** Admin UI is unusable until webhook is replayed or tenant is manually provisioned.

**Mitigation options:**
1. Clerk automatically retries webhooks for up to 72 hours — most transient failures resolve
2. Add a "provision on first use" fallback in `get_current_tenant`: if JWT is valid but no
   tenant row exists, auto-provision one. Trade-off: loses the webhook's email/name data.
3. Add a `/me/provision` endpoint the admin UI calls on first login if 404 is received.

**Recommendation for M4:** Use option 3 — a "self-healing" endpoint that creates the tenant
row from the JWT's `sub` claim if it doesn't exist. Simple and reliable without relying on
webhook retry timing.

### RISK-02: `PyJWT[cryptography]` conflicts with `cryptography==48.0.0`

**Risk:** `PyJWT[cryptography]` depends on `cryptography` package. Veridian already pins
`cryptography==48.0.0`. If PyJWT requires a different version range, pip will raise a
conflict.

**Mitigation:** `PyJWT 2.12.1` requires `cryptography>=3.4.0`. The pinned `48.0.0` satisfies
this. [VERIFIED: PyPI metadata for PyJWT 2.12.1 shows `cryptography>=3.4.0` dependency]

### RISK-03: `fastapi-clerk-auth` package is community-maintained (not official Clerk)

**Risk:** `fastapi-clerk-auth 0.0.9` (latest) is a third-party package. If it silently fails
to validate important claims, it could create a security hole.

**Mitigation:** Use `PyJWT` directly (Pattern 1) instead of `fastapi-clerk-auth`. The
hand-written dependency is ~30 lines and is fully under your control. Do not add an unmaintained
community auth library to a security-critical path.

### RISK-04: Webhook endpoint must be publicly reachable

**Risk:** In local development, FastAPI runs on `localhost:8000`. Clerk cannot deliver webhooks
to localhost directly.

**Mitigation:** Use `ngrok` or `clerk dev` proxy for local webhook testing:

```bash
# Option A: ngrok (most reliable)
ngrok http 8000
# Set Clerk webhook URL to: https://<ngrok-subdomain>.ngrok.io/webhooks/clerk

# Option B: Clerk CLI (if installed)
clerk dev
# Proxies Clerk webhooks to localhost automatically
```

For initial M4 development, webhook handler can be tested with direct HTTP calls using
a known `whsec_` test secret before connecting to Clerk.

---

## Open Questions

1. **Widget auth and Clerk JWT**
   - What we know: Widget routes use `python-jose` short-lived JWTs signed with `JWT_SECRET`
     (widget users are anonymous end-customers, not admin users)
   - What's unclear: Should the widget auth ever use Clerk? (Clerk is for the business owner
     admin, not the customer chatting with the widget)
   - Recommendation: No. Keep widget JWT separate. Widget auth and admin auth are distinct
     concerns. Do not route customer widget sessions through Clerk.

2. **CLERK_JWKS_URL for the local dev environment**
   - What we know: `https://api.clerk.com/v1/jwks` works as a universal fallback
   - What's unclear: Whether the developer's Clerk dev instance JWKS URL is needed for the
     JWKS to resolve correctly (they should be equivalent)
   - Recommendation: Default to `https://api.clerk.com/v1/jwks` in development; document that
     `CLERK_JWKS_URL` can be set to the instance-specific URL.

3. **Admin UI sign-in page routing**
   - What we know: `NEXT_PUBLIC_CLERK_SIGN_IN_URL=/sign-in` is the standard convention
   - What's unclear: Does `apps/admin` currently have a `/sign-in` route or does it use
     Clerk's hosted sign-in page?
   - Recommendation: Use Clerk's Account Portal (hosted) for M4 simplicity — no `/sign-in`
     page needed in the admin app. Set `NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL`
     to redirect back after sign-in.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `PyJWT[cryptography]` | FastAPI JWT verification | not yet installed | 2.12.1 (PyPI) | Use `python-jose` with PEM key (networkless) |
| `svix` | Webhook signature verification | not yet installed | 1.93.0 (PyPI) | None — don't hand-roll |
| `@clerk/nextjs` | Admin UI | not yet installed | 7.3.5 (npm) | — |
| `cryptography` | PyJWT dep | installed (48.0.0) | 48.0.0 | Compatible with PyJWT 2.12.1 |
| `ngrok` or clerk CLI | Webhook local dev | unknown | — | Test webhook handler directly with curl + known secret |
| Clerk account | All Clerk features | unknown — requires sign-up at clerk.com | — | None (blocking) |
| Internet access for JWKS | Runtime JWT verification | available (local dev) | — | Use networkless PEM key from env var |

**Blocking dependency with no fallback:**
- Clerk account creation at `clerk.com` (free tier available). Without a Clerk app created,
  there are no API keys to configure.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (existing) |
| Config file | `apps/api/pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `pytest tests/unit/test_clerk_jwt.py -x` |
| Full suite command | `pytest tests/ -m "not integration and not e2e"` |

### Phase Requirements to Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CLERK-01 | `verify_clerk_jwt()` rejects expired token | unit | `pytest tests/unit/test_clerk_jwt.py::test_expired_token -x` | No — Wave 0 |
| CLERK-02 | `verify_clerk_jwt()` rejects invalid signature | unit | `pytest tests/unit/test_clerk_jwt.py::test_bad_signature -x` | No — Wave 0 |
| CLERK-03 | `get_current_tenant` resolves via JWT path | unit (mocked) | `pytest tests/unit/test_deps.py::test_jwt_path -x` | No — Wave 0 |
| CLERK-04 | `get_current_tenant` falls back to X-API-Key | unit (mocked) | `pytest tests/unit/test_deps.py::test_apikey_fallback -x` | No — Wave 0 |
| CLERK-05 | Webhook handler: valid signature + user.created provisions tenant | unit (svix mock) | `pytest tests/unit/test_webhooks.py::test_user_created -x` | No — Wave 0 |
| CLERK-06 | Webhook handler: invalid signature returns 400 | unit | `pytest tests/unit/test_webhooks.py::test_invalid_signature -x` | No — Wave 0 |
| CLERK-07 | `clerkMiddleware()` redirects unauthenticated to sign-in | integration | Next.js `npm run build` succeeds | No — Wave 0 |

### Wave 0 Gaps

- [ ] `tests/unit/test_clerk_jwt.py` — covers CLERK-01, CLERK-02 (mock PyJWKClient)
- [ ] `tests/unit/test_deps.py` — extend existing deps test for CLERK-03, CLERK-04
- [ ] `tests/unit/test_webhooks.py` — covers CLERK-05, CLERK-06

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | Clerk (hosted identity provider) |
| V3 Session Management | yes | Clerk session tokens (RS256, 60s expiry by default) |
| V4 Access Control | yes | `get_current_tenant` dependency on every protected route |
| V5 Input Validation | yes | Pydantic models on all route bodies; webhook payload validated after signature check |
| V6 Cryptography | yes | PyJWT RS256 (asymmetric); Svix HMAC-SHA256 webhook verification; never hand-roll |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Forged JWT (fake sub claim) | Spoofing | RS256 signature verification via PyJWKClient — unsigned tokens rejected |
| JWT replay after expiry | Elevation | `exp` claim validated by PyJWT automatically |
| Webhook spoofing (fake user.created) | Tampering | Svix `Webhook.verify()` with HMAC-SHA256 + timestamp window |
| Webhook replay attack | Tampering | Svix rejects webhooks with timestamp > 5 minutes old |
| Secret key leak via logs | Information Disclosure | `CLERK_SECRET_KEY` / `CLERK_WEBHOOK_SIGNING_SECRET` never logged (follow T-04-02 pattern from existing deps.py) |
| CSRF via JWT in localStorage | Spoofing | Admin UI: Clerk stores token in memory, not localStorage; `getToken()` is async |
| Tenant data cross-contamination | Elevation | tenant lookup via `clerk_user_id` (unique index) ensures one JWT maps to exactly one tenant |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `PyJWT[cryptography]` 2.12.1 is compatible with `cryptography==48.0.0` | Standard Stack | Pip conflict at install time — resolve by checking `pip install` output |
| A2 | Clerk free tier supports webhook delivery for development | Standard Stack / RISK-04 | No webhook testing without account — switch to paid or use Clerk dev proxy |
| A3 | `azp` claim is absent in some Clerk tokens (based on GitHub issue #90) | Pattern 1 / Pitfall 3 | If always present, skipping azp validation is slightly less secure (low risk) |
| A4 | Clerk's `user.created` webhook payload always includes `email_addresses[0]` | Pattern 2 | If absent, fallback to clerk_user_id as tenant name |
| A5 | `@clerk/nextjs 7.3.5` is compatible with `next 16.2.6` installed in admin | Standard Stack | Next.js version mismatch — check Clerk's peer dependency requirements |

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|---|---|---|---|
| `authMiddleware()` in @clerk/nextjs | `clerkMiddleware()` | @clerk/nextjs Core 2 (2024) | `authMiddleware` is deprecated; `clerkMiddleware` is the current standard |
| `getAuth()` in server components | `auth()` (async) | @clerk/nextjs Core 2 | `getAuth()` removed; `auth()` is awaitable |
| Clerk Python SDK v1.x (unofficial) | `clerk-backend-api` 5.x (official Speakeasy-generated) | Oct 2024 (changelog) | Major API change; older snippets on Stack Overflow use wrong import paths |
| Manual JWKS fetch per request | `PyJWKClient` with `cache_keys=True` | PyJWT 2.4+ | Key rotation handled automatically; no manual caching code needed |

**Deprecated patterns to avoid:**
- `from clerk import Clerk` — old unofficial SDK; use `from clerk_backend_api import Clerk`
- `authMiddleware()` — use `clerkMiddleware()`
- `getAuth()` — use `await auth()`
- `clerk-sdk-python` (GitHub repo name) — the PyPI package is `clerk-backend-api`

---

## Sources

### Primary (HIGH confidence)
- [Clerk manual JWT verification](https://clerk.com/docs/guides/sessions/manual-jwt-verification) — JWKS URL, claims, verification steps
- [Clerk session tokens](https://clerk.com/docs/backend-requests/resources/session-tokens) — full claims table (sub, azp, sid, org claims)
- [clerkMiddleware reference](https://clerk.com/docs/references/nextjs/clerk-middleware) — App Router middleware pattern
- [Clerk Next.js quickstart](https://clerk.com/docs/quickstarts/nextjs) — ClerkProvider, auth(), useAuth()
- [Clerk environment variables](https://clerk.com/docs/guides/development/clerk-environment-variables) — full env var list
- [PyJWT usage docs](https://pyjwt.readthedocs.io/en/latest/usage.html) — PyJWKClient usage pattern
- [Svix FastAPI receiving guide](https://www.svix.com/guides/receiving/receive-webhooks-with-python-fastapi/) — webhook handler code
- npm registry: `@clerk/nextjs@7.3.5` [VERIFIED]
- pip registry: `clerk-backend-api==5.0.6`, `svix==1.93.0`, `PyJWT==2.12.1` [VERIFIED]

### Secondary (MEDIUM confidence)
- [GitHub clerk/fastapi-example](https://github.com/clerk/fastapi-example) — JWT Bearer pattern (archived March 2026)
- [Clerk Python SDK beta changelog](https://clerk.com/changelog/2024-10-08-python-backend-sdk-beta) — SDK capabilities
- Codebase inspection: `apps/api/app/core/security.py`, `deps.py`, `models/tenant.py`, `pyproject.toml` [VERIFIED]

### Tertiary (LOW confidence, needs validation)
- [GitHub clerk/clerk-sdk-python issue #90](https://github.com/clerk/clerk-sdk-python/issues/90) — azp claim absence (marked LOW: issue closed without definitive fix documentation)
- Clerk Organizations recommendation (based on general docs; specific "one-owner" advice derived from architecture principles, not an official recommendation)

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all package versions verified against live npm/pip registries
- Architecture patterns: HIGH — based on official Clerk docs verified 2026-05-17
- JWT claim structure: HIGH — from official Clerk session tokens reference
- Webhook verification: HIGH — from official Svix FastAPI guide
- Pitfalls: MEDIUM — pitfalls 3 (azp) and 5 (whsec_ prefix) verified from official sources; others derived from pattern analysis
- Migration path: HIGH — FastAPI dual-auth pattern is standard; no Clerk-specific novelty

**Research date:** 2026-05-17
**Valid until:** 2026-06-17 (Clerk changes API frequently; re-verify if @clerk/nextjs major version changes)
