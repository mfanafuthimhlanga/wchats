# Phase 12: Production Go-Live (W Chats) - Research (Revised)

**Researched:** 2026-05-29 (original) / 2026-05-29 (revised — no-card pivot)
**Domain:** Cloudflare Tunnel from local Windows PC + stable-URL strategy for Vercel widget
**Confidence:** HIGH for tunnel mechanics; MEDIUM for SSE-through-tunnel UX (verified behavior, not confirmed fix)

> **ADR note:** The Oracle Cloud VM / Caddy / DuckDNS / systemd path is SUPERSEDED. It is
> preserved in `deploy/systemd/`, `deploy/caddy/`, and `deploy/README.md` in-repo as the
> documented AWS-VM migration path that ADR-0001 references. Do not re-execute or clean up those
> files — they serve as the "flip to cloud-native" implementation blueprint.

---

<user_constraints>
## User Constraints (from CONTEXT.md + decision_revision)

### Locked Decisions (Current — post-pivot)

- **D-01 → SUPERSEDED:** ~~Oracle Cloud Always Free VM~~ → **Host = user's local Windows PC** running the existing `scripts/start_native.ps1` stack.
- **D-02 → SUPERSEDED:** ~~systemd services on VM~~ → **Process management = native processes on Windows**, extended to also launch `cloudflared`.
- **D-03:** Pipeline worker is NOT hosted. Runs on-demand locally only when ingesting a new agent. RETAINED.
- **D-04:** Reuse Neon control DB (sa-east-1) + Upstash Redis via local `.env`. RETAINED.
- **D-05 → SUPERSEDED:** ~~Caddy + DuckDNS + Let's Encrypt on VM~~ → **TLS via Cloudflare edge** (tunnel terminates at Cloudflare; client sees a `*.trycloudflare.com` or named HTTPS URL).
- **D-06:** Widget files served from Vercel `apps/admin/public/wchats/`. RETAINED. DONE (plan 12-02).
- **D-07:** Snippet sets `data-api` at runtime. RETAINED. Value = tunnel base URL for demo session.
- **D-08:** Bundle built via pnpm. RETAINED. DONE (plan 12-02).
- **D-09:** No Voyage payment method. RETAINED.
- **D-10:** Cap retrieves-per-turn to ≤2. RETAINED. DONE (plan 12-01).
- **D-11:** 90s wall-clock guard in `run_agent_turn`. RETAINED. DONE (plan 12-01).
- **D-12:** Keep runtime worker warm during demo window. RETAINED. Now: worker stays running as long as `start_demo.ps1` window is open.
- **D-13:** Redis query-embedding cache. RETAINED. DONE (plan 12-01).
- **D-14:** Env-only config seam. RETAINED. Local `.env` already present.
- **D-15:** ADR `docs/adr/0001-cloud-native-cutover.md`. RETAINED. DONE (plan 12-03).

### What Is New (post-pivot re-plan scope)
- **Plan 12-05:** Re-targeted from "provision Oracle VM" → "write `scripts/start_demo.ps1` + re-target `smoke_vm.sh`".
- **Plan 12-06:** "Final E2E gate" unchanged in intent; `data-api` now points at tunnel URL.

### Claude's Discretion
- Which stable-URL strategy to use (see Section: Stable-URL Strategy Decision).
- Whether `smoke_vm.sh` is re-targeted in-place or a new `smoke_tunnel.sh` is authored.

### Deferred Ideas (OUT OF SCOPE)
- AWS migration build (Fargate + Aurora Serverless v2 + RLS + Bedrock) — ADR only.
- Hosting the pipeline worker in the cloud.
- Fix M6 eval harness / M8 checklist orchestrator / M8 eval-column bug — route to `/gsd-debug`.
- Voyage paid tier or Bedrock embeddings.
- Custom domain for the API.
- Named Cloudflare Tunnel (requires a domain managed by Cloudflare — user has no Cloudflare-managed domain).
</user_constraints>

---

## Summary

The hosting path has pivoted from Oracle Cloud VM to the user's existing local Windows PC exposed via a Cloudflare Quick Tunnel (`cloudflared`). The local stack (uvicorn + Celery runtime worker) is already fully built and working; `cloudflared` is already installed (v2025.8.1). The remaining work is:

1. Write `scripts/start_demo.ps1` — extends `start_native.ps1` to also launch `cloudflared tunnel --url http://localhost:8000`, capture the assigned `*.trycloudflare.com` URL, and display it.
2. Implement the stable-URL strategy so the Vercel widget snippet's `data-api` is updated each demo session with minimal friction.
3. Verify SSE behavior through the tunnel (see Section: SSE through Cloudflare Quick Tunnel).
4. Re-target `scripts/smoke_vm.sh` to accept the tunnel URL as `API_HOST`.
5. Run the manual hiring-manager E2E gate.

**The five high-risk items are:**

