# Phase 10: Maintenance + Observability — Research (Plans 10-04, 10-05, 10-06)

**Researched:** 2026-05-25
**Domain:** FastAPI service layer, SQLAlchemy sync mocking, Next.js Clerk auth, Celery task tests, Bash demo scripting
**Confidence:** HIGH — all findings verified against actual codebase files in this session

---

## Summary

Plans 10-01 through 10-03 are complete. The backend pipeline is fully built: alert_service.py, digest_service.py, alert.py/digest.py Celery tasks, observability.py FastAPI routes, and all wired in celery_app.py / main.py. The remaining three plans involve client-side UI (10-04), de-xfailing 9 test stubs (10-05), and a demo script + guarded E2E test (10-06).

**Critical finding for 10-05:** The 9 xfail stubs written in 10-01 contain multiple signature mismatches against the actual code built in 10-02 and 10-03. Every single test stub needs its implementation rewritten to match the actual interfaces — they cannot simply be de-xfailed as-is. The existing stub code will fail immediately on import/call errors, not on meaningful logic assertions.

**Critical finding for 10-04:** The 10-04-PLAN.md specifies an `apiKey` prop on AlertsBanner, but the actual admin UI uses Clerk `useAuth().getToken()` for all client-side API calls. The 10-UI-SPEC.md (already approved) correctly specifies the `Authorization: Bearer {token}` pattern with no `apiKey` prop. The plan's code template must be overridden by the UI-SPEC.

**Primary recommendation:** Execute against the UI-SPEC and verified service interfaces, not the plan's code templates (which were written before 10-02/10-03 existed and before the UI was audited).

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Alert display + resolve | Browser (Next.js client component) | FastAPI (GET/POST /alerts) | Alerts are operational state; must poll; client component owns fetch lifecycle |
| Langfuse link | Browser (static anchor in page.tsx) | — | Static external link; no server state needed |
| Alert threshold evaluation | API (alert_service.py) | Celery task (alert.py) | Pure function called by task; passes db handle in |
| Digest stats collection | API (digest_service.py) | Celery task (digest.py) | Called by task; control DB via SQLAlchemy, tenant DB via psycopg2 |
| Beat scheduling | Celery (celery_app.py) | — | Both alert-daily and digest-weekly already registered |
| E2E demo orchestration | Shell (demo_m10.sh) | Celery (task dispatch) | Follows existing demo_m8/m9 pattern |

---

## Plan 10-04: AlertsBanner + Langfuse Link

### Verified File Structure

The agent detail page is at `apps/admin/app/agents/[id]/page.tsx`. [VERIFIED: file read]

The `components/` subdirectory does NOT yet exist inside `apps/admin/app/agents/[id]/`. [VERIFIED: directory listing]

Current page.tsx structure:
- `'use client'` — already a client component
- Uses `useAuth()` from `@clerk/nextjs` — `const { getToken, isLoaded, isSignedIn } = useAuth()`
- All API calls use `Authorization: Bearer ${token}` (verified: agentQuery.queryFn, docsQuery.queryFn)
- No `apiKey` prop exists anywhere on this page — zero occurrences
- Right panel content is wrapped in `<div style={{ padding: '32px 40px' }}>` with this exact structure:

```tsx
<div style={{ padding: '32px 40px' }}>
  {/* Error alert — existing */}
  {loadError && <div role="alert">…</div>}

  {/* AlertsBanner MOUNTS HERE — above {panel} */}

  {panel}

  {/* Langfuse link MOUNTS HERE — below {panel} */}
</div>
```

[VERIFIED: file read, lines 331-353]

### Auth Pattern — Critical Override

**10-04-PLAN.md is WRONG about auth.** The plan's `AlertsBanner` template uses:
```tsx
headers: { "X-API-Key": apiKey },
```
This does NOT match the codebase. The correct pattern (from 10-UI-SPEC.md and verified from page.tsx) is:
```tsx
const { getToken } = useAuth()
const token = await getToken()
if (!token) return
fetch(`${apiBase}/api/v1/agents/${agentId}/alerts`, {
  headers: { Authorization: `Bearer ${token}` },
})
```
[VERIFIED: 10-UI-SPEC.md and page.tsx both confirm Clerk Bearer pattern; VERIFIED: observability.py uses `get_current_tenant` which accepts Bearer tokens]

**No `apiKey` prop should exist on AlertsBanner.** The component receives only `agentId: string`.

### Mount Location (UI-SPEC authoritative)

AlertsBanner mounts **after the `loadError` div and before `{panel}`**.
Langfuse link mounts **after `{panel}`**, inside a `div` with `marginTop: '24px'`, `paddingTop: '16px'`, `borderTop: '1px solid var(--border-soft)'`.

### Design System Facts

