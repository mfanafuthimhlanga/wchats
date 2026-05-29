# ADR-0001: Cloud-Native AWS Cutover from Oracle Cloud Always Free

**Status:** Proposed
**Date:** 2026-05-29
**Deciders:** Bantuson

---

## Context

### Current Hosting Baseline

W Chats is deployed on an **Oracle Cloud Always Free ARM VM** (VM.Standard.A1.Flex, 2 OCPU /
12 GB RAM, Ubuntu 22.04 aarch64). The API tier runs as two always-on systemd services — uvicorn
(FastAPI) and a Celery runtime worker — behind a Caddy reverse proxy with DuckDNS DNS-01 TLS.
The data tier is a set of **per-tenant Neon projects** (sa-east-1), one Neon project per tenant,
plus Upstash Redis for the Celery broker and SSE pub/sub channel.

This architecture is the deliberate choice for the portfolio phase: **$0 operating cost** while
retaining the architectural seams for a clean cloud-native migration later. The Oracle VM is a
cost-appropriate platform for a solo engineer building a portfolio piece; it is not a permanent
production target.

### Constraints That Drive the Cutover Decision

**Neon project limit.** Per-tenant Neon projects (not schema-per-tenant) are a hard architectural
requirement for this codebase: Neon branching is used for snapshot-and-restore eval isolation in
the evaluation pipeline (each evaluation run clones the tenant branch to avoid polluting live
data). Neon's free tier caps the number of projects per account. As tenant count grows toward
the free-tier limit, the current data layer hits a hard ceiling that cannot be worked around
without switching away from the free tier or changing the isolation model.

**API cost.** The runtime path relies on Voyage AI (3 RPM free tier) for per-turn query
embeddings and Anthropic's API for agent turns. At low traffic these costs are zero or negligible
against free-tier limits. As traffic grows, both Voyage call volume and Anthropic token spend
escalate linearly with conversations.

**VM RAM headroom.** The Oracle Always Free A1 pool provides 4 OCPU / 24 GB RAM total, of which
2 OCPU / 12 GB RAM are allocated to the W Chats instance. Uvicorn, the Celery runtime worker,
and the claude-agent-sdk bundled binary (spawned per turn) compete for this headroom. Sustained
load above the current single-tenant demonstration level will push RAM utilisation to the
constraint boundary.

**SLA expectation.** Oracle Cloud Always Free carries no uptime SLA. For a portfolio piece with
occasional hiring-manager traffic this is acceptable. For paying customers who depend on W Chats
as a production customer service channel, best-effort is not sufficient.

### Why Per-Tenant Neon Projects Were Chosen

The CLAUDE.md architecture principle is explicit: "Per-tenant Neon projects (not schema-per-tenant)
— required for Neon branching in evals." This decision was made at project inception to support
the evaluation pipeline's snapshot isolation requirement. The cutover to Aurora replicates this
isolation guarantee using a different mechanism (RLS + schema-per-tenant + Aurora fast clones),
so the ADR does not propose relaxing the isolation model — it proposes rehosting it on AWS-managed
infrastructure.

### The Env-Only Config Seam (D-14)

All configuration — database URLs, API keys, Redis URLs, JWT secrets, Clerk credentials — is
loaded exclusively from environment variables. The application entry point (`apps/api/app/core/
config.py`) uses `_find_env_file()` to walk parent directories and load a `.env` file. No
connection string, secret, or infrastructure endpoint is hard-coded. This seam means that the
compute layer is fully decoupled from any specific infrastructure: swapping the underlying
database, broker, or embedding service is an `.env` value change, not a code change.

---

## Decision

**Cut over to a cloud-native AWS architecture when any one of the trigger threshold conditions
below is met.** The cutover is executed as a configuration swap (D-14 env seam), with the Neon
to Aurora data migration handled as a separate, explicitly sequenced operation.

---

## Target Architecture

### Compute: ECS Fargate

Replace the Oracle systemd services with **Amazon ECS Fargate** tasks.

- The FastAPI uvicorn container and the Celery runtime worker container run as Fargate tasks in
  a shared VPC. Both containers already exist as `Dockerfile` and `Dockerfile.pipeline` build
  targets in the repository.
- The uvicorn API service runs on standard Fargate; the Celery pipeline worker (torch/docling
  ingestion) runs on **Fargate Spot** to reduce cost, since pipeline jobs are tolerant of
  interruption (tasks have `acks_late=True` and are idempotent).
- Fargate provides automatic scaling, managed OS patching, and an AWS-native uptime SLA,
  replacing the single Oracle VM and its manual systemd supervision.

### Database: Aurora Serverless v2 with pgvector, RLS, and Schema-Per-Tenant

