# Phase 12: Production Go-Live (W Chats) - Pattern Map

**Mapped:** 2026-05-29
**Files analyzed:** 8 (2 in-repo code changes, 6 new ops/deploy artifacts)
**Analogs found:** 7 / 8 (Caddyfile is greenfield)

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `apps/api/app/worker/tasks/runtime/agent.py` | service/task (MODIFY) | request-response | self (existing file) | exact — editing live file |
| `apps/api/tests/unit/test_agent_task.py` | test (EXTEND) | — | self (existing file, 527 lines) | exact — appending to live test file |
| `deploy/systemd/wchats-api.service` | config | request-response | `scripts/start_native.ps1` (uvicorn flags) | role-match |
| `deploy/systemd/wchats-celery-runtime.service` | config | event-driven | `scripts/start_native.ps1` (celery runtime flags) | role-match |
| `/etc/caddy/Caddyfile` | config | request-response | none | greenfield |
| `scripts/smoke_vm.sh` | utility/script | request-response | `scripts/demo_m10.sh` | role-match (bash strict mode + curl + SSE poll) |
| `docs/adr/0001-cloud-native-cutover.md` | documentation | — | none in repo | greenfield (use Nygard format per RESEARCH.md) |
| `apps/admin/public/wchats/{widget.js,index.html,widget.iife.js,widget.css}` | static assets (COPY) | — | `apps/widget/embed/` (source of truth) | exact — copy operation |

---

## Pattern Assignments

### `apps/api/app/worker/tasks/runtime/agent.py` (MODIFY — D-10 + D-11)

**Analog:** Self. All edits are surgical two-line changes inside the existing function body.

**Current `ClaudeAgentOptions` construction** (lines 511–524 — verified by RESEARCH.md):

```python
# apps/api/app/worker/tasks/runtime/agent.py  lines 511-524
options = ClaudeAgentOptions(
    model="claude-haiku-4-5-20251001",
    system_prompt=system_prompt,
    mcp_servers={"customer-tools": tool_server},
    allowed_tools=[
        "mcp__customer-tools__retrieve",
        "mcp__customer-tools__lookup_structured",
        "mcp__customer-tools__escalate_to_human",
        "mcp__customer-tools__clarify",
    ],
    resume=sdk_resume,
    max_turns=10,          # <-- D-10: change to 3
    max_budget_usd=0.05,
)
```

**D-10 change:** `max_turns=10` → `max_turns=3`.

**D-10 retrieve-cap system-prompt instruction** — append to `system_prompt` string BEFORE the `ClaudeAgentOptions(...)` call. The insertion point is after `system_prompt = build_system_prompt(agent)` at line 508. Pattern mirrors how `agent_prompt.py` lines 81–107 build the final string:

```python
# Insert immediately after line 508:  system_prompt = build_system_prompt(agent)
system_prompt += (
    "\n\nIMPORTANT: Call the `retrieve` tool AT MOST ONCE per response. "
    "After receiving retrieve results, synthesize an answer immediately. "
    "Do not call retrieve again."
)
```

**Current wall-clock guard** (lines 532–545 — verified by RESEARCH.md):

```python
# apps/api/app/worker/tasks/runtime/agent.py  lines 532-545
result = asyncio.run(
    asyncio.wait_for(
        _run_sdk_turn(
            message=message,
            options=options,
            job_id=job_id,
            local_conversation_id=local_conversation_id,
            conn_str=conn_str,
            db=db,
            redis=_redis,
        ),
        timeout=30,        # <-- D-11: change to 90
    )
)
```

**D-11 change:** `timeout=30` → `timeout=90`.

**No other changes needed:** SSE guard is `asyncio.timeout(120)` at `widget.py:507` (30s headroom). Celery `visibility_timeout=3600` at `celery_app.py:~158` is unaffected.

---

### `apps/api/tests/unit/test_agent_task.py` (EXTEND — two new tests for D-10 + D-11)

**Analog:** Self — the file exists at 527 lines and already contains 8 tests. The new tests extend it using the exact same patterns already established in the file.

**SDK monkeypatch pattern** (lines 1–69 of the existing file — must not be duplicated; tests just import after it):

```python
# Already at top of file — DO NOT re-add. New tests just import the task module.
if "claude_agent_sdk" not in sys.modules:
    sys.modules["claude_agent_sdk"] = _make_fake_claude_agent_sdk()
```

**asyncio.run mock boundary** — the established pattern mocks `app.worker.tasks.runtime.agent.asyncio.run` with a canned dict, never touching the real SDK. New tests for D-10/D-11 follow this boundary exactly.

