# Phase 10: Maintenance + Observability — Pattern Map

**Mapped:** 2026-05-25
**Files analyzed:** 7 (2 new, 5 modified)
**Analogs found:** 7 / 7

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `apps/admin/app/agents/[id]/components/AlertsBanner.tsx` | component | request-response (polling) | `apps/admin/app/agents/[id]/page.tsx` | exact — same Clerk useAuth pattern, same apiBase env var, same fetch style |
| `apps/admin/app/agents/[id]/page.tsx` | component | request-response | self (modify) | self — mount point already confirmed at line 350 |
| `apps/api/tests/unit/test_digest_service.py` | test | CRUD + task | `apps/api/tests/unit/test_deployment_task.py` | role-match — same `_make_sync_db_ctx` helper, same `.run()` invocation, same env-var header block |
| `apps/api/tests/unit/test_alert_service.py` | test | CRUD | `apps/api/tests/unit/test_deployment_task.py` | role-match — same env-var header block, same `MagicMock()` db injection pattern |
| `apps/api/tests/unit/test_observability_routes.py` | test | request-response | `apps/api/tests/unit/test_deployment_routes.py` | exact — identical ASGITransport + dependency_overrides + `finally: clear()` pattern |
| `scripts/demo_m10.sh` | utility | batch | `scripts/demo_m9.sh` | exact — same 5-section structure, same `python` one-liner pattern, same pipefail header |
| `apps/api/tests/e2e/test_observability_e2e.py` | test (e2e) | request-response | `apps/api/tests/e2e/test_strategy_e2e.py` | exact — same guard variable + pytestmark + synchronous httpx pattern |

---

## Pattern Assignments

### `apps/admin/app/agents/[id]/components/AlertsBanner.tsx` (component, request-response)

**Analog:** `apps/admin/app/agents/[id]/page.tsx`

**Imports pattern** (page.tsx lines 1-6):
```tsx
'use client'
import Link from 'next/link'
import { use } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '@clerk/nextjs'
import StepSubtaskCard from '../../components/StepSubtaskCard'
```
AlertsBanner needs only:
```tsx
"use client"
import { useEffect, useState } from "react"
import { useAuth } from "@clerk/nextjs"
```

**Auth pattern — Clerk Bearer token** (page.tsx lines 65-77):
```tsx
const { getToken, isLoaded, isSignedIn } = useAuth()
const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''

// Inside queryFn / async fetch:
const token = await getToken()
if (!token) throw new Error('Not authenticated. Please sign in.')
const r = await fetch(`${apiBase}/api/v1/agents/${id}`, {
  headers: { Authorization: `Bearer ${token}` },
})
```
AlertsBanner copies this pattern verbatim — `apiBase` from `process.env.NEXT_PUBLIC_API_BASE || ''`, `getToken()` from `useAuth()`, `Authorization: Bearer ${token}` header. NO `X-API-Key` header. NO `apiKey` prop.

**Core polling pattern** (10-UI-SPEC.md authoritative, mirroring page.tsx useQuery refetchInterval):
```tsx
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
  // ... render rows
}
```

**Error alert pattern for row background + border** (page.tsx lines 333-348):
```tsx
// Error alert uses inline CSS vars — alert rows follow the same pattern:
<div
  role="alert"
  style={{
    padding: '12px 16px',
    marginBottom: '20px',
    background: 'var(--red-bg)',
    border: '1px solid rgba(192,57,43,0.3)',
    borderRadius: 'var(--radius-xs)',
    fontSize: '14px',
    color: 'var(--red)',
  }}
>
```
Alert rows use `var(--amber-bg)` / `var(--red-bg)` for background and CSS rgba borders — NOT Tailwind surface classes. Tailwind classes (`bg-amber-100 text-amber-800`, `bg-red-100 text-red-800`) apply only inside badge `<span>` elements.

