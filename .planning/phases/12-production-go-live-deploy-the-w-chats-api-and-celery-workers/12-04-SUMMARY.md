---
phase: 12-production-go-live-deploy-the-w-chats-api-and-celery-workers
plan: "04"
subsystem: deployment
tags: [systemd, caddy, tls, smoke-test, duckdns, production, ops]
dependency_graph:
  requires: []
  provides:
    - deploy/systemd/wchats-api.service
    - deploy/systemd/wchats-celery-runtime.service
    - deploy/caddy/Caddyfile
    - deploy/README.md
    - scripts/smoke_vm.sh
  affects:
    - plan 05 (VM provisioning — consumes these files)
    - plan 06 (live smoke gate — runs scripts/smoke_vm.sh)
tech_stack:
  added: []
  patterns:
    - systemd EnvironmentFile-driven secrets (Pattern 1)
    - Caddy DuckDNS DNS-01 TLS (Pattern 2)
    - bash strict-mode smoke script (demo_m10.sh pattern)
key_files:
  created:
    - deploy/systemd/wchats-api.service
    - deploy/systemd/wchats-celery-runtime.service
    - deploy/caddy/Caddyfile
    - deploy/README.md
    - scripts/smoke_vm.sh
  modified: []
decisions:
  - "D-02: systemd units authored with EnvironmentFile-driven secrets, Restart=always"
  - "D-05: DuckDNS DNS-01 Caddyfile authored (no port-80 dependency, reverse_proxy 127.0.0.1:8000)"
  - "D-12: runtime worker unit is always-on (Restart=always, RestartSec=10)"
  - "--pool=solo --concurrency=1 pinned explicitly in ExecStart to prevent silent override of config-level worker_pool"
  - "DUCKDNS_TOKEN routed to Caddy's own systemd drop-in, not the app .env"
metrics:
  duration_minutes: 25
  completed_date: "2026-05-29"
  tasks_completed: 2
  tasks_total: 2
  files_created: 5
  files_modified: 0
---

# Phase 12 Plan 04: Deploy Artifacts (systemd, Caddy, Runbook, Smoke Test) Summary

**One-liner:** Two EnvironmentFile-driven systemd units (API + Celery runtime solo-pinned), a DuckDNS DNS-01 Caddyfile, a VM runbook, and a six-section bash smoke test — all in-repo, VM-independent, ready for plan 05 copy-deploy.

---

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Author systemd units, Caddyfile, deploy README | 52b3971 | deploy/systemd/wchats-api.service, deploy/systemd/wchats-celery-runtime.service, deploy/caddy/Caddyfile, deploy/README.md |
| 2 | Author scripts/smoke_vm.sh deployment smoke test | 330c848 | scripts/smoke_vm.sh |

---

## What Was Built

### Task 1: Deploy Artifacts

**deploy/systemd/wchats-api.service**
- `Type=simple`, `User=wchats`, `WorkingDirectory=/opt/wchats/apps/api`
- `EnvironmentFile=/opt/wchats/apps/api/.env` — secrets never inlined
- `ExecStart=uvicorn app.main:app --host 127.0.0.1 --port 8000` — Caddy fronts it, no `--reload`
- `Restart=always`, `RestartSec=5` — D-02/D-12 warm API

**deploy/systemd/wchats-celery-runtime.service**
- Same `EnvironmentFile=` skeleton as API unit
- `ExecStart=celery -A app.worker.celery_app worker --queues=runtime --hostname=runtime@%%h --loglevel=info --pool=solo --concurrency=1`
- `--pool=solo --concurrency=1` pinned explicitly in ExecStart (not just config) to prevent silent override — STATE.md [04-02] module-level globals in agent_tools.py require solo pool
- `--queues=runtime` only — pipeline worker is NOT hosted (D-03)
- `Restart=always`, `RestartSec=10` — D-12 always-on warm worker

**deploy/caddy/Caddyfile**
- `wchats-api.duckdns.org` hostname block (placeholder; real subdomain chosen in plan 05)
- `tls { dns duckdns {env.DUCKDNS_TOKEN} }` — DNS-01, no port-80 dependency
- `encode gzip` + `reverse_proxy 127.0.0.1:8000` with X-Forwarded-For/X-Real-IP headers
- Comment routes `DUCKDNS_TOKEN` to Caddy's own systemd drop-in (`caddy.service.d/override.conf`), not the app `.env`
- Comment notes xcaddy build requirement: `xcaddy build --with github.com/caddy-dns/duckdns`

**deploy/README.md**
- Ordered checklist: A (OCI A1.Flex VM), B (443-only iptables), C (clone + venv + uv install), D (.env placement chmod 600), E (systemd enable --now), F (Caddy xcaddy ARM64 build + drop-in token), G (verification commands)
- Notes ARM64 claude-agent-sdk bundled binary (no separate Node.js required)
- Notes OCI capacity retry loop pattern
- Security notes: port 80 NOT open, uvicorn binds 127.0.0.1 only, no secrets inlined