- No shadcn, no component library beyond plain Tailwind + CSS custom properties. [VERIFIED: 10-UI-SPEC.md]
- Severity badge: `bg-amber-100 text-amber-800` (warning), `bg-red-100 text-red-800` (critical) — these go inside the `<span>` only.
- Row background and border use CSS variable inline styles (`var(--amber-bg)`, `var(--red-bg)`) — NOT Tailwind surface classes.
- `var(--text-3)` = #8A6060 for muted text (Resolve button, triggered_at, Langfuse link)
- `var(--border-soft)` separates Langfuse link from panel above
- `apiBase = process.env.NEXT_PUBLIC_API_BASE || ''` — already used in page.tsx for all fetch calls [VERIFIED: page.tsx line 66]

### triggered_at Formatting

Display as relative time (local helper, no date library):
- < 60s: "just now"
- < 3600s: "{n}m ago"
- < 86400s: "{n}h ago"
- >= 86400s: "{n}d ago"
[VERIFIED: 10-UI-SPEC.md §triggered_at formatting]

### Langfuse Link

Static href `https://cloud.langfuse.com`. Do NOT use `process.env.NEXT_PUBLIC_LANGFUSE_HOST`. [VERIFIED: 10-UI-SPEC.md explicitly says this env var is not declared in Next.js config]

---

## Plan 10-05: De-xfail 9 Unit Test Stubs

### Critical: Stub Signature Mismatches

Every xfail stub has at least one mismatch against the actual built code. These are not cosmetic — they are call-site errors that will cause `TypeError` before reaching the assertion.

#### test_digest_service.py — 4 stubs

**Stub 1: `test_collect_digest_stats_shape`**
- Stub calls: `_collect_digest_stats(agent_id="fake-id", conn_str="fake")` — 2 positional args
- Actual signature: `_collect_digest_stats(agent_id: str, conn_str: str, db)` — requires 3 args
- Fix: pass a `MagicMock()` as the third `db` argument; mock `db.execute().fetchone()` to return `None` (no eval/red team data)
[VERIFIED: digest_service.py line 28]

**Stub 2: `test_send_digest_email_calls_smtp`**
- Stub calls: `send_digest_email(to_email="owner@example.com", agent_name="Test Agent", stats={...})`
- Actual signature: `send_digest_email(agent_name: str, agent_id: str, stats: dict)`
- No `to_email` parameter exists; email is taken from `settings.OWNER_EMAIL`
- Fix: rewrite call as `send_digest_email("Test Agent", "agent-123", {...})`; must also configure settings so `all([settings.SMTP_HOST, settings.SMTP_FROM, settings.OWNER_EMAIL])` is True; `settings.DIGEST_ENABLED` must be True
- The SMTP patch path is `app.services.digest_service.smtplib.SMTP` (module-boundary)
[VERIFIED: digest_service.py lines 97-128]

**Stub 3: `test_digest_beat_skips_when_disabled`**
- Calls `run_weekly_digest_beat()` without `.run()` — in CELERY_TASK_ALWAYS_EAGER mode, calling the task directly like a function works (Celery wraps to a callable).
- BUT patching `app.core.config.settings` as a MagicMock will replace the entire settings object. The task also accesses `get_sync_db` — if settings mock triggers it, the test may fail differently.
- Safer pattern: patch only `settings.DIGEST_ENABLED` with monkeypatch or `patch.object(settings, 'DIGEST_ENABLED', False)` rather than replacing the whole settings object.
- Also need to patch `app.worker.tasks.runtime.digest.get_sync_db` since the task checks DIGEST_ENABLED before opening db — if False, it returns early (no db needed).
[VERIFIED: digest.py lines 36-37]

**Stub 4: `test_digest_idempotency_within_7d`**
- Patches `app.worker.tasks.runtime.digest.get_sync_db` as context manager — correct path.
- Calls `run_weekly_digest(agent_id=agent_id)` without `.run()` — acceptable in eager mode.
- Mock returns `MagicMock()` from `scalar_one_or_none()` but actual task uses `fetchone()` not `scalar_one_or_none()`.
- Actual code: `db.execute(...).fetchone()` — the mock should chain as `mock_db.execute.return_value.fetchone.return_value = MagicMock()` (not `scalar_one_or_none`)
- Assert `result.get("status") == "already_sent"` — matches actual return value `{"status": "already_sent"}` [VERIFIED: digest.py line 72]
[VERIFIED: digest.py lines 62-73]

#### test_alert_service.py — 3 stubs

**Critical: `alert_service.py` does NOT import or use `get_sync_db`.** [VERIFIED: alert_service.py imports — only imports `settings`, `Alert`, `structlog`, `text`]

