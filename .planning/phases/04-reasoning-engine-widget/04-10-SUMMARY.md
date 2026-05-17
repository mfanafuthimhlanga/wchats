---
phase: 04-reasoning-engine-widget
plan: "10"
subsystem: clerk-auth
tags: [auth, clerk, jwt, webhooks, admin-ui]
dependency_graph:
  requires: [04-09]
  provides: [clerk-auth, dual-auth, webhook-provisioning, admin-ui-clerk]
  affects: [M4-complete]
tech_stack:
  added: [PyJWT-cryptography-2.12.1, svix-1.93.0, "@clerk/nextjs-7.3.5"]
  patterns: [PyJWKClient-lru_cache, dual-auth-dependency, svix-webhook-verify, getToken-useAuth]
key_files:
  created:
    - apps/api/alembic/versions/0005_tenant_clerk_user_id.py
    - apps/api/app/core/clerk_jwt.py
    - apps/api/app/api/v1/webhooks.py
    - apps/api/tests/unit/test_clerk_jwt.py
    - apps/admin/middleware.ts
    - apps/admin/app/sign-in/[[...sign-in]]/page.tsx
    - apps/admin/.env.local.example
  modified:
    - apps/api/app/models/tenant.py
    - apps/api/app/core/config.py
    - apps/api/app/api/deps.py
    - apps/api/app/main.py
    - apps/api/pyproject.toml
    - apps/api/.env.example
    - apps/admin/app/layout.tsx
    - apps/admin/app/agents/[id]/soul/page.tsx
commits:
  - 6ca157a
  - fa342d8
  - 42f93fe
  - 131002b
decisions:
  - Used --ignore-scripts on Windows to work around @clerk/shared postinstall script path bug (Windows compat; scripts are non-essential telemetry/build optimisations)
  - Kept --legacy-peer-deps because react@19.2.0 predates @clerk/nextjs@7.3.5 peer dep range of ~19.2.3 by a patch
  - azp claim validation skipped per RESEARCH.md Pitfall 3; RS256 + exp/nbf is sufficient
  - soul/page.tsx useEffect wrapped in async loadAgent() inner function to support await getToken() without making the effect callback itself async
status: complete
---

# Phase 4 Plan 10: Clerk Full Platform Auth Summary

Clerk authentication added as the production auth backbone for the Veridian platform. FastAPI
accepts Clerk RS256 session JWTs on `Authorization: Bearer` (falling back to `X-API-Key` for
legacy tenants). The admin Next.js app is gated by `clerkMiddleware()`. Tenant rows are
auto-provisioned from Clerk webhooks and a self-healing `POST /me/provision` endpoint. The
soul editor no longer uses the sessionStorage API-key hack.

## Migration 0005

File: `apps/api/alembic/versions/0005_tenant_clerk_user_id.py`

- Revision: `0005`, down_revision: `0004`
- Upgrade: `ALTER TABLE tenants ADD COLUMN clerk_user_id TEXT UNIQUE` + partial index
  `tenants_clerk_user_id_idx ON tenants(clerk_user_id) WHERE clerk_user_id IS NOT NULL`
- Downgrade: drops index then drops column
- Uses raw `op.execute()` SQL only (matches 0004 pattern; no Alembic helpers)
- Column is nullable — existing rows continue to work (api_key path unaffected)

## clerk_jwt.py

File: `apps/api/app/core/clerk_jwt.py`

- `_get_jwks_client()` — `@lru_cache(maxsize=1)` singleton; `PyJWKClient` with
  `cache_keys=True, lifespan=3600` (1-hour key cache, auto-refresh on unknown kid)
- `verify_clerk_jwt(token)` — RS256 only (`algorithms=["RS256"]`); `verify_exp=True`,
  `verify_nbf=True`, `verify_aud=False`; all jwt exceptions wrapped into `InvalidTokenError`
  before re-raise so callers catch a single exception type
- `azp` claim not validated — per 04-9-RESEARCH.md Pitfall 3 (unreliable in some Clerk configs)
- JWKS URL default: `https://api.clerk.com/v1/jwks` (from Settings; overridable via env)

## Dual-auth deps.py

File: `apps/api/app/api/deps.py`

