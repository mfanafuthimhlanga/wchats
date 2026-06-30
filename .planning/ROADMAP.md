### Phase 12: Production Go-Live: deploy the W Chats API and Celery workers to a public managed host and publish the embeddable widget so a hiring manager can chat with the live agent on bantuson.vercel.app; env and interface driven for a later cloud-native AWS flip

**Goal:** A hiring manager opens bantuson.vercel.app, launches the W Chats widget, and gets a grounded, cited, live answer from the deployed agent (fe230a9d) — served live-on-demand from the user's local Windows PC (uvicorn + runtime Celery worker) exposed over HTTPS via a Cloudflare quick tunnel (cloudflared), on $0/no-card infra, with a cloud-native AWS cutover ADR for the future flip. (Host pivoted from the original Oracle ARM VM + Caddy TLS path — no credit card — see 12-CONTEXT.md decision_revision; the VM/systemd/Caddy artifacts are retained in-repo as the AWS-VM reference.)
**Requirements:** D-01 through D-15 (CONTEXT.md locked decisions, as amended by decision_revision; no formal REQ-IDs mapped to this phase)
**Depends on:** Phase 11
**Plans:** 6/6 — ✓ **COMPLETE 2026-06-28** (demo path proven live end-to-end, job `fdf93abd`: 1741-char grounded + cited answer streamed via localhost.run. The durable always-on hosting intent of 12-05/12-06 is intentionally superseded by Phase 13 — the local-PC + tunnel approach was a deliberate $0 demo compromise, not the production target.)

Plans:
**Wave 1**