The `db` parameter is passed in FROM the task (`alert.py` opens `get_sync_db()` and calls `check_and_write_alerts(..., db=db)`). The stubs patch `app.services.alert_service.get_sync_db` — this path does NOT exist in alert_service.py. That patch will succeed silently (patch creates the attribute) but the mock db won't be used because the function doesn't open db itself.

**Fix for all 3 alert tests:** Call `check_and_write_alerts(...)` directly with a real `mock_db` argument (no patch of `get_sync_db` needed at all). Pass `faithfulness` and `critical_red_team_count` directly as kwargs — the function accepts these directly and bypasses DB queries when they are not None.

Actual signature:
```python
def check_and_write_alerts(
    agent_id: str,
    faithfulness: float | None = None,
    critical_red_team_count: int | None = None,
    agent_name: str = "",
    db=None,
) -> list[Alert]:
```
[VERIFIED: alert_service.py lines 102-109]

When `db is not None` AND `faithfulness is not None` (passed directly), the function skips `_get_latest_faithfulness()` and goes straight to threshold evaluation. It then calls `_active_alert_exists()` which does `db.execute(...).fetchone()`.

**Mock chain for `_active_alert_exists` to return no existing alert:**
```python
mock_db.execute.return_value.fetchone.return_value = None
```
Then for the alert write path, `_write_alert()` calls `db.add()`, `db.commit()`, `db.refresh(alert)`. The mock needs `db.refresh` to not mutate the Alert. A plain `MagicMock()` handles this.

**Stub 1 (`test_eval_regression_triggers_alert`):** Pass `faithfulness=0.4`, `critical_red_team_count=0`, `db=mock_db`. Assert `mock_db.add.called` and `mock_db.add.call_args[0][0].alert_type == "eval_regression"`. [VERIFIED: alert_service.py lines 119-125]

**Stub 2 (`test_red_team_critical_triggers_alert`):** Pass `faithfulness=0.9`, `critical_red_team_count=2`, `db=mock_db`. Assert `alert_type == "red_team_critical"`. [VERIFIED: alert_service.py lines 128-134]