Replace per-tenant Neon projects with a **single Aurora Serverless v2 PostgreSQL cluster**
shared across all tenants, with per-tenant isolation enforced via **Row-Level Security (RLS)**
and a dedicated schema per tenant.

- The pgvector extension is available on Aurora PostgreSQL 15+ and provides HNSW indexes for
  the same vector similarity queries that Neon currently hosts.
- RLS policies enforce that each tenant's application role can only read and write rows in its
  own schema, replicating the hard isolation that separate Neon projects currently provide.
- Aurora Serverless v2 scales Aurora Capacity Units (ACUs) to zero when idle (minimum 0.5 ACU),
  keeping cost near-zero for a portfolio-scale workload while allowing scale-to-production.

This replaces the per-tenant Neon project model. The control database (Neon, sa-east-1) is also
migrated to Aurora, consolidating both control and tenant data planes into the same managed cluster.

### Eval Branch Isolation: Aurora Fast Clones

Replace Neon's branching mechanism (used for evaluation snapshot isolation) with **Aurora fast
clones** (Aurora Database Cloning). Aurora clones create copy-on-write snapshots of the cluster
in seconds without duplicating storage. Each evaluation run clones the tenant schema into an
ephemeral clone, runs the Ragas evaluation against it, then deletes the clone. This preserves
the snapshot-and-restore isolation guarantee that the evaluation pipeline requires, without
depending on Neon project branching.

### Embeddings and LLM: Amazon Bedrock

Replace direct Voyage AI and Anthropic API calls with **Amazon Bedrock**:

- The embedding model (Voyage embed) is replaced by a Bedrock-hosted embedding model (e.g.,
  Amazon Titan Embeddings or Cohere Embed v3 on Bedrock), eliminating the Voyage 3 RPM free-tier
  constraint and the external third-party dependency.
- Claude model invocations (agent turns, validators, red-team probe) are routed through
  **Bedrock's Claude endpoint** instead of the direct Anthropic API, consolidating billing,
  enabling IAM-based auth, and removing the requirement for `ANTHROPIC_API_KEY` as a long-lived
  secret.

Switching to Bedrock is a one-line change per service: replace the `anthropic.Anthropic()` or
`voyageai.Client()` constructor call with the corresponding Bedrock client call. The D-14 env
seam ensures the switch is a config change (swap `ANTHROPIC_API_KEY` + `VOYAGE_API_KEY` for
`AWS_REGION` + IAM role credentials).

### Queue and Broker: Amazon SQS or ElastiCache Redis

Replace Upstash Redis with either **Amazon SQS** (for durable task queuing) or **ElastiCache
Redis** (for drop-in Celery broker compatibility). ElastiCache Redis is preferred because it
requires no Celery transport code change — the broker URL in `.env` changes from `rediss://
<upstash-host>` to `rediss://<elasticache-endpoint>` and the application is unaware. SQS is an
alternative if cost or operational simplicity favours a managed queue over a managed Redis.

---

## Trigger Threshold

Cut over to the cloud-native AWS architecture when **any one** of the following conditions is met:

1. **Tenant count exceeds approximately 50.** Neon's free tier limits the number of projects
   per account. At roughly 50 tenants, the per-tenant Neon project count approaches this limit
   and the data layer can no longer accept new tenants without upgrading to a paid Neon plan.
   At that inflection point, the Aurora migration is more cost-effective than a Neon subscription.

2. **Monthly external API spend exceeds $100.** This threshold captures the combined cost of
   Voyage AI query embeddings and Anthropic API token usage. At $100/month the portfolio-phase
   free-tier arrangement has been exhausted and a managed cloud budget with AWS Budgets monitoring
   provides cleaner cost governance.

3. **VM RAM sustained above 80% for 7 consecutive days.** This condition indicates that the
   Oracle A1 instance is the bottleneck for live traffic — not a transient spike. At sustained
   >80% RAM utilisation, cold SDK subprocess spawns, Celery worker restarts under memory pressure,
   and latency degradation all become routine. Fargate auto-scaling resolves this permanently.

4. **An uptime SLA is required.** Any paying customer or internal stakeholder requiring a defined
   uptime commitment (e.g., 99.9% monthly availability) triggers the cutover, since Oracle Cloud
   Always Free carries no SLA and provides no credit mechanism for downtime.

---

## Flip Mechanism

### Compute flip: config-only

The compute flip is a **configuration swap, not a code rewrite**. All services read their
configuration exclusively from environment variables via the `_find_env_file()` function in
`apps/api/app/core/config.py`. This function walks parent directories to locate a `.env` file
and loads it into Pydantic settings at application startup.

On the Oracle VM, systemd units load secrets from `EnvironmentFile=/opt/wchats/apps/api/.env`.
On AWS Fargate, the equivalent is an ECS task definition `secrets` block referencing AWS Secrets
Manager, or an `environment` block referencing Parameter Store values. Either way, the
application code is unchanged — only the environment variable values change.