**`ClaudeAgentOptions` capture pattern** (lines 287–303 — test 4 in existing file) — used to assert constructor kwargs. The new D-10 test must capture `max_turns`:

```python
# Pattern from existing test_subsequent_turn_resumes_with_stored_sdk_session_id
options_kwargs_captured: list[dict] = []

class FakeClaudeAgentOptions:
    def __init__(self, **kwargs):
        options_kwargs_captured.append(kwargs)

with (
    patch("app.worker.tasks.runtime.agent.ClaudeAgentOptions", side_effect=FakeClaudeAgentOptions),
    patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=_CANNED_RESULT_WITH_CITATION),
    # ... other patches matching the happy-path test
):
    run_agent_turn.run(...)

assert options_kwargs_captured[0]["max_turns"] == 3
```

**Standard patch stack** (from any happy-path test, e.g. lines 228–244):

```python
with (
    patch("app.worker.tasks.runtime.agent.get_sync_db", return_value=_make_db_ctx(mock_db)),
    patch("app.worker.tasks.runtime.agent.fernet_decrypt", return_value="postgresql://tenant"),
    patch("app.worker.tasks.runtime.agent._create_conversation_row", return_value=local_conv_id),
    patch("app.worker.tasks.runtime.agent._set_sdk_session_id"),
    patch("app.worker.tasks.runtime.agent._persist_messages"),
    patch("app.worker.tasks.runtime.agent.build_tool_server", return_value=MagicMock()),
    patch("app.worker.tasks.runtime.agent.build_system_prompt", return_value="sys prompt"),
    patch("app.worker.tasks.runtime.agent.asyncio.run", return_value=_CANNED_RESULT_WITH_CITATION),
    patch("app.worker.tasks.runtime.agent.emit"),
):
```

**D-11 timeout test approach** — `asyncio.wait_for` is called INSIDE `asyncio.run`, and both are patched at the `asyncio.run` boundary. To assert the timeout value the test must NOT mock `asyncio.run`; instead patch `asyncio.wait_for` directly and capture its `timeout` kwarg:

```python
import asyncio as _asyncio

wait_for_kwargs: list[dict] = []
original_wait_for = _asyncio.wait_for

async def fake_wait_for(coro, timeout):
    wait_for_kwargs.append({"timeout": timeout})
    # return a coroutine that yields the canned result
    coro.close()
    return _CANNED_RESULT_WITH_CITATION

with (
    patch("app.worker.tasks.runtime.agent.asyncio.wait_for", side_effect=fake_wait_for),
    # keep asyncio.run real so it drives the coroutine:
    # ... other patches as per standard stack
):
    run_agent_turn.run(...)

assert wait_for_kwargs[0]["timeout"] == 90
```

**Helper factories** (already in file — reuse, do not duplicate):
- `_make_agent()` — line 76
- `_make_job()` — line 91
- `_make_db_ctx()` — line 98

---

### `deploy/systemd/wchats-api.service` (CREATE)

**Analog:** `scripts/start_native.ps1` — the canonical local process startup. Translates Windows PowerShell process invocation to Linux systemd unit. The uvicorn command at `start_native.ps1:52` is the authoritative source of flags.

**Source command from analog** (`start_native.ps1` line 52):

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Linux systemd translation** (from RESEARCH.md Pattern 1 — use these exact settings):

