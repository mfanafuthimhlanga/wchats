# Phase 12: Production Go-Live (W Chats) - Context

**Gathered:** 2026-05-29
**Status:** Ready for planning

<domain>
## Phase Boundary

Make **W Chats** publicly reachable and embeddable so a hiring manager can open the widget on **bantuson.vercel.app** and chat with the **already-deployed** agent (`fe230a9d-09f0-4043-b2f1-4506a2ef0059`) live. The product is branded **W Chats** (Veridian is the old internal name).

**In scope:**
- Host the FastAPI API + the **runtime** Celery worker on a public, always-on, $0 host (warm so the Agent SDK doesn't cold-start).
- Serve TLS/HTTPS for the API (the widget runs on an https Vercel page → http API = mixed-content block).
- Publish the embed delivery layer (loader + host page + bundle) on Vercel `public/wchats/`.
- Make live agent turns reliably complete **without paying** (work within Voyage free tier).
- Preserve an env/interface seam + an ADR so a later cloud-native AWS migration is config-not-rewrite.
- Verify a real hiring-manager Q&A end-to-end through the public widget path.

**Out of scope (future phases / other commands):**
- AWS migration build (Fargate / Aurora+RLS / Bedrock) — documented only, via the ADR.
- Hosting the **pipeline** worker (torch/docling) — run on-demand locally for occasional new-agent ingestion.
- Fixing M6 eval harness / M8 checklist orchestrator / M8 eval-column bug — separate `/gsd-debug`.
- Any paid service (Voyage payment method, Bedrock, paid host tiers).
</domain>

<decisions>
## Implementation Decisions

### Backend hosting
- **D-01:** Host on an **Oracle Cloud Always Free VM** (ARM Ampere, up to 24 GB free, always-on — no sleep). Genuinely free forever; enough RAM to keep uvicorn + runtime worker warm. Chosen over Render-free (sleeps ~50s cold start) and a tunnel (PC-dependent, flagged as tech debt).
- **D-02:** On the VM, run **uvicorn (API)** + **celery runtime worker** as **always-on `systemd` services** (NOT Docker — mirrors the existing local `scripts/start_native.ps1` native-process pattern; the 4 GB-RAM no-Docker constraint extends to "don't require Docker"). The VM may run Docker if a plan finds it strictly easier, but native + systemd is the default.
- **D-03:** **Pipeline worker is NOT hosted.** It (torch/docling) runs on-demand locally (or manually on the VM) only when ingesting a new agent. The hosted footprint stays light — chat path only (`runtime` queue).
- **D-04:** Keep using the **existing remote infra** — Neon control DB (sa-east-1) + Upstash Redis. No DB/Redis rewrite; the VM connects to them via the same env vars. (Optional: a local Redis on the VM is allowed if Upstash latency/limits bite, but default is reuse Upstash.)
- **D-05:** **TLS is required.** The API must be served over HTTPS (widget origin is https). Default approach: a reverse proxy on the VM (Caddy or nginx + Let's Encrypt) on a hostname, OR Cloudflare (free) in front for TLS + a free hostname. Planner/researcher to choose; a raw http IP will NOT work from the Vercel page.

### Widget delivery
- **D-06:** Serve the embed folder from **Vercel `public/wchats/`** (same domain as bantuson.vercel.app, zero new accounts). Files: `widget.js` (loader), `index.html` (iframe host), `widget.iife.js`, `widget.css`.
- **D-07:** The paste-in snippet sets **`data-api`** to the VM's public HTTPS base URL at runtime (no rebuild to repoint). `<script src="https://bantuson.vercel.app/wchats/widget.js" data-agent="fe230a9d-…" data-api="https://<api-host>" async></script>`.
- **D-08:** The existing `apps/widget/embed/` files (drafted this session) are the **starting implementation** — review/verify within this phase; rebuild the bundle with **pnpm** (`pnpm --filter veridian-widget build`) if widget src changes, never npm.

### Live-answer reliability — the $0 path (no Voyage card)
- **D-09:** **Do NOT add a Voyage payment method or switch to a paid embedder.** The corpus is already embedded (195/195); live, only the per-turn **query** embed hits Voyage. At 3 RPM that is sufficient for low-traffic portfolio chat **provided the agent does few retrieves per turn.**
- **D-10:** **Cap retrieves-per-turn** for the customer agent (the live failure was 6 retrieves in one turn → exceeded 3 RPM). Limit via the agent loop/tool/prompt to ~1 (max 2) retrieve calls per turn. (Researcher: locate where `run_agent_turn` / the Agent SDK tool loop allows repeated `retrieve`.)
- **D-11:** **Raise the agent-turn wall-clock guard** in `run_agent_turn` (currently `asyncio.wait_for(..., timeout=30)`, per [04-03]) to ~90s so a single retrieve + answer completes despite SDK warm-up.
- **D-12:** **Keep the runtime worker warm** (always-on systemd service on the VM, D-02) so the Agent SDK subprocess isn't cold on each turn.
- **D-13:** Optional free hardening: cache query embeddings (repeat queries cost no Voyage call). Planner's discretion.

### Cloud-native flip seam
- **D-14:** **Env-only config** — the app already reads all config from env; keep it. No data-layer refactor this phase.
- **D-15:** Write an **ADR** (e.g., `docs/adr/0001-cloud-native-cutover.md` or `.planning/adr/…`) documenting: the cloud-native AWS target (ECS Fargate compute, Aurora Serverless v2 + pgvector with RLS/schema-per-tenant, Aurora fast clones replacing Neon eval branching, Bedrock for Claude+embeddings), AND the **trigger threshold** for the per-tenant-Neon → pooled-Aurora cutover (e.g., ~low-hundreds of tenants / cost or Neon project-limit signal). This ADR IS the "flip a switch" plan and doubles as a portfolio artifact.

### Claude's Discretion
- Exact reverse-proxy choice (Caddy vs nginx vs Cloudflare tunnel-for-TLS), VM image/shape specifics, systemd unit layout, and the precise retrieve-cap mechanism — resolve during research/planning.
- Whether to run a local Redis on the VM vs reuse Upstash (default reuse Upstash).
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project rules & state
- `CLAUDE.md` — non-negotiables (no Docker locally, acks_late+idempotency, conn-strings never in task args, two queues, Langfuse v4, Ragas 0.4.x, no pg_search). Note: "no Docker" is a *local 4 GB* constraint; the VM should still prefer native processes.
- `.planning/PROJECT.md` — product vision, per-tenant Neon decision, "Preact widget <20kb" deliverable.
- `C:\Users\Bantu\.claude\projects\C--Users-Bantu-mzansi-agentive-veridian\memory\project_portfolio_agent_e2e.md` — the 2026-05-29 E2E test: deployed agent id, the 4 blockers (Voyage 3 RPM, M6 ragas drift, M8 eval-col bug, M8 SDK tool_use bug), and corrected harnesses in `apps/api/_runlogs/`.

### Embed delivery (starting implementation — review/verify)
- `apps/widget/embed/widget.js` — loader (reads data-agent/data-api, injects launcher + sandboxed iframe).
- `apps/widget/embed/index.html` — iframe host page (loads bundle, reads `?agent_id=&api=`).
- `apps/widget/embed/README.md` — snippet + deploy steps.
- `apps/widget/src/index.jsx` — widget bootstrap (reads `agent_id`/`api` from `location.search`).
- `apps/widget/vite.config.js` — iife/lib build (`pnpm --filter veridian-widget build`).

### Backend hosting / live path
- `scripts/start_native.ps1` — the canonical local process startup (uvicorn + workers, solo pool) — translate to Linux systemd units on the VM.
- `apps/api/app/worker/tasks/runtime/agent.py` — `run_agent_turn`, the 30s wall-clock guard (D-11), and the SDK tool loop (D-10 retrieve cap).
- `apps/api/app/api/v1/widget.py` — public widget routes (`/widget/{id}/config|chat`, SSE events; wildcard CORS already set).
- `apps/api/app/services/embedding_service.py` — Voyage `voyage-3`, `_get_vo()`, retry behavior (3 RPM source of truth).
- `apps/api/app/core/config.py` — `_find_env_file()` + all env settings (the env seam, D-14).
- `apps/api/Dockerfile`, `apps/api/Dockerfile.pipeline` — reference only; reuse only if a host builder needs them (must not require local Docker).

### Tooling note
- `gsd-sdk` global shim was broken (npx cache wiped) and repaired this session to delegate to `~/.claude/get-shit-done/bin/gsd-tools.cjs` (v1.41.2). Originals backed up `*.broken.bak`.

[No external ADRs yet — the cutover ADR (D-15) is to be created in this phase.]
</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `apps/widget/embed/` — the entire embed delivery layer is drafted and self-contained (loader 5 KB + host page + 17.8 KB bundle + css). Just needs hosting + the snippet wired with the real `data-api`.
- `scripts/start_native.ps1` — process inventory + flags (uvicorn `--host 0.0.0.0 --port 8000`; celery `--pool=solo` for Windows — on Linux VM the default prefork pool is fine, but `celery_app.py` forces `worker_pool="solo"` in config, so confirm worker concurrency settings for the VM).
- Existing `apps/api/Dockerfile` (slim python:3.12, uv install `.[dev]`, uvicorn CMD) — a working build recipe a host's cloud-builder can consume if needed.

### Established Patterns
- All config via env (`_find_env_file` walks up to `apps/api/.env`/root `.env`) — the env-only flip seam (D-14) is already the norm; the VM just needs the same env vars set (systemd `EnvironmentFile=`).
- Widget routes set `Access-Control-Allow-Origin: *` — Vercel origin works with no CORS change.
- Agent turn dispatched to `runtime` queue via Celery; SSE via Redis pub/sub on `job_events:{job_id}` — both API and runtime worker must point at the same Upstash Redis (they already do via env).

### Integration Points
- Vercel `public/wchats/` (static) → loads `widget.js` → iframe `index.html?agent_id=&api=<VM https url>` → `GET /widget/{id}/config` (JWT) → `POST /widget/{id}/chat` → SSE `/widget/jobs/{job}/events`.
- VM systemd: `uvicorn` service + `celery -A app.worker.celery_app worker -Q runtime` service, both with `EnvironmentFile` carrying NEON/UPSTASH/ANTHROPIC/VOYAGE/JWT/CLERK keys.
- Agent SDK on the VM needs whatever runtime `claude-agent-sdk` requires (likely Node + the Claude CLI) — researcher to confirm install steps on ARM Linux.

</code_context>

<specifics>
## Specific Ideas

- CV claim must be honest: "W Chats is production-grade" → the bar is a real hiring manager opening bantuson.vercel.app and getting a grounded answer about Bantuson, live, on $0 infra.
- Test agent already live: `fe230a9d-09f0-4043-b2f1-4506a2ef0059` (Neon `nameless-fog-19651218`), is_deployed=True, 195 chunks/embeddings, eval faithfulness 0.978.
- "Flip a switch to cloud-native AWS on demand if growth warrants" — the ADR (D-15) defines the switch + trigger.
</specifics>

<deferred>
## Deferred Ideas

- **Cloud-native AWS migration** (Fargate + Aurora Serverless v2 pgvector + RLS/schema-per-tenant + fast clones + Bedrock) — its own future phase, triggered by the D-15 ADR threshold.
- **Fix M6 eval harness** (ragas 0.4.3 + claude-haiku-4-5 incompatibilities), **M8 checklist orchestrator** (Agent SDK tool_use never fires — port to direct Anthropic API like M9), **M8 `_fetch_eval_summary_sync`** wrong columns — route to `/gsd-debug` (not this phase).
- **Host the pipeline worker** in the cloud — when ingestion volume / self-serve onboarding warrants.
- **Voyage paid tier or Bedrock embeddings** — when traffic exceeds free-tier 3 RPM or budget allows.
- **Custom domain for the API** (vs free Cloudflare/DuckDNS hostname) — optional polish.

### Reviewed Todos (not folded)
None — todo cross-reference not run; no pending todos matched.
</deferred>

<decision_revision>
## Decision Revision (2026-05-29) — No-Card Pivot

**Trigger:** At Wave 2 execution (plan 12-05, Task 1), the user confirmed they have **no credit card**. Oracle Cloud Always-Free signup requires a card ($1 hold) — verified in 12-RESEARCH.md. Every always-on cloud free tier (Oracle / GCP e2-micro / AWS / Azure / Fly.io / Railway) requires a card. This makes the originally chosen VM host infeasible.

**New host decision (user-selected):** **Run the existing local stack on the user's PC + expose it over HTTPS via a Cloudflare Tunnel (`cloudflared`)**, live on demand (during portfolio/demo windows). No card, no new hardware, no NAT/port-forwarding (cloudflared connects outbound). Reuses Neon (sa-east-1) + Upstash unchanged.

**Superseded decisions:**
- **D-01 (Oracle ARM Always-Free VM)** → SUPERSEDED. Host = the user's local Windows PC (the existing `scripts/start_native.ps1` stack: uvicorn + `runtime` Celery worker, solo pool).
- **D-02 (systemd services on a VM)** → SUPERSEDED. Process management = the existing local native-process pattern (`start_native.ps1`), extended to also launch `cloudflared`. No systemd, no Linux VM.
- **D-05 (Caddy + DuckDNS Let's Encrypt TLS on a VM)** → SUPERSEDED. TLS is terminated at the **Cloudflare edge** by the tunnel — no Caddy, no DuckDNS, no Let's Encrypt management.

**Retained / revised:**
- **D-04 (reuse Neon + Upstash via env)** → RETAINED, now via the local `.env` (already present for dev).
- **D-12 (warm worker)** → RETAINED, now satisfied by the always-running local runtime worker (warm SDK) while the demo window is up.
- **D-14 (env-only seam)** → RETAINED, unchanged.
- **D-09/D-10/D-11/D-13 (live-answer hardening, plan 12-01)** → UNCHANGED and already complete on `main`. The Voyage 3 RPM cap, 90s guard, and query-embed cache apply regardless of host.
- **D-06/D-07/D-08 (widget on Vercel, plan 12-02)** → UNCHANGED and already complete; `data-api` now points at the tunnel URL instead of a DuckDNS host.
- **D-15 (cloud-native AWS cutover ADR, plan 12-03)** → UNCHANGED and complete; note the AWS target it documents is itself card-gated, so that future cutover is blocked until the no-card constraint changes.

**Impact on existing plan artifacts (drive the re-plan):**
- **12-04 (deploy artifacts)** — its `deploy/systemd/*.service`, `deploy/caddy/Caddyfile`, and the Oracle-VM `deploy/README.md` runbook are now **superseded** (keep in-repo as the documented AWS-VM path the ADR references, or remove). `scripts/smoke_vm.sh` is still useful — re-target it at the tunnel URL.
- **12-05 (was: provision Oracle VM)** — **re-plan** to: install `cloudflared` on Windows, a "start demo" runbook/script that launches the local stack + tunnel, and records the live HTTPS tunnel base URL.
- **12-06 (final E2E gate)** — `data-api` host = the tunnel URL; otherwise unchanged (run smoke, hiring-manager Q&A).

**Open research questions for the re-plan (warrant `--research`):**
1. **Stable URL:** quick tunnel (`cloudflared tunnel --url`) yields a random `*.trycloudflare.com` URL per run → the Vercel widget snippet's `data-api` would change each session. Resolve: named tunnel (needs a domain on Cloudflare — user has none) vs. a runtime-config approach where the widget reads the API base from an editable `wchats/config.json` on Vercel (no redeploy) vs. accept per-session URL update. **This is the key design question.**
2. **SSE through cloudflared within the 90s turn budget** — confirm Cloudflare's edge streaming/timeout (the ~100s limit flagged in 12-RESEARCH.md for the orange-cloud proxy) does not cut SSE before `agent.response`; D-11's 90s guard sits just under it.
3. **`cloudflared` on Windows** — install (winget/standalone), run alongside uvicorn + celery (extend `start_native.ps1`), keep-warm behavior.
4. **CV/portfolio honesty** — "live on demand" (PC + tunnel up) vs. "always-on production" framing.
</decision_revision>

---

*Phase: 12-Production Go-Live*
*Context gathered: 2026-05-29*
*Revised: 2026-05-29 — no-card pivot: Oracle VM (D-01/D-02/D-05) → Cloudflare Tunnel from local PC*