**Mount location in page.tsx** (page.tsx lines 330-352):
```tsx
return (
  <div style={{ padding: '32px 40px' }}>
    {/* Error alert — existing */}
    {loadError && (
      <div role="alert" style={{ ... }}>
        {loadError}
      </div>
    )}

    {/* AlertsBanner MOUNTS HERE — after loadError div, before {panel} */}
    <AlertsBanner agentId={id} />

    {panel}

    {/* Langfuse link MOUNTS HERE — after {panel} */}
    <div style={{ marginTop: '24px', paddingTop: '16px',
                  borderTop: '1px solid var(--border-soft)' }}>
      <a
        href="https://cloud.langfuse.com"
        target="_blank"
        rel="noopener noreferrer"
        style={{ fontSize: '13px', color: 'var(--text-3)',
                 textDecoration: 'underline' }}
      >
        View Langfuse Dashboard →
      </a>
    </div>
  </div>
)
```

---

### `apps/admin/app/agents/[id]/page.tsx` (modify — mount AlertsBanner + Langfuse link)

**Analog:** self

**Import to add** (after line 6, after existing imports):
```tsx
import { AlertsBanner } from "./components/AlertsBanner"
```

**Mount location** — insert between the `loadError` div (line 332–348) and `{panel}` (line 350). The `{panel}` reference is at line 350 in the current file. Langfuse link div follows `{panel}` before the closing `</div>`.

**No new props, no new state** — `id` is already in scope from `const { id } = use(params)` (line 64).

---

### `apps/api/tests/unit/test_digest_service.py` (modify — de-xfail 4 stubs)

**Analog:** `apps/api/tests/unit/test_deployment_task.py`

**Env-var safety header block** (test_deployment_task.py lines 26-34):
```python
import os
import base64

os.environ.setdefault("NEON_API_KEY", "test_neon_key")
os.environ.setdefault("NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ADMIN_KEY", "test_admin_key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")
os.environ.setdefault("JWT_SECRET", "test_jwt_secret")
os.environ.setdefault("CLERK_WEBHOOK_SIGNING_SECRET", "test_clerk_secret")
```
The existing test_digest_service.py already has a partial header (lines 10-19) — it is missing `VOYAGE_API_KEY`, `JWT_SECRET`, and `CLERK_WEBHOOK_SIGNING_SECRET`. Add those three lines.

**`_make_sync_db_ctx` helper** (test_deployment_task.py lines 46-52):
```python
from contextlib import contextmanager

def _make_sync_db_ctx(mock_db):
    """Return a patched get_sync_db that yields mock_db when used as 'with get_sync_db() as db'."""
    @contextmanager
    def _fake_get_sync_db():
        yield mock_db
    return _fake_get_sync_db
```
Copy verbatim into test_digest_service.py. Required for `test_digest_idempotency_within_7d`.

**Task invocation via `.run()`** (test_deployment_task.py line 94):
```python
result = run_deployment_checklist.run(agent_id=agent_id)
```
Pattern for digest tests:
- `run_weekly_digest_beat.run()` (no args)
- `run_weekly_digest.run(agent_id=agent_id)`

**Mock db chain for `fetchone()`** (RESEARCH.md verified pattern — NOT `scalar_one_or_none`):
```python
# For idempotency test — simulate a row found (skip):
mock_db.execute.return_value.fetchone.return_value = MagicMock()

# For stats shape test — simulate no eval/red team data:
mock_db.execute.return_value.fetchone.return_value = None
```

**SMTP mock pattern** (RESEARCH.md, verified against digest_service.py):
```python
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
            stats={ ... },
        )

    mock_smtp_cls.assert_called_once()
```
Note the patch path: `"app.services.digest_service.smtplib.SMTP"` (module-boundary), NOT `"smtplib.SMTP"`. The existing stub patches `"smtplib.SMTP"` — that is wrong.

**settings patch for DIGEST_ENABLED** — use `patch.object`, not whole-object replacement:
```python
from unittest.mock import patch
from app.core.config import settings

with patch.object(settings, 'DIGEST_ENABLED', False):
    from app.worker.tasks.runtime.digest import run_weekly_digest_beat
    result = run_weekly_digest_beat.run()

assert result == {"dispatched": 0}
```

