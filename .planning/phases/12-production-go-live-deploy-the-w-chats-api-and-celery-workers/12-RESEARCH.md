# Phase 12: Production Go-Live (W Chats) - Research

**Researched:** 2026-05-29
**Domain:** Hosting / deployment — Oracle Cloud Always Free ARM, systemd, TLS/reverse-proxy, Agent SDK on ARM64, Voyage rate-cap, Vercel static delivery, ADR
**Confidence:** HIGH (most items verified against official docs or codebase; Oracle capacity workaround is MEDIUM)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Host on Oracle Cloud Always Free VM (ARM Ampere, up to 24 GB free, always-on).
- **D-02:** Run uvicorn (API) + celery runtime worker as always-on systemd services. NOT Docker. VM may use Docker if strictly easier, but native + systemd is the default.
- **D-03:** Pipeline worker is NOT hosted. Runs on-demand locally only when ingesting a new agent.
- **D-04:** Keep existing remote infra — Neon control DB (sa-east-1) + Upstash Redis. No DB/Redis rewrite.
- **D-05:** TLS required. API must be served over HTTPS. Default: reverse proxy on the VM (Caddy or nginx + Let's Encrypt) on a hostname, OR Cloudflare free proxy. Raw http IP will NOT work from the Vercel page.
- **D-06:** Serve the embed folder from Vercel `public/wchats/`. Files: `widget.js`, `index.html`, `widget.iife.js`, `widget.css`.
- **D-07:** Paste-in snippet sets `data-api` to the VM's public HTTPS base URL at runtime. No rebuild to repoint.
- **D-08:** The existing `apps/widget/embed/` files are the starting implementation — review/verify within this phase; rebuild with `pnpm --filter veridian-widget build` if widget src changes.
- **D-09:** Do NOT add a Voyage payment method or switch to a paid embedder.
- **D-10:** Cap retrieves-per-turn to ~1 (max 2) to stay within Voyage 3 RPM free tier.
- **D-11:** Raise the agent-turn wall-clock guard from `timeout=30` to ~90s.
- **D-12:** Keep the runtime worker warm (always-on systemd service).
- **D-13:** Optional: cache query embeddings (repeat queries cost no Voyage call). Planner's discretion.
- **D-14:** Env-only config — no data-layer refactor this phase.
- **D-15:** Write an ADR (`docs/adr/0001-cloud-native-cutover.md`) documenting the cloud-native AWS target and the cutover trigger threshold.

### Claude's Discretion

- Exact reverse-proxy choice (Caddy vs nginx vs Cloudflare tunnel-for-TLS), VM image/shape specifics, systemd unit layout, precise retrieve-cap mechanism.
- Whether to run a local Redis on the VM vs reuse Upstash (default reuse Upstash).

### Deferred Ideas (OUT OF SCOPE)

- AWS migration build (Fargate + Aurora Serverless v2 + RLS + Bedrock) — ADR only.
- Hosting the pipeline worker in the cloud.
- Fix M6 eval harness / M8 checklist orchestrator / M8 eval-column bug — route to `/gsd-debug`.
- Voyage paid tier or Bedrock embeddings.
- Custom domain for the API.
</user_constraints>

---

## Summary

Phase 12 deploys an already-working stack — FastAPI + Celery runtime worker — to a public, always-on, $0 host so a hiring manager can reach the W Chats widget at bantuson.vercel.app and get a live answer from agent `fe230a9d`. Nothing is being built from scratch; the work is provisioning, wiring, hardening, and publishing.

**The five high-risk items are:**

1. Oracle Cloud capacity errors (workaround: OCI CLI retry loop; credit card is required at signup but no charges occur).
2. `claude-agent-sdk==0.1.81` bundled Claude Code binary must be ARM64-compatible — confirmed via pypi.org wheels (Linux aarch64 wheel present for 0.1.81).
3. Voyage 3 RPM (3 requests/minute free tier) — the live E2E test showed 6 retrieve calls in one turn caused timeout/failure. Fix: cap `max_turns` in `ClaudeAgentOptions` AND add a system-prompt hard stop after first retrieve.
4. The 30-second wall-clock guard in `run_agent_turn` is too tight for SDK warm-up + one retrieve + Anthropic API call. Raise to 90s. The SSE `asyncio.timeout(120)` wrapper in `widget.py` is already at 120s, so it will not kill the turn before the 90s task guard fires — no change needed there.
5. `celery_app.py` forces `worker_pool="solo"` via config — this is fine on Linux (solo is safe and appropriate for a 1-OCPU single-tenant VM running one task-at-a-time), but confirm the explicit CLI flag `--pool=solo` is carried into the Linux systemd unit to prevent the config key from being silently overridden by a stale env variable.

**Primary recommendation:** Caddy + DuckDNS hostname (DNS-01 ACME challenge) as the $0 TLS approach. No port-80 dependency; survives Oracle iptables defaults. One Caddyfile line.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Widget static files delivery | Vercel CDN (static) | — | bantuson.vercel.app/wchats/ already hosts the admin Next.js app; public/ dir is zero-config |
| Chat API (REST + SSE) | VM / API tier (uvicorn) | — | FastAPI already written; just needs HTTPS reverse proxy in front |
| Agent turn execution | VM / Worker tier (Celery runtime) | — | run_agent_turn task on `runtime` queue; worker must be co-located with API on same Redis |
| TLS termination | Caddy on VM | — | Caddy terminates HTTPS, proxies to uvicorn on 127.0.0.1:8000 |
| Session state / job events | Upstash Redis (remote) | — | SSE pub/sub and rate-limit counters; no change needed |
| Tenant knowledge base | Neon (sa-east-1, remote) | — | Existing per-tenant project; no change |
| Embeddings (query only) | Voyage AI (external) | — | Only query embeds hit Voyage at runtime; corpus already embedded |
| Agent loop | claude-agent-sdk subprocess on VM | — | SDK spawns bundled Claude Code binary; needs ANTHROPIC_API_KEY env |
| Widget snippet host page | bantuson.vercel.app (hiring manager's browser) | — | Admin UI is already on Vercel; paste snippet to public/wchats/ |

---

## Standard Stack

### Core

| Component | Version / Config | Purpose | Why |
|-----------|-----------------|---------|-----|
| Oracle Cloud VM.Standard.A1.Flex | 2 OCPU / 12 GB RAM (from 4 OCPU / 24 GB pool) | Always-on compute | Genuinely free forever; ARM64; enough RAM for uvicorn + warm Celery worker + SDK subprocess |
| Ubuntu 22.04 aarch64 | Canonical minimal image | Base OS | Best OCI ARM support; LTS; apt packages available for all deps |
| Caddy 2.x | via apt (stable repo) | TLS-terminating reverse proxy | Single binary; auto HTTPS; ARM64 native; Caddyfile is 3 lines |
| DuckDNS | free tier | Free subdomain for ACME | DNS-01 challenge, no port-80 dependency; proven with Caddy DuckDNS module |
| uv | latest | Python package manager on VM | Same tooling as local dev; fast installs |
| systemd | OS default | Process supervision | Two units: `wchats-api.service`, `wchats-celery-runtime.service` |
| Vercel | Hobby free tier | Static widget delivery | bantuson.vercel.app; public/ dir served without config |

### Supporting

| Component | Version | Purpose | When to Use |
|-----------|---------|---------|-------------|
| `caddy-dns/duckdns` plugin | latest | DNS-01 ACME via DuckDNS | Only if using DuckDNS DNS challenge (recommended over HTTP-01) |
| `oci-arm-host-capacity` script | hitrov/oci-arm-host-capacity | Retry loop for OCI capacity errors | Run as cron during VM provisioning phase |
| `gzip` / Brotli | OS | Response compression | Caddy can compress; low memory cost |
| Upstash Redis (existing) | TLS rediss:// | Broker + SSE pub/sub | Already configured; no local Redis needed |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Caddy + DuckDNS | nginx + certbot | nginx requires more config; certbot cron renewal; DuckDNS DNS-01 same; Caddy simpler |
| Caddy + DuckDNS | Cloudflare Tunnel (cloudflared) | No open ports needed at all; BUT SSE long-lived connections can be cut by Cloudflare's 100s HTTP response timeout on free plans; risky for SSE |
| DuckDNS + DNS-01 | sslip.io + HTTP-01 | sslip.io works but requires port 80 open AND Oracle iptables rule; DuckDNS is more reliable |
| Oracle Cloud A1 | Fly.io free tier | Fly free tier removed in 2024; not $0 |
| Oracle Cloud A1 | Render free | Render free sleeps after 15 min inactivity (~50s cold start); violates D-12 warm-worker requirement |

**Installation on VM:**
```bash
# Caddy (arm64 via apt)
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install caddy

# OR: for DuckDNS DNS-01, build Caddy with the duckdns plugin:
# xcaddy build --with github.com/caddy-dns/duckdns
```

**Version verification:** [VERIFIED: Caddy stable channel, arm64 packages available via cloudsmith apt; confirmed Caddy installs on ARM64 Ubuntu via official apt repo]

---

## Architecture Patterns

### System Architecture Diagram

```
Hiring manager browser
        |
        | https://bantuson.vercel.app/wchats/widget.js  (static, Vercel CDN)
        |
        | widget.js → injects iframe → index.html?agent_id=fe230a9d&api=https://<duckdns-host>
        |
        +--> GET  https://<duckdns-host>/widget/fe230a9d/config   (JWT mint)
        |    POST https://<duckdns-host>/widget/fe230a9d/chat     (dispatch job)
        |    GET  https://<duckdns-host>/widget/jobs/{job_id}/events  (SSE)
        |
        v
  Caddy (VM, :443, TLS via Let's Encrypt / DuckDNS DNS-01)
        |
        | reverse_proxy 127.0.0.1:8000
        v
  uvicorn (wchats-api.service, :8000)
        |
        |  apply_async(run_agent_turn, queue="runtime")
        v
  Celery runtime worker (wchats-celery-runtime.service, --pool=solo)
        |
        |  ANTHROPIC_API_KEY → spawns claude-agent-sdk bundled binary (aarch64)
        |  Voyage API → embed_query() [1 call per turn]
        v
  Upstash Redis (rediss://) ← SSE pub/sub, rate limits, job events
  Neon sa-east-1 ← agent config, tenant DB (conversation rows, chunks)
```

### Recommended Project Structure (VM layout)

```
/opt/wchats/
├── apps/api/             # git clone of this repo
│   ├── .env              # env file (systemd EnvironmentFile points here)
│   └── ...
/etc/caddy/
└── Caddyfile             # reverse proxy + auto-TLS
/etc/systemd/system/
├── wchats-api.service
└── wchats-celery-runtime.service
```

### Pattern 1: systemd EnvironmentFile for secrets

**What:** All env vars (ANTHROPIC_API_KEY, VOYAGE_API_KEY, NEON_*, UPSTASH REDIS_URL, JWT_SECRET, CLERK_*) loaded by systemd from a single file, not hard-coded in units.

**When to use:** Every service on the VM. The app's `_find_env_file()` in `config.py` already walks up from the script location to find `.env` — point the VM `.env` at `/opt/wchats/apps/api/.env` and systemd `EnvironmentFile=` at the same path.

**Systemd unit (API):**
```ini
# /etc/systemd/system/wchats-api.service
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

**Systemd unit (Celery runtime worker):**
```ini
# /etc/systemd/system/wchats-celery-runtime.service
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

**Note on `worker_pool="solo"` vs prefork on Linux:**
`celery_app.py` sets `worker_pool = "solo"` in config. On Linux, prefork is the default and would normally be fine, but solo is correct here because: (a) `agent_tools.py` uses module-level globals (`_conn_str`, `_agent_id`, etc.) for tenant-scoped injection — these are explicitly documented as `[VERIFIED: apps/api/STATE.md [04-02]]` as safe only for `worker_pool=solo`; (b) a 1-2 OCPU free VM would not benefit from prefork concurrency. Keep `--pool=solo --concurrency=1` explicit in the CLI flag so it is not silently overridden. [VERIFIED: codebase audit of celery_app.py + agent_tools.py]

### Pattern 2: Caddyfile reverse proxy with DuckDNS DNS-01

**What:** Caddy terminates HTTPS and proxies to uvicorn on localhost. DuckDNS provides a free subdomain (e.g., `wchats-api.duckdns.org`). DNS-01 challenge means port 80 never needs to be open — critical because Oracle Cloud default iptables blocks port 80.

**Caddyfile:**
```caddyfile
wchats-api.duckdns.org {
    tls {
        dns duckdns {env.DUCKDNS_TOKEN}
    }
    reverse_proxy 127.0.0.1:8000
}
```

**Requirements:**
- Caddy must be built with the `caddy-dns/duckdns` plugin (`xcaddy build --with github.com/caddy-dns/duckdns`), OR use the xcaddy build from caddyserver.com/download.
- `DUCKDNS_TOKEN` must be in the systemd environment (add to Caddy's service file or `/etc/caddy/.env`).
- Oracle VCN Security List: open ingress rule for TCP 443. No port-80 rule needed for DNS-01.
- Oracle iptables: add `sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT` (and persist via `iptables-persistent`).

**Widget `data-api` value:** `https://wchats-api.duckdns.org`

[VERIFIED: Caddy docs https://caddyserver.com/docs/automatic-https; DuckDNS module https://github.com/caddy-dns/duckdns; Oracle iptables pattern from DEV article]

### Pattern 3: Vercel public/ static delivery

**What:** bantuson.vercel.app is the admin Next.js app deployed on Vercel. The `public/` directory in the Next.js app is served verbatim at the root URL. Placing files under `apps/admin/public/wchats/` makes them available at `https://bantuson.vercel.app/wchats/`.

**What exists today:**
- `apps/admin/public/` — exists and contains favicons, skyline PNG, SVG assets. No `wchats/` subdirectory yet. [VERIFIED: codebase listing]
- `apps/widget/embed/` — contains all four required files (`widget.js`, `index.html`, `widget.iife.js`, `widget.css`). [VERIFIED: codebase listing]
- Bundle: `widget.iife.js` is 17,833 bytes + `widget.css` 4,711 bytes = 22,544 bytes total. Well under 20 KB gzipped target. [VERIFIED: codebase byte count]

**Deploy action:** Copy `apps/widget/embed/` contents into `apps/admin/public/wchats/`, commit, push — Vercel auto-deploys.

**No Vercel config needed:** Next.js serves `public/` files statically; no `vercel.json` required for this. [VERIFIED: Next.js static file serving convention; no vercel.json found in repo]

**Rebuild needed?** Only if widget source (`apps/widget/src/`) changed since the bundle was last built. The embed files in `apps/widget/embed/` already contain a built bundle. The planner should include a verification step: `pnpm --filter veridian-widget build` followed by `cp apps/widget/dist/widget.iife.js apps/widget/dist/widget.css apps/widget/embed/` before copying to `public/wchats/`. Never npm.

### Anti-Patterns to Avoid

- **Opening port 80 on Oracle VCN for HTTP-01 ACME:** Oracle ARM instances ship with a default iptables REJECT rule for ports other than 22. HTTP-01 requires port 80 externally accessible. Use DNS-01 instead — it never touches port 80. [VERIFIED: Oracle iptables behavior confirmed in multiple community sources]
- **Cloudflare Tunnel for SSE:** Cloudflare's free-tier HTTP response timeout is 100 seconds for proxied connections. SSE streams can be open for the full 120-second SSE timeout in `widget.py`. This creates a risk of Cloudflare killing in-progress SSE streams at the 100s mark. If using Cloudflare, must set `asyncio.timeout` in widget.py to < 95s to be safe. Prefer Caddy + DuckDNS to avoid this entirely.
- **Cloudflare Flexible SSL mode:** Cloudflare Flexible encrypts browser→CF but sends plain HTTP from CF→origin. This creates mixed-content confusion and is explicitly deprecated by Cloudflare for API backends. If using Cloudflare, use Full (not Flexible). A self-signed cert on the VM is sufficient for Full mode.
- **Running `--pool=prefork` with module-level globals:** `agent_tools.py` uses module-level globals (`_conn_str`, `_agent_id`) injected by `build_tool_server()`. These are not thread-safe / process-safe if concurrent tasks run. `worker_pool=solo` is mandatory for this codebase. Do not switch to prefork. [VERIFIED: codebase audit STATE.md [04-02]]
- **Hard-coding env vars in systemd unit files:** Use `EnvironmentFile=` + a `.env` file not checked into git. The `.env` file should have `chmod 600 wchats:wchats` permissions.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| TLS certificate issuance | Custom ACME client | Caddy built-in | Caddy auto-renews via DuckDNS DNS-01; zero maintenance |
| Process supervision + restart | Custom watchdog script | systemd `Restart=always` | OS-level reliability; auto-starts on reboot |
| Free subdomain for HTTPS | Register a domain | DuckDNS free subdomain | Zero cost; works with DNS-01 ACME; widget only needs stable hostname |
| ARM64 Claude Code binary | Cross-compile or ship Node.js manually | `claude-agent-sdk==0.1.81` bundled aarch64 wheel | SDK bundles the correct platform binary; no Node.js install needed |
| Query embedding cache | Custom in-memory dict | Redis (already present) | Use Upstash Redis SETNX with TTL; hash the query string as key |

---

## Runtime State Inventory

> Phase is NOT a rename/refactor. Runtime state section included because the phase provisions a new VM and must enumerate what must be configured at runtime on the new host.

| Category | Items Found | Action Required |
|----------|-------------|-----------------|
| Stored data | Neon control DB (sa-east-1): `agents` row for `fe230a9d` has `is_deployed=True`; 195 embeddings in Neon tenant project `nameless-fog-19651218`. No rename. | No migration — connect to existing Neon via env vars |
| Live service config | Upstash Redis: existing TLS URL in `.env` (`rediss://...`). Celery broker + result backend + SSE pub/sub all point here. | Copy REDIS_URL to VM `.env`. Keep using Upstash. |
| OS-registered state | VM: two new systemd units (`wchats-api.service`, `wchats-celery-runtime.service`) must be registered (`systemctl enable --now`). DuckDNS: create subdomain, set A record to VM public IP. | One-time provisioning steps |
| Secrets/env vars | All secrets currently in local `.env` (ANTHROPIC_API_KEY, VOYAGE_API_KEY, NEON_API_KEY, NEON_ENCRYPTION_KEY, CONTROL_DB_URL, CONTROL_DB_SYNC_URL, REDIS_URL, JWT_SECRET, CLERK_WEBHOOK_SIGNING_SECRET, CLERK_JWKS_URL). None are renamed. | Copy to VM at `/opt/wchats/apps/api/.env`; `chmod 600`; add DUCKDNS_TOKEN for Caddy |
| Build artifacts | `apps/widget/embed/widget.iife.js` + `widget.css` — compiled bundle, must be copied to `apps/admin/public/wchats/`. | `pnpm --filter veridian-widget build`, copy output, commit |

---

## Common Pitfalls

### Pitfall 1: Oracle Cloud "Out of Host Capacity"
**What goes wrong:** Creating an ARM A1 instance in most regions returns `Out of host capacity` immediately. The portal UI gives no ETA.
**Why it happens:** ARM Ampere A1 demand outstrips supply in popular home regions (US-Ashburn, US-Phoenix). Capacity opens up intermittently.
**How to avoid:** Use the `oci-arm-host-capacity` script (github.com/hitrov/oci-arm-host-capacity) to poll every 5 minutes via cron. Success reported in EU-Frankfurt-1 and other European regions. Alternatively, upgrade to PAYG (credit card still required, no charge for Always Free resources) — PAYG accounts get capacity priority.
**Warning signs:** Immediate "Out of host capacity" error. Do NOT mistake this for a configuration error.

**Credit card requirement:** Oracle requires a credit card or debit-card-that-functions-as-credit-card at signup. A $1 USD hold is placed (released within 5 days). The account is NOT charged for Always Free resources. Virtual, prepaid, or PIN-only debit cards are NOT accepted. **This conflicts with a strict "$0, no payment method" interpretation.** A credit card is required to sign up — but actual charges are $0. [VERIFIED: Oracle FAQ https://www.oracle.com/cloud/free/faq/]

### Pitfall 2: Oracle iptables blocks ports 80 and 443 by default
**What goes wrong:** After adding VCN Security List ingress rules for TCP 443, external connections still fail. The OS iptables drops them before the app sees them.
**Why it happens:** Oracle Linux / Ubuntu images on OCI ship with a restrictive iptables INPUT chain that blocks everything except SSH (port 22).
**How to avoid:** Run both:
```bash
# Cloud-level: add ingress rule in OCI Console → VCN → Security List
# Host-level:
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
sudo apt install iptables-persistent && sudo netfilter-persistent save
```
Do NOT need port 80 if using DuckDNS DNS-01. [VERIFIED: DEV community article dev.to/armiedema; GitHub gist mrladeia]

### Pitfall 3: Voyage 3 RPM exceeded — 6 retrieve calls per turn
**What goes wrong:** The live E2E test (2026-05-29) showed the agent making 6 `retrieve` tool calls in a single turn. Each call hits `embed_query()` → Voyage API. At 3 RPM free tier, the 4th call within a 60-second window fails with `RateLimitError`, which propagates up through the 30-second `asyncio.wait_for` timeout as an unhandled exception.
**Why it happens:** `ClaudeAgentOptions(max_turns=10)` allows up to 10 tool invocations. Claude's default reasoning causes it to issue multiple retrieves to refine its answer.
**How to avoid:** Two independent guards (belt-and-suspenders):
1. Lower `max_turns` in `ClaudeAgentOptions` in `agent.py` to `max_turns=3` (cap total tool calls in the loop). [VERIFIED: codebase agent.py line ~511, `max_turns=10` currently]
2. Add a system-prompt instruction: "Call the `retrieve` tool at most once per response. Do not call it again after receiving results." in `build_system_prompt()` or directly in `agent.py`. [VERIFIED: `agent_prompt.py` exists and is called via `build_system_prompt(agent)`]
**Warning signs:** `agent.failed` SSE event immediately after `agent.tool_result` on the 4th retrieve.
**Note:** `embedding_service.py` uses `tenacity` with `stop_after_attempt(5)` and exponential backoff (2–30s). But each retry of a Voyage call within the SDK turn costs time against the 30s guard. The fix is to prevent excess calls, not to retry harder.

### Pitfall 4: 30-second wall-clock guard too tight for SDK startup
**What goes wrong:** `asyncio.run(asyncio.wait_for(_run_sdk_turn(...), timeout=30))` in `agent.py` line 532–545. The SDK must: (1) spawn the bundled Claude Code binary, (2) establish stdio pipes, (3) send the query, (4) wait for Anthropic API response, (5) execute the retrieve tool (Voyage API call + Neon query), (6) return the final response. On a cold process, steps 1–2 alone can take 5–10s on ARM. Step 4 adds 2–5s network. One retrieve at 3 RPM is instantaneous (it is the first call). Total wall-clock: realistically 20–40s. 30s is too tight for a warm-but-not-hot SDK.
**Why it happens:** The guard was set during M4 development on a local PC with fast SSD. ARM VM has slower binary spawn.
**How to avoid:** Change `timeout=30` to `timeout=90` at `agent.py` line 544. The SSE layer has `asyncio.timeout(120)` in `widget.py` line 507, so 90s task timeout still has 30s buffer before the SSE stream closes. [VERIFIED: codebase agent.py line 544; widget.py line 507]

### Pitfall 5: Solo pool worker_pool setting silently overridden
**What goes wrong:** `celery_app.py` sets `worker_pool="solo"` in config. A Linux system default or a CLI invocation without `--pool=solo` could use prefork. With prefork, `agent_tools.py` module-level globals are NOT safe across forked processes.
**Why it happens:** Linux default Celery pool is prefork. If the systemd ExecStart omits `--pool=solo`, Celery may ignore the config-level `worker_pool` setting in some Celery versions.
**How to avoid:** Explicitly pass `--pool=solo --concurrency=1` in the systemd unit ExecStart. [VERIFIED: celery_app.py line 204; agent_tools.py module-level globals; STATE.md [04-02]]

### Pitfall 6: Cloudflare SSE timeout
**What goes wrong:** If using Cloudflare as the TLS proxy (instead of Caddy), Cloudflare's free plan has a 100-second HTTP response timeout. SSE connections in `widget.py` use `asyncio.timeout(120)`. Cloudflare kills the connection at 100s before the SSE generator can close cleanly.
**How to avoid:** Use Caddy + DuckDNS instead. If Cloudflare is used, reduce SSE `asyncio.timeout` to 90s. [ASSUMED: Cloudflare 100s limit is a known free-plan constraint; Caddy has no response timeout by default]

### Pitfall 7: bantuson.vercel.app Vercel build uses Next.js — public/ must be in the admin dir
**What goes wrong:** The widget embed files go in `apps/admin/public/wchats/` (the Next.js app), NOT the repo root. If placed in the wrong location, Vercel ignores them.
**Why it happens:** bantuson.vercel.app is the Next.js admin app. Vercel detects it via the `apps/admin` directory. The `public/` subdirectory of the Next.js root is the correct static serving location.
**How to avoid:** Place all four embed files under `apps/admin/public/wchats/`. Commit. Push. Vercel auto-deploys on push to main. [VERIFIED: codebase — `apps/admin/public/` exists with favicon assets; no `wchats/` subdir yet]

---

## Code Examples

Verified patterns from official sources and codebase audit:

### D-10 Retrieve cap — exact insertion point in agent.py

The `ClaudeAgentOptions` object is constructed at `agent.py` lines 511–524:

```python
# Source: apps/api/app/worker/tasks/runtime/agent.py lines 511-524 [VERIFIED: codebase]
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
    max_turns=10,          # <-- D-10: change to max_turns=3
    max_budget_usd=0.05,
)
```

**Change:** `max_turns=10` → `max_turns=3`. This caps the total number of tool round-trips per turn.

Additionally, add a retrieve-cap instruction to the system prompt in `build_system_prompt()` (or directly in `agent.py` before constructing `options`):

```python
# Source: apps/api/app/services/agent_prompt.py — add to SYSTEM_PROMPT_SUFFIX or build_system_prompt()
# [VERIFIED: codebase — build_system_prompt() called at agent.py line 508]
retrieve_cap_instruction = (
    "\n\nIMPORTANT: Call the `retrieve` tool AT MOST ONCE per response. "
    "After receiving retrieve results, synthesize an answer immediately. "
    "Do not call retrieve again."
)
```

This dual guard (max_turns=3 + prompt instruction) ensures at most 1–2 retrieve calls per turn even if the SDK decides to use clarify or lookup_structured.

### D-11 Wall-clock guard raise — exact line in agent.py

```python
# Source: apps/api/app/worker/tasks/runtime/agent.py lines 532-545 [VERIFIED: codebase]
# CURRENT:
result = asyncio.run(
    asyncio.wait_for(
        _run_sdk_turn(...),
        timeout=30,        # <-- D-11: change to timeout=90
    )
)
# CHANGE TO:
result = asyncio.run(
    asyncio.wait_for(
        _run_sdk_turn(...),
        timeout=90,
    )
)
```

**Celery visibility_timeout check:** `celery_app.py` line ~158 sets `visibility_timeout=3600`. At 90s task timeout, the task finishes well within 3600s visibility window — no change needed. [VERIFIED: celery_app.py lines 145-160]

**SSE timeout check:** `widget.py` line 507 has `asyncio.timeout(120)`. A 90s task timeout means the Celery task completes at ≤90s. The SSE stream has 120s before timing out — 30s headroom. No change needed in widget.py. [VERIFIED: widget.py line 507]

**Celery task hard time limit:** No `time_limit` or `soft_time_limit` is set on `run_agent_turn`. Only the `asyncio.wait_for` inside is the guard. Safe to raise to 90. [VERIFIED: agent.py task decorator lines 371-378]

### Caddy DNS-01 Caddyfile (DuckDNS)

```caddyfile
# Source: Caddy docs + caddy-dns/duckdns module docs [CITED: https://github.com/caddy-dns/duckdns]
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

### D-13 Optional query embedding cache (Redis)

```python
# Slot in retrieval_service.embed_query() or at the top of retrieve_tool()
# [ASSUMED — pattern, not yet in codebase]
import hashlib, json

def _query_cache_key(query: str) -> str:
    return f"qembed:{hashlib.sha256(query.encode()).hexdigest()}"

def embed_query_cached(query: str) -> list[float]:
    key = _query_cache_key(query)
    cached = _redis.get(key)
    if cached:
        return json.loads(cached)
    vector = embed_query(query)  # existing function
    _redis.setex(key, 3600, json.dumps(vector))  # 1-hour TTL
    return vector
```

Cache hit = zero Voyage calls = no rate-limit risk. Slot in: `retrieve_tool` in `agent_tools.py`, replacing the `embed_query(query)` call at line ~201.

---

## Specific Decision Resolutions

### D-01/D-02: Oracle Cloud Always Free ARM

**VM Shape:** `VM.Standard.A1.Flex` — configure with 2 OCPU + 12 GB RAM (leaves the other 2 OCPU / 12 GB available for a second instance or expansion). 1 OCPU is sufficient for the chat path, but 2 gives headroom during SDK subprocess startup spikes. [VERIFIED: Oracle docs — 4 OCPU / 24 GB RAM total pool]

**OS:** Ubuntu 22.04 aarch64 (Canonical minimal). Oracle Linux is also supported but Ubuntu has better community tooling for apt-based installs (Caddy, uv, etc.).

**Credit card:** Required at OCI signup. $1 hold (released in 5 days). No ongoing charge for Always Free. This IS a constraint for the "$0 no payment method" goal if interpreted strictly — a credit card must be on file but is not charged. [VERIFIED: Oracle FAQ]

**Capacity workaround:** Use `github.com/hitrov/oci-arm-host-capacity` script run via cron every 5 minutes. EU-Frankfurt-1 region tends to have better availability. Plan must include a Wave 0 task for VM provisioning with explicit capacity-wait documentation.

**iptables:** Both OCI VCN Security List ingress rule AND host-level iptables must allow port 443. Port 80 NOT required if using DNS-01. [VERIFIED: DEV article + GitHub gist]

### D-05: TLS / Reverse Proxy Recommendation

**Recommended: Caddy 2.x with DuckDNS DNS-01 challenge.**

Reasons:
- No port 80 required (solves Oracle iptables friction).
- Caddy is a single binary; ARM64 available via Caddy's official apt repo (Go-compiled, multi-arch). [VERIFIED: apt install confirmed via search]
- Auto-renewal: Caddy handles Let's Encrypt cert renewal silently.
- Caddyfile is 6 lines.
- DuckDNS is free, no email needed, no credit card, supports DNS TXT record updates via token.

**Alternative if Caddy build complexity is a concern:** Cloudflare free proxy in Full mode (self-signed origin cert). No port 80/443 to open — Cloudflare connects back to the VM IP using the Cloudflare IP range. But: SSE timeout risk (100s Cloudflare limit vs 120s SSE timeout). If using Cloudflare, must reduce SSE `asyncio.timeout` to 90s.

**API base URL for widget:** `https://wchats-api.duckdns.org` (exact subdomain chosen during VM setup; any `.duckdns.org` subdomain is free).

### D-04: claude-agent-sdk on ARM64 Linux

**Finding:** `claude-agent-sdk==0.1.81` bundles the Claude Code binary for each platform. The PyPI page for 0.1.81 shows a Linux aarch64 wheel (`claude_agent_sdk-0.1.81-...-linux_aarch64.whl`). The bundled CLI is approximately 60–72 MB per platform-specific wheel. [VERIFIED: pypi.org/project/claude-agent-sdk/0.1.81/]

**Node.js requirement:** The Python SDK does NOT require a separate Node.js install. The bundled Claude Code binary is self-contained (it is the compiled Node.js runtime + app). From official docs: "The Claude Code CLI is automatically bundled with the package - no separate installation required!" [VERIFIED: code.claude.com/docs/en/agent-sdk/overview + pypi.org]

**Headless (no TTY) operation:** The SDK is designed for programmatic use without a TTY. The existing code in `agent.py` uses `asyncio.run(asyncio.wait_for(...))` inside a Celery worker — no terminal required. The `--pool=solo` worker runs in the main process, not a forked subprocess. [VERIFIED: Agent SDK hosting docs; existing codebase pattern]

**glibc requirement:** Linux aarch64 wheel requires glibc 2.17+. Ubuntu 22.04 ships glibc 2.35 — well above the minimum. [VERIFIED: pypi.org wheel metadata + Ubuntu 22.04 release notes]

**Risk level:** LOW after verification. The bundled binary for ARM64 Linux is present in the wheel. The critical unknown (does it run headless under systemd) is answered: yes, by design.

**Fallback if bundled binary fails:** Set `ClaudeAgentOptions(cli_path="/usr/local/bin/claude")` and install Claude Code CLI separately via `npm install -g @anthropic-ai/claude-code` (requires Node.js 18+). This is the fallback, not the default.

### D-09 / D-13: Voyage 3 RPM and embed cache

**Ingestion:** All 195 chunks are already embedded (2026-05-29 E2E). Ingestion (document corpus) does NOT run on the VM — it runs locally on-demand (D-03). [VERIFIED: state.env + memory file project_portfolio_agent_e2e.md]

**Runtime path:** Only `embed_query()` in `retrieval_service.py` hits Voyage at runtime — called once per `retrieve` tool call. With D-10 cap at max 1–2 retrieves per turn, that is 1–2 Voyage calls per turn. At 3 RPM, this is sustainable for low-traffic portfolio use: one conversation per minute is feasible.

**embed_query retry:** `embedding_service.py` uses `tenacity` with `stop_after_attempt(5)` and `wait_exponential(min=2, max=30)`. A rate limit on call 2 within the same minute would backoff up to 30s before retrying — this still fits within the 90s wall-clock guard. However, with D-10 capping retrieves to 1–2, this scenario should not arise.

**D-13 cache decision for planner:** Caching is LOW complexity (one Redis SETNX + JSON) and HIGH value (eliminates ALL Voyage calls for repeat questions). Recommend including as a task in the plan. Slot: `agent_tools.py` `retrieve_tool()` function, replacing the `embed_query(query)` call at line ~201. [ASSUMED — implementation pattern not yet in codebase]

### D-15: ADR location and format

**Recommended location:** `docs/adr/0001-cloud-native-cutover.md`. Create `docs/adr/` directory (does not exist yet). [VERIFIED: `ls C:/Users/Bantu/mzansi-agentive/veridian/` — no `docs/` directory found]

**ADR outline:**
```markdown
# ADR-0001: Cloud-Native AWS Cutover from Oracle Cloud Always Free

**Status:** Proposed
**Date:** 2026-05-29
**Deciders:** Bantuson

## Context
Current hosting: Oracle Cloud Always Free ARM VM ($0, single-tenant, manual ops).
Constraint: $0 infra for portfolio phase; AWS Bedrock, Aurora, Fargate cost money.

## Decision
Cutover to cloud-native AWS when the trigger threshold is met.

## Target Architecture
- Compute: ECS Fargate (uvicorn + Celery runtime; pipeline on Fargate Spot)
- Database: Aurora Serverless v2 + pgvector + RLS/schema-per-tenant
  (replaces per-tenant Neon projects)
- Fast eval clones: Aurora fast clones (replaces Neon branching)
- Embeddings/LLM: Amazon Bedrock (Claude + embedding models)
- Queue/broker: Amazon SQS or ElastiCache Redis

## Trigger Threshold
Cutover when ANY of:
- Tenant count > ~50 (Neon free tier projects limit approached)
- Monthly API spend > $100 (Voyage free tier / Anthropic direct cost)
- VM RAM becomes the bottleneck (> 80% sustained over 7 days)
- SLA requirement (uptime SLA needed beyond Oracle best-effort)

## Flip Mechanism
Config-only: all services read from env vars (D-14 seam). Swap `.env` values.
No data-layer rewrite needed for compute flip.
Data migration (Neon → Aurora) is a separate task with pg_dump/restore.
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| TypeScript SDK bundles binary; Python SDK needed separate Node.js | Python SDK 0.1.81+ bundles platform binary (aarch64 included) | Early 2026 | No Node.js install needed on VM |
| Render free tier (sleeps) | Oracle Cloud Always Free (no sleep) | 2025 | Genuinely always-on for $0 |
| Let's Encrypt HTTP-01 requires port 80 | Caddy DNS-01 via DuckDNS plugin | 2023+ | No port-80 requirement; works behind strict firewalls |
| Solo pool Windows-only workaround | Solo pool appropriate for module-global-safe single-tenant VM | Established | Keep on Linux VM |
| Celery prefork on Linux | Solo pool for this specific codebase | M4 decision | Module-level globals not safe for prefork |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Caddy DuckDNS DNS-01 plugin works on ARM64 Ubuntu 22.04 without custom build | TLS / Reverse Proxy | Must xcaddy-build with plugin; standard apt Caddy won't have it |
| A2 | Cloudflare free plan has 100s HTTP response timeout (SSE cut risk) | Anti-Patterns | If timeout is longer, Cloudflare Tunnel is also viable |
| A3 | D-13 query embedding cache: `embed_query()` is in `retrieval_service.py` and is the correct interception point | D-13 cache | May need to check retrieval_service.py import path |
| A4 | `docs/adr/` directory does not yet exist in the repo | D-15 ADR | Safe to create; no conflicts |
| A5 | Oracle capacity workaround (retry script) typically resolves within hours to days in EU regions | D-01 VM provision | Could take longer; plan must allow for capacity wait |

---

## Open Questions (RESOLVED)

1. **Caddy build with DuckDNS plugin** — RESOLVED: plans 04 (authors deploy/caddy/Caddyfile + the build note in deploy/README.md) and 05 (builds Caddy with the duckdns plugin via xcaddy ARM64 on the VM).
   - What we know: Standard apt Caddy does NOT include the DuckDNS DNS provider plugin.
   - What's unclear: Is `xcaddy` available as an ARM64 binary, or must it be compiled? xcaddy is written in Go and has ARM64 prebuilt releases.
   - Recommendation: Plan Wave 0 includes downloading the xcaddy ARM64 binary and building `caddy` with `--with github.com/caddy-dns/duckdns`. Alternatively, download a pre-built custom Caddy binary from caddyserver.com/download with the DuckDNS plugin selected.

2. **OCI VM provisioning wait time** — RESOLVED: the wave graph parallelizes around it — plans 01 (code), 02 (widget), 03 (ADR), 04 (deploy artifacts) all run in Wave 1 independent of the VM; only plan 05 (VM provision) and plan 06 (live gate) wait on capacity.
   - What we know: Capacity errors are common in US regions. EU regions are better.
   - What's unclear: How long the retry loop will take in practice.
   - Recommendation: Plan must NOT block all other work on VM provisioning. Waves for widget delivery (Vercel) and code changes (D-10, D-11) should proceed in parallel.

3. **widget.iife.js bundle freshness** — RESOLVED: plan 02 rebuilds with pnpm --filter veridian-widget build and syncs dist/ to embed/ before copying to apps/admin/public/wchats/.
   - What we know: `apps/widget/embed/widget.iife.js` exists at 17.8 KB. `apps/widget/dist/widget.iife.js` also exists.
   - What's unclear: Whether the `embed/` bundle is current with `dist/`. The `README.md` says to `cp ../dist/widget.iife.js ../dist/widget.css .` after build.
   - Recommendation: Plan Wave 0 includes `pnpm --filter veridian-widget build` + sync from dist/ to embed/ before copying to public/wchats/.

---

## Environment Availability

| Dependency | Required By | Available (local) | Available (VM) | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.11+ | uvicorn + Celery | Yes (local dev) | Install via apt on VM | — |
| uv | pip install | Yes | `pip install uv` or curl install | pip directly |
| Neon control DB (sa-east-1) | FastAPI + Alembic | Yes (remote) | Yes (remote, env var) | — |
| Upstash Redis (rediss://) | Celery broker + SSE | Yes (remote) | Yes (remote, env var) | Local Redis on VM if needed |
| Voyage API (free tier, 3 RPM) | embed_query() | Yes | Yes (env var VOYAGE_API_KEY) | — |
| Anthropic API | Agent SDK | Yes | Yes (env var ANTHROPIC_API_KEY) | — |
| Clerk (JWKS + webhook) | JWT auth | Yes | Yes (env vars) | — |
| Caddy + DuckDNS plugin | TLS proxy | N/A (local dev) | Build on VM | nginx + certbot |
| claude-agent-sdk aarch64 | runtime worker | N/A (local is x86) | Install via pip (aarch64 wheel) | cli_path fallback |
| Vercel (bantuson.vercel.app) | widget static | Yes (admin app deployed) | N/A (Vercel CDN) | — |

**Missing dependencies with no fallback:**
- None that block deployment.

**Missing dependencies with fallback:**
- Caddy DuckDNS plugin: must be built with xcaddy; fallback is nginx + certbot or Cloudflare proxy.
- OCI VM: capacity errors may delay provisioning; all other work can proceed in parallel.

---

## Validation Architecture

This section maps each locked decision to how it will be validated. The orchestrator uses this to create VALIDATION.md.

| Decision | What to Validate | Automated? | Command / Method |
|----------|-----------------|------------|-----------------|
| D-01 VM alive | VM responds to SSH | Manual | `ssh ubuntu@<vm-ip>` succeeds |
| D-02 systemd services | Both services running | Auto (on VM) | `systemctl is-active wchats-api wchats-celery-runtime` |
| D-02 API reachable | uvicorn responds | Auto | `curl http://127.0.0.1:8000/health` from VM |
| D-05 TLS | HTTPS works from external | Auto | `curl -I https://wchats-api.duckdns.org/health` — expect 200 + valid cert |
| D-05 mixed-content | Widget iframe loads over https | Manual | Open bantuson.vercel.app, open DevTools → no mixed-content errors |
| D-06 Vercel files | Files accessible | Auto | `curl -I https://bantuson.vercel.app/wchats/widget.js` — expect 200 |
| D-07 data-api wiring | Widget contacts VM API | Manual | Open widget, check Network tab — first request to `/widget/.../config` goes to DuckDNS host |
| D-08 bundle freshness | Bundle size unchanged | Auto (pre-deploy) | `pnpm --filter veridian-widget build` produces same-byte output |
| D-09 no Voyage payment | No rate limit at 1 call/turn | Auto (smoke test) | Send one chat message, observe SSE: `agent.response` received, no `agent.failed` |
| D-10 retrieve cap | Agent makes ≤2 retrieve calls per turn | Auto | SSE event log: count `agent.tool_call` events with `tool_name: retrieve`; assert ≤ 2 |
| D-11 90s guard | Turn completes before timeout | Auto | Measure SSE latency from POST /chat to `agent.response` event; assert < 85s |
| D-12 worker warm | No cold-start on second turn | Manual | Send second chat message; latency should be < 20s (SDK already spawned) |
| D-13 embed cache (optional) | Cache hit on repeat query | Auto (optional) | Send identical question twice; second turn has no `embed_query` Voyage call (check logs) |
| D-14 env-only config | No secrets in code | Auto (existing) | `grep -r "sk-ant\|voyage\|neon" apps/api/app/` — expect no matches |
| D-15 ADR exists | ADR file written | Auto | `test -f docs/adr/0001-cloud-native-cutover.md` |
| End-to-end success | Hiring-manager Q&A works | Manual (human gate) | Open bantuson.vercel.app; click chat launcher; ask "What is W Chats?"; receive grounded answer about Bantuson portfolio with citations; no error |

**Manual-only checkpoints:**
1. **VM SSH access** — cannot be automated before VM exists.
2. **Widget visual launch** — open bantuson.vercel.app in browser, click chat button, confirm panel opens with no console errors.
3. **Live Q&A** — ask a question about Bantuson/W Chats and receive a grounded answer. This is the "real hiring-manager test" and is the phase SUCCESS criterion.
4. **Second-turn latency** — send a follow-up question; confirm SDK warm-up is not repeated (fast response).

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (already installed in dev extras) |
| Config file | `apps/api/pyproject.toml` (pytest.ini_options) |
| Quick run command | `cd apps/api && pytest tests/ -x -q` |
| Full suite command | `cd apps/api && pytest tests/ -v --tb=short` |

### Phase Requirements → Test Map

Phase 12 has no formal REQ-IDs. Coverage is driven by D-01..D-15 decisions. The automated tests focus on the code changes (D-10, D-11) and the infrastructure smoke tests.

| Decision | Behavior | Test Type | Automated Command | File Exists? |
|----------|----------|-----------|-------------------|-------------|
| D-10 | max_turns=3 in ClaudeAgentOptions | unit | `pytest tests/test_agent_task.py -k max_turns -x` | ❌ Wave 0 |
| D-11 | timeout=90 in asyncio.wait_for | unit | `pytest tests/test_agent_task.py -k timeout -x` | ❌ Wave 0 |
| D-05 TLS | API health over HTTPS | smoke (manual/script) | `curl -sf https://wchats-api.duckdns.org/health` | ❌ Wave 0 script |
| D-06 Vercel | widget.js reachable | smoke | `curl -sf https://bantuson.vercel.app/wchats/widget.js` | ❌ Wave 0 script |
| D-15 ADR | ADR file exists | file check | `test -f docs/adr/0001-cloud-native-cutover.md` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `cd apps/api && pytest tests/test_agent_task.py -x -q` (if test file exists)
- **Per wave merge:** `cd apps/api && pytest tests/ -q`
- **Phase gate:** Manual E2E Q&A test before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/test_agent_task.py` — covers D-10 (max_turns assertion) and D-11 (timeout assertion); these are small parametric tests on the ClaudeAgentOptions construction in `run_agent_turn`
- [ ] `scripts/smoke_vm.sh` — curl-based smoke test script for TLS, health, and widget.js reachability (not a pytest test; a deployment verification script)

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | Clerk JWKS (existing); widget JWT HS256 (existing) |
| V3 Session Management | yes | JWT 15-min expiry (existing); SDK session_id in tenant DB (existing) |
| V4 Access Control | yes | EnvironmentFile chmod 600; IDOR guard in widget routes (existing) |
| V5 Input Validation | yes | sanitize_chunk_text for soul fields (existing); message text never logged (existing) |
| V6 Cryptography | yes | Fernet for Neon conn strings (existing); HS256 JWT (existing); NEVER hand-rolled |
| V7 Error Handling | yes | No secrets in error responses (existing); structlog fields controlled |
| V9 Communications | yes | TLS via Caddy (new); Upstash uses rediss:// TLS (existing) |

### Known Threat Patterns for This Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Secrets in systemd unit file | Information Disclosure | EnvironmentFile= + chmod 600 |
| VM public IP exposed without firewall | Spoofing / DoS | OCI Security List + iptables; only 443 open |
| Stale JWT token reuse | Elevation of Privilege | 15-min expiry on widget JWT (existing) |
| SSE connection exhaustion | DoS | `_MAX_CONCURRENT_SSE_PER_AGENT=50` cap (existing widget.py) |
| Direct Anthropic API key exposure on VM | Repudiation | EnvironmentFile only; not in code; not in task args |

---

## Sources

### Primary (HIGH confidence)
- `apps/api/app/worker/tasks/runtime/agent.py` — verified timeout=30 at line 544, max_turns=10 at line 521
- `apps/api/app/worker/celery_app.py` — verified worker_pool="solo" at line 204, visibility_timeout=3600
- `apps/api/app/services/agent_tools.py` — verified module-level globals pattern, max_turns impact
- `apps/api/app/services/embedding_service.py` — verified tenacity retry, BATCH_SIZE=128, 3 RPM source
- `apps/api/app/api/v1/widget.py` — verified asyncio.timeout(120) at line 507
- `apps/widget/embed/` — verified bundle sizes (22,544 bytes total), all four files present
- `apps/admin/public/` — verified no wchats/ subdir yet; correct destination for widget files
- `apps/api/pyproject.toml` — verified claude-agent-sdk==0.1.81 pinned
- `apps/api/_runlogs/state.env` — verified agent ID, tenant ID
- Memory file `project_portfolio_agent_e2e.md` — verified 4 blockers; 6 retrieve calls per turn was the failure mode
- [code.claude.com/docs/en/agent-sdk/overview](https://code.claude.com/docs/en/agent-sdk/overview) — Python SDK bundles CLI, no separate Node.js needed
- [code.claude.com/docs/en/agent-sdk/quickstart](https://code.claude.com/docs/en/agent-sdk/quickstart) — Node.js 18+ prerequisite is for TypeScript SDK, not Python; Python requires Python 3.10+
- [pypi.org/project/claude-agent-sdk/0.1.81/](https://pypi.org/project/claude-agent-sdk/0.1.81/) — Linux aarch64 wheel present; glibc 2.17+
- [Oracle FAQ](https://www.oracle.com/cloud/free/faq/) — credit card required; $1 hold; no charge for Always Free
- [Oracle Always Free docs](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) — 4 OCPU / 24 GB ARM A1 pool
- [Caddy automatic HTTPS docs](https://caddyserver.com/docs/automatic-https) — HTTP-01 requires port 80; DNS-01 does not
- [github.com/caddy-dns/duckdns](https://github.com/caddy-dns/duckdns) — DuckDNS provider for Caddy DNS-01
- [dev.to/armiedema — Oracle iptables guide](https://dev.to/armiedema/opening-up-port-80-and-443-for-oracle-cloud-servers-j35) — both VCN and iptables rules required

### Secondary (MEDIUM confidence)
- [hitrov.medium.com — OCI capacity workaround](https://hitrov.medium.com/resolving-oracle-cloud-out-of-capacity-issue-and-getting-free-vps-with-4-arm-cores-24gb-of-a3d7e6a027a8) — retry script approach; EU regions better; PAYG helps
- [github.com/hitrov/oci-arm-host-capacity](https://github.com/hitrov/oci-arm-host-capacity) — OCI CLI retry script
- [celery.school/the-solo-worker-pool](https://celery.school/the-solo-worker-pool) — solo pool appropriate for sequential single-process; same as prefork --concurrency=1 but no subprocess overhead

### Tertiary (LOW confidence — assumptions logged)
- Cloudflare free plan 100s HTTP response timeout for SSE — widely reported in community but not in official CF docs; marked A2 in assumptions log
- xcaddy ARM64 binary availability — likely but not directly checked; marked A1

---

## Metadata

**Confidence breakdown:**
- Oracle VM provisioning: MEDIUM — capacity errors are well-documented but workaround timing is unpredictable
- claude-agent-sdk ARM64: HIGH — verified via pypi.org wheel listings; bundled CLI confirmed in official docs
- TLS/Caddy/DuckDNS: HIGH — official Caddy docs + DuckDNS module verified
- retrieve cap (D-10): HIGH — exact code location verified in codebase
- timeout raise (D-11): HIGH — exact code location verified in codebase; SSE/Celery guards checked
- Vercel static delivery: HIGH — confirmed apps/admin/public/ convention; no wchats/ subdir yet
- ADR format: HIGH — docs/adr/ convention + outline provided

**Research date:** 2026-05-29
**Valid until:** 2026-07-01 (stable deployment patterns; Oracle Always Free terms are stable; Caddy releases quarterly; claude-agent-sdk changes rapidly but v0.1.81 is pinned)
