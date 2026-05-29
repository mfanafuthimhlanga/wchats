# Phase 12: Production Go-Live - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-29
**Phase:** 12-production-go-live
**Areas discussed:** Backend host, Widget host, Live-answer reliability, Flip-a-switch seam

---

## Backend host (API + worker)

| Option | Description | Selected |
|--------|-------------|----------|
| Render: API + runtime worker | Cloud-side build, paid always-on; pipeline local | (initial pick, revised) |
| Fly.io: API + runtime worker | Always-on micro-VMs | |
| Render: API + both workers | Bigger instance for torch/docling | |
| AWS App Runner + ECS | AWS-family, most setup | |
| **Oracle Cloud Always Free VM** | Free forever + always-on (ARM, ≤24GB); runs uvicorn + runtime worker warm; more setup | ✓ |

**User's choice:** Initially "Render: API + runtime worker", then revised after the **"$0 budget"** constraint surfaced (Render free sleeps; no free worker) → **Oracle Cloud Always Free VM**.
**Notes:** Pipeline worker stays local/on-demand. Reuse remote Neon + Upstash.

---

## Widget host + embed snippet

| Option | Description | Selected |
|--------|-------------|----------|
| **Vercel `public/wchats/`** | Same domain, zero new accounts | ✓ |
| Serve from API host | One origin, couples widget to API | |
| Separate static host (S3+CloudFront) | Dedicated CDN, AWS setup | |

**User's choice:** Vercel `public/wchats/`.
**Notes:** Snippet sets `data-api` to the VM HTTPS URL at runtime — no rebuild to repoint.

---

## Live-answer reliability (Voyage + latency)

| Option | Description | Selected |
|--------|-------------|----------|
| Voyage card + raise guard + warm worker | Lifts 3 RPM; costs money | |
| Voyage card only | Card only | |
| Swap retrieve embeds to Bedrock/Cohere | In-account embeddings; costs money | |
| **$0 free path (derived)** | No card; cap retrieves/turn + raise guard + warm worker | ✓ |

**User's choice:** "i don't want to pay for anything" → **$0 free path**.
**Notes:** Corpus already embedded; only per-turn query embed hits Voyage. 3 RPM suffices for low traffic IF retrieves-per-turn are capped (live failure was 6 retrieves/turn). Raise the 30s turn guard to ~90s; keep worker warm (free on always-on VM).

---

## Flip-a-switch seam depth

| Option | Description | Selected |
|--------|-------------|----------|
| **Env-only config + cutover ADR** | No refactor; ADR documents AWS target + trigger | ✓ |
| Env-only config (no ADR) | Leanest | |
| Add a thin tenant-DB interface now | True config swap later; more work now | |

**User's choice:** Env-only config + cutover ADR.
**Notes:** ADR is both the "flip switch" plan and a portfolio artifact.

## Claude's Discretion
- Reverse-proxy/TLS choice (Caddy vs nginx+LE vs Cloudflare), VM shape, systemd layout, retrieve-cap mechanism, local-Redis-vs-Upstash on VM.

## Deferred Ideas
- Cloud-native AWS migration (Fargate/Aurora+RLS/Bedrock) — future phase per ADR threshold.
- `/gsd-debug` the M6 eval harness + M8 checklist orchestrator + M8 eval-column bug.
- Host the pipeline worker in the cloud; Voyage paid tier; custom API domain.