**Correct `_collect_digest_stats` call signature** (3 args, not 2):
```python
# WRONG (existing stub):
stats = _collect_digest_stats(agent_id="fake-id", conn_str="fake")

# CORRECT (verified from digest_service.py line 28):
mock_db = MagicMock()
mock_db.execute.return_value.fetchone.return_value = None
with patch("app.services.digest_service.psycopg2.connect") as mock_connect:
    mock_connect.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_connect.return_value.__exit__ = MagicMock(return_value=False)
    stats = _collect_digest_stats(agent_id="fake-id", conn_str="fake", db=mock_db)
```

---

### `apps/api/tests/unit/test_alert_service.py` (modify — de-xfail 3 stubs)

**Analog:** `apps/api/tests/unit/test_deployment_task.py`

**Env-var safety header** — same block as test_deployment_task.py lines 26-34 (existing stub has partial header, add missing vars).

**Direct db injection — NO `get_sync_db` patch** (RESEARCH.md verified: alert_service.py does not import get_sync_db):
```python
from unittest.mock import MagicMock
from app.services.alert_service import check_and_write_alerts

def test_eval_regression_triggers_alert():
    agent_id = "test-agent-id"
    mock_db = MagicMock()
    # _active_alert_exists uses db.execute(...).fetchone() — None = no existing alert
    mock_db.execute.return_value.fetchone.return_value = None

    result = check_and_write_alerts(
        agent_id=agent_id,
        faithfulness=0.4,            # below default threshold 0.6
        critical_red_team_count=0,
        db=mock_db,
    )

    mock_db.add.assert_called_once()
    added = mock_db.add.call_args[0][0]
    assert added.alert_type == "eval_regression"
```

The existing stubs patch `app.services.alert_service.get_sync_db` — this patch target does not exist in that module. Remove all `patch("app.services.alert_service.get_sync_db")` calls. Remove all `patch("app.core.config.settings")` whole-object patches. Pass `db=mock_db` directly.

**Real settings values** — `ALERT_FAITHFULNESS_THRESHOLD=0.6`, `ALERT_RED_TEAM_CRITICAL_COUNT=1` match the default config. Tests do not need to override settings:
- `faithfulness=0.4` → below 0.6 → triggers eval_regression
- `critical_red_team_count=2` → >= 1 → triggers red_team_critical
- `faithfulness=0.95, critical_red_team_count=0` → neither threshold crossed → no alert

**`_write_alert` mock chain** (`db.add`, `db.commit`, `db.refresh` all called — plain `MagicMock()` handles all three without setup):
```python
mock_db = MagicMock()
mock_db.execute.return_value.fetchone.return_value = None  # no existing active alert
# db.add(), db.commit(), db.refresh() will all be auto-handled by MagicMock
```

---

### `apps/api/tests/unit/test_observability_routes.py` (modify — de-xfail 2 stubs)

**Analog:** `apps/api/tests/unit/test_deployment_routes.py`

**The existing stubs are already correctly written** per RESEARCH.md. The only change needed is removing `@pytest.mark.xfail(strict=True, ...)` decorators.

**ASGITransport + dependency_overrides pattern** (test_deployment_routes.py lines 137-149):
```python
app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
app.dependency_overrides[get_async_db] = lambda: mock_db

try:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/api/v1/agents/{agent_id}/checklist-runs/{run_id}",
            headers={"X-API-Key": "vrd_live_test"},
        )
finally:
    app.dependency_overrides.clear()
```
The existing observability stubs (lines 51-63, 72-103) already follow this pattern exactly.

**IDOR mock setup** (test_deployment_routes.py lines 316-323, for the "blocked" analog):
```python
# IDOR: attacker_tenant_id != agent.tenant_id → 403
mock_tenant.id = attacker_tenant_id  # different from agent's owner
mock_agent.tenant_id = owner_tenant_id
# Route returns HTTPException(status_code=403)
assert resp.status_code in (401, 403)
```
The existing stub at test_observability_routes.py lines 78-88 already implements this correctly.