- [x] 12-01-PLAN.md — Wave 1: Live-answer hardening (D-09/D-10/D-11/D-13) — max_turns=3 + retrieve-cap prompt + timeout=90 in agent.py, Redis query-embed cache, two regression tests
- [x] 12-02-PLAN.md — Wave 1: Widget publish (D-06/D-07/D-08) — pnpm bundle freshness + copy embed files to apps/admin/public/wchats/ for Vercel
- [x] 12-03-PLAN.md — Wave 1: Cloud-native cutover ADR (D-14/D-15) — docs/adr/0001-cloud-native-cutover.md (Nygard, AWS target + trigger threshold)
- [x] 12-04-PLAN.md — Wave 1: Deploy artifacts in-repo (D-02/D-05) — systemd units, Caddy DuckDNS DNS-01 Caddyfile, deploy/README runbook, scripts/smoke_vm.sh (now the AWS-VM reference paired with ADR 0001; smoke_vm.sh reused for the tunnel)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 12-05-PLAN.md — Wave 2: Tunnel bring-up (D-01/D-02/D-04/D-05/D-12, autonomous:false) — author scripts/start_demo.ps1 (uvicorn 0.0.0.0 + runtime worker + cloudflared quick tunnel), adapt smoke_vm.sh §5 for buffered-flush SSE, wire bantuson.vercel.app landing page data-api; then live tunnel up + empirical SSE-survival checkpoint. **CLOSED 2026-06-28:** SSE-survival proven via localhost.run (cloudflared quick tunnel can't stream SSE); start_demo.ps1 authored. Durable always-on equivalent → Phase 13 Wave 1.

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 12-06-PLAN.md — Wave 3: Final live gate (D-05/D-07/D-09/D-10/D-11/D-12, autonomous:false) — set data-api to the real trycloudflare URL + Vercel deploy, run smoke_vm.sh against the tunnel, hiring-manager Q&A success gate. **CLOSED 2026-06-28:** live Q&A gate met on job `fdf93abd` (empty-answer bug fixed `132f529`+`9572f01`; portfolio embed integrated into portfolio-dashboard). Stable always-on URL + CDN embed (vs ephemeral tunnel) → Phase 13 Waves 1–2.

### Phase 13: Production Hosting and Durable Deployment: stand up always-on managed hosting for the API + runtime worker + Redis, CDN-host the widget with a working self-serve embed, move uploads to object storage, and make the runtime worker concurrency-safe — turning "deploy" from a DB flag into a durable, reachable live agent on the owner's own site

**Goal:** Convert "deploy" from a control-DB boolean (`Agent.is_deployed=true` + a non-functional snippet) into a real guarantee: a durable, always-on, multi-tenant serving substrate plus a working self-serve embed, so a real business owner can sign up, build an agent, click **Approve**, and paste a snippet that works on their own site with zero hand-editing and no developer's laptop staying on. Executes the ADR-0001 (`docs/adr/0001-cloud-native-cutover.md`) D-14 env-only config seam onto always-on managed infrastructure and closes the demo-grade compromises Phase 12 deliberately took (local Windows PC + ephemeral tunnel + 108–144s cold start + placeholder CDN). Full ECS Fargate / Aurora cutover is NOT required day one — an interim always-on managed container reachable via the same env seam satisfies this phase.

**Requirements:** PROD-01 through PROD-15 (defined below; to be formalised by `/gsd-plan-phase 13`)

*Wave 1 — Durable always-on hosting (Tier 0 compute):*

- PROD-01 — API (FastAPI/uvicorn) runs on an always-on managed host at a stable HTTPS URL — not a local PC, not an ephemeral SSH/Cloudflare tunnel.
- PROD-02 — One warm Celery `runtime` worker runs always-on; the 108–144s cold-start SDK/import penalty is off the request path (worker stays warm; `Restart=always`-equivalent supervision).
- PROD-03 — Redis (Celery broker + SSE pub/sub) is a managed always-on instance.
- PROD-04 — Health/liveness/readiness checks wired to the host so a crashed service is detected and auto-recovered, not silently reported "alive".
- PROD-05 — Per-turn tenant DB access uses connection pooling (Neon pooled endpoint / PgBouncer) instead of a fresh `psycopg2.connect()` per Celery task (`agent.py`).
- PROD-06 — Query embeddings served from a managed/paid tier with no 3 RPM cap (Voyage paid or Bedrock), removing the 2-retrieve-per-turn throttle (`agent_tools.py`) as a hard traffic ceiling.
- PROD-07 — The host swap is executed purely via the ADR-0001 D-14 env seam (`apps/api/app/core/config.py`) — config/secrets change only, no application source change to point at the managed host.

*Wave 2 — Real widget delivery + working embed (Tier 1):*

- PROD-08 — The <20KB widget bundle (`apps/admin/public/wchats/`) is hosted on a real CDN at a stable, cache-correct URL.
- PROD-09 — `EMBED_SNIPPET` (`apps/admin/app/agents/[id]/deploy/page.tsx:114`) emits a real CDN `src` AND a real `data-api` pointing at the stable production API host; the "CDN not yet live" disclaimer (`deploy/page.tsx:522`) is removed.
- PROD-10 — A stable production API domain (real DNS, not a tunnel) backs the embed so a pasted snippet survives across sessions — the "deploy on their system" contract.
- PROD-11 — End-to-end self-serve proof: a real owner copies the snippet from Deploy → Embed and it works on an external third-party site with zero hand-editing.

*Wave 3 — Object storage for uploads (Tier 1):*

- PROD-12 — Document uploads are stored in S3/object storage instead of local-disk `UPLOADS_DIR` (default `/vrd-uploads`, `documents.py`); uploads survive worker restarts and are reachable from any host.
- PROD-13 — The ingestion pipeline (parse → chunk → embed) reads source files from object storage with no local-disk dependency anywhere in the chain.

*Wave 4 — Horizontal worker scaling (Tier 0 scaling):*

- PROD-14 — The module-level globals in `apps/api/app/services/agent_tools.py` (`_conn_str`, `_agent_id`, `_retrieve_call_count`) are refactored to `ContextVar` (or equivalent per-task isolation) so worker concurrency > 1 carries no cross-request state bleed.
- PROD-15 — The runtime worker runs at concurrency > 1 and/or as a horizontal fleet, verified correct under concurrent multi-tenant load (replacing the `--pool=solo --concurrency=1` constraint).

**Out of scope (explicit):** Neon free-tier project-cap mitigation / Aurora schema-per-tenant migration — not a constraint at current scale; deferred to the ADR-0001 trigger threshold. Post-M10 transactional / A2A / MCP / Actor-validator / output-firewall / audit-log security layers (`Post-M10-PRD.md` v1.1–v1.3) — separate future milestone.

**Depends on:** Phase 12 (Production Go-Live demo — proves the live-answer path end-to-end; Phase 13 makes it durable).

**Plans:** 11 plans (planned 2026-06-29; AWS ECS Fargate stack per 13-CONTEXT.md; execution waves 1–4 by dependency; live-gate plans are autonomous:false). All PROD-01..PROD-15 covered.

Plans:

**ROADMAP Wave 1 — Durable always-on hosting + Bedrock embeddings + Neon pooling** *(PROD-01..PROD-07)*

- [x] 13-01-PLAN.md — (exec wave 1) Terraform IaC: VPC, ECR, ElastiCache, 3 Fargate services, SSE-safe ALB (idle 4000), stable Route53 domain, private S3 buckets, widget CloudFront, least-privilege IAM — PROD-01,02,03,04,07,10
- [x] 13-02-PLAN.md — (exec wave 1) Bedrock Titan v2 embedder swap (both doc + query paths) behind a provider seam; 1024-dim guard; boto3 — PROD-06
- [x] 13-03-PLAN.md — (exec wave 1) Neon runtime connection pooling: collapse 4 per-turn psycopg2.connect → 1 pooled conn in agent.py — PROD-05
- [x] 13-04-PLAN.md — (exec wave 2) Per-tenant re-embed/backfill Celery task (acks_late, idempotent, resumable, tenant-isolated, direct-conn REINDEX) — PROD-06
- [ ] 13-08-PLAN.md — (exec wave 3, autonomous:false) Live bring-up: terraform apply + ECR push + ECS deploy + ACM/Route53 + health & ALB SSE-survival smoke + live re-embed with retrieval regression — PROD-01,02,03,04,06,07

**ROADMAP Wave 2 — Real widget delivery + working embed** *(PROD-08..PROD-11; depends on Wave 1 stable API host)*

- [x] 13-05-PLAN.md — (exec wave 2) Fix `EMBED_SNIPPET` (env-driven real CloudFront src + `data-api` ALB domain); remove the CDN-disclaimer — PROD-09,10
- [ ] 13-09-PLAN.md — (exec wave 4, autonomous:false) Publish widget bundle to CloudFront (OAC; bucket private) + external-site self-serve embed proof — PROD-08,11

**ROADMAP Wave 3 — Object storage for uploads** *(PROD-12..PROD-13)*

- [x] 13-06-PLAN.md — (exec wave 2) S3 uploads code: storage_service + tenant-scoped put_object; parse reads bytes from S3 (parse_document_from_bytes); fix hardcoded `/vrd-uploads` cleanup — PROD-12,13
- [ ] 13-10-PLAN.md — (exec wave 4, autonomous:false) Live upload→S3→parse smoke (bucket private; no local-disk dependency) — PROD-12,13

**ROADMAP Wave 4 — Horizontal worker scaling** *(PROD-14..PROD-15)*

- [x] 13-07-PLAN.md — (exec wave 3) `agent_tools` globals → `ContextVar` (asyncio.run-propagation-verified); ENVIRONMENT-conditional worker_pool; prefork concurrency=2 CMD; lift Voyage-era retrieve throttle — PROD-14,15,06
- [ ] 13-11-PLAN.md — (exec wave 4, autonomous:false) Live concurrency verify: prefork concurrency=2 with two concurrent multi-tenant turns, isolation proven — PROD-15

---

## Milestone v1.1 — Transactional Capability (Phases 14–19)

*Source: `Post-M10-PRD.md` §4. Agents move from answering to acting. Security layers L1–L3 / L5 / L6 (+ partial L4) are first-class deliverables, not a later hardening pass. Builds on the live M1–M11 platform; does NOT depend on Phase 13's production deploy (paused on a domain purchase) — v1.1 is code-buildable in parallel. Target: 6–8 weeks, parallelizable across the four integration adapters once the tool framework + Actor validator are stable.*

### Phase 14: Transactional tool contract & capability/audit substrate — typed mutating tools with idempotency keys, the capability_envelopes table + enforcement middleware, and the tool_calls_audit / pending_confirmations tables (security L1 + L2 foundation)

**Goal:** Establish the authorization substrate every transactional action rides on — six typed transactional tools tagged `mutating:true` with idempotency-key handling, the per-skill capability-envelope table + enforcement middleware, and the audit/confirmation tables — so no action can execute without a typed contract, a capability check, and an audit row.
**Requirements:** TXN-01, TXN-02, TXN-03, TXN-04, TXN-05, CAP-01, CAP-02, AUD-01, AUD-02
**Depends on:** M4 reasoning engine (the agent tool loop)
**Success criteria:**

1. The six transactional tools + `confirm_action` exist as typed Pydantic functions; no string-blob/SQL/URL inputs anywhere in the set
2. A side-effecting tool replayed with the same idempotency key returns the original result and does not re-execute
3. A disabled / over-limit / constraint-violating skill call is rejected and logged as `capability.denial`
4. Every mutating tool call writes a complete `tool_calls_audit` row

**Plans:** 8/8 plans complete
**Wave 1**

- [x] 14-01-PLAN.md (wave 1) — migration 0014 + 4 control-DB tables + ORM models (CAP-01, AUD-01, AUD-02, TXN-02)
- [x] 14-02-PLAN.md (wave 1) — typed Pydantic schemas + TransactionalToolDef registry + StubProviderAdapter + actor_seam (TXN-01, TXN-03, TXN-05)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 14-03-PLAN.md (wave 2) — fail-closed capability enforcement + control-DB idempotency + audit writer (CAP-02, TXN-02, AUD-01)

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 14-04-PLAN.md (wave 3) — 7 tool handlers + dispatcher + build_tool_server registration (TXN-01, TXN-02, TXN-03, TXN-04, AUD-01, AUD-02)

**Gap closure** *(from 14-REVIEW security findings; run via `/gsd-execute-phase 14 --gaps-only`)*

- [x] 14-05-PLAN.md (wave 1) — migration 0015 + ORM: idempotency reservation columns (status/args_hash/reserved_at, nullable result) — substrate for CR-02/WR-02 (TXN-02)
- [x] 14-06-PLAN.md (wave 2) — atomic reserve/finalize/release idempotency engine + arg fingerprint + executor offload; live-DB concurrency proof (CR-02, WR-02, WR-03) (TXN-02)
- [x] 14-07-PLAN.md (wave 1) — capability-access/rate-constraint split + Redis TLS verify + pipelined INCR/EXPIRE + falsy-zero + enforcement/audit offload (WR-01 substrate, WR-04, WR-03, IN-01, IN-02) (CAP-02, AUD-01)
- [x] 14-08-PLAN.md (wave 3) — dispatcher rewrite to reserve-before-execute + confirm_action capability gate + agent_id precondition; live-DB e2e replay (CR-02, WR-01, WR-02, WR-05, IN-03) (TXN-02, CAP-02, AUD-01)

### Phase 15: Actor validator (L3) + four-node validation chain — a pre-mutation Haiku gate in the Agent SDK tool loop

**Goal:** Insert the Actor validator as a synchronous pre-execution gate before any mutating tool runs, catching the prompt-injection-to-action class where the conversation looks legitimate but the proposed action does not align with the customer's intent.
**Requirements:** ACT-01, ACT-02, ACT-03, ACT-04, ACT-05, ACT-06
**Depends on:** Phase 14
**Success criteria:**

1. Every `mutating:true` call routes through the Actor → `approve | block | require_human` with rationale; the hook never fires for non-mutating tools
2. `require_human` creates a `pending_confirmations` row and routes through `confirm_action`; the action executes only on approval
3. Low-value actions under the per-tenant skip threshold short-circuit the Actor (cost control)
4. Gatekeeper/Auditor/Strategist still run async post-response; Actor p95 < 1s, total added latency on a mutating call < 1.5s

**Plans:** 3/3 plans complete

Plans:
**Wave 1**

- [x] 15-01-PLAN.md — Settings skip-threshold + Actor seam body (forced-tool-use Haiku judge, history fetch, Langfuse v4) + unit tests [ACT-01, ACT-02, ACT-03, ACT-06]

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 15-02-PLAN.md — require_human dispatcher branch + conn_str wiring + four-node structural test [ACT-02, ACT-04, ACT-05]

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 15-03-PLAN.md — Live integration: require_human/four-node control-DB e2e + Actor p95 latency (autonomous: false) [ACT-04, ACT-05, ACT-06]

### Phase 16: Integration adapters + platform credential service (L5 extension) — Shopify, WooCommerce, Stripe, Calendly with encrypted, server-held credentials

**Goal:** Wire the transactional tools to real providers via adapters backed by encrypted, agent-invisible credentials resolved through a platform credential service — so an agent can take real actions without any code path ever seeing a raw credential.
**Requirements:** INT-01, INT-02, INT-03, INT-04, INT-05, INT-06, INT-07
**Depends on:** Phase 14
**Build decisions (see `docs/adr/0002-agent-tool-and-provisioning-strategy.md`):**

- **Call provider SDKs directly behind our typed tools — NOT provider MCP servers or vendor "agent toolkits" (e.g. the Stripe Agent Toolkit).** A provider MCP/toolkit hands raw provider operations to the LLM, bypassing the Actor validator + capability envelope + audit (L1–L3) AND dumping the provider's whole API into context (token bloat + degraded tool-selection). Our narrow, hand-curated tool set is both the security boundary and the context-efficiency win.
- **Stripe (INT-05):** Refunds API (`issue_refund`), Subscriptions API (`update_subscription`), Checkout Session / Payment Link for `place_order` (no card handling). Pass the TXN-02 client idempotency key straight to Stripe's native `Idempotency-Key`. Provision a per-tenant **Stripe Restricted API Key** scoped to only the enabled skills — defense-in-depth at the Stripe layer mirroring the L2 envelope.
- **Shopify / WooCommerce / Calendly:** same pattern — provider SDK/REST behind typed tools, with scope-restricted credentials. (Stripe Agent Toolkit / MCP kept only as a *reference* for which ops to expose.)

**Success criteria:**

1. `integration_credentials` is Fernet-encrypted and never read by agent code; the credential service returns only short-lived in-memory handles
2. Each of Shopify / WooCommerce / Stripe / Calendly performs its real action behind the typed tool contract
3. Single-currency per tenant is enforced at deploy time

**Plans:** 6/7 plans executed

Plans:

**ROADMAP Wave 1 — Credential substrate + SDK provisioning** *(INT-01, INT-02; INT-03/04/05 prerequisite)*

- [x] 16-01-PLAN.md — (exec wave 1) integration_credentials tenant migration 0007 + PLATFORM_CREDENTIAL_KEY + HKDF per-tenant Fernet + CredentialHandle + _fetch_credential_config + _tenant_id_var ContextVar (Open Q1) — INT-01, INT-02
- [x] 16-02-PLAN.md — (exec wave 1, autonomous:false) provider SDK legitimacy checkpoint + pin stripe/ShopifyAPI/WooCommerce (or httpx OAuth1 fallback) + install smoke — INT-03, INT-04, INT-05 (prereq)

**ROADMAP Wave 2 — Provider adapters** *(depends on substrate + SDKs)*

- [x] 16-03-PLAN.md — (exec wave 2) StripeAdapter: refund + subscription + Checkout place_order; native Idempotency-Key; currency from config — INT-05, INT-07
- [x] 16-04-PLAN.md — (exec wave 2) ShopifyAdapter: place/cancel order + refund via Admin GraphQL mutations — INT-03
- [x] 16-05-PLAN.md — (exec wave 2) WooCommerceAdapter (wc/v3, HTTPS-only) + CalendlyAdapter (async httpx, config_data event_type mapping, Open Q2) — INT-04, INT-06

**ROADMAP Wave 3 — Credential resolution + dispatcher wiring** *(depends on all adapters)*

- [x] 16-06-PLAN.md — (exec wave 3) get_adapter_for_skill (decrypt + dispatch by provider_type) + tools.py step-6 change + dispatch unit test + env-gated e2e — INT-02

**ROADMAP Wave 4 — Deploy-time provisioning + live proof** *(depends on wiring)*

- [ ] 16-07-PLAN.md — (exec wave 4, autonomous:false) deploy-time provisioning script + single-currency guard + operator runbook (Open Q3) + live Stripe test-mode refund gate — INT-07, INT-05

### Phase 17: Customer identity verification — email/SMS OTP, per-skill, server-enforced

**Goal:** Require a verified customer identity before account-affecting actions, configurable per skill, enforced server-side and never inferred from agent prose.
**Requirements:** IDV-01, IDV-02, IDV-03, IDV-04, IDV-05
**Depends on:** Phase 14, Phase 16
**Success criteria:**

1. `customer_identities` table + email-OTP and SMS-OTP flows issue short-lived verified sessions
2. Per-skill verification requirement is driven by the capability envelope
3. A mutating tool requiring verification is blocked server-side until a valid verified session exists

**Plans:** 0 — run `/gsd-plan-phase 17`

### Phase 18: Blast-radius gate, capability admin UI, transaction red-team & injection-defense extensions

**Goal:** Make the transactional posture owner-configurable and adversarially tested — the financial blast-radius gate + capability UI in the M8 checklist, transaction-specific red-team probes, and the L4 PII firewall + L6 ingestion-injection hardening.
**Requirements:** BLR-01, BLR-02, CAP-03, CAP-04, RTX-01, RTX-02, RTX-03, RTX-04, SEC-01, SEC-02, SEC-03
**Depends on:** Phase 14, Phase 15, Phase 16, Phase 17
**Success criteria:**

1. The M8 checklist reports max single-action value + max hourly aggregate; owner acknowledges the envelope hash at deploy; envelope changes re-trigger the checklist
2. The capability UI lets owners tighten (never loosen) per-skill limits + verification + Actor mode
3. Transaction red-team probes (confused-deputy, value-bound evasion, identity-bypass) run with zero high-severity findings on a clean tenant
4. PII output-firewall pass live; retrieval "treat as data, not instructions" wrapper in place; injection agent split into conversation/content variants

**Plans:** 0 — run `/gsd-plan-phase 18`

### Phase 19: Documentation + v1.1 verification

**Goal:** Ship the author/provider/owner guides and prove the milestone's success criteria end-to-end.
**Requirements:** DOC-01, DOC-02, DOC-03, VER-01, AUD-03
**Depends on:** Phase 14, Phase 15, Phase 16, Phase 17, Phase 18
**Success criteria:**

1. Tool-author, integration-provider, and owner capability-configuration guides published
2. A non-technical tester deploys a refund + Shopify-order agent end-to-end without code
3. 100 synthetic adversarial messages → zero unauthorized state mutations escape L1–L3; 30-day synthetic audit-gap test passes (zero gaps)

**Plans:** 0 — run `/gsd-plan-phase 19`

*v1.1 roadmap added 2026-06-29 (safe parallel track). The standard new-milestone reset was deliberately NOT run — it would have cleared the paused Phase 13 directory and reset its checkpoint. Phase 13 stays paused & resumable (`/gsd-execute-phase 13 --wave 3` once the domain + Bedrock are ready). 6 phases, 43 requirements, all mapped. Out of scope: A2A/MCP (v1.2), schema-bound exfiltration + classifier firewall (v1.2), continuous alerting/audit-infra (v1.3).*

---

*Roadmap created: 2026-05-12*
*Last updated: 2026-05-29 — Phase 12 host pivot (no credit card): Oracle ARM VM + Caddy/DuckDNS TLS (D-01/D-02/D-05) superseded by local Windows PC + Cloudflare quick tunnel. 12-05/12-06 re-planned in place (tunnel bring-up + live gate); 12-01/02/03/04 unchanged. The VM/systemd/Caddy deploy artifacts (12-04) are retained as the AWS-VM reference for ADR 0001. Still 6 plans / 3 waves; all D-01..D-15 (as amended) covered.*
*Updated 2026-06-28 — Phase 13 added (Production Hosting and Durable Deployment): turns "deploy" from a control-DB flag into a durable always-on multi-tenant serving substrate + working self-serve embed. Four waves (PROD-01..PROD-15): durable managed hosting, CDN widget + working embed snippet, object storage for uploads, concurrency-safe horizontal workers. Executes the ADR-0001 D-14 env seam onto always-on infra. Out of scope: Neon project-cap/Aurora migration and the Post-M10 transactional/A2A/MCP security layers. Not planned yet — run /gsd-plan-phase 13.*