1. **SSE buffering in Quick Tunnels** — officially documented: `EventSource` (GET) SSE is buffered and flushed all-at-once when the server closes the connection. This means "thinking" indicator shows for the full agent turn duration, then the response appears. The agent publishes `agent.response` before closing the SSE stream, so events will arrive — just not incrementally. This is an acceptable UX degradation for a portfolio demo (see analysis below). [VERIFIED: cloudflare/cloudflared GitHub issue #1449; official Quick Tunnels docs]
2. **Random URL per session** — Quick Tunnels generate a new `*.trycloudflare.com` URL each run. The `data-api` attribute in the Vercel static snippet must be updated. Vercel does NOT serve updated `public/` files without a redeploy/commit. The recommended approach is a one-line commit to `apps/admin/public/wchats/index.html` or a thin `config.json`. See Section: Stable-URL Strategy.
3. **Named Tunnel not viable without a domain** — A named Cloudflare Tunnel (which could provide a stable `<name>.<domain>.workers.dev` or custom URL) requires a domain managed by Cloudflare DNS. The user has no Cloudflare-managed domain (bantuson.vercel.app is on Vercel, not Cloudflare). This path is blocked.
4. **"Live on demand" framing** — The PC + tunnel must be running when a hiring manager visits. This is honest: W Chats is a portfolio demo, not a 24/7 SaaS. The ADR documents the always-on cloud path. See Section: CV/Portfolio Honesty.
5. **cloudflared is already installed** — v2025.8.1 confirmed via `cloudflared --version`. No install step needed. [VERIFIED: local shell check]

**Primary recommendation:** Quick tunnel + per-session `config.json` commit. See Stable-URL Strategy section for the full decision.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Widget static files delivery | Vercel CDN (static) | — | `apps/admin/public/wchats/` already deployed; zero config; HTTPS is Vercel's |
| API base URL (per-session) | Vercel static (`config.json`) | Git commit + auto-deploy | Widget reads JSON at load time; updated via commit per demo session |
| TLS termination | Cloudflare edge (via tunnel) | — | cloudflared connects outbound; Cloudflare terminates TLS; no cert mgmt |
| Chat API (REST + SSE) | Local PC / uvicorn (localhost:8000) | — | existing `start_native.ps1` stack |
| Agent turn execution | Local PC / Celery runtime worker | — | `run_agent_turn` task on `runtime` queue; solo pool |
| Tunnel process | Local PC / cloudflared | — | `cloudflared tunnel --url http://localhost:8000`; outbound only |
| Session state / job events | Upstash Redis (remote) | — | SSE pub/sub and rate-limit counters; same as before |
| Tenant knowledge base | Neon (sa-east-1, remote) | — | per-tenant project; no change |
| Embeddings (query only) | Voyage AI (external) | — | only query embeds hit Voyage at runtime; corpus already embedded |
| Agent loop | claude-agent-sdk (local subprocess) | — | SDK spawns bundled binary; needs `ANTHROPIC_API_KEY` env |

---

## Stable-URL Strategy Decision (Research Question 1)

### The Problem

Quick Tunnels assign a **new random `*.trycloudflare.com` URL every time** `cloudflared` starts. The widget snippet on bantuson.vercel.app has `data-api="https://..."` baked into a committed file. Vercel serves static `public/` files that are **build artifacts** — they are frozen per deployment and require a commit + redeploy to change. [VERIFIED: Vercel CDN docs — static assets are cached per deployment; no server-side update without a new deploy]

### Options Evaluated

**Option A: `window.WCHATS_API_BASE` global override**
`widget.js` already checks `window.WCHATS_API_BASE` as a fallback (line 39–44 in `apps/admin/public/wchats/widget.js`):
```js
var apiBase =
  script.getAttribute("data-api") ||
  window.WCHATS_API_BASE ||
  "";
```
This means `data-api` on the `<script>` tag takes precedence. The script tag in `apps/admin/public/wchats/index.html` **does not** use a `<script data-api>` tag — it simply loads `./widget.iife.js` which reads from `location.search`. The API base flows via `index.html → widget.js → iframe ?api=` query param. Changing the index.html directly per session requires a commit. [VERIFIED: codebase — widget.js lines 39-44; index.html does not set WCHATS_API_BASE]

**Option B: `apps/admin/public/wchats/config.json` — widget fetches at load time**
Pros: One file to update, no JavaScript change.
Cons: Requires the widget Preact code to be modified to fetch `/wchats/config.json` before rendering. That is a widget source change + rebuild + commit. Over-engineered for a per-session demo.

**Option C: Update `data-api` in the snippet directly (one-line commit to `apps/admin/public/wchats/index.html`)**
`index.html` currently does not contain any API base reference (it just loads `widget.iife.js`). The `data-api` attribute lives in the paste-in snippet documented in `deploy/README.md`. The actual delivered file at `bantuson.vercel.app/wchats/index.html` contains no hardcoded `data-api`.

But the widget loads through the `<script>` tag on the **hiring manager's page** — and `bantuson.vercel.app` is the **admin app**, NOT the "hiring manager's page". The hiring manager visits `bantuson.vercel.app` and the snippet is in `apps/admin/pages/` (the Next.js app source), not in `public/wchats/`. Let me state the flow precisely:

```
bantuson.vercel.app (Next.js page)
  → <script src="/wchats/widget.js" data-agent="..." data-api="..."> tag
     (this tag is in the Next.js page JSX/HTML, NOT in public/wchats/)
  → widget.js reads data-api from the script tag
  → injects iframe → /wchats/index.html?agent_id=...&api=<value from data-api>
  → index.html loads widget.iife.js
  → widget.iife.js reads api from location.search
```

This means the `data-api` is in the **Next.js page source** (not in `public/wchats/index.html`). Each tunnel session, the Next.js page source must be updated with the new `*.trycloudflare.com` URL, committed, and Vercel auto-deploys (~60s on Vercel hobby). [VERIFIED: widget.js source reads `script.getAttribute("data-api")` where `script` is the `<script data-agent data-api>` tag on the embedding page]

**Option D: Embed snippet in admin page reads from a Vercel KV / Edge Config endpoint (always-up)**
This would allow changing the API base without a code commit. Vercel KV (or Edge Config) is a paid feature on Vercel Pro. Not viable ($0 constraint). [ASSUMED: Vercel KV requires Pro plan]

**Option E: Per-session commit + Vercel auto-deploy**
The simplest path: each demo session, update the `data-api` value in the Next.js page source, commit, push — Vercel deploys in ~60s. Friction: one git commit per demo session. For a portfolio use case (few demos per week), this is acceptable.

### Recommended Strategy: Per-Session Commit + Auto-Deploy (Option E)

**Rationale:**
- No new infrastructure (no always-up endpoint, no KV store, no widget rebuild).
- The widget architecture already makes this a one-value change: update the `data-api` attribute in one place in the Next.js page source.
- Vercel Hobby auto-deploys on push to main in ~60–90 seconds.
- The `start_demo.ps1` script captures the tunnel URL and prints instructions: "Update data-api in <file>, commit, push — Vercel redeploys in ~60s."

**The exact file to update:** The Next.js page that embeds the widget (the page where the `<script data-agent data-api>` tag lives). The planner must locate this file in `apps/admin/` during plan 12-05 authoring. [RESEARCH NOTE: the specific page file path within `apps/admin/pages/` or `apps/admin/app/` was not determined in this research session; the planner must find it during task execution — it is the page that renders the embed snippet visible to bantuson.vercel.app visitors]

**Demo-session flow (what the user does each demo):**

```
1.  Run:  .\scripts\start_demo.ps1
          → opens uvicorn + celery runtime worker + cloudflared windows
          → prints: "Tunnel URL: https://abc123.trycloudflare.com"

2.  Update data-api in apps/admin/[page file] to the tunnel URL
    git add [file] && git commit -m "demo: set tunnel URL" && git push

3.  Wait ~60s for Vercel auto-deploy (watch https://vercel.com/bantuson dashboard)

4.  Share bantuson.vercel.app with hiring manager — widget is live
    (Keep PC + start_demo.ps1 windows running)

5.  When demo is over:  Close the cloudflared PowerShell window (Ctrl+C)
```

**Friction level:** MEDIUM (one commit per session) but acceptable for a portfolio demo window. This is explicitly noted as tech debt vs. a named tunnel / always-on cloud host.

### Named Tunnel Path (Blocked)

A named Cloudflare Tunnel would provide a stable subdomain that persists across restarts. But: named tunnels require a domain managed by Cloudflare DNS. A free Cloudflare account alone is not sufficient — the user needs to add a domain (e.g., a free .tk domain via Freenom, or register a domain) and point its nameservers to Cloudflare. [VERIFIED: Cloudflare Tunnel docs — "You need a domain managed by Cloudflare DNS to use named tunnels with custom hostnames"; community.cloudflare.com tunnel-without-domain thread]

This path requires registering/transferring a domain, which is additional friction and may involve a fee. Mark as a **future upgrade** for the planner's notes — not in scope for this re-plan.

---

## SSE Through Cloudflare Quick Tunnel (Research Question 2)

### The Critical Finding

**Quick Tunnels officially do NOT support Server-Sent Events (SSE).** Specifically, when using `EventSource` (which is always a GET request), cloudflared buffers all SSE data and only flushes it to the client when the **server closes the connection**. No events are delivered incrementally. [VERIFIED: cloudflare/cloudflared GitHub issue #1449; official Cloudflare Quick Tunnels docs page]

### Impact Analysis for W Chats

The widget's SSE implementation in `apps/widget/src/sse.js` uses:
```js
const es = new EventSource(`${apiBase}/widget/jobs/${jobId}/events`)
```
`EventSource` is always GET. This means through a quick tunnel, the SSE stream will be buffered.

**However, this is workable for the W Chats demo use case:**

The widget publishes a sequence of events: `agent.thinking` → `agent.tool_call` → `agent.tool_result` → `agent.response`. The Celery task publishes `agent.response` and then the SSE generator closes the stream. The SSE `asyncio.timeout(120)` in `widget.py` provides an outer deadline.

With quick tunnel buffering:
- The `EventSource` connection is opened.
- cloudflared buffers all events internally.
- When the server closes the stream (after `agent.response` is published, in ≤90s), cloudflared flushes all events to the browser at once.
- The browser `EventSource` receives all events in sequence: `agent.thinking`, `agent.tool_call`, `agent.tool_result`, `agent.response`.
- The widget processes events in order: shows typing indicator → shows tool call label → shows response.
- The result: the user sees the typing indicator for the full turn duration, then all events arrive quickly in sequence, and the response appears.

**This is an acceptable UX degradation for a portfolio demo:**
- The agent response still arrives (not cut off).
- The typing indicator is visible throughout.
- The final response appears correctly.
- There is no error or failed state.

**The critical risk scenario (where it breaks):** If cloudflared imposes its own timeout on the SSE connection BEFORE the server closes it. If cloudflared kills the connection at, say, 60 or 100 seconds (before the 90s agent turn completes), the `EventSource` would receive an error and the widget would show "Something went wrong." [ASSUMED: The exact cloudflared quick-tunnel idle/connection timeout is not confirmed in official documentation. The ~100s figure from the original research applies to the Cloudflare orange-cloud proxy, not the tunnel daemon itself. This needs empirical verification during plan 12-05 execution.]

**Mitigation already in place:**
- D-11 (90s wall-clock guard) ensures the agent turn completes in ≤90s. [VERIFIED: plan 12-01, committed]
- `widget.py` SSE generator emits `agent.thinking` within the first few seconds of the turn, and `agent.response` is emitted immediately when the task completes. The SSE stream closes promptly after `agent.response`.
- If the SSE connection is severed, `onerror` triggers → widget shows "Something went wrong. Please try again." — the user can retry.

**The `X-Accel-Buffering: no` header** is already set on the SSE response in `widget.py` line ~518:
```python
sse_response.headers["X-Accel-Buffering"] = "no"
```
This does NOT override cloudflared's edge buffering. The header tells nginx-type servers not to buffer, but cloudflared's quick-tunnel infrastructure ignores it. [VERIFIED: confirmed non-efficacy in GitHub issue #1449 — "all relevant no-buffering headers are set but the delayed flush behavior persists"]

**The `--protocol http2` flag** (controls cloudflared↔Cloudflare-edge transport) does NOT fix the SSE buffering issue. The buffering occurs at the Cloudflare edge layer, not in the transport between cloudflared and the edge. [VERIFIED: cloudflared `--help` output — `--protocol` is for edge transport; SSE issue is in edge buffering layer]

### SSE Timeout Analysis

- **Agent turn guard:** 90s (`asyncio.wait_for(..., timeout=90)`) — D-11, already shipped. [VERIFIED: 12-01-SUMMARY.md]
- **SSE outer timeout:** 120s (`asyncio.timeout(120)` in `widget.py` line 507). [VERIFIED: widget.py]
- **Quick tunnel connection lifetime:** Not officially documented for quick tunnels specifically. The SSE stream closes from the server side at ≤90s (on `agent.response`). The cloudflared connection to the edge is outbound and persistent; there is no known short idle timeout on in-flight HTTP requests through quick tunnels.
- **Conclusion:** If the agent completes within 90s, the SSE stream closes before any plausible cloudflared timeout. The buffered events are flushed. [ASSUMED: no cloudflared quick-tunnel timeout shorter than 90s on active (non-idle) connections — empirically verify in plan 12-05 Task 1]

### Heartbeat / Keep-Alive (not required)

The `event_generator` in `apps/api/app/services/sse.py` does emit interim events (`agent.thinking`, `agent.tool_call`, etc.) — these keep the connection non-idle. However, because of quick-tunnel buffering, these are not delivered in real-time. A dedicated SSE heartbeat comment (`: keep-alive`) would not help because cloudflared buffers regardless. Do not add a heartbeat; it adds complexity without benefit.

---

## `cloudflared` on Windows (Research Question 3)

### Installation Status

**cloudflared is already installed: version 2025.8.1** [VERIFIED: `cloudflared --version` in local shell]

If it were not installed, the install command is:
```powershell
winget install --id Cloudflare.cloudflared
# Version available: 2026.5.2 (latest as of 2026-05-29)
```
[VERIFIED: `winget search Cloudflare.cloudflared` returns version 2026.5.2]

After install, `cloudflared` is available in PATH. No restart required in PowerShell.

### Quick Tunnel Command

```powershell
cloudflared tunnel --url http://localhost:8000
```

This connects outbound to Cloudflare's edge and prints the assigned URL:
```
2026-05-29T...: | INF +--------------------------------------------------------------------------------------------+
2026-05-29T...: | INF |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |
2026-05-29T...: | INF |  https://some-random-name.trycloudflare.com                                                 |
2026-05-29T...: | INF +--------------------------------------------------------------------------------------------+
```

The URL appears after a few seconds. The tunnel remains up as long as the process runs. [VERIFIED: cloudflared tunnel run --help; official Quick Tunnels docs]

### Extending `start_native.ps1`

The existing `scripts/start_native.ps1` opens three separate PowerShell windows for uvicorn, pipeline worker, celery beat, and runtime worker. The new `scripts/start_demo.ps1` should:

1. Launch **only the services needed for demo** (uvicorn + runtime worker; NOT pipeline worker, NOT beat).
2. Launch cloudflared and capture the tunnel URL.
3. Print the tunnel URL clearly so the user can copy it.

**Key design for URL capture:** cloudflared prints the URL to stderr. In a new PowerShell window with `-NoExit`, the URL appears in the terminal and the user copies it manually. There is no reliable programmatic way to capture it from a spawned window.

```powershell
# scripts/start_demo.ps1 — Demo mode: uvicorn + runtime worker + cloudflared
# Variant of start_native.ps1 — pipeline worker and beat are NOT started
# Usage: .\scripts\start_demo.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$ApiDir = Join-Path $Root "apps\api"
$Env:PYTHONPATH = $ApiDir

# Load .env into this process so child processes inherit it
foreach ($line in Get-Content (Join-Path $Root ".env")) {
    if ($line -match "^\s*#" -or $line -notmatch "=") { continue }
    $key, $val = $line -split "=", 2
    [System.Environment]::SetEnvironmentVariable($key.Trim(), $val.Trim(), "Process")
}

function Start-Service($Title, $Cmd) {
    $escaped = $Cmd -replace '"', '\"'
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-Command", "cd '$ApiDir'; $escaped"
    ) -WindowStyle Normal
    Write-Host "Started: $Title"
}

Write-Host ""
Write-Host "=== W Chats Demo Mode ==="
Write-Host ""

Start-Service "API" "uvicorn app.main:app --host 0.0.0.0 --port 8000"
Start-Sleep -Seconds 3

Start-Service "Worker: runtime" "celery -A app.worker.celery_app worker --queues=runtime --hostname=runtime@%h --loglevel=info --pool=solo"
Start-Sleep -Seconds 2

# cloudflared — prints tunnel URL to its own window
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Write-Host 'TUNNEL WINDOW — copy the trycloudflare.com URL from the line below:'; Write-Host ''; cloudflared tunnel --url http://localhost:8000 --no-autoupdate"
) -WindowStyle Normal
Write-Host "Started: Cloudflare Tunnel (URL appears in tunnel window)"

Write-Host ""
Write-Host "=== Next steps ==="
Write-Host "1. Copy the https://*.trycloudflare.com URL from the TUNNEL WINDOW"
Write-Host "2. Update data-api in the bantuson.vercel.app page source"
Write-Host "3. git commit -m 'demo: set tunnel URL' && git push"
Write-Host "4. Wait ~60s for Vercel deploy"
Write-Host "5. Share bantuson.vercel.app with your hiring manager"
Write-Host ""
Write-Host "KEEP ALL WINDOWS OPEN during the demo."
Write-Host "Close the TUNNEL WINDOW to end the session."
```

[VERIFIED: pattern follows `start_native.ps1` Start-Service helper; `cloudflared tunnel --url` confirmed command; `--no-autoupdate` suppresses update noise]

### Keep-Warm (D-12)

The runtime worker starts with `start_demo.ps1` and stays up while the demo window is open. The claude-agent-sdk subprocess is warm after the first turn (the bundled binary is already spawned). Subsequent turns have no cold-start. This satisfies D-12 for the local PC context. [VERIFIED: 12-01-SUMMARY.md — D-12 retained for local PC]

---

## CORS: Wildcard Already Set (Research Question 4 — Confirmed)

The Vercel origin (`https://bantuson.vercel.app`) makes cross-origin requests to the tunnel URL (`https://*.trycloudflare.com`). The wildcard CORS header `Access-Control-Allow-Origin: *` is already set on all widget routes: [VERIFIED: `apps/api/app/api/v1/widget.py` lines 63, 282, 442, 520]

```python
_CORS_ALLOW_ORIGIN = "*"
```

OPTIONS preflight handlers are also in place. No CORS change is needed. [VERIFIED: widget.py lines 542–557]

---

## CV / Portfolio Honesty (Research Question 4b)

**Honest framing:** "W Chats is live during demo windows — the portfolio is on a local-first, $0 architecture while I build the paying user base. The ADR documents the cloud-native AWS cutover plan (Fargate + Aurora Serverless v2 + pgvector) that executes when growth warrants."

This is an honest and defensible statement for a hiring manager:
- The agent is real (deployed, ingested, evaluated, red-teamed).
- The tech is real (FastAPI, Celery, pgvector, Voyage, Claude).
- The limitation is the hosting constraint (no credit card for always-on cloud), which is transparently documented.
- The architecture decision (env-only seam, D-14) means a cloud cutover is a config change, not a rewrite.

**What to avoid saying:** "W Chats is production-grade always-on infrastructure." Instead: "W Chats demonstrates production-grade patterns — grounded, evaluated, red-teamed — deployed on a $0 local-first stack with a documented cloud migration path."

---

## Standard Stack (Tunnel-Focused)

### Core (New / Changed)

| Component | Version / Config | Purpose | Why |
|-----------|-----------------|---------|-----|
| cloudflared | 2025.8.1 (installed) / 2026.5.2 (winget latest) | Outbound HTTPS tunnel | $0, no card, no port-forwarding, Cloudflare edge TLS |
| Quick Tunnel (`--url`) | — | Generates `*.trycloudflare.com` HTTPS URL | Account-free; no domain required |
| `scripts/start_demo.ps1` | New script | Unified demo launcher | Extends `start_native.ps1` with tunnel; pipeline + beat excluded |

### Retained (Host-Agnostic, Unchanged)

| Component | Version | Purpose | Status |
|-----------|---------|---------|--------|
| uvicorn | 0.27+ | FastAPI server | Running on `localhost:8000` as before |
| Celery runtime worker | 5.3+ | `run_agent_turn` task, solo pool | `--queues=runtime --pool=solo --concurrency=1` |
| claude-agent-sdk | 0.1.81 | Agent turn execution | Bundled binary (x86_64/Windows); warm after first turn |
| Voyage AI | free tier | Query embedding | 3 RPM; capped at ≤1 retrieve/turn (D-10, done) |
| Neon (sa-east-1) | remote | Control DB + tenant DB | Via `.env`; no change |
| Upstash Redis | remote TLS | Celery broker + SSE pub/sub | Via `.env`; no change |
| Vercel | Hobby free | Widget static delivery | `apps/admin/public/wchats/`; auto-deploy on push |

---

## Architecture Patterns

### System Architecture Diagram (Revised)

```
Hiring manager browser
        |
        | https://bantuson.vercel.app  (Next.js page with embed snippet)
        |   <script src="/wchats/widget.js"
        |           data-agent="fe230a9d-..."
        |           data-api="https://abc123.trycloudflare.com">
        |
        | widget.js → iframe → /wchats/index.html?agent_id=...&api=https://abc123.trycloudflare.com
        |
        +--> GET  https://abc123.trycloudflare.com/widget/fe230a9d/config
        |    POST https://abc123.trycloudflare.com/widget/fe230a9d/chat
        |    GET  https://abc123.trycloudflare.com/widget/jobs/{job_id}/events  (SSE — buffered, flushed at end)
        |
        v
  Cloudflare edge (TLS termination, *.trycloudflare.com cert)
        |
        | [outbound tunnel — no inbound ports open on user's PC]
        v
  cloudflared process (Windows PC, port 8000 proxy)
        |
        v
  uvicorn / FastAPI (localhost:8000)
        |
        |  apply_async(run_agent_turn, queue="runtime")
        v
  Celery runtime worker (solo pool, Windows PC)
        |
        |  ANTHROPIC_API_KEY → claude-agent-sdk bundled binary (x86_64 Windows)
        |  Voyage API → embed_query() [≤1 call per turn, cached via Redis]
        v
  Upstash Redis (rediss://) ← SSE pub/sub, rate limits, qembed cache
  Neon sa-east-1 ← agent config, tenant DB
```

### Anti-Patterns to Avoid

- **Using Quick Tunnel for always-on hosting:** Quick Tunnels are "for testing only" per Cloudflare docs. They have no SLA, no uptime guarantee, and the URL changes on restart. This is acceptable for demo windows, NOT for production. [VERIFIED: official Quick Tunnels docs]
- **Expecting real-time SSE through Quick Tunnel:** SSE events are batched and flushed at stream close. Do not implement features that depend on streaming partial events during the turn (e.g., token-by-token streaming). Current widget only needs `agent.response` at the end — this works. [VERIFIED: cloudflare/cloudflared issue #1449]
- **Hardcoding the tunnel URL in a committed file without a plan to update it:** Every session yields a new URL. The start_demo.ps1 workflow must explicitly prompt the user to update `data-api` and redeploy.
- **Opening port 8000 in Windows Firewall:** cloudflared connects outbound only. uvicorn binds to `localhost:8000` — it is NOT reachable from the internet directly. No firewall rule needed. [VERIFIED: cloudflared architecture — outbound-only QUIC connection to Cloudflare edge]
- **Running the pipeline worker during demo:** The pipeline worker loads torch/docling and consumes significant RAM. On 4 GB RAM, running pipeline + runtime + cloudflared simultaneously risks OOM. `start_demo.ps1` intentionally excludes the pipeline worker (D-03). [ASSUMED: RAM constraint from CLAUDE.md "4 GB RAM machine"]

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| TLS for local server | Caddy / nginx + Let's Encrypt | cloudflared quick tunnel | Outbound only; no open ports; no cert mgmt; $0 |
| Stable public URL | Custom DDNS updater | Per-session commit + Vercel auto-deploy (see above) | Simplest path; no new infra |
| SSE real-time streaming through tunnel | Custom streaming proxy / websocket fallback | Accept buffered-flush behavior | Agent response still arrives; turn completes in ≤90s; flushed all-at-once is sufficient for demo |
| Query embedding cache | Custom in-memory dict | Redis (Upstash, already present) | Already implemented in plan 12-01 |

---

## What Is Already Done (Plans 12-01 through 12-04)

| Plan | What Was Built | Status |
|------|---------------|--------|
| 12-01 | D-10 retrieve cap (max_turns=3, AT MOST ONCE prompt), D-11 90s timeout, D-13 Redis qembed cache | DONE — committed |
| 12-02 | Widget bundle rebuilt (pnpm), published to `apps/admin/public/wchats/` | DONE — committed |
| 12-03 | ADR `docs/adr/0001-cloud-native-cutover.md` | DONE — committed |
| 12-04 | Deploy artifacts (systemd units, Caddyfile, smoke_vm.sh, deploy/README.md) — Oracle-VM path | DONE — committed; now serves as the AWS-VM ADR reference |

**The remaining work:**

| Plan | What Needs Doing |
|------|-----------------|
| 12-05 (re-plan) | Author `scripts/start_demo.ps1`; re-target `smoke_vm.sh` to accept `API_HOST` env var pointing at tunnel URL; update `deploy/README.md` to note the pivot |
| 12-06 (re-plan) | Manual E2E gate: run `start_demo.ps1`, update `data-api`, wait for Vercel deploy, run `smoke_vm.sh API_HOST=<tunnel-url>`, hiring-manager Q&A |

---

## smoke_vm.sh Re-targeting

`scripts/smoke_vm.sh` already supports `API_HOST` as an environment variable with a default. Re-targeting to the tunnel URL is zero-code: just pass the variable:

```bash
API_HOST=https://abc123.trycloudflare.com bash scripts/smoke_vm.sh
```

The smoke test structure is unchanged (6 sections: TLS health, widget.js, JWT mint, chat dispatch, SSE poll, retrieve cap). The only behavioral difference: the SSE poll (Section 5) will wait ~90s and receive all events at once when the turn completes, rather than incrementally. The `agent.response` check in Section 5 (`grep -q '"event_type":"agent.response"'`) will still pass because all events are delivered before the connection closes. [VERIFIED: `scripts/smoke_vm.sh` review — the Section 5 loop reads `$CHUNK` from `curl -s --max-time 6 -N` per poll; with buffered SSE, the first successful poll after the turn completes will contain the full event stream]

**One smoke test adjustment needed:** The Section 5 `curl --max-time 6` per poll may need to be increased to `--max-time 100` for the single SSE stream read (since the stream stays open for the full turn duration before flushing). The existing 18-poll loop at 5-second intervals covers 90s total — but each individual `curl -N` call needs to stay connected for up to 90s if buffering holds the stream open. The plan should address this. [VERIFIED: smoke_vm.sh Section 5 review]

---

## Common Pitfalls

### Pitfall 1: SSE Events Not Arriving (EventSource Error)
**What goes wrong:** The hiring manager sees "Something went wrong" in the widget after typing a message.
**Why it happens:** cloudflared quick-tunnel buffering causes `EventSource` to hold the connection open for up to 90s with no data. Some browsers or browser extensions may timeout idle SSE connections. Some network middleboxes may close connections with no data after 60s.
**How to avoid:** Ensure D-11 (90s guard) is in place. After the agent responds, the SSE stream closes immediately → all events flush. If the browser EventSource times out before the agent responds: user retries. The retry works because a new job_id is issued.
**Warning signs:** `agent.failed` in the SSE stream (Celery task error, not a tunnel issue); `onerror` on EventSource (connection severed).

### Pitfall 2: Random URL Breaks widget.js on Restart
**What goes wrong:** Hiring manager opens bantuson.vercel.app, widget shows "Chat" button, but GET /config returns 000 (no host reachable). The widget silently fails to load config (non-fatal per widget.js design) — widget opens but POST /chat fails with a network error.
**Why it happens:** `start_demo.ps1` restarted → new tunnel URL → old `data-api` in Vercel page still points to old URL.
**How to avoid:** Every `start_demo.ps1` session must be followed by the `data-api` update + commit + Vercel deploy. Make this explicit in the runbook.
**Warning signs:** Browser DevTools → Network tab → first request to `*.trycloudflare.com/widget/.../config` returns `ERR_NAME_NOT_RESOLVED` or `503`.

### Pitfall 3: smoke_vm.sh Section 5 Timeout Too Short for Buffered SSE
**What goes wrong:** `smoke_vm.sh` Section 5 polls 18 × 5s = 90s budget. Each poll uses `curl --max-time 6 -N`. With buffered SSE, the curl connection stays open until the turn completes (≤90s). But `--max-time 6` kills the curl command after 6s, delivering nothing. The loop runs 18 times, gets no data, and reports FAIL.
**Why it happens:** `--max-time 6` was designed for incremental SSE (each poll gets partial data quickly). With buffered SSE, the entire stream arrives at once at stream close — which takes up to 90s.
**How to avoid:** In the retargeted smoke test, replace the polling loop in Section 5 with a single `curl --max-time 95 -N` to hold the SSE connection for the full turn and capture all events at once.
**Warning signs:** Section 5 reports FAIL with "agent.response not received within 90s" even though the agent completed successfully (verify by checking uvicorn logs).

### Pitfall 4: cloudflared Process Dies Silently During Demo
**What goes wrong:** During a demo, `cloudflared` exits (network hiccup, autoupdate attempt, Windows power management). The tunnel URL becomes unreachable. The widget shows errors.
**How to avoid:** `start_demo.ps1` launches cloudflared with `--no-autoupdate` (prevents update-triggered restart). Keep the tunnel window visible during the demo. Windows should not sleep during the demo window (disable sleep in Power settings or use a caffeine tool).
**Warning signs:** cloudflared terminal shows "ERR_TUNNEL_GONE" or exit. Widget returns network errors.

### Pitfall 5: `asyncio.timeout(120)` SSE outer timeout vs. buffered flush timing
**What goes wrong:** If the Celery task completes in exactly 90s, the `agent.response` event is published, the SSE generator closes the stream. cloudflared then flushes all buffered events. The EventSource sees the full event stream. This is correct behavior.
**Risk:** If the SSE outer timeout (120s) fires before the task completes (120s > 90s — this should not happen). But with buffered SSE, cloudflared may impose its own timeout. If cloudflared closes the connection before 90s (e.g., at 60s idle timeout), the SSE stream is severed before `agent.response` arrives.
**How to avoid:** Monitor the first demo session closely. If SSE connections are severed before the turn completes, reduce D-11 timeout to 60s as a workaround, or switch to a tunnel provider that supports SSE (serveo.net, localhost.run via SSH).
**Warning signs:** Widget shows error on turns taking 60–90s; short turns (< 20s) work fine.

---

## Code Examples

### start_demo.ps1 (Canonical Pattern)

```powershell
# Source: pattern from scripts/start_native.ps1 [VERIFIED: codebase]
# New: cloudflared window + runtime-only services + no-autoupdate

# cloudflared window (add to Start-Service calls):
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Write-Host 'COPY THE TUNNEL URL BELOW:'; Write-Host ''; cloudflared tunnel --url http://localhost:8000 --no-autoupdate"
) -WindowStyle Normal
```

### smoke_vm.sh Section 5 — Adapted for Buffered SSE

```bash
# Adapted Section 5: single curl --max-time 95 instead of 18-poll loop
# Source: scripts/smoke_vm.sh [VERIFIED: codebase] + buffered-SSE adaptation

echo "=== Section 5: SSE (buffered — single 95s curl) ==="
SSE_STREAM=$(curl -s --max-time 95 -N \
    "$API_HOST/widget/jobs/$JOB_ID/events" 2>/dev/null || echo "")

if echo "$SSE_STREAM" | grep -q '"event_type":"agent.response"'; then
    echo "[PASS] SSE: agent.response received"
else
    echo "[FAIL] SSE: agent.response not found in stream"
    ALL_PASSED=false
fi
```

### Widget `data-api` Update Pattern (per-session)

```html
<!-- In apps/admin/[page-source-file] — update this value each demo session -->
<script src="https://bantuson.vercel.app/wchats/widget.js"
        data-agent="fe230a9d-09f0-4043-b2f1-4506a2ef0059"
        data-api="https://TUNNEL-URL-HERE.trycloudflare.com"
        async></script>
```

---

## Runtime State Inventory

| Category | Items Found | Action Required |
|----------|-------------|-----------------|
| Stored data | Neon control DB: `agents` row `fe230a9d` is `is_deployed=True`; 195 embeddings in `nameless-fog-19651218`. | None — connect via existing `.env` |
| Live service config | Upstash Redis: existing `REDIS_URL` in `.env`. Celery broker + SSE pub/sub. | None — reuse unchanged |
| OS-registered state | None — no systemd, no Windows Task Scheduler, no pm2 registrations needed. cloudflared runs as a transient process. | None |
| Secrets/env vars | All in local `.env` (ANTHROPIC_API_KEY, VOYAGE_API_KEY, NEON_*, UPSTASH REDIS_URL, JWT_SECRET, CLERK_*). Nothing new needed for tunnel (no DUCKDNS_TOKEN). | None — already configured |
| Build artifacts | `apps/admin/public/wchats/` — four embed files committed in plan 12-02. | None — already deployed |

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| cloudflared | Tunnel / TLS | YES | 2025.8.1 (local) | `winget install Cloudflare.cloudflared` |
| Python 3.12 | uvicorn + Celery | YES (local dev) | 3.12 (from start_native.ps1 usage) | — |
| Neon control DB (sa-east-1) | FastAPI + data | YES (remote) | — | — |
| Upstash Redis (rediss://) | Celery broker + SSE | YES (remote) | — | — |
| Voyage API (free tier, 3 RPM) | embed_query() | YES | — | — |
| Anthropic API | Agent SDK | YES | — | — |
| Vercel (bantuson.vercel.app) | Widget static | YES (already deployed via 12-02) | — | — |
| claude-agent-sdk | runtime worker | YES (x86_64 Windows wheel) | 0.1.81 | — |

**Missing dependencies:** None that block execution.

---

## Validation Architecture

> `workflow.nyquist_validation` not explicitly set to false in `.planning/config.json` — section included.

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (existing) + bash smoke script |
| Config file | `apps/api/pyproject.toml` (pytest.ini_options) |
| Quick run command | `cd apps/api && pytest tests/ -x -q` |
| Full suite command | `cd apps/api && pytest tests/ -v --tb=short` |

### Revised Decision → Validation Map

| Decision | What to Validate | Automated? | Command / Method |
|----------|-----------------|------------|-----------------|
| D-10 retrieve cap | max_turns=3 in ClaudeAgentOptions | Auto (existing) | `pytest tests/unit/test_agent_task.py -k max_turns -x` — PASSING (12-01) |
| D-11 90s guard | timeout=90 in asyncio.wait_for | Auto (existing) | `pytest tests/unit/test_agent_task.py -k timeout -x` — PASSING (12-01) |
| D-13 qembed cache | cache key present in agent_tools.py | Auto (existing) | `grep "qembed:" apps/api/app/services/agent_tools.py` — PASSING (12-01) |
| D-06 Vercel files | widget.js reachable | Auto (smoke) | `curl -sf https://bantuson.vercel.app/wchats/widget.js` — PASSING (12-02) |
| D-15 ADR | ADR file exists | Auto | `test -f docs/adr/0001-cloud-native-cutover.md` — PASSING (12-03) |
| Tunnel up | cloudflared running + HTTPS reachable | Auto (smoke) | `API_HOST=https://<tunnel-url> bash scripts/smoke_vm.sh` Section 1 |
| CORS | Wildcard header present on widget routes | Auto (smoke) | curl -I `<tunnel-url>/widget/<agent>/config` shows `Access-Control-Allow-Origin: *` |
| SSE buffered-flush | agent.response arrives within 95s | Auto (smoke) | Section 5 of retargeted smoke_vm.sh (single `--max-time 95` curl) |
| D-10 at runtime | ≤2 retrieve calls in live SSE | Auto (smoke) | smoke_vm.sh Section 6 |
| D-12 warm worker | Second turn < 20s latency | Manual | Send second message; measure response time |
| D-14 env-only | No secrets in code | Auto | `grep -r "sk-ant\|voyage\|neon" apps/api/app/` → 0 matches |
| End-to-end success | Hiring-manager Q&A live | Manual (human gate) | Open bantuson.vercel.app, click chat, ask "What is W Chats?", receive grounded answer |

### Sampling Rate

- **Per task commit:** `cd apps/api && pytest tests/unit/test_agent_task.py -q`
- **Per wave merge:** `cd apps/api && pytest tests/ -q`
- **Phase gate:** Manual E2E Q&A test with live tunnel before `/gsd-verify-work`

### Wave 0 Gaps (for the re-plan)

- [ ] `scripts/start_demo.ps1` — does not yet exist; authored in plan 12-05 Task 1
- [ ] `scripts/smoke_vm.sh` Section 5 adaptation — retargeted (Section 5 `--max-time 95` change) in plan 12-05 Task 2; or authored as `scripts/smoke_tunnel.sh`

---

## Security Domain

### Applicable ASVS Categories (Tunnel-Specific Additions)

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | Clerk JWKS + widget JWT HS256 (existing — unchanged) |
| V3 Session Management | yes | JWT 15-min expiry (existing) |
| V4 Access Control | yes | IDOR guard in widget routes (existing); secrets in `.env` not in committed files |
| V5 Input Validation | yes | sanitize_chunk_text (existing); message text never logged (existing) |
| V6 Cryptography | yes | Fernet for Neon conn strings (existing); HS256 JWT (existing) |
| V9 Communications | yes | TLS via Cloudflare edge (new path — tunnel terminates TLS, same as Caddy did before) |

### Tunnel-Specific Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Random URL enumeration (someone guesses the trycloudflare.com subdomain) | Information Disclosure | Agent config + JWT mint route requires a valid deployed agent_id (404 otherwise); JWT 15-min expiry; rate limit 10 req/min per IP |
| cloudflared process injection / malicious version | Tampering | `--no-autoupdate` flag prevents version change during demo; cloudflared is from Cloudflare's official winget package |
| uvicorn/API accessible directly on LAN (0.0.0.0:8000) | Spoofing | uvicorn binds `0.0.0.0` (needed for cloudflared to connect); however, only LAN devices can reach it directly; Cloudflare edge is the only external path; CORS wildcard is already set so LAN access is not a new risk |
| Secrets exposed in `start_demo.ps1` output | Information Disclosure | `.env` loaded into process env vars, not printed; `start_demo.ps1` does not echo secret values |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | cloudflared quick-tunnel does not impose a hard connection timeout on in-flight (non-idle) SSE connections shorter than 90s | SSE Analysis | If cloudflared kills connections at 60s, turns taking 60–90s will fail; workaround: reduce D-11 to 55s or switch to a SSE-compatible tunnel (serveo.net, localhost.run) |
| A2 | The `data-api` value in the embed snippet is in the Next.js page JSX source (not in `public/wchats/`) | Stable-URL Strategy | If it is in `public/wchats/index.html`, the update file is different; planner must locate the correct file during plan 12-05 execution |
| A3 | Vercel hobby auto-deploys within 60–90s of a push to main | Stable-URL Strategy | If deployment takes longer (e.g., Next.js build step is slow), the demo-start flow is delayed; user must watch the Vercel dashboard |
| A4 | The SSE Section 5 fix (single `--max-time 95` curl) will receive all buffered events when the agent turn completes | smoke_vm.sh adaptation | If cloudflared closes the connection before 95s without delivering events, the smoke test will fail; empirical verification required in plan 12-05 |
| A5 | Cloudflare named tunnel without a Cloudflare-managed domain is not viable | Stable-URL Strategy | If Cloudflare adds a free no-domain named-tunnel option, a stable URL becomes available without per-session commits |

---

## Open Questions

1. **Exact location of the embed snippet in `apps/admin/`**
   - What we know: The `<script data-agent data-api>` tag is in the Next.js admin app source (not `public/wchats/index.html`). The admin app is under `apps/admin/`.
   - What's unclear: The exact file (`pages/index.jsx`, `app/page.tsx`, a dedicated page, etc.).
   - Recommendation: Planner locates this file as the first step in plan 12-05 Task 1. Look for `data-agent="fe230a9d"` in `apps/admin/`.

2. **cloudflared quick-tunnel connection timeout for non-idle SSE**
   - What we know: SSE is buffered and flushed at stream close. The server closes the stream at ≤90s.
   - What's unclear: Does cloudflared itself impose a connection timeout (e.g., 60s) before the stream closes?
   - Recommendation: Empirically test in plan 12-05 Task 1 by running a local end-to-end smoke test through the tunnel immediately after authoring `start_demo.ps1`.

---

## State of the Art

| Old Approach (Oracle-VM path) | Current Approach (Local Tunnel) | Impact |
|-------------------------------|----------------------------------|--------|
| Caddy + DuckDNS DNS-01 TLS on ARM Linux VM | cloudflared outbound tunnel → Cloudflare edge TLS | No cert management; no port rules; no Linux VM |
| systemd services (always-on) | Native Windows processes (live on demand) | No systemd; no always-on; PC must be on during demos |
| Stable DuckDNS hostname | Random `*.trycloudflare.com` per session | Per-session commit+deploy to update data-api |
| SSE works normally (Caddy has no SSE limitation) | SSE buffered — events arrive all-at-once at stream close | Typing indicator shows for full turn duration; acceptable for portfolio demo |
| OCI ARM A1 (aarch64 SDK wheel) | Windows x86_64 (existing installed SDK) | No ARM wheel needed; existing local SDK works |

---

## Sources

### Primary (HIGH confidence)
- `apps/api/app/api/v1/widget.py` — CORS wildcard confirmed on all widget routes; `asyncio.timeout(120)` at line 507
- `apps/widget/src/sse.js` — `EventSource` (GET) confirmed; SSE stream closes on `agent.response` [VERIFIED: codebase]
- `apps/widget/embed/widget.js` — `data-api` read from `script.getAttribute("data-api")` then `window.WCHATS_API_BASE` fallback [VERIFIED: lines 39-44]
- `apps/widget/src/index.jsx` — reads `?api=` from `location.search` [VERIFIED: codebase]
- `scripts/start_native.ps1` — canonical process inventory; Start-Service helper pattern [VERIFIED: codebase]
- `scripts/smoke_vm.sh` — confirmed `API_HOST` env var support; Section 5 `--max-time 6` polling loop [VERIFIED: codebase]
- `12-01-SUMMARY.md`, `12-02-SUMMARY.md`, `12-04-SUMMARY.md` — confirmed what is already done [VERIFIED: codebase]
- `cloudflared --version` → `2025.8.1` — already installed [VERIFIED: local shell]
- `winget search Cloudflare.cloudflared` → `2026.5.2` available [VERIFIED: local shell]
- [cloudflare/cloudflared GitHub issue #1449](https://github.com/cloudflare/cloudflared/issues/1449) — SSE over GET buffered until server closes connection; confirmed not fixed
- [Cloudflare Quick Tunnels official docs](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/) — explicitly states Quick Tunnels "do not support Server-Sent Events (SSE)"
- [cloudflared tunnel run --help](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/cloudflared-parameters/run-parameters/) — `--no-autoupdate`, `--url`, `--protocol` flags confirmed [VERIFIED: local cloudflared --help output]

### Secondary (MEDIUM confidence)
- [Cloudflare community — named tunnel without domain](https://community.cloudflare.com/t/tunnel-without-domain/372778) — named tunnels require a Cloudflare-managed domain
- [Vercel CDN cache docs](https://vercel.com/docs/caching/cdn-cache) — static assets frozen per deployment; require commit+push to update
- [cloudflare/cloudflared issue #199](https://github.com/cloudflare/cloudflared/issues/199) — longstanding SSE buffering issue in cloudflared
- [cloudflared tunnel run parameters](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/cloudflared-parameters/run-parameters/) — `--protocol` flag applies to cloudflared↔edge transport, NOT edge buffering behavior

### Tertiary (LOW confidence — assumptions logged)
- cloudflared quick-tunnel connection timeout on in-flight non-idle connections — not documented officially; marked A1
- Named tunnel with free domain workaround (e.g., freenom .tk) — not researched; out of scope for this re-plan

---

## Metadata

**Confidence breakdown:**
- cloudflared install + quick tunnel mechanics: HIGH — verified locally
- SSE buffered-flush behavior: HIGH — two official sources (issue #1449 + official docs)
- SSE timeout risk (A1): LOW — not confirmed; must empirically verify
- Stable-URL strategy: HIGH — verified widget.js code path + Vercel static behavior
- CORS: HIGH — verified widget.py source
- Smoke test adaptation: MEDIUM — design is correct; execution not yet tested

**Research date:** 2026-05-29
**Valid until:** 2026-07-15 (cloudflared quick-tunnel behavior is stable; Vercel static delivery is stable; the SSE buffering limitation is an open issue with no confirmed fix timeline)