---

### `scripts/demo_m10.sh` (new)

**Analog:** `scripts/demo_m9.sh`

**Pipefail header** (demo_m9.sh line 37):
```bash
set -euo pipefail
```
Exactly once, near top of file, after the comment block.

**Config + env guard block** (demo_m9.sh lines 43-61):
```bash
BASE_URL="${BASE_URL:-http://localhost:8000}"
ADMIN_KEY="${ADMIN_KEY:-}"
API_KEY="${API_KEY:-}"

if [[ -z "$ADMIN_KEY" ]]; then
    echo "ERROR: ADMIN_KEY env var is required."
    echo "  Usage: ADMIN_KEY=<admin-key> API_KEY=<tenant-key> bash scripts/demo_m10.sh"
    exit 1
fi

if [[ -z "$API_KEY" ]]; then
    echo "ERROR: API_KEY env var is required."
    echo "  Usage: ADMIN_KEY=<admin-key> API_KEY=<tenant-key> bash scripts/demo_m10.sh"
    exit 1
fi
```

**Prerequisites check pattern** (demo_m9.sh lines 74-87):
```bash
if ! redis-cli ping >/dev/null 2>&1; then
    echo "ERROR: Redis is not reachable. Start with: redis-server"
    exit 1
fi
echo "  [OK] Redis reachable (redis-cli ping)"

if ! curl -sf --max-time 5 "$BASE_URL/health" >/dev/null 2>&1; then
    echo "ERROR: FastAPI not reachable at $BASE_URL/health"
    echo "  Start with: cd apps/api && uvicorn app.main:app --reload"
    exit 1
fi
echo "  [OK] FastAPI reachable ($BASE_URL/health)"
```

**Python JSON extraction one-liner** (demo_m9.sh line 118 — use `python`, NOT `python3`):
```bash
AGENT_ID=$(echo "$AGENT_RESPONSE" | python -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null || echo "")
```

**Agent create + poll loop** (demo_m9.sh lines 100-142):
```bash
AGENT_RESPONSE=$(curl -sf -X POST "$BASE_URL/api/v1/agents" \
    -H "X-API-Key: $API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"name": "M10 Demo Agent", "soul": {}, "role": "support"}' 2>/dev/null || echo "")

AGENT_ID=$(echo "$AGENT_RESPONSE" | python -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null || echo "")

for i in $(seq 1 24); do
    AGENT_STATUS=$(curl -sf --max-time 5 \
        -H "X-API-Key: $API_KEY" \
        "$BASE_URL/api/v1/agents/$AGENT_ID" 2>/dev/null | \
        python -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','unknown'))" 2>/dev/null || echo "unknown")
    echo "  [Poll $i/24] status: $AGENT_STATUS"
    if [[ "$AGENT_STATUS" == "ready" ]]; then break; fi
    sleep 5
done
```

**Assertions + ALL_PASSED pattern** (demo_m9.sh lines 534-605):
```bash
ALL_PASSED=true

ALERTS_STATUS=$(curl -sf -o /dev/null -w "%{http_code}" \
    -H "X-API-Key: $API_KEY" \
    "$BASE_URL/api/v1/agents/$AGENT_ID/alerts" 2>/dev/null || echo "000")

if [[ "$ALERTS_STATUS" == "200" ]]; then
    echo "[PASS] OPS-04: alerts endpoint returns 200"
else
    echo "[FAIL] OPS-04: alerts endpoint returned $ALERTS_STATUS"
    ALL_PASSED=false
fi

if [[ "$ALL_PASSED" == "true" ]]; then
    echo "=== M10 Demo: PASSED ==="
    exit 0
else
    echo "=== M10 Demo: FAILED ==="
    exit 1
fi
```

**Celery task trigger one-liner** (demo_m10.sh context — via `python`, not python3):
```bash
python -c "
import django; import os; os.chdir('apps/api')
from app.worker.tasks.runtime.alert import run_alert_check
run_alert_check.apply_async(kwargs={'agent_id': '$AGENT_ID'})
" 2>/dev/null || true
```
Note: Must be run from `apps/api/` directory so imports resolve. Alternative: `run_alert_check.run(agent_id='$AGENT_ID')` for synchronous in-process execution.

