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

**Plans:** 7/7 plans complete

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

- [x] 16-07-PLAN.md — (exec wave 4, autonomous:false) deploy-time provisioning script + single-currency guard + operator runbook (Open Q3) + live Stripe test-mode refund gate — INT-07, INT-05
  - **Live gate deferred:** Stripe test-mode refund + idempotency replay proof requires real test credentials (STRIPE_TEST_MODE_ENABLED=1 + STRIPE_TEST_API_KEY + STRIPE_TEST_CHARGE_ID). Test authored at `tests/integration/test_stripe_live.py`. See `16-UAT.md` for close-out runbook.

### Phase 17: Customer identity verification — email/SMS OTP, per-skill, server-enforced

**Goal:** Require a verified customer identity before account-affecting actions, configurable per skill, enforced server-side and never inferred from agent prose.
**Requirements:** IDV-01, IDV-02, IDV-03, IDV-04, IDV-05
**Depends on:** Phase 14, Phase 16
**Success criteria:**

1. `customer_identities` table + email-OTP and SMS-OTP flows issue short-lived verified sessions
2. Per-skill verification requirement is driven by the capability envelope
3. A mutating tool requiring verification is blocked server-side until a valid verified session exists

**Plans:** 6/6 plans complete
**Wave 1**

- [x] 17-01-PLAN.md — Foundation: config settings + customer_identities migration 0008 + live tenant-DB apply/roundtrip (IDV-01) [wave 1]
- [x] 17-02-PLAN.md — Package legitimacy gate + twilio pin for SMS OTP (IDV-03) [wave 1, autonomous:false]
- [x] 17-03-PLAN.md — Enforcement plumbing: _verified_session_token_var ContextVar + task threading (IDV-05) [wave 1]

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 17-04-PLAN.md — identity_service.py: OTP engine, session issuance, email+SMS delivery seam, check_verified_session (IDV-02, IDV-03, IDV-05) [wave 2]

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 17-05-PLAN.md — Widget identity routes (request/verify) + verified_session_token transport (IDV-02, IDV-03, IDV-05) [wave 3]
- [x] 17-06-PLAN.md — Step 2.5 IDV enforcement gate in the transactional dispatcher (IDV-04, IDV-05) [wave 3]

### Phase 18: Blast-radius gate, capability admin UI, transaction red-team & injection-defense extensions

**Goal:** Make the transactional posture owner-configurable and adversarially tested — the financial blast-radius gate + capability UI in the M8 checklist, transaction-specific red-team probes, and the L4 PII firewall + L6 ingestion-injection hardening.
**Requirements:** BLR-01, BLR-02, CAP-03, CAP-04, RTX-01, RTX-02, RTX-03, RTX-04, SEC-01, SEC-02, SEC-03
**Depends on:** Phase 14, Phase 15, Phase 16, Phase 17
**Success criteria:**

1. The M8 checklist reports max single-action value + max hourly aggregate; owner acknowledges the envelope hash at deploy; envelope changes re-trigger the checklist
2. The capability UI lets owners tighten (never loosen) per-skill limits + verification + Actor mode
3. Transaction red-team probes (confused-deputy, value-bound evasion, identity-bypass) run with zero high-severity findings on a clean tenant
4. PII output-firewall pass live; retrieval "treat as data, not instructions" wrapper in place; injection agent split into conversation/content variants

**Plans:** 10/11 plans executed

Plans:

- [x] 18-01-PLAN.md — Control migration 0019 (envelope hash, actor_mode, tenant blast-radius thresholds) + ORM columns + settings *(wave 1)*
- [x] 18-02-PLAN.md — SEC-01 PII output firewall + SEC-02 retrieval "data, not instructions" framing *(wave 1)*
- [x] 18-03-PLAN.md — RTX probe substrate: red-team mode, StubProviderAdapter short-circuit, transactional probe_fn, clean-tenant fixture *(wave 1)*
- [x] 18-04-PLAN.md — capability_service: platform defaults, canonical envelope hash, tighten-only comparator, drift predicate *(wave 2)*
- [x] 18-05-PLAN.md — BLR-01 blast-radius collector (control DB) + tenant thresholds + deterministic warnings *(wave 2)*
- [x] 18-06-PLAN.md — RTX-01/02/03 runners against the real dispatcher + integration roundtrips *(wave 2)*
- [x] 18-07-PLAN.md — BLR-02/CAP-04 envelope-hash persistence, drift on read, approve-deployment 422 at the route *(wave 3)*
- [x] 18-08-PLAN.md — CAP-03 capability-envelope GET/PATCH routes with server-side tighten-only enforcement *(wave 3)*
- [x] 18-09-PLAN.md — SEC-03 injection agent split into conversation and content variants *(wave 3)*
- [x] 18-10-PLAN.md — CAP-03/BLR admin UI in the Pre-Deploy screen (GOTHAM, UI-SPEC D1-D6) + human verify *(wave 4, autonomous:false — executed and committed, `18-10-SUMMARY.md`; CAP-03 itself stays open per that plan's own note pending its checkpoint disposition)*
- [ ] 18-11-PLAN.md — RTX-04 clean-tenant zero-high-severity gate + live-gate UAT + STATE.md closeout *(wave 5, autonomous:false)*

### Phase 19: Documentation + v1.1 verification

**Goal:** Ship the author/provider/owner guides and prove the milestone's success criteria end-to-end.
**Requirements:** DOC-01, DOC-02, DOC-03, VER-01, AUD-03
**Depends on:** Phase 14, Phase 15, Phase 16, Phase 17, Phase 18
**Success criteria:**

1. Tool-author, integration-provider, and owner capability-configuration guides published
2. A non-technical tester deploys a refund + Shopify-order agent end-to-end without code
3. 100 synthetic adversarial messages → zero unauthorized state mutations escape L1–L3; 30-day synthetic audit-gap test passes (zero gaps)

**Plans:** 5/5 plans executed

Plans:
**Wave 1**

- [x] 19-01-PLAN.md — DOC-01 tool-author guide + DOC-02 integration-provider guide under `docs/guides/`; carries the phase's five resolved open decisions *(wave 1)*
- [x] 19-02-PLAN.md — DOC-03 owner capability guide + the VER-01 demo tenant locked as executable data with its Actor skip-boundary proof *(wave 1)*
- [x] 19-03-PLAN.md — AUD-03 seeded-backdated-rows 30-day gate + DB-free per-day coverage-parity unit companion *(wave 1)*
- [x] 19-04-PLAN.md — VER-01 SC3 100-message adversarial harness on the shipped probe substrate + mocked-boundary unit companion *(wave 1)*

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 19-05-PLAN.md — 19-UAT.md record, operator live-gate run/deferral, and planning-record reconciliation *(wave 2, autonomous:false — operator recorded VER-01 SC2 `failed — blocked`, VER-01 SC3 and AUD-03 `deferred` on 2026-07-28; the phase's success criteria 2 and 3 are therefore not met — see `19-UAT.md`)*

*v1.1 roadmap added 2026-06-29 (safe parallel track). The standard new-milestone reset was deliberately NOT run — it would have cleared the paused Phase 13 directory and reset its checkpoint. Phase 13 stays paused & resumable (`/gsd-execute-phase 13 --wave 3` once the domain + Bedrock are ready). 6 phases, 43 requirements, all mapped. Out of scope: A2A/MCP (v1.2), schema-bound exfiltration + classifier firewall (v1.2), continuous alerting/audit-infra (v1.3).*

## Milestone v1.2 — Gotham console + comprehensive agent management (Phases 20–21)

*The dusk/skyline admin UI is a retired design direction and the operations room behind it is mostly mock: only PROVISIONING (create → tenant DB → ingest → first deploy) is end-to-end (`.planning/AGENT-MGMT-GAPS.md`). This milestone (a) cuts the frontend over to the already-built Gotham prototypes and (b) builds the real backends the operations room needs so managing a live agent is E2E, not UI-only. Phase 20 is a pure re-skin+re-IA; Phase 21 derives its requirements directly from the gap table.*

### Phase 20: Frontend cutover — replace the skyline "dusk" admin UI with the Gotham console

**Goal:** Retire the retired-direction dusk-indigo/glass admin frontend and replace it with the "Gotham / Bone on Graphite" design system the owner already rebuilt as static prototypes in `prototypes/gotham/` (colour-is-a-verdict, single-surface console, physical gate shutter, provisioning kept distinct from operations). This is a pure-frontend re-skin + re-information-architecture of `apps/admin`: it ports the prototypes into real routed Next.js pages and must **not regress the working provisioning flow** or any live endpoint the dusk pages already call. The operations room is stood up here with graceful empty states for the regions Phase 21 backs with real data.

**Requirements:** UI2-01 through UI2-08

*Wave 1 — Gotham design system:*

- UI2-01 — Port the Gotham tokens into `apps/admin/app/globals.css` (Bone-on-Graphite palette, the four `--ch-1..4` channel luminances, the `data-gate` shutter mechanism), replacing the dusk-indigo/glass token set.

*Wave 2 — Landing + provisioning re-skin (flow unchanged):*

- UI2-02 — Rebuild the landing page as a routed Next.js page and wire the three.js specimen (three.js confined to landing/auth only, per the design law).
- UI2-03 — Rebuild the agents dashboard as a real routed page reading `GET /agents`.
- UI2-04 — Rebuild agent-new as the provisioning page with steps 2–4 locked until step 1 completes (provisioning distinct from operations); the create → provision → ingest → deploy path is unchanged.

*Wave 3 — Operations room:*

- UI2-05 — Build the agent operations room as a real routed page (the six regions) consuming the endpoints that already exist (alerts, eval-runs, red-team-runs, checklist/approve) with honest empty states for the not-yet-backed regions.
- UI2-06 — Keep the customer widget preview (Preact, <20 KB gzipped) embedded in the console.

*Wave 4 — Cutover + parity:*

- UI2-07 — Delete the retired dusk `dusk-*` / skyline / `amber-console` styles and pages from the production `apps/admin` bundle.
- UI2-08 — Accessibility + reduced-motion parity: `prefers-reduced-motion` skips the gate-shutter repaint and row fades; no horizontal overflow at 1440 / 1280 / 900px.

**Success criteria:**

1. `globals.css` exposes the Gotham tokens (`--ch-1..4`, `data-gate` shutter, Bone-on-Graphite palette) and **no** `dusk-*` / skyline / `amber-console` class or token remains anywhere in the production `apps/admin` bundle.
2. Landing, agents dashboard, agent-new, and the operations room are real routed Next.js pages (not static HTML); the three.js specimen renders on landing/auth only.
3. The provisioning flow (create → provision → ingest → deploy) still works end-to-end with steps 2–4 locked until step 1 completes — no regression against the dusk flow or its live endpoints.
4. `prefers-reduced-motion` skips the shutter repaint and row fades, and there is no horizontal overflow at 1440 / 1280 / 900px.

**Depends on:** Phase 11 (the dusk/skyline admin UI this re-skins and re-IAs).

**Plans:** 15/15 plans complete

Plans:

**Wave 1 — Foundation (tokens-first)**

- [x] 20-02-PLAN.md — three.js dependency provisioning: supply-chain legitimacy checkpoint (blocking-human) + pinned `three`/`@types/three` install — UI2-02
- [x] 20-03-PLAN.md — Gotham token cutover in globals.css (Bone-on-Graphite, `--ch-1..4`, `data-gate`) + root layout font/Clerk re-theme + GateProvider — UI2-01
- [x] 20-04-PLAN.md — Shared Gotham component library (Rail/PageChrome/Zone/Chip/Ledger/Btn/EmptyState/icons) + console shell mount (agents/layout Rail, [id]/layout passthrough) — UI2-01, UI2-05

**Wave 2 — Page rebuilds + harness** *(blocked on Wave 1)*

- [x] 20-01-PLAN.md — Wave-0 validation harness: Playwright + axe + 3-viewport config + spec stubs + `check:no-dusk-tokens` gate — UI2-08
- [x] 20-05-PLAN.md — Landing rebuild + client-only SceneMount (brass→LIVE rename) + auth re-skin (three.js landing/auth only) — UI2-02
- [x] 20-06-PLAN.md — Agents dashboard rebuild (GET /agents, /me/provision preserved; fake command strip cut) + AgentCard restyle — UI2-03
- [x] 20-07-PLAN.md — Provisioning rebuild (create→provision→poll preserved; steps 2–4 locked) + JourneyStepper restyle — UI2-04
- [x] 20-08-PLAN.md — Operations room: six regions (honest empty states for Live/Retrieval/bench/prompt; Judgement+Adversary wired) + AlertsBanner gate-fold — UI2-05
- [x] 20-09-PLAN.md — Soul rebuild (drop three.js → CSS-only temperament; PATCH preserved) — UI2-04
- [x] 20-10-PLAN.md — Ingest rebuild (SSE-driven swarm; brass hex → --live) + DocumentDetailModal restyle — UI2-04
- [x] 20-11-PLAN.md — Eval rebuild (channel colours → --ch-1..4; eval-runs preserved) — UI2-05
- [x] 20-12-PLAN.md — Deploy rebuild (widget preview retained; test-gate buttons dropped; endpoints preserved) — UI2-06
- [x] 20-13-PLAN.md — Settings rebuild (real DELETE wired; fake prototype message dropped) — UI2-07

**Wave 3 — Cutover** *(blocked on all page rebuilds)*

- [x] 20-14-PLAN.md — Delete dusk components (TopNav/HeroPipeline/HeroSteps/StepSubtaskCard/UserAvatar) + skyline PNG; drive check:no-dusk-tokens to green (whole bundle) — UI2-07

**Wave 4 — Parity gate** *(blocked on cutover, autonomous:false)*

- [x] 20-15-PLAN.md — Fill Playwright specs; route smoke + three-confinement + overflow (1440/1280/900) + reduced-motion + axe; blocking visual-fidelity checkpoint — UI2-08

### Phase 21: Agent management backend completion — make the operations room real per AGENT-OPS.md

**Goal:** Close every non-provisioning E2E gap in `.planning/AGENT-MGMT-GAPS.md` so the Gotham operations room is backed by real data end-to-end, not mock. Requirements are derived directly from the gap table and grouped into waves that mirror the ops-research capability areas. Each requirement names the concrete backend artifact (table, endpoint, worker task, or service) it delivers. Every new Celery task is `acks_late=True` **and** idempotent, receives only `tenant_id`/agent IDs (connection strings fetched and decrypted at runtime, never in task args), uses Langfuse v4 (`start_as_current_span` / `update_current_generation`) for any tracing, Ragas 0.4.x for any generation-side scoring, and native `tsvector` + `ts_rank_cd` for the BM25 baseline of reranker lift (no `pg_search`/pgbm25). No Docker anywhere in the run/verify path.

**Requirements:** OPS-01 through OPS-16

*Wave 1 — Trace/span capture + live performance metrics:*

- OPS-01 — `turn_metrics` tenant table + write from `run_agent_turn` capturing the SDK `ResultMessage` (cost_usd, num_turns, latency_ms, escalated, tool_count) that is currently logged-only (`agent.py:355-365`).
- OPS-02 — `message_feedback` tenant table + widget `POST /widget/agents/{id}/feedback` route (thumbs up/down, optional 1–5 CSAT) persisted per assistant message.
- OPS-03 — `GET /agents/{id}/metrics` service+endpoint computing containment, deflection, escalation rate, CSAT/thumbs-down, p95 latency, and cost/session over a window from `turn_metrics` + `message_feedback` + conversations.
- OPS-04 — Extend Langfuse v4 tracing to the agent turn in `run_agent_turn` (a trace + generation per production turn) linked to the `turn_metrics` row by `job_id` (agent.py is currently untraced).

*Wave 2 — RAG health instrumentation:*

- OPS-05 — `retrieval_metrics` tenant table + instrument `retrieval_service` to record per-query recall@k, nDCG@10, MRR, reranker lift (BM25→vector→hybrid→reranker delta; BM25 via native `tsvector`), and cited-chunk rank.
- OPS-06 — Extend the OPS-05 write path to record context-window utilization (retrieved tokens vs the 200k budget), carried-but-never-cited token count, and compaction ratio per turn.
- OPS-07 — Production groundedness + citation coverage: compute per-turn citation coverage from the CITATIONS parse in `run_agent_turn` plus a lightweight groundedness score (Ragas 0.4.x `faithfulness`), stored in `retrieval_metrics`.
- OPS-08 — `check_index_staleness` Celery task (acks_late + idempotent; tenant_id in args) flagging stale documents (source newer than last embed) and embedding-model drift, surfaced via `GET /agents/{id}/retrieval-health`.

*Wave 3 — Failure-triage flywheel:*

- OPS-09 — `GET /agents/{id}/traces?status=failing` service+endpoint surfacing failing production turns (Gatekeeper/Auditor `fail`/`ungrounded`/`partial` from `job_events`) with the customer turn, agent turn, and judge rationale.
- OPS-10 — `POST /agents/{id}/traces/{trace_id}/grade` (filed | held | dismissed) persisting the operator grade + bench tally; a `filed` trace cannot be withdrawn (TERRARIUM law).
- OPS-11 — `promote_trace_to_scenario` Celery task (acks_late + idempotent; tenant_id in args, conn_str at runtime) inserting a filed trace into `eval_scenarios` with `source='production'` + `origin_trace_id`, incrementing the born-in-production count.
- OPS-12 — Add a `provenance` column to `eval_scenarios` (origin trace-id / finding-id / authored) and surface born-in-production vs authored counts in the eval-runs response (the ORRERY ledger).

*Wave 4 — Red-team programme + prompt versioning:*

- OPS-13 — `red_team_strategies` + `red_team_probes` tenant tables + coverage rollup, so strategies/probes/coverage are first-class queryable objects (not a per-run JSON blob), via `GET /agents/{id}/red-team/programme`.
- OPS-14 — `red_team_findings` tenant table (one row per finding: severity, status) replacing the embedded findings JSON; containing/closing a critical finding files it into `eval_scenarios` (`source='red_team'`, provenance = finding-id) — the same flywheel a complaint feeds.
- OPS-15 — Wire the critical-finding gate-block to the real deploy gate: assert `_fetch_red_team_summary_sync` (`deployment.py:154`) drives `run_deployment_checklist` → `recommendation='block'`, so a live red-team critical finding makes `POST /approve-deployment` return 422.
- OPS-16 — `prompt_versions` table capturing every soul edit as an immutable version; `GET /agents/{id}/prompt-versions` + diff, `POST .../canary` (percent routing chosen at turn dispatch in `run_agent_turn`), and `POST .../rollback` (`patch_agent` no longer overwrites history).

**Success criteria:**

1. A completed production turn writes a `turn_metrics` row and a Langfuse v4 trace; `GET /agents/{id}/metrics` returns containment, escalation rate, CSAT/thumbs, p95 latency, and cost/session computed from stored rows — no mock data.
2. `GET /agents/{id}/retrieval-health` returns recall@k, nDCG@10, reranker lift, context-window utilization, compaction ratio, citation coverage, and index staleness, each computed from stored `retrieval_metrics` rows.
3. An operator grades a failing production trace `filed`; `promote_trace_to_scenario` inserts it into `eval_scenarios` with `source='production'` + `origin_trace_id`, the born-in-production count increments, and the scenario appears in the **next eval run tagged born-in-production**; a filed trace cannot be withdrawn.
4. Red-team strategies/probes/coverage and per-finding severity are queryable rows; containing a critical finding files a `source='red_team'` scenario, and a live critical finding sets the deploy checklist to `block` so `POST /approve-deployment` returns 422.
5. Editing a soul writes an immutable `prompt_versions` row; the versions list + diff render, a canary set to a percentage routes that share of turns to the new version, and rollback restores the prior version — history is never overwritten.

**Depends on:** Phase 20 (the Gotham operations room that consumes this data), Phase 6 (eval system — `eval_runs`/`eval_scenarios`), Phase 7 (red team — `red_team_runs`), Phase 10 (observability — alerts + Langfuse v4).

**Plans:** 9/9 plans complete

Plans:

- [x] 21-01-PLAN.md — (W1) OPS-01/04: tenant migration 0009 (turn_metrics+message_feedback) + agent-turn write path + Langfuse v4 trace
- [x] 21-05-PLAN.md — (W1) OPS-09/10: bench failing-trace listing (cross-DB) + grade (filed irrevocable)
- [x] 21-02-PLAN.md — (W2) OPS-02/03: metrics_service + GET /metrics + widget feedback route
- [x] 21-03-PLAN.md — (W2) OPS-05/06: tenant migration 0010 (retrieval_metrics) + instrument retrieve_tool (job_id ContextVar)
- [x] 21-06-PLAN.md — (W3) OPS-11/12: tenant migration 0011 (widen source CHECK + provenance) + promote_trace_to_scenario + ORRERY ledger
- [x] 21-04-PLAN.md — (W4) OPS-07/08: sampled Ragas faithfulness + check_index_staleness + GET /retrieval-health
- [x] 21-07-PLAN.md — (W4) OPS-13: tenant migration 0012 (red_team_strategies/probes/findings) + programme endpoint
- [x] 21-08-PLAN.md — (W5) OPS-14/15: red_team_findings rows + contain→file scenario + deploy-gate rewire (422)
- [x] 21-09-PLAN.md — (W5) OPS-16: control migration 0017 (prompt_versions) + diff/canary/rollback + canary at dispatch

## Milestone v1.1 completion — VER-01 blocker closure (Phase 22)

### Phase 22: Owner capability control + pending-confirmation resolution — close VER-01's two structural blockers

**Goal:** Make a transactional agent deployable and completable by a non-technical owner — give the owner a way to turn a capability on, and give an approver a way to resolve a `require_human` confirmation so the action actually executes.
**Requirements:** CAP-05, ACT-07
**Depends on:** Phase 14, Phase 15, Phase 18, Phase 19
**Success criteria:**

1. An owner can enable a previously-disabled skill through the shipped admin UI, with no direct database action, and the tighten-only guarantee still holds on every other field and dimension — **met, code-level.** Shipped (22-01/22-04) and unit-proven (diff-scope gate, guard-removal demonstrations); the live non-technical-tester walkthrough that would observe it end to end was attempted and deferred (`22-06/22-UAT.md` item 1) — **not marked met by a live observation.**
2. An approver can approve or reject a `pending_confirmations` row; on approval the mutating action executes exactly once and the row is marked resolved; on rejection and on expiry it never executes — **NOT marked met.** Shipped (22-02/22-03/22-04) and proven at the unit-boundary level only; the live-database gate that would prove exactly-once execution against real rows was attempted and deferred, unobserved (`22-06/22-UAT.md` item 2). No real database has ever run this code path.
3. An approval created before an owner tightened a capability cannot execute against the looser envelope it was created under — **met, code-level.** The resolver re-checks the live envelope, not a stored snapshot (`22-02`), proven by `TestLiveEnvelope` unit tests; the same live-database gate as SC2 above would additionally prove it against real rows and was deferred with it.
4. VER-01 SC2 is re-run and its `[failed — blocked]` disposition in `19-UAT.md` is replaced by an observed result — **NOT marked met.** The re-run itself was attempted and deferred (`22-06/22-UAT.md` item 1); `19-UAT.md` item 1's disposition is amended in place (dated 2026-07-28) to record that both original causes are closed in code and the criterion's status moved from `blocked` to `unproven` — that is not the same as an observed pass, and this criterion stays open pending a live re-run.

**Plans:** 6/6 plans executed

Plans:
**Wave 1**

- [x] 22-01-PLAN.md — (W1) CAP-05: remove the platform-default gate from `validate_tighten_only`'s `enabled` branch; prove the other five dimensions bit-for-bit as strict
- [x] 22-02-PLAN.md — (W1) ACT-07 core: `SKILL_INPUT_MODELS`, extract the dispatcher's steps 6-7, and build the steps-2/3/4/6/7 resolver that skips the Actor seam and the IDV gate

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 22-03-PLAN.md — (W2) ACT-07 surface: the queue read, the atomic `UPDATE … WHERE resolved_at IS NULL … RETURNING` claim, and the `runtime`-queue execution task

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 22-04-PLAN.md — (W3) Deploy-page UI: unlock the Enabled control with staged confirm for a live agent, and add the approver's pending-confirmation queue

**Wave 4** *(blocked on Wave 3 completion)*

- [x] 22-05-PLAN.md — (W4) Correct `docs/guides/owner-capability-guide.md` (CAP-05 falsifies it), author the gated live-DB module, fill `22-VALIDATION.md`

**Wave 5** *(blocked on Wave 4 completion)*

- [x] 22-06-PLAN.md — (W5, `autonomous:false`) Operator gates: VER-01 SC2 re-run, the live ACT-07 proof, `22-UAT.md`, and planning-doc reconciliation. **All three operator-gated items (SC2 re-run, ACT-07 live-DB gate, two held-out visual checks) deferred 2026-07-28** — no un-briefed non-technical tester available, no local PostgreSQL server installed on the executing machine; see `22-UAT.md`.

*Planned 2026-07-28 with research + patterns + validation + UI-SPEC but **no CONTEXT.md** (no discuss-phase pass, matching Phases 15-19), so the planner owned and closed six Open Decisions, recorded in `22-01-PLAN.md § Open Decisions Resolved`: (1) identity verification is not re-checked at resolution, accepted as `T-22-ACT-08` and enforced as a source-absence assertion; (2) expiry is lazy inside the atomic claim, no sweep task; (3) the execution-outcome gap is closed by a read-time `tool_calls_audit` lookup, **not** a `0020` migration; (4) the queue lives on the Deploy page, confirmed; (5) the resolver's execution-context shim is an explicit parameter contract, not ContextVar seeding; (6) the claim commits **before** the task is dispatched, overturning the ordering shown in RESEARCH and PATTERNS. Wave 1 (22-01, 22-02) is autonomous with zero `files_modified` overlap; waves 2-4 are autonomous; wave 5 is `autonomous:false` (no local PostgreSQL, and the criterion needs an un-briefed non-technical tester).*

*Added 2026-07-28 from Phase 19's verification. These are **missing product capabilities, not environment gaps** — which is why VER-01 SC2 was recorded `[failed]` rather than `[deferred]`. Phase 19 was scoped as documentation-plus-verification and deliberately shipped zero production code; both fixes require changes under `apps/api/app/`, one of them a human-approved bypass seam inside `_execute_transactional_tool` at exactly the position `19-VERIFICATION.md`'s threat model flags as highest-risk. Bolting that onto a docs phase's gap-closure pass was rejected in favour of planning it properly here. Note the non-obvious constraint recorded in ACT-07: a resolver that re-enters the dispatcher will re-run the Actor and receive `require_human` again, so approval loops rather than completes — this is not a plain CRUD route. Also corrects two stale requirement states this analysis surfaced: ACT-04 (marked complete, but only its row-creation half ever shipped) and CAP-03's note (which claimed plan 18-10 had not run; it has).*

---

*Roadmap created: 2026-05-12*
*Last updated: 2026-05-29 — Phase 12 host pivot (no credit card): Oracle ARM VM + Caddy/DuckDNS TLS (D-01/D-02/D-05) superseded by local Windows PC + Cloudflare quick tunnel. 12-05/12-06 re-planned in place (tunnel bring-up + live gate); 12-01/02/03/04 unchanged. The VM/systemd/Caddy deploy artifacts (12-04) are retained as the AWS-VM reference for ADR 0001. Still 6 plans / 3 waves; all D-01..D-15 (as amended) covered.*
*Updated 2026-06-28 — Phase 13 added (Production Hosting and Durable Deployment): turns "deploy" from a control-DB flag into a durable always-on multi-tenant serving substrate + working self-serve embed. Four waves (PROD-01..PROD-15): durable managed hosting, CDN widget + working embed snippet, object storage for uploads, concurrency-safe horizontal workers. Executes the ADR-0001 D-14 env seam onto always-on infra. Out of scope: Neon project-cap/Aurora migration and the Post-M10 transactional/A2A/MCP security layers. Not planned yet — run /gsd-plan-phase 13.*
*Updated 2026-07-28 — Phase 22 added (VER-01 blocker closure, CAP-05 + ACT-07). Phase 19 executed 5/5 and shipped DOC-01/02/03, but its verification recorded VER-01 SC2 `failed — blocked` on two missing product capabilities: no shipped API can set a capability's `enabled=True`, and nothing resolves a `pending_confirmations` row. Phase 22 owns both. VER-01 SC3 and AUD-03 remain deferred pending a local PostgreSQL server — their harnesses are authored and unit-proven but have never run against a live database. Unplanned — run /gsd-plan-phase 22.*
*Updated 2026-07-15 — Milestone v1.2 added (Phases 20–21). Phase 20: frontend cutover from the retired dusk/skyline admin UI to the Gotham "Bone on Graphite" console (UI2-01..UI2-08; pure re-skin+re-IA, provisioning flow preserved). Phase 21: agent-management backend completion (OPS-01..OPS-16) closing the non-provisioning E2E gaps in AGENT-MGMT-GAPS.md — live-performance metrics, RAG-health instrumentation, the failure-triage flywheel, first-class red-team programme, and prompt versioning. Both unplanned — run /gsd-plan-phase 20 / 21.*