```ini
# deploy/systemd/wchats-api.service
[Unit]
Description=W Chats FastAPI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=wchats
WorkingDirectory=/opt/wchats/apps/api
EnvironmentFile=/opt/wchats/apps/api/.env
Environment=PYTHONPATH=/opt/wchats/apps/api
ExecStart=/opt/wchats/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**Key differences from start_native.ps1:**
- `--host 127.0.0.1` (not `0.0.0.0`) — Caddy sits in front; uvicorn does NOT need to be public-facing on the VM.
- No `--reload` — production service; reload wastes RAM on a constrained VM.
- `EnvironmentFile=` loads secrets from `/opt/wchats/apps/api/.env` (chmod 600, not checked in).
- `Restart=always` + `RestartSec=5` replaces the Start-Process restart behavior.

**Repo location:** `deploy/systemd/wchats-api.service` (create `deploy/systemd/` directory — does not exist yet).

---

### `deploy/systemd/wchats-celery-runtime.service` (CREATE)

**Analog:** `scripts/start_native.ps1` — celery runtime worker at line 58.

**Source command from analog** (`start_native.ps1` line 58):

```powershell
celery -A app.worker.celery_app worker --queues=runtime --hostname=runtime@%h --loglevel=info --pool=solo
```

**Linux systemd translation** (from RESEARCH.md Pattern 1):

```ini
# deploy/systemd/wchats-celery-runtime.service
[Unit]
Description=W Chats Celery Runtime Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=wchats
WorkingDirectory=/opt/wchats/apps/api
EnvironmentFile=/opt/wchats/apps/api/.env
Environment=PYTHONPATH=/opt/wchats/apps/api
ExecStart=/opt/wchats/venv/bin/celery -A app.worker.celery_app worker \
    --queues=runtime \
    --hostname=runtime@%%h \
    --loglevel=info \
    --pool=solo \
    --concurrency=1
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Critical constraint:** `--pool=solo --concurrency=1` MUST be explicit in ExecStart. `celery_app.py` sets `worker_pool="solo"` in config, but CLI flags must make it unambiguous — `agent_tools.py` module-level globals (`_conn_str`, `_agent_id`) are only safe for the solo pool (STATE.md [04-02]). `%%h` is the systemd-escaped form of `%h`.

**`RestartSec=10`** (vs 5 for API) — worker gets longer restart interval to allow the SDK subprocess to clean up between restarts.

---

### `/etc/caddy/Caddyfile` (CREATE — greenfield)

**Analog:** None in codebase. No existing reverse-proxy config. Greenfield per ops/TLS domain.

**Canonical source:** RESEARCH.md Pattern 2 (Caddy DuckDNS DNS-01 — verified against Caddy docs + caddy-dns/duckdns module).

**File content:**

```caddyfile
# /etc/caddy/Caddyfile
# Requires Caddy built with caddy-dns/duckdns plugin:
#   xcaddy build --with github.com/caddy-dns/duckdns
# DUCKDNS_TOKEN must be in Caddy's environment (add to /etc/systemd/system/caddy.service.d/override.conf)

wchats-api.duckdns.org {
    tls {
        dns duckdns {env.DUCKDNS_TOKEN}
    }
    encode gzip
    reverse_proxy 127.0.0.1:8000 {
        header_up X-Forwarded-For {remote_host}
        header_up X-Real-IP {remote_host}
    }
}
```

**Note on `{env.DUCKDNS_TOKEN}`:** This is Caddy's env var interpolation syntax. `DUCKDNS_TOKEN` is NOT in the app `.env` file — it belongs in Caddy's own environment. Add a systemd drop-in at `/etc/systemd/system/caddy.service.d/override.conf` with `Environment=DUCKDNS_TOKEN=<token>`.

**Oracle firewall prerequisite** (not in the Caddyfile, but must accompany it):

```bash
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
sudo apt install iptables-persistent && sudo netfilter-persistent save
```

---

### `scripts/smoke_vm.sh` (CREATE)

**Analog:** `scripts/demo_m10.sh` — the closest existing bash script (strict mode, curl checks, SSE polling, env-var configuration, exit codes). Shared structure with `demo_m9.sh` (both are no-Docker, pure-bash, same boilerplate).

**Strict mode header** (from `demo_m10.sh` line 27 + `demo_m9.sh` line 37):

```bash
#!/usr/bin/env bash
set -euo pipefail
```

**Env-var + default pattern** (from `demo_m10.sh` lines 32–35 and `demo_m9.sh` lines 43–48):

```bash
BASE_URL="${BASE_URL:-http://localhost:8000}"
ADMIN_KEY="${ADMIN_KEY:-}"
API_KEY="${API_KEY:-}"
```

For smoke_vm.sh the equivalent is:

```bash
API_HOST="${API_HOST:-https://wchats-api.duckdns.org}"
WIDGET_HOST="${WIDGET_HOST:-https://bantuson.vercel.app}"
AGENT_ID="${AGENT_ID:-fe230a9d-09f0-4043-b2f1-4506a2ef0059}"
```

**Env-var validation guard** (from `demo_m10.sh` lines 40–51 — copy pattern):

```bash
if [[ -z "$API_HOST" ]]; then
    echo "ERROR: API_HOST env var is required."
    echo "  Usage: API_HOST=https://wchats-api.duckdns.org bash scripts/smoke_vm.sh"
    exit 1
fi
```

**curl health check pattern** (from `demo_m10.sh` lines 70–78):

```bash
if ! curl -sf --max-time 5 "$API_HOST/health" >/dev/null 2>&1; then
    echo "ERROR: FastAPI not reachable at $API_HOST/health"
    exit 1
fi
echo "  [OK] API health ($API_HOST/health)"
```