**Stub 3 (`test_no_alert_when_thresholds_met`):** Pass `faithfulness=0.95`, `critical_red_team_count=0`, `db=mock_db`. Assert `mock_db.add.assert_not_called()`. Note: when `faithfulness=0.95` (above 0.6) and `critical_red_team_count=0` (below 1), neither branch fires — `db.add` never called. But also need to confirm: `_active_alert_exists` is NOT called when thresholds are not crossed (so the mock doesn't need careful setup for this). [VERIFIED: alert_service.py lines 119 and 128 — conditions checked first]

**Settings dependency in alert tests:** `settings.ALERT_FAITHFULNESS_THRESHOLD` and `settings.ALERT_RED_TEAM_CRITICAL_COUNT` are read from the real `settings` object. These have defaults of 0.6 and 1 respectively. Tests MUST NOT patch the entire settings object — that causes import issues. Either use real settings values (0.4 < 0.6, 2 >= 1 are valid without override) or use `patch.object(settings, 'ALERT_FAITHFULNESS_THRESHOLD', 0.6)`.
[VERIFIED: config.py — `ALERT_FAITHFULNESS_THRESHOLD: float = 0.6`, `ALERT_RED_TEAM_CRITICAL_COUNT: int = 1`]

#### test_observability_routes.py — 2 stubs

These stubs are already well-formed. The pattern matches `test_deployment_routes.py` exactly:
- `ASGITransport(app=app)` with `AsyncClient`
- `dependency_overrides` for `get_current_tenant` and `get_async_db`
- `mock_db.get.return_value = mock_agent`
- `finally: app.dependency_overrides.clear()`

**One correctness issue:** `test_get_alerts_returns_list` mocks `mock_db.execute.return_value.scalars.return_value.all.return_value = []` — returns empty list. The route then returns `[]`. Assert `resp.json() == []` (a list, length 0) — the test asserts `isinstance(resp.json(), list)` which passes. This is correct.

**IDOR test:** Route returns 403 when `agent.tenant_id != tenant.id`. Stub correctly sets `attacker_tenant_id` != `owner_tenant_id`. Route raises `HTTPException(status_code=403)`. Stub asserts `status_code in (401, 403)`. [VERIFIED: observability.py line 29]

**No changes needed to observability route stubs** — they are correctly written and will pass once de-xfailed.

### _make_sync_db_ctx Pattern

Multiple existing tests define this helper locally (not from a shared module):
```python
from contextlib import contextmanager

def _make_sync_db_ctx(mock_db):
    @contextmanager
    def _fake_get_sync_db():
        yield mock_db
    return _fake_get_sync_db
```
[VERIFIED: test_deployment_task.py lines 46-52, test_red_team_task.py lines 43-49, test_strategy_task.py lines 44-50]

This is needed for `test_digest_idempotency_within_7d` (patching `digest.get_sync_db`). The pattern must be copied into test_digest_service.py. Not needed for alert tests (alert_service doesn't use get_sync_db).

### Task Invocation Pattern in Eager Mode

`CELERY_TASK_ALWAYS_EAGER=True` is set in `tests/conftest.py`. In this mode:
- `task.delay(...)` and `task.apply_async(...)` run synchronously
- `task.run(...)` calls the underlying function directly (most reliable for unit tests)
- `task(...)` also works (Celery wraps task as callable)

Existing tests use `.run(agent_id=agent_id)` — this is the established pattern.
[VERIFIED: test_deployment_task.py line 94, test_red_team_task.py line 106]

For `run_weekly_digest_beat` (no args): call as `run_weekly_digest_beat.run()`.
For `run_weekly_digest` (agent_id kwarg): call as `run_weekly_digest.run(agent_id=agent_id)`.

---

## Plan 10-06: demo_m10.sh + Guarded E2E

### demo_m9.sh Patterns to Follow (Verified)

[VERIFIED: demo_m9.sh full read]

Key structural patterns:
1. `set -euo pipefail` — exactly once, near top
2. `BASE_URL="${BASE_URL:-http://localhost:8000}"` config block
3. Guard block for empty `ADMIN_KEY` / `API_KEY` with usage message
4. `redis-cli ping` + `curl -sf /health` prereq checks
5. `python` (not `python3`) for all JSON extraction one-liners
6. Pattern: `python -c "import sys,json; print(json.load(sys.stdin)['id'])"` piped from curl
7. Polling loops: `for i in $(seq 1 N); do ... sleep N; done`
8. `[PASS]` / `[FAIL]` assertion lines followed by `ALL_PASSED` exit code check

**Rule: use `python` not `python3`** — enforced by 10-06-PLAN.md must-have and confirmed in demo_m9.sh [VERIFIED: all `python -c` calls in demo_m9.sh use `python`]

### demo_m10.sh Structure (5 Sections)

Section 1 — Prerequisites: `redis-cli ping`, `curl /health`
Section 2 — Create + deploy agent: POST /api/v1/agents → poll → deploy → confirm is_deployed=true
Section 3 — Trigger alert check: invoke `run_alert_check` via Python one-liner, sleep 5s, GET /alerts
Section 4 — Show alerts output: print JSON, `[PASS] OPS-04: alerts endpoint returns 200`
Section 5 — Verify beats registered: `celery -A app.worker.celery_app inspect registered` and grep for `digest-weekly` and `alert-daily`

**Celery task trigger pattern** (from 10-06-PLAN.md context):
```bash
python -c "from app.worker.tasks.runtime.alert import run_alert_check; \
  run_alert_check.apply_async(kwargs={'agent_id': '$AGENT_ID'})"
```
Note: requires Celery worker running. Alternative is `run_alert_check.run(agent_id='$AGENT_ID')` which runs synchronously in the current process (useful for demo without a worker).

**Deployment trigger** — demo requires a deployed agent. The deployment checklist flow (POST /api/v1/agents/{id}/deployments/trigger → poll → POST .../approve) must complete before the alert check is meaningful. See demo_m8.sh Section 2-5 for the exact checklist pattern.
[VERIFIED: demo_m8.sh structure read]

**No Docker** — demo_m10.sh must only reference local processes (redis-server, uvicorn, celery worker). No `docker-compose` anywhere.

### E2E Test Pattern (test_observability_e2e.py)

The stub in 10-06-PLAN.md is already correct and complete. The guard pattern matches existing E2E tests:
```python
OPS_E2E_ENABLED = os.environ.get("OPS_E2E_ENABLED", "0") == "1"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(not OPS_E2E_ENABLED, ...)]
```
[VERIFIED: pattern confirmed across test_strategy_e2e.py, test_deployment_e2e.py]

Uses synchronous `httpx.get()` / `httpx.post()` (not async) — matching the existing E2E test style.

Test file location: `apps/api/tests/e2e/test_observability_e2e.py`

---

## Critical Mismatches Table

| Stub | Mismatch Type | What the Stub Does | What the Code Requires |
|------|--------------|-------------------|----------------------|
| `test_collect_digest_stats_shape` | Wrong arity | `_collect_digest_stats(id, conn_str)` — 2 args | Requires 3 args: `(id, conn_str, db)` |
| `test_send_digest_email_calls_smtp` | Wrong signature | `send_digest_email(to_email=..., agent_name=..., stats=...)` | `send_digest_email(agent_name, agent_id, stats)` — no `to_email` |
| `test_digest_beat_skips_when_disabled` | Risky patch | Patches entire `settings` object | Should use `patch.object(settings, 'DIGEST_ENABLED', False)` |
| `test_digest_idempotency_within_7d` | Wrong mock chain | `mock_db.execute.return_value.scalar_one_or_none.return_value` | Actual code uses `.fetchone()`, not `.scalar_one_or_none()` |
| `test_eval_regression_triggers_alert` | Wrong patch target | Patches `alert_service.get_sync_db` (does not exist in module) | `alert_service` has no `get_sync_db`; pass `db=mock_db` directly |
| `test_red_team_critical_triggers_alert` | Same as above | Same bad patch | Same fix |
| `test_no_alert_when_thresholds_met` | Same as above | Same bad patch | Same fix |
| `test_get_alerts_returns_list` | None | Already correct | Already correct — will pass after de-xfail |
| `test_get_alerts_idor_guard` | None | Already correct | Already correct — will pass after de-xfail |

---

## Actual Function Signatures (Verified)

```python
# digest_service.py
def _collect_digest_stats(agent_id: str, conn_str: str, db) -> dict:
    # db is SQLAlchemy sync session (from get_sync_db context manager)
    # conn_str is decrypted Neon connection string for tenant DB

def send_digest_email(agent_name: str, agent_id: str, stats: dict) -> None:
    # DIGEST_ENABLED checked first; SMTP_HOST/SMTP_FROM/OWNER_EMAIL all required
    # NEVER raises (fire-and-forget)
```
[VERIFIED: digest_service.py]

```python
# alert_service.py
def check_and_write_alerts(
    agent_id: str,
    faithfulness: float | None = None,
    critical_red_team_count: int | None = None,
    agent_name: str = "",
    db=None,
) -> list[Alert]:
    # When faithfulness is passed directly: skips _get_latest_faithfulness(db)
    # When critical_red_team_count is passed directly: skips _get_latest_critical_count(db)
    # _active_alert_exists() uses db.execute(...).fetchone()
    # _write_alert() uses db.add(), db.commit(), db.refresh()
    # Does NOT import or use get_sync_db — db is injected by the task
```
[VERIFIED: alert_service.py]

```python
# observability.py routes
@router.get("/{agent_id}/alerts")
async def list_alerts(
    agent_id: UUID,
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_async_db),
):
    # IDOR: agent.tenant_id != tenant.id → HTTPException(403)
    # Returns list of dicts with: id, alert_type, severity, message, triggered_at

@router.post("/{agent_id}/alerts/{alert_id}/resolve")
async def resolve_alert(agent_id: UUID, alert_id: UUID, tenant=..., db=...):
    # IDOR on agent + alert ownership
    # Returns {"resolved": True}
```
[VERIFIED: observability.py]

---

## Architecture Patterns

### Service Layer (alert_service.py, digest_service.py)

Both services are **pure functions** — no dependency injection, no ORM session attributes on the class. The `db` handle (sync SQLAlchemy session) is passed in from the calling Celery task. Connection strings for tenant DB are passed as plain strings (decrypted by the task from the control DB) — never logged per CTL-08.

### Celery Task Pattern (alert.py, digest.py)

All tasks: `acks_late=True`, `bind=True`, retry logic, `queue="runtime"`. Tasks open `get_sync_db()` as context manager, pass the db handle into service functions. This is the consistent pattern across M5-M10.

### Route IDOR Pattern (observability.py)

```python
agent = await db.get(Agent, agent_id)
if agent is None or agent.tenant_id != tenant.id:
    raise HTTPException(status_code=403, detail="Forbidden")
```
This returns 403 (not 404) for both missing agents and IDOR attempts — consistent with deployment routes. [VERIFIED: observability.py lines 27-29]

### Admin UI Auth Pattern (page.tsx)

All client-side API calls in the admin UI use:
```tsx
const { getToken } = useAuth()
const token = await getToken()
headers: { Authorization: `Bearer ${token}` }
```
This is Clerk's `getToken()` returning a short-lived JWT. The FastAPI backend accepts Bearer tokens via `get_current_tenant` dual-auth (Bearer first, X-API-Key fallback). [VERIFIED: page.tsx lines 65, 71-73]

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead |
|---------|-------------|-------------|
| Relative time formatting | External date lib | Local 4-branch helper (< 60s / < 3600s / < 86400s / else) — no new dependency |
| Auth in AlertsBanner | Custom apiKey prop | `useAuth()` from `@clerk/nextjs` (already installed) |
| SMTP context manager mocking | Custom SMTP test harness | `patch("app.services.digest_service.smtplib.SMTP")` at module boundary |
| Celery task execution in tests | Running real worker | `task.run(...)` in CELERY_TASK_ALWAYS_EAGER=True mode |
| get_sync_db mock | Custom fixture | `_make_sync_db_ctx(mock_db)` local helper (copy from test_deployment_task.py) |

---

## Common Pitfalls

### Pitfall 1: Plan's `apiKey` prop conflicts with actual codebase

**What goes wrong:** Following 10-04-PLAN.md's AlertsBanner template verbatim creates a component that passes `apiKey` from `page.tsx` — but `page.tsx` has no `apiKey` available. The build will fail (TypeScript) or the component will call the API with `X-API-Key: undefined`.

**How to avoid:** Use the 10-UI-SPEC.md as the authoritative source. AlertsBanner has no `apiKey` prop. It calls `useAuth().getToken()` internally.

**Warning signs:** TypeScript error "Property 'apiKey' does not exist on type..." OR network request with `X-API-Key: undefined`.

### Pitfall 2: Patching `alert_service.get_sync_db` (module doesn't import it)

**What goes wrong:** `patch("app.services.alert_service.get_sync_db")` succeeds silently — Python's `unittest.mock.patch` creates a new attribute on the module. But the function body never calls `get_sync_db`, so the patch has no effect. The `db` param is `None`, so `_active_alert_exists()` is skipped (checked with `if db is None or not _active_alert_exists(...)`), meaning alerts will be written without checking for duplicates. Tests that check `db.add` was called may accidentally pass for wrong reasons.

**How to avoid:** Do not patch `get_sync_db` in alert service tests. Pass `mock_db` directly as the `db` argument to `check_and_write_alerts()`.

### Pitfall 3: `scalar_one_or_none()` vs `fetchone()` in digest idempotency mock

**What goes wrong:** Stub mocks `mock_db.execute.return_value.scalar_one_or_none.return_value = MagicMock()` but the actual task code calls `db.execute(...).fetchone()`. `MagicMock()` returns a new MagicMock for any attribute access, so `scalar_one_or_none` is truthy but the `if existing:` check uses `fetchone()`. The idempotency guard is never triggered — test passes for wrong reason OR fails depending on mock behavior.

**How to avoid:** Mock `mock_db.execute.return_value.fetchone.return_value = MagicMock()` to simulate a row found.

### Pitfall 4: `send_digest_email` requires settings.DIGEST_ENABLED=True and SMTP settings

**What goes wrong:** SMTP test expects the function to call smtplib.SMTP, but the function has an early return if `not settings.DIGEST_ENABLED` OR if SMTP settings are not all set. In the test environment, `SMTP_HOST`, `SMTP_FROM`, `OWNER_EMAIL` are all `None` by default.

**How to avoid:** Use `patch.object(settings, 'SMTP_HOST', 'localhost')`, `patch.object(settings, 'SMTP_FROM', 'test@test.com')`, `patch.object(settings, 'OWNER_EMAIL', 'owner@test.com')`, and ensure `DIGEST_ENABLED=True`. Or use monkeypatch fixture on each settings field individually.

### Pitfall 5: demo_m10.sh must use `python` not `python3`

**What goes wrong:** If `python3` is used in the demo script, the 10-06 must-have check fails: "demo_m10.sh uses python (not python3) for JSON extraction".

**How to avoid:** Copy the exact `python -c "import sys,json; ..."` pattern from demo_m9.sh. Do not substitute `python3`.

### Pitfall 6: `components/` directory for AlertsBanner does not exist yet

**What goes wrong:** Attempting to import from `./components/AlertsBanner` in page.tsx fails at build time if the file is not created first.

**How to avoid:** Task 1 (create AlertsBanner.tsx) must complete and commit before Task 2 (modify page.tsx to import it).

---

## Code Examples

### AlertsBanner — Correct Implementation (from UI-SPEC)

```tsx
// Source: 10-UI-SPEC.md (approved 2026-05-25)
"use client"
import { useEffect, useState } from "react"
import { useAuth } from "@clerk/nextjs"

interface Alert {
  id: string
  alert_type: string
  severity: string
  message: string
  triggered_at: string
}

function relativeTime(iso: string): string {
  const secs = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (secs < 60) return "just now"
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

export function AlertsBanner({ agentId }: { agentId: string }) {
  const { getToken } = useAuth()
  const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''
  const [alerts, setAlerts] = useState<Alert[]>([])

  const fetchAlerts = async () => {
    const token = await getToken()
    if (!token) return
    try {
      const res = await fetch(`${apiBase}/api/v1/agents/${agentId}/alerts`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (res.ok) setAlerts(await res.json())
    } catch {}
  }

  useEffect(() => {
    fetchAlerts()
    const id = setInterval(fetchAlerts, 30_000)
    return () => clearInterval(id)
  }, [agentId])

  if (alerts.length === 0) return null

  const resolve = async (alertId: string) => {
    setAlerts(prev => prev.filter(a => a.id !== alertId))  // optimistic
    const token = await getToken()
    if (!token) return
    await fetch(`${apiBase}/api/v1/agents/${agentId}/alerts/${alertId}/resolve`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    })
  }

  return (
    <div style={{ marginBottom: '16px' }} className="space-y-2">
      {alerts.map(a => (
        <div
          key={a.id}
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            justifyContent: 'space-between',
            padding: '12px 16px',
            borderRadius: 'var(--radius-xs)',
            background: a.severity === 'critical' ? 'var(--red-bg)' : 'var(--amber-bg)',
            border: a.severity === 'critical'
              ? '1px solid rgba(185, 28, 28, 0.20)'
              : '1px solid rgba(146, 64, 14, 0.20)',
          }}
        >
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span className={a.severity === 'critical'
                ? 'bg-red-100 text-red-800 px-1.5 py-0.5 rounded text-xs font-semibold uppercase'
                : 'bg-amber-100 text-amber-800 px-1.5 py-0.5 rounded text-xs font-semibold uppercase'
              }>
                {a.severity}
              </span>
              <span style={{ fontSize: '14px', fontWeight: 600 }}>
                {a.alert_type === 'eval_regression' ? 'Eval Regression'
                  : a.alert_type === 'red_team_critical' ? 'Critical Red Team Finding'
                  : a.alert_type.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
              </span>
            </div>
            <p style={{ fontSize: '14px', color: 'var(--text-2)', margin: '4px 0 2px' }}>
              {a.message}
            </p>
            <p style={{ fontSize: '12px', color: 'var(--text-3)', margin: 0 }}>
              {relativeTime(a.triggered_at)}
            </p>
          </div>
          <button
            onClick={() => resolve(a.id)}
            style={{
              marginLeft: '16px',
              flexShrink: 0,
              fontSize: '12px',
              color: 'var(--text-3)',
              textDecoration: 'underline',
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              padding: 0,
            }}
          >
            Resolve
          </button>
        </div>
      ))}
    </div>
  )
}
```

### Correct Alert Service Test Pattern

```python
# Source: verified against alert_service.py interface
from unittest.mock import MagicMock
from app.services.alert_service import check_and_write_alerts

def test_eval_regression_triggers_alert():
    agent_id = "test-agent-id"
    mock_db = MagicMock()
    # _active_alert_exists uses db.execute(...).fetchone() — return None = no existing alert
    mock_db.execute.return_value.fetchone.return_value = None

    result = check_and_write_alerts(
        agent_id=agent_id,
        faithfulness=0.4,             # below default threshold 0.6
        critical_red_team_count=0,
        db=mock_db,
    )

    mock_db.add.assert_called_once()
    added = mock_db.add.call_args[0][0]
    assert added.alert_type == "eval_regression"
```

### Correct Digest Idempotency Test Pattern

```python
# Source: verified against digest.py interface
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
from uuid import uuid4

def _make_sync_db_ctx(mock_db):
    @contextmanager
    def _fake():
        yield mock_db
    return _fake

def test_digest_idempotency_within_7d():
    agent_id = str(uuid4())
    mock_db = MagicMock()
    # Simulate existing digest_runs row: fetchone() returns a row
    mock_db.execute.return_value.fetchone.return_value = MagicMock()

    with patch("app.worker.tasks.runtime.digest.get_sync_db", _make_sync_db_ctx(mock_db)):
        from app.worker.tasks.runtime.digest import run_weekly_digest
        result = run_weekly_digest.run(agent_id=agent_id)

    assert result == {"status": "already_sent"}
```

### Correct SMTP Test Pattern

```python
# Source: verified against digest_service.py interface
from unittest.mock import MagicMock, patch
from app.core.config import settings

def test_send_digest_email_calls_smtp(monkeypatch):
    monkeypatch.setattr(settings, 'SMTP_HOST', 'localhost')
    monkeypatch.setattr(settings, 'SMTP_FROM', 'test@test.com')
    monkeypatch.setattr(settings, 'OWNER_EMAIL', 'owner@test.com')
    monkeypatch.setattr(settings, 'DIGEST_ENABLED', True)
    monkeypatch.setattr(settings, 'SMTP_PORT', 587)

    mock_smtp_cls = MagicMock()
    mock_smtp_instance = MagicMock()
    mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
    mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

    with patch("app.services.digest_service.smtplib.SMTP", mock_smtp_cls):
        from app.services.digest_service import send_digest_email
        send_digest_email(
            agent_name="Test Agent",
            agent_id="agent-123",
            stats={
                "conversation_count": 5,
                "faithfulness_score": 0.85,
                "critical_red_team_count": 0,
                "escalation_count": 1,
            },
        )

    mock_smtp_cls.assert_called_once()
```

---

## Environment Availability

Step 2.6: SKIPPED for 10-04 and 10-05 (code/test changes only, no new external dependencies).

For 10-06 (demo_m10.sh):

| Dependency | Required By | Notes |
|------------|------------|-------|
| redis-server | Section 1 prereq check | Must be running locally |
| uvicorn (FastAPI) | Section 1 prereq check | `apps/api/` uvicorn process |
| celery worker | Section 3 (task dispatch) | `celery -A app.worker.celery_app worker` |
| python (not python3) | JSON extraction one-liners | Must be `python` per 10-06 must-have |
| `celery inspect registered` | Section 5 beat verification | Requires worker running |

All dependencies are local processes, no Docker per CLAUDE.md rule.

---

## Project Constraints (from CLAUDE.md)

- No Docker — all demo scripts target local processes only (redis-server, uvicorn, celery)
- `acks_late=True` AND idempotency on every Celery task — both already present in alert.py and digest.py (not relevant for 10-04/10-05/10-06 execution)
- Connection strings never in Celery task args — satisfied in existing digest.py and alert.py
- Langfuse v4 API only — not applicable to 10-04/10-05/10-06
- Ragas 0.4.x API only — not applicable to these plans
- No pg_search / pgbm25 — not applicable
- Use `pnpm` for Next.js package management (10-04 adds no new npm dependencies)

---

## Open Questions (RESOLVED)

1. **`test_digest_beat_skips_when_disabled` — settings patch scope**
   - What we know: `run_weekly_digest_beat` reads `settings.DIGEST_ENABLED` before opening db
   - RESOLVED: Use `patch.object(settings, 'DIGEST_ENABLED', False)` on individual settings fields, not the whole object. Patching the entire `app.core.config.settings` object replaces the module reference and breaks other imports in the same test process.

2. **`test_collect_digest_stats_shape` — mock DB for stats collection**
   - What we know: Function queries eval_runs and red_team_runs via `db.execute().fetchone()`; queries tenant DB via psycopg2.connect(conn_str)
   - RESOLVED: Patch `app.services.digest_service.psycopg2.connect` at the module boundary (alongside `mock_db`) to prevent a real connection attempt on `conn_str="fake"`. The function catches all exceptions and falls back to 0 counts, but explicit mocking is safer and tests the happy path correctly.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `process.env.NEXT_PUBLIC_API_BASE` is the correct env var name for the API base URL in admin UI | Plan 10-04 | Component fetches wrong URL if env var name differs |

**Note:** A1 is LOW risk — the variable name `NEXT_PUBLIC_API_BASE` is verified from page.tsx line 66. [VERIFIED]

**If this table is effectively empty:** All claims verified from codebase files in this session.

---

## Sources

### Primary (HIGH confidence — verified by file read this session)
- `apps/api/app/services/alert_service.py` — actual function signatures, imports, logic
- `apps/api/app/services/digest_service.py` — actual function signatures, imports, logic
- `apps/api/app/api/v1/observability.py` — route dependencies, IDOR pattern
- `apps/api/app/worker/tasks/runtime/digest.py` — task structure, idempotency pattern
- `apps/api/app/worker/tasks/runtime/alert.py` — task structure
- `apps/api/tests/unit/test_alert_service.py` — existing stubs (mismatches documented)
- `apps/api/tests/unit/test_digest_service.py` — existing stubs (mismatches documented)
- `apps/api/tests/unit/test_observability_routes.py` — existing stubs (already correct)
- `apps/admin/app/agents/[id]/page.tsx` — confirmed Clerk auth pattern, page structure
- `.planning/phases/10-maintenance-observability/10-UI-SPEC.md` — approved design contract
- `apps/api/tests/unit/test_deployment_task.py` — `_make_sync_db_ctx` reference pattern
- `apps/api/tests/unit/test_deployment_routes.py` — route test reference pattern
- `scripts/demo_m9.sh` — demo script structure, `python` vs `python3`
- `apps/api/tests/conftest.py` — CELERY_TASK_ALWAYS_EAGER=True confirmed

### Secondary
- `.planning/phases/10-maintenance-observability/10-0{1,2,3}-SUMMARY.md` — what was built
- `.planning/phases/10-maintenance-observability/10-0{4,5,6}-PLAN.md` — plan targets

---

## Metadata

**Confidence breakdown:**
- Service interfaces: HIGH — read directly from source files
- Test stub mismatches: HIGH — side-by-side comparison of stubs vs implementations
- UI auth pattern: HIGH — verified from page.tsx and UI-SPEC
- Demo script pattern: HIGH — read demo_m9.sh in full

**Research date:** 2026-05-25
**Valid until:** Indefinite for this milestone (all claims are codebase-pinned, not ecosystem version-dependent)