**Beat verification pattern** (demo_m10.sh Section 5):
```bash
BEATS_OUTPUT=$(cd apps/api && celery -A app.worker.celery_app inspect registered 2>/dev/null || echo "")
if echo "$BEATS_OUTPUT" | grep -q "digest-weekly" && echo "$BEATS_OUTPUT" | grep -q "alert-daily"; then
    echo "[PASS] OPS-02/OPS-04: beats registered"
else
    echo "[FAIL] OPS-02/OPS-04: beats not found in celery inspect"
    ALL_PASSED=false
fi
```

---

### `apps/api/tests/e2e/test_observability_e2e.py` (new)

**Analog:** `apps/api/tests/e2e/test_strategy_e2e.py`

**Guard pattern** (test_strategy_e2e.py lines 30-38):
```python
STRATEGY_E2E_ENABLED = os.environ.get("STRATEGY_E2E_ENABLED", "0") == "1"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not STRATEGY_E2E_ENABLED,
        reason="STRATEGY_E2E_ENABLED=1 required for real strategist E2E test",
    ),
]
```
For observability, rename to `OPS_E2E_ENABLED`:
```python
OPS_E2E_ENABLED = os.environ.get("OPS_E2E_ENABLED", "0") == "1"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not OPS_E2E_ENABLED,
        reason="OPS_E2E_ENABLED=1 required",
    ),
]
```

**Synchronous httpx pattern** (test_strategy_e2e.py lines 119-124):
```python
resp = httpx.get(
    f"{base_url}/api/v1/agents/{agent_id}",
    headers={"X-API-Key": api_key},
    timeout=15,
)
assert resp.status_code == 200
```
E2E tests use synchronous `httpx.get()` / `httpx.post()` — NOT `async def` test functions, NOT `AsyncClient`. This matches the existing E2E test style throughout.

**Env-var pattern** (test_strategy_e2e.py lines 56-58):
```python
agent_id = os.environ["STRATEGY_E2E_AGENT_ID"]
api_key = os.environ["STRATEGY_E2E_API_KEY"]
base_url = os.environ.get("STRATEGY_E2E_BASE_URL", "http://localhost:8000")
```
For observability (module-level, not inside test functions):
```python
BASE_URL = "http://localhost:8000/api/v1"
AGENT_ID = os.environ.get("OPS_E2E_AGENT_ID", "")
API_KEY = os.environ.get("OPS_E2E_API_KEY", "")
HEADERS = {"X-API-Key": API_KEY}
```

**Conditional skip inside test** (test_strategy_e2e.py lines 245-247):
```python
if not alerts:
    pytest.skip("No active alerts — cannot test resolve roundtrip")
```

---

## Shared Patterns

### Clerk Bearer Auth (admin UI client components)
**Source:** `apps/admin/app/agents/[id]/page.tsx` lines 65-77
**Apply to:** `AlertsBanner.tsx` and any future client components that call the FastAPI backend
```tsx
const { getToken, isLoaded, isSignedIn } = useAuth()
const apiBase = process.env.NEXT_PUBLIC_API_BASE || ''

// In async fetch functions:
const token = await getToken()
if (!token) return  // or throw new Error('Not authenticated')
const r = await fetch(`${apiBase}/api/v1/agents/${id}/some-endpoint`, {
  headers: { Authorization: `Bearer ${token}` },
})
```
**NEVER use** `X-API-Key` header or an `apiKey` prop in client components.

### `_make_sync_db_ctx` Helper (Celery task unit tests)
**Source:** `apps/api/tests/unit/test_deployment_task.py` lines 46-52
**Apply to:** Any test file that patches `get_sync_db` used inside a Celery task
```python
from contextlib import contextmanager

def _make_sync_db_ctx(mock_db):
    @contextmanager
    def _fake_get_sync_db():
        yield mock_db
    return _fake_get_sync_db
```
Usage:
```python
with patch("app.worker.tasks.runtime.digest.get_sync_db", _make_sync_db_ctx(mock_db)):
    result = run_weekly_digest.run(agent_id=agent_id)
```