**HTTP status code assertion pattern** (from `demo_m10.sh` lines 222–234):

```bash
STATUS=$(curl -sf -o /dev/null -w "%{http_code}" --max-time 10 "$URL" 2>/dev/null || echo "000")
if [[ "$STATUS" == "200" ]]; then
    echo "[PASS] ..."
else
    echo "[FAIL] ... returned $STATUS (expected 200)"
    ALL_PASSED=false
fi
```

**ALL_PASSED pattern + exit code** (from `demo_m10.sh` lines 222, 277–285):

```bash
ALL_PASSED=true
# ... assertion blocks setting ALL_PASSED=false on failure

if [[ "$ALL_PASSED" == "true" ]]; then
    echo "=== Smoke: PASSED ==="
    exit 0
else
    echo "=== Smoke: FAILED ==="
    exit 1
fi
```

**SSE poll pattern** (from `demo_m10.sh` lines 117–134 — the `for i in $(seq 1 N)` + sleep loop):

```bash
# Poll SSE endpoint for agent.response event (up to 90s)
AGENT_RESPONSE=false
for i in $(seq 1 18); do
    EVENT=$(curl -sf --max-time 5 -N \
        -H "Authorization: Bearer $WIDGET_JWT" \
        "$API_HOST/widget/jobs/$JOB_ID/events" 2>/dev/null | \
        grep '"event_type":"agent.response"' | head -1 || echo "")
    if [[ -n "$EVENT" ]]; then
        AGENT_RESPONSE=true
        break
    fi
    sleep 5
done
```

**Script sections to implement** (D-05, D-06, D-09/D-10, D-11 validation per RESEARCH.md Validation Architecture):

1. TLS health: `curl -sf https://$API_HOST/health` — asserts 200 + valid cert (curl validates by default).
2. Widget.js reachable: `curl -sf https://$WIDGET_HOST/wchats/widget.js` — asserts 200.
3. Widget JWT: `POST /widget/$AGENT_ID/config` — asserts 200, extracts `token`.
4. Chat dispatch: `POST /widget/$AGENT_ID/chat` with JWT — asserts 202, extracts `job_id`.
5. SSE poll: poll `/widget/jobs/$JOB_ID/events` until `agent.response` event — asserts received within 90s (no `agent.failed`).
6. Retrieve cap: count `agent.tool_call` lines with `retrieve` in the SSE stream — assert `<= 2`.

---

### `docs/adr/0001-cloud-native-cutover.md` (CREATE — greenfield)

**Analog:** None in repo. `docs/adr/` directory does not yet exist. Greenfield.

**Format:** Nygard ADR format (Status / Context / Decision / Consequences). RESEARCH.md D-15 resolution provides the full outline — use it verbatim as the structural template.

**File location:** `docs/adr/0001-cloud-native-cutover.md` (create `docs/` and `docs/adr/` directories).

**Nygard template fields** (standard for this format):

```markdown
# ADR-0001: <title>

**Status:** Proposed | Accepted | Deprecated | Superseded
**Date:** YYYY-MM-DD
**Deciders:** <names>

## Context
## Decision
## Consequences
```

**Required sections per D-15:**
- Target architecture: ECS Fargate, Aurora Serverless v2 + pgvector + RLS/schema-per-tenant, Aurora fast clones, Bedrock.
- Trigger threshold: ~50 tenants, $100/mo API spend, >80% VM RAM sustained 7 days, SLA requirement.
- Flip mechanism: env-only swap (D-14 seam already in place — `_find_env_file()` in `config.py`).
- Data migration note: Neon → Aurora via pg_dump/restore (separate task, not this flip).

---

### `apps/admin/public/wchats/` widget files (COPY operation)

**Source:** `apps/widget/embed/` — four files: `widget.js`, `index.html`, `widget.iife.js`, `widget.css`.

**Destination:** `apps/admin/public/wchats/` (create `wchats/` subdirectory — does not exist yet).

**Analog for directory convention:** `apps/admin/public/` — exists with `favicon.ico`, `site.webmanifest`, PNG assets, `fonts/`, `wordmark.svg`. Pattern: flat static files and subdirectories placed here are served at `https://bantuson.vercel.app/<path>` by Next.js with no config needed.

**Widget.js loader** (`apps/widget/embed/widget.js` lines 1–17) — the loader comment explains the data-api runtime wiring:

```javascript
// widget.js reads its own data-* attributes — no rebuild to change API host.
// <script src="https://bantuson.vercel.app/wchats/widget.js"
//         data-agent="fe230a9d-09f0-4043-b2f1-4506a2ef0059"
//         data-api="https://wchats-api.duckdns.org"
//         async></script>
```

**index.html** (`apps/widget/embed/index.html` lines 1–19) — iframe host page. References `./widget.css` and `./widget.iife.js` by relative path — all four files MUST be in the same directory level.

**vite.config.js build seam** (`apps/widget/vite.config.js` lines 1–16) — produces `dist/widget.iife.js` + `dist/widget.css`. Build command: `pnpm --filter veridian-widget build` (NEVER npm). After build, sync:

```bash
cp apps/widget/dist/widget.iife.js apps/widget/dist/widget.css apps/widget/embed/
cp apps/widget/embed/widget.js apps/widget/embed/index.html \
   apps/widget/embed/widget.iife.js apps/widget/embed/widget.css \
   apps/admin/public/wchats/
```

**Bundle size check** (from RESEARCH.md): `widget.iife.js` 17,833 bytes + `widget.css` 4,711 bytes = 22,544 bytes total. Well under 20 KB gzipped target. A pre-deploy size assertion in the smoke script or plan step can verify this has not regressed.

---

## Shared Patterns

### Env-Only Config (D-14)

**Source:** `apps/api/app/core/config.py` — `_find_env_file()` walks parent directories to find `.env`. The pattern that makes VM deployment a drop-in: copy `.env` to `/opt/wchats/apps/api/.env`, point systemd `EnvironmentFile=` at it, and the app reads all config identically to local dev.

**Apply to:** Both systemd units (`EnvironmentFile=` directive), Caddy service drop-in (`Environment=DUCKDNS_TOKEN=...`), and smoke_vm.sh (reads env vars, never hard-codes API keys).

**Security constraint:** `.env` on VM must be `chmod 600 wchats:wchats`. Never committed to git (already in `.gitignore`).

### Connection Strings Never in Task Args

**Source:** `apps/api/app/worker/tasks/runtime/agent.py` lines 15–19 (module docstring) + task signature at line 379.

**Pattern:** Task receives `(job_id, agent_id, message, conversation_id)` only. `conn_str` is fetched via `fernet_decrypt(agent.neon_connection_string)` at runtime inside the task body. No env vars or connection strings in Celery task kwargs.

**Apply to:** No new tasks in Phase 12, but the smoke script must not pass any conn-string-like values in curl request bodies.

### acks_late + Idempotency

**Source:** `apps/api/app/worker/tasks/runtime/agent.py` lines 371–378 (task decorator) + lines 413–421 (idempotency guard).

**Pattern:** `@celery_app.task(bind=True, acks_late=True, ...)` + early-return if `agent.response` event row already exists for the `job_id`. No changes needed for Phase 12 — this is preserved as-is.

### bash strict mode for scripts

**Source:** `scripts/demo_m10.sh` line 27 + all demo scripts.

**Pattern:** `#!/usr/bin/env bash` shebang + `set -euo pipefail` on the next line, always. `smoke_vm.sh` must follow this exactly — any unhandled error exits non-zero.

### pnpm only (no npm/yarn)

**Source:** Established project constraint (CLAUDE.md feedback). Widget rebuild: `pnpm --filter veridian-widget build`. No `npm run build`, no `yarn build`.

**Apply to:** Any plan step or smoke script step that rebuilds the widget bundle.

---

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `/etc/caddy/Caddyfile` | config | request-response | No reverse-proxy config exists in the repo. Caddy DuckDNS DNS-01 pattern sourced from RESEARCH.md (verified against Caddy docs + caddy-dns/duckdns module). |
| `docs/adr/0001-cloud-native-cutover.md` | documentation | — | No ADR files exist in the repo (`docs/adr/` directory does not exist). Use Nygard format; content fully specified in RESEARCH.md D-15 resolution. |

---

## Metadata

**Analog search scope:** `apps/api/`, `apps/widget/`, `apps/admin/public/`, `scripts/`, repo root
**Files scanned:** 12 source files read (agent.py, test_agent_task.py, start_native.ps1, demo_m10.sh, demo_m9.sh, demo_m2.sh, widget.js, index.html, vite.config.js, agent_prompt.py, embed/README.md, apps/admin/public/ listing)
**Glob searches:** `apps/api/tests/**/*.py`, `scripts/demo_*.sh`
**Pattern extraction date:** 2026-05-29