- `_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)` (changed from True)
- `_bearer_scheme = HTTPBearer(auto_error=False)` (new)
- `get_current_tenant(bearer, api_key, db)`:
  - **Path 1 (JWT)**: if `bearer` present, `verify_clerk_jwt()`, look up tenant by
    `clerk_user_id = payload["sub"]`; 404 if tenant not provisioned (triggers /me/provision),
    401 if token invalid
  - **Path 2 (API Key)**: existing O(1) HMAC-prefix lookup + fallback full scan for legacy
    rows with NULL `api_key_prefix`; verbatim copy from old deps.py
  - **No credentials**: 401 "Authentication required"
- `get_admin` and `get_async_redis` unchanged

## webhooks.py

File: `apps/api/app/api/v1/webhooks.py`

Router has **no prefix** — both routes use explicit full paths so there is no shadowing risk.

**POST /webhooks/clerk** (status 204):
- `payload = await request.body()` is the first line (raw bytes for HMAC; Pitfall 2 from RESEARCH)
- `Webhook(CLERK_WEBHOOK_SIGNING_SECRET).verify(payload, headers)` — 400 on `WebhookVerificationError`
- `user.created`: idempotent `INSERT INTO tenants ... ON CONFLICT (clerk_user_id) DO NOTHING`
  with generated api_key, hash, and prefix
- `user.deleted`: soft-delete `UPDATE tenants SET deleted_at = now() WHERE clerk_user_id = :cuid AND deleted_at IS NULL`
- All other event types: acknowledged silently (no-op)

**POST /me/provision** (default 201, returns 200 if already exists):
- HTTPBearer (auto_error=True) + `verify_clerk_jwt`; 401 on invalid JWT
- If tenant with `clerk_user_id = payload["sub"]` exists: `response.status_code = 200`,
  returns `{"status":"exists","tenant_id":...}`
- If not: inserts new tenant, returns 201 + `{"status":"created","tenant_id":...,"api_key":raw_key}`
  (api_key returned only once — single opportunity to retrieve it)

## Admin UI

**@clerk/nextjs installation:**
- Version: `^7.3.5` (installed via `npm install --legacy-peer-deps --ignore-scripts`)
- `--legacy-peer-deps`: react@19.2.0 is one patch below @clerk/nextjs@7.3.5's declared range (~19.2.3)
- `--ignore-scripts`: `@clerk/shared` postinstall script fails on Windows (spawn arg undefined bug);
  scripts are non-functional on Windows in any case (telemetry/build cache optimisation only)

**middleware.ts** (`apps/admin/middleware.ts`):
- `clerkMiddleware()` with `createRouteMatcher(['/sign-in(.*)', '/sign-up(.*)'])`
- Three-part `config.matcher`: skip _next internals, always run for api routes, always run for __clerk

**layout.tsx** (`apps/admin/app/layout.tsx`):
- `ClerkProvider` wraps `{children}` INSIDE `<body>` (not wrapping `<html>` — avoids Pitfall 4)
- Inter and JetBrains_Mono font variables preserved on `<body className={...}>`

**sign-in page** (`apps/admin/app/sign-in/[[...sign-in]]/page.tsx`):
- Centered `<SignIn />` Clerk component; no "use client" directive needed (server component in v7)

**soul/page.tsx** changes:
- Removed: `useState<string>` for apiKey, sessionStorage read in initializer, sessionStorage write
  in onChange, `if (!apiKey) return` guard, `X-API-Key` header in both fetch calls,
  API Key `<div>` + `<label>` + `<input>` block from JSX
- Added: `import { useAuth } from '@clerk/nextjs'`; `const { getToken } = useAuth()` in component
  body; `const token = await getToken()` before each fetch call; `Authorization: Bearer ${token}`
  header in both the load useEffect and handleSave PATCH
- useEffect wrapped in `async loadAgent()` inner function (effect callbacks cannot be async directly)
- Dependency array changed from `[id, apiKey, apiBase]` to `[id, apiBase]`