### Env-Var Safety Header (unit test files)
**Source:** `apps/api/tests/unit/test_deployment_task.py` lines 26-34
**Apply to:** All unit test files in `apps/api/tests/unit/`
```python
import os
import base64

os.environ.setdefault("NEON_API_KEY", "test_neon_key")
os.environ.setdefault("NEON_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
os.environ.setdefault("CONTROL_DB_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("CONTROL_DB_SYNC_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("ADMIN_KEY", "test_admin_key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("VOYAGE_API_KEY", "test_voyage_key")
os.environ.setdefault("JWT_SECRET", "test_jwt_secret")
os.environ.setdefault("CLERK_WEBHOOK_SIGNING_SECRET", "test_clerk_secret")
```

### ASGITransport Route Test (async route tests)
**Source:** `apps/api/tests/unit/test_deployment_routes.py` lines 137-149
**Apply to:** All route test files that test FastAPI endpoints
```python
app.dependency_overrides[get_current_tenant] = lambda: fake_tenant
app.dependency_overrides[get_async_db] = lambda: mock_db

try:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/...", headers={"X-API-Key": "test-key"})
finally:
    app.dependency_overrides.clear()
```

### demo_m10.sh No-Docker Rule
**Source:** `scripts/demo_m9.sh` (comment block lines 8-11)
**Apply to:** `scripts/demo_m10.sh`
```bash
# Prerequisites (ALL local — no Docker):
#   1. Redis:    redis-server (running on localhost:6379)
#   2. Postgres: local install (running on localhost:5432)
#   3. API:      uvicorn app.main:app --reload       (from apps/api/)
#   4. Worker:   celery -A app.worker.celery_app worker --queues pipeline,runtime
```
Never use `docker-compose` or container references. All processes are local.

---

## Critical Override Notes

### 1. AlertsBanner auth: UI-SPEC overrides PLAN
The `10-04-PLAN.md` code template uses `apiKey` prop and `X-API-Key` header. This is **incorrect**. The `10-UI-SPEC.md` and `page.tsx` both confirm the correct pattern is `useAuth().getToken()` with `Authorization: Bearer ${token}`. The plan template must be ignored — use the UI-SPEC and the `page.tsx` analog exclusively.

### 2. Alert service test: no `get_sync_db` patch
The existing stubs in `test_alert_service.py` patch `app.services.alert_service.get_sync_db` — this path does not exist in `alert_service.py`. All three alert tests must be rewritten to pass `db=mock_db` as a direct argument to `check_and_write_alerts()`.

### 3. Digest idempotency: `fetchone` not `scalar_one_or_none`
The existing stub mocks `scalar_one_or_none` but the actual `digest.py` task calls `db.execute(...).fetchone()`. Use `mock_db.execute.return_value.fetchone.return_value = MagicMock()`.

### 4. SMTP patch path: module-boundary
Patch `"app.services.digest_service.smtplib.SMTP"`, not `"smtplib.SMTP"`. The existing stub patches at the wrong level.

### 5. `python` not `python3` in demo script
All `python -c "..."` one-liners in `demo_m10.sh` must use `python`, matching `demo_m9.sh` exactly. This is a checked must-have in `10-06-PLAN.md`.

---

## No Analog Found

All 7 files have analogs. No entries in this section.

---

## Metadata

**Analog search scope:** `apps/admin/app/agents/`, `apps/api/tests/unit/`, `apps/api/tests/e2e/`, `scripts/`
**Files scanned:** 11 (page.tsx, test_deployment_task.py, test_deployment_routes.py, test_strategy_task.py, test_strategy_e2e.py, demo_m9.sh, test_alert_service.py, test_digest_service.py, test_observability_routes.py, 10-RESEARCH.md, 10-UI-SPEC.md)
**Pattern extraction date:** 2026-05-25