To execute the compute flip:

1. Build the Docker images (already maintained as `apps/api/Dockerfile` and
   `apps/api/Dockerfile.pipeline`) and push to Amazon ECR.
2. Create ECS task definitions referencing the ECR images and the new environment values
   (Aurora endpoint, ElastiCache Redis URL, Bedrock region).
3. Register the ECS services behind an Application Load Balancer with ACM TLS.
4. Update the `data-api` attribute in the widget snippet to the new ALB hostname.
5. Decommission the Oracle VM systemd services and Caddy proxy.

No application source file changes are required for the compute flip.

### Data migration: separate task (pg_dump/restore)

The Neon to Aurora data migration is a **separate, explicitly sequenced task** that is
independent of the compute flip. The two operations are decoupled by design: the compute layer
can be migrated to Fargate while continuing to point at Neon, and the data can be migrated to
Aurora while the compute layer is still on the Oracle VM. The recommended sequence is:

1. Provision the Aurora cluster and validate connectivity.
2. For each tenant, run `pg_dump` against the Neon tenant project's direct connection string
   and `pg_restore` into the corresponding Aurora schema, while the application is in a
   maintenance window or under blue-green routing.
3. Validate row counts and vector index integrity on Aurora.
4. Update the `CONTROL_DB_URL`, `CONTROL_DB_SYNC_URL`, and per-tenant connection strings in
   environment to point at Aurora.
5. Decommission the Neon projects.

The control database (`CONTROL_DB_URL`) and per-tenant databases are both migrated in this step.
Because the D-14 env seam is already in place, the application connects to Aurora using the same
`config.py` settings lookup — no code changes are required for the data migration either.

---

## Consequences

### Positive

- **Scalability.** ECS Fargate auto-scales CPU and memory per task. Aurora Serverless v2 scales
  ACUs to match query load. No manual instance resizing.
- **Managed operations.** AWS handles OS patching (Fargate), database engine upgrades (Aurora),
  certificate renewal (ACM), and broker maintenance (ElastiCache). Operational overhead drops
  from manual systemd supervision to IAM policy management.
- **RLS data isolation.** Aurora RLS enforces per-tenant data boundaries at the database engine
  level, which is at least as strong as per-project isolation and is independently auditable
  via `pg_dump` of the security policy definitions.
- **Fast eval clones.** Aurora Database Cloning creates copy-on-write snapshots in seconds,
  enabling the evaluation pipeline's snapshot-and-restore isolation without the per-project Neon
  limit.
- **Consolidated billing.** A single AWS account with Budgets alerts replaces three separate
  vendor accounts (Neon, Upstash, Voyage) plus the Oracle tenancy.
- **Portfolio credibility.** The ADR makes the "flip a switch to AWS on demand" claim honest and
  inspectable: a hiring manager can read this document and verify that the migration path is
  concrete, not aspirational.

### Negative

- **Cost.** AWS Fargate, Aurora Serverless v2, ElastiCache Redis, and ALB all carry ongoing
  charges. The $0 operating cost of the Oracle Always Free tier is eliminated. Estimated minimum
  monthly AWS cost at portfolio scale: $50–$150 USD depending on ACU scaling and data transfer.
- **AWS lock-in.** Aurora fast clones, ECS service discovery, and Bedrock model invocations are
  AWS-specific. Migrating away from AWS after the cutover would require revisiting each
  component.
- **Migration effort.** The pg_dump/restore data migration requires a maintenance window or
  blue-green traffic routing. At low tenant counts this is a few hours of work; at hundreds of
  tenants the migration requires careful sequencing and validation.
- **RLS complexity.** Schema-per-tenant RLS policies must be authored, tested, and maintained
  as the schema evolves. This is more operationally complex than the per-project isolation model,
  which provides hard boundaries at the Neon project level with no application-layer policy code.
- **Bedrock model availability.** Not all Claude model versions available via the direct Anthropic
  API are available on Bedrock. Model version pinning (e.g., `claude-haiku-4-5-20251001`) must
  be verified against the Bedrock model catalogue before the switch.

---

## Links

- `apps/api/app/core/config.py` — `_find_env_file()`: the D-14 env seam that makes the compute
  flip a config swap.
- `apps/api/app/worker/celery_app.py` — broker and result backend configured from `REDIS_URL`
  env var; no broker-specific code.
- `apps/api/Dockerfile`, `apps/api/Dockerfile.pipeline` — existing build recipes for Fargate
  task images.
- `.planning/phases/12-production-go-live-deploy-the-w-chats-api-and-celery-workers/12-CONTEXT.md`
  — D-14 (env-only config) and D-15 (this ADR) locked decisions.
