# Phase 13: Production Hosting and Durable Deployment — Context

**Gathered:** 2026-06-28
**Status:** Ready for planning
**Source:** Interactive decisions (inline discuss during /gsd-plan-phase 13)

<domain>
## Phase Boundary

Turn "deploy" from a control-DB boolean (`Agent.is_deployed=true` + a non-functional snippet) into a durable guarantee: an always-on, multi-tenant AWS serving substrate plus a working self-serve embed, so a real business owner can sign up, build an agent, click **Approve**, and paste a snippet that works on their own site with zero hand-editing and no developer laptop staying on.

**This phase EXECUTES the compute / broker / embeddings / CDN portions of ADR-0001** (`docs/adr/0001-cloud-native-cutover.md`) via the D-14 env seam.

**It explicitly does NOT** perform the Neon→Aurora data-tier migration, does NOT route Claude *agent turns* through Bedrock (only embeddings move), and does NOT touch the Post-M10 transactional/A2A/MCP/security layers.
</domain>

<decisions>
## Implementation Decisions

### Hosting / Compute — AWS (PROD-01, PROD-02, PROD-04, PROD-07)
- **AWS is the production target — execute the ADR-0001 cutover.** A payment method is now available (Phase 12's local-PC + tunnel was a no-credit-card stopgap; that constraint is resolved).
- API (uvicorn/FastAPI) runs on **ECS Fargate**, always-on, behind an **Application Load Balancer** with **ACM TLS** at a stable domain.
- The Celery **runtime** worker runs as an always-on Fargate task — kept **warm** so the 108–144s cold-start SDK/import penalty is off the request path.
- The Celery **pipeline** worker (Docling/torch ingestion) runs on **Fargate Spot** (interruption-tolerant — every task is `acks_late=True` + idempotent per CLAUDE.md). 
- Reuse the existing `apps/api/Dockerfile` and `apps/api/Dockerfile.pipeline` for ECR images.
- **Compute flip is config-only** via the D-14 env seam (`apps/api/app/core/config.py`): no application source change to point at AWS. Secrets via **AWS Secrets Manager / SSM Parameter Store** referenced from the ECS task definition.

### Health & supervision (PROD-04)
- ALB target-group health checks hit `GET /health`; ECS auto-replaces unhealthy tasks. A crashed service is detected and recovered, not silently "alive."

### Broker / Redis (PROD-03)
- **Amazon ElastiCache Redis** replaces Upstash as the Celery broker + SSE pub/sub. `REDIS_URL` env swap only — no transport code change.

### Database connection pooling (PROD-05) — STAY ON NEON
- **Per-tenant Neon projects are retained. NO Aurora migration in this phase** (Neon project cap is not a constraint at current scale — user). 
- Add pooling on the per-turn runtime path via **Neon's pooled connection endpoint** (replace the fresh `psycopg2.connect()` per Celery task in `runtime/agent.py`). RDS Proxy is not applicable (Neon, not RDS).

### Embeddings (PROD-06)
- **Amazon Bedrock embeddings** replace Voyage for query embeddings, removing the 3 RPM free-tier cap and the 2-retrieve-per-turn throttle (`agent_tools.py`). IAM-auth; D-14 env seam (`AWS_REGION` + IAM role) per ADR-0001.
- **Agent turns, validators, and red-team Claude calls STAY on the direct Anthropic API** for this phase. Routing Claude through Bedrock is out of scope — only the embedding client moves.

### Widget delivery + working embed (PROD-08, PROD-09, PROD-10, PROD-11)
- The <20KB widget bundle (`apps/admin/public/wchats/`) is hosted on **S3 + CloudFront** at a stable, cache-correct URL / custom domain.
- Fix `EMBED_SNIPPET` (`apps/admin/app/agents/[id]/deploy/page.tsx:114`) to emit the real CloudFront `src` **AND** a real `data-api` (the stable ALB/API domain). Remove the "CDN not yet live" disclaimer (`deploy/page.tsx:522`).
- A **stable production API domain** (Route53 + ACM) backs `data-api` — not a tunnel — so a pasted snippet survives across sessions (the "deploy on their system" contract).
- **End-to-end proof:** copy the snippet from Deploy → Embed, paste it on an external third-party site, and it works with zero hand-editing.

### Object storage for uploads (PROD-12, PROD-13)
- **AWS S3** replaces local-disk `UPLOADS_DIR` (`documents.py`). Uploads are written to S3 under tenant-scoped key prefixes; the ingestion chain (parse → chunk → embed) reads source bytes from S3 — no local-disk dependency anywhere in the chain. Uploads survive worker restarts and are reachable from any Fargate task.

### Horizontal worker scaling (PROD-14, PROD-15)
- Refactor the module-level globals in `apps/api/app/services/agent_tools.py` (`_conn_str`, `_agent_id`, `_retrieve_call_count`) to **`ContextVar`** (or equivalent per-task isolation) so worker concurrency > 1 carries no cross-request state bleed.
- Run the runtime worker at **concurrency > 1 and/or as multiple Fargate tasks**, verified correct under concurrent multi-tenant load (replacing the `--pool=solo --concurrency=1` constraint).

### Claude's Discretion (research to recommend)
- Exact Bedrock embedding model: **Amazon Titan Text Embeddings v2** vs **Cohere Embed v3 on Bedrock** — MUST match the existing **1024-dim** pgvector HNSW schema, or the plan MUST include a re-embed/backfill + index note. This is a hard correctness gate.
- IaC tool: **Terraform vs AWS CDK vs Copilot/console** — prefer reproducible IaC; research to recommend.
- `data-api` topology for the "stable per-agent endpoint" (PROD-10): a **single shared API host with `agent_id` in the path** already satisfies "stable" — per-tenant subdomains are optional polish, only if cheap/correct.
- S3 read pattern for ingestion: presigned URLs vs server-side `boto3` fetch.
- Whether to keep the `voyageai`/`cohere` clients behind the embedding interface as a fallback, or remove them once Bedrock is primary.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### AWS cutover contract
- `docs/adr/0001-cloud-native-cutover.md` — the AWS target architecture + flip mechanism (Fargate, ElastiCache, Bedrock, S3/CloudFront, env seam). **This phase executes the compute / broker / embeddings / CDN portions. The Neon→Aurora data migration in that ADR is OUT OF SCOPE.**

### The env seam (config-only flip)
- `apps/api/app/core/config.py` — `_find_env_file()` D-14 env seam; the single config swap point.
- `apps/api/app/worker/celery_app.py` — broker + result backend configured from `REDIS_URL`.
- `apps/api/Dockerfile`, `apps/api/Dockerfile.pipeline` — existing build recipes for Fargate task images.

### Runtime path to harden
- `apps/api/app/worker/tasks/runtime/agent.py` — per-turn `psycopg2.connect()` (pooling target, PROD-05) + per-request Fernet decrypt; `max_turns`, `timeout=90`, `max_budget_usd`.
- `apps/api/app/services/agent_tools.py` — module-level globals `_conn_str` / `_agent_id` / `_retrieve_call_count` (ContextVar refactor, PROD-14); the 2-retrieve/turn cap tied to Voyage 3 RPM (PROD-06).

### Widget / embed
- `apps/admin/app/agents/[id]/deploy/page.tsx` — `EMBED_SNIPPET` (~line 114) + "CDN not yet live" disclaimer (~line 522).
- `apps/widget/embed/README.md` + `apps/admin/public/wchats/` — the bundle + the working `data-agent`/`data-api` embed contract; the loader resolves `data-api` → `window.WCHATS_API_BASE` → empty.

### Uploads
- `apps/api/.../api/v1/documents.py` — `UPLOADS_DIR` local-disk upload (default `/vrd-uploads`) to replace with S3 (PROD-12/13); the parse→chunk→embed Celery chain that reads the file.

### Retained reference (superseded for this phase)
- `deploy/` (systemd units, Caddy DuckDNS Caddyfile, README) — the AWS-VM reference paired with ADR-0001; superseded by Fargate for Phase 13 but useful as the env/secrets + process-model reference.
</canonical_refs>

<specifics>
## Specific Ideas
- Reuse the existing Dockerfiles for ECR/Fargate — they were authored in Phase 12 (12-04) as the cutover-ready images.
- The widget bundle already passes the <20KB gzip gate; it needs CDN hosting + a correct snippet, not a rebuild.
- Keep `acks_late=True` + idempotency on every Celery task (CLAUDE.md rule 5) — a hard prerequisite for running the pipeline worker on Fargate Spot.
- The "stable API domain" is what makes `data-api` durable; it is the single most important artifact for the "deploy on their system" contract (PROD-10/11).
</specifics>

<deferred>
## Deferred Ideas
- **Neon→Aurora migration + schema-per-tenant RLS** (ADR-0001 data tier) — deferred to the ADR trigger threshold; Neon project cap is not a constraint now (user).
- **Routing Claude agent turns / validators / red-team through Bedrock** — future; only the embedding client moves to Bedrock in this phase.
- **Post-M10 transactional / A2A / MCP / Actor-validator / output-firewall / audit-log layers** (`Post-M10-PRD.md` v1.1–v1.3) — separate future milestone.
- **Advanced autoscaling / multi-region** beyond basic ALB-health-based task replacement — can follow once the baseline is durable.

---

*Phase: 13-production-hosting-and-durable-deployment*
*Context gathered: 2026-06-28 via inline discuss during /gsd-plan-phase 13*
</content>
</invoke>