**apps/admin/.env.local.example**:
- Contains `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, sign-in/sign-up routing vars,
  and `NEXT_PUBLIC_API_BASE`; committed as example (real `.env.local` stays out of git)

## Unit Tests

File: `apps/api/tests/unit/test_clerk_jwt.py` — **7 tests, all passing**

| Test | Covers |
|------|--------|
| `test_expired_token` | CLERK-01: expired JWT rejected by verify_clerk_jwt |
| `test_bad_signature` | CLERK-02: bad/missing signing key raises InvalidTokenError |
| `test_get_current_tenant_jwt_path` | CLERK-03: valid JWT resolves to tenant via clerk_user_id |
| `test_get_current_tenant_apikey_fallback` | CLERK-04: X-API-Key path still works when no Bearer |
| `test_webhook_user_created_provisions_tenant` | CLERK-05: user.created event inserts tenant row |
| `test_webhook_invalid_signature_returns_400` | CLERK-06: bad Svix signature returns 400 |
| `test_no_credentials_returns_401` | CLERK-07: no credentials → 401 |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Windows @clerk/shared postinstall script failure**
- **Found during:** Task 3 npm install
- **Issue:** `@clerk/shared@4.12.0` postinstall script spawns a child process with `undefined`
  as the script path on Windows (Node.js `ERR_INVALID_ARG_TYPE`), crashing npm install
- **Fix:** Added `--ignore-scripts` to npm install. The postinstall script is a non-functional
  Windows telemetry/build-cache optimisation; the package itself works without it
- **Files modified:** None (install flag only)
- **Impact:** None — all @clerk/nextjs runtime functionality is unaffected

**2. [Rule 1 - Bug] react@19.2.0 vs @clerk/nextjs@7.3.5 peer dep mismatch**
- **Found during:** Task 3 npm install
- **Issue:** @clerk/nextjs@7.3.5 declares `peer react@"~19.2.3"` but project has `react@19.2.0`
  (one patch behind); npm strict mode blocks install
- **Fix:** Added `--legacy-peer-deps` flag; the actual react@19.2.0 API is fully compatible —
  this is a semver range declaration issue, not a real incompatibility
- **Files modified:** None (install flag only)

**3. [Rule 1 - Bug] useEffect async pattern for getToken()**
- **Found during:** Task 3 soul page migration
- **Issue:** Plan spec said "the useEffect must call getToken()" but React disallows async
  effect callbacks directly (can't write `useEffect(async () => {...})` cleanly)
- **Fix:** Wrapped fetch logic in a named inner `async loadAgent()` function called inside the
  effect, which is the idiomatic React pattern for async effects
- **Files modified:** `apps/admin/app/agents/[id]/soul/page.tsx`

## Known Stubs

None. All auth paths are fully wired. The soul editor will fail to load agent data if
`NEXT_PUBLIC_API_BASE` is not set in `.env.local`, but this is documented in `.env.local.example`
and is a configuration concern, not a code stub.

## Threat Flags

No new threat surface beyond what is documented in the plan's threat model (T-04-10-01 through
T-04-10-10). All `mitigate` dispositions are implemented.

## Self-Check: PASSED

- `apps/api/alembic/versions/0005_tenant_clerk_user_id.py` exists with revision "0005" and two op.execute() calls
- `apps/api/app/core/clerk_jwt.py` exists with PyJWKClient, lru_cache, verify_clerk_jwt
- `apps/api/app/api/v1/webhooks.py` exists with /webhooks/clerk and /me/provision routes
- `apps/api/tests/unit/test_clerk_jwt.py` — 7 tests pass (`pytest tests/unit/test_clerk_jwt.py -x -q`)
- `apps/admin/middleware.ts` exists with clerkMiddleware and createRouteMatcher
- `apps/admin/app/sign-in/[[...sign-in]]/page.tsx` exists with SignIn component
- `apps/admin/.env.local.example` exists with NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY and CLERK_SECRET_KEY
- `apps/admin/app/layout.tsx` contains ClerkProvider inside body with font classNames preserved
- `apps/admin/app/agents/[id]/soul/page.tsx` contains getToken, does NOT contain sessionStorage or X-API-Key
- `apps/admin/package.json` contains "@clerk/nextjs": "^7.3.5"
- All 4 commits verified in git log: 6ca157a, fa342d8, 42f93fe, 131002b