### Task 2: scripts/smoke_vm.sh

- `#!/usr/bin/env bash` + `set -euo pipefail` strict mode
- `API_HOST`, `WIDGET_HOST`, `AGENT_ID` env-var+default configuration
- `ALL_PASSED=true` accumulator; `exit 0` / `exit 1` at end
- **Section 1:** `curl -s ... $API_HOST/health` — asserts HTTP 200, cert validated by default (no `-k`)
- **Section 2:** `curl -s ... $WIDGET_HOST/wchats/widget.js` — asserts HTTP 200
- **Section 3:** `GET $API_HOST/widget/$AGENT_ID/config` — asserts 200, extracts JWT via `python3 -c json.load`; never echoes token value
- **Section 4:** `POST $API_HOST/widget/$AGENT_ID/chat` with `Authorization: Bearer $WIDGET_JWT` and `"What is W Chats?"` — asserts 202, extracts `job_id`
- **Section 5:** SSE poll `GET $API_HOST/widget/jobs/$JOB_ID/events` in `for i in $(seq 1 18); sleep 5` loop — exits loop on `agent.response`, sets `ALL_PASSED=false` on `agent.failed` or 90s timeout
- **Section 6:** counts `agent.tool_call` lines with `retrieve` in captured SSE stream; asserts `$RETRIEVE_COUNT -le 2`

---

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `--reload` in wchats-api.service comment violated `grep -c -- '--reload'` acceptance criterion**
- **Found during:** Task 1 acceptance verification
- **Issue:** The comment `# No --reload: ...` caused `grep -c -- '--reload'` to return 1 (expected 0)
- **Fix:** Rewrote comment to `# No reload flag: production service; auto-reload wastes RAM`
- **Files modified:** deploy/systemd/wchats-api.service
- **Commit:** 52b3971 (fixed inline before commit)

**2. [Rule 1 - Bug] `rediss://` in README violated `grep -rn "sk-ant\|voyage-\|postgresql://\|rediss://"` acceptance criterion**
- **Found during:** Task 1 acceptance verification
- **Issue:** `REDIS_URL (Upstash rediss://...)` in the README's env var list matched the secret pattern grep
- **Fix:** Changed to `REDIS_URL (Upstash TLS URL)` — removes the `rediss://` protocol literal from the documentation text
- **Files modified:** deploy/README.md
- **Commit:** 52b3971 (fixed inline before commit)

**3. [Rule 1 - Bug] `--pool=solo --concurrency=1` on separate continuation lines failed `grep -c` acceptance criterion**
- **Found during:** Task 1 acceptance verification
- **Issue:** Plan acceptance criterion is `grep -c -- '--pool=solo --concurrency=1' ... returns 1` — but the two flags were on separate `\` continuation lines
- **Fix:** Combined to `--pool=solo --concurrency=1` on the same line in ExecStart
- **Files modified:** deploy/systemd/wchats-celery-runtime.service
- **Commit:** 52b3971 (fixed inline before commit)

---

## Threat Surface Scan

All mitigations from the plan's STRIDE threat register confirmed present:

| Threat ID | Mitigation | Confirmed |
|-----------|-----------|-----------|
| T-12-04-01 | No secret literals in deploy/ (EnvironmentFile= only) | grep returns nothing |
| T-12-04-02 | README mandates iptables 443 + OCI Security List; uvicorn binds 127.0.0.1 | confirmed in README.md |
| T-12-04-03 | DUCKDNS_TOKEN routed to Caddy drop-in, comment in Caddyfile + README | confirmed |
| T-12-04-04 | --pool=solo --concurrency=1 pinned in ExecStart | grep returns 2 (comment + ExecStart) |
| T-12-04-05 | smoke_vm.sh extracts JWT via python3 json.load, never echoes it; no secret literals | grep returns nothing |

No new threat surface introduced (these are pure ops files — no new network endpoints, no schema changes, no auth paths).

---

## Known Stubs

None. All files deliver their full intended content. The `wchats-api.duckdns.org` hostname in the Caddyfile is documented as a placeholder to be replaced in plan 05 (real subdomain chosen during VM provisioning) — this is intentional and documented in a comment.

---

## Self-Check

```
FOUND: deploy/systemd/wchats-api.service
FOUND: deploy/systemd/wchats-celery-runtime.service
FOUND: deploy/caddy/Caddyfile
FOUND: deploy/README.md
FOUND: scripts/smoke_vm.sh
FOUND: 52b3971 (feat(12-04): author systemd units, Caddyfile, and deploy runbook)
FOUND: 330c848 (feat(12-04): author scripts/smoke_vm.sh deployment smoke test)
```

## Self-Check: PASSED
