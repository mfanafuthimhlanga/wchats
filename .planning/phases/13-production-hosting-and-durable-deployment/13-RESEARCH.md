# Phase 13: Production Hosting and Durable Deployment — Research

**Researched:** 2026-06-28
**Domain:** AWS ECS Fargate / ElastiCache Redis / Bedrock Embeddings / S3 / CloudFront / Route53 / ACM / IaC
**Confidence:** MEDIUM-HIGH (all code findings VERIFIED from source; AWS service specifics [ASSUMED] from training knowledge)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **AWS is the production target.** Executes ADR-0001 compute/broker/embeddings/CDN portions via D-14 env seam.
- **ECS Fargate** for API (uvicorn), always-on Celery runtime worker, and Fargate Spot pipeline worker.
- **ALB + ACM TLS + Route53** for the stable API domain.
- **Amazon ElastiCache Redis** replaces Upstash as Celery broker + SSE pub/sub (`REDIS_URL` env swap only).
- **Per-tenant Neon projects retained. NO Aurora migration in this phase.**
- **Neon pooled endpoint** for per-turn runtime path (replace fresh `psycopg2.connect()` per task).
- **Amazon Bedrock embeddings** replace Voyage for embeddings, removing the 3 RPM free-tier cap.
  - Agent turns, validators, red-team Claude calls STAY on direct Anthropic API.
- **S3 + CloudFront** for widget bundle and uploads.
- **Fix `EMBED_SNIPPET`** to emit real CloudFront `src` AND real `data-api`; remove "CDN not yet live" disclaimer.
- **ContextVar refactor** of `agent_tools.py` module-level globals for worker concurrency > 1.
- Reuse existing `apps/api/Dockerfile` and `apps/api/Dockerfile.pipeline` for ECR images.
- Secrets via **AWS Secrets Manager / SSM Parameter Store** referenced from ECS task definition.
- **Compute flip is config-only** via D-14 env seam — no application source change.
- **Health checks:** `GET /health` on API; ECS auto-replaces unhealthy tasks.

### Claude's Discretion

- Exact Bedrock embedding model: Titan Text v2 vs Cohere Embed v3 on Bedrock — must yield 1024-dim, or plan MUST include re-embed/backfill + index note.
- IaC tool: Terraform vs AWS CDK vs Copilot/console.
- `data-api` topology: single shared API host with `agent_id` in path vs per-tenant subdomains.
- S3 read pattern for ingestion: presigned URLs vs server-side `boto3` fetch.
- Whether to retain `voyageai`/`cohere` clients as fallback or remove once Bedrock is primary.

### Deferred Ideas (OUT OF SCOPE)

- Neon-to-Aurora data migration and schema-per-tenant RLS.
- Routing Claude agent turns / validators / red-team through Bedrock.
- Post-M10 transactional / A2A / MCP / Actor-validator / output-firewall / audit-log layers.
- Advanced autoscaling / multi-region.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| PROD-01 | API (FastAPI/uvicorn) runs on always-on managed host at stable HTTPS URL | ECS Fargate + ALB + ACM; Dockerfile.api → ECR |
| PROD-02 | One warm Celery `runtime` worker always-on; 108–144s cold-start off request path | Always-on Fargate task; warm Node.js subprocess via pre-warm strategy |
| PROD-03 | Redis (Celery broker + SSE pub/sub) is managed always-on | ElastiCache Redis in-VPC; `rediss://` URL swap |
| PROD-04 | Health/liveness checks wired; crashed service auto-recovered | `GET /health` → ALB target group; ECS task replacement |
| PROD-05 | Per-turn DB access uses connection pooling instead of fresh `psycopg2.connect()` per task | Neon pooled endpoint already stored as `neon_connection_string`; batch per-task connections |
| PROD-06 | Query embeddings from managed/paid tier, no 3 RPM cap; 2-retrieve-per-turn throttle removed | CRITICAL: requires full re-embed of corpus if Bedrock used; see Landmine #1 |
| PROD-07 | Host swap via D-14 env seam only — no application source change | `config.py` + Secrets Manager/SSM task definition secrets block |
| PROD-08 | Widget bundle on real CDN at stable cache-correct URL | S3 origin + CloudFront distribution |
| PROD-09 | `EMBED_SNIPPET` emits real CDN `src` AND real `data-api` | Fix `deploy/page.tsx:114`; add `data-api` attribute |
| PROD-10 | Stable production API domain backs embed snippet | Route53 + ACM on ALB |
| PROD-11 | End-to-end self-serve proof on external third-party site | Smoke test from external origin |
| PROD-12 | Document uploads in S3/object storage, not local-disk | Replace `documents.py` UPLOADS_DIR with S3 put |
| PROD-13 | Ingestion chain reads source files from object storage | parse.py `parse_document_from_bytes` pattern from S3 get_object |
| PROD-14 | Module-level globals in `agent_tools.py` refactored to `ContextVar` | ContextVar replaces `_conn_str`, `_agent_id`, `_retrieve_call_count` etc. |
| PROD-15 | Runtime worker at concurrency > 1 verified under concurrent multi-tenant load | Switch `--pool=prefork --concurrency=N`; remove Windows-solo workaround |
</phase_requirements>

---

## Summary

Phase 13 converts the Phase 12 demo (local Windows PC + ephemeral localhost.run tunnel) into a durable AWS serving substrate. The execution path is clear: push the existing Dockerfiles to ECR, author ECS task definitions for three services (API, runtime worker, pipeline Spot worker), wire the ALB + Route53 + ACM chain, swap Redis and embeddings via env vars, shift uploads to S3, CDN-host the widget bundle on CloudFront, fix the embed snippet, and refactor the three module-level globals in `agent_tools.py` to `ContextVar`.

Two research findings materially change the planning scope and must be resolved before PLAN.md files are written:

**Finding 1 — Bedrock embedding space mismatch (scope change for PROD-06):** The locked decision states "move query embeddings to Bedrock." This is technically impossible without also moving the document embedding step. All 1024-dim vectors in `embeddings.vector VECTOR(1024)` were produced by `voyage-3`. A Bedrock-embedded query vector and a Voyage-embedded document vector exist in different high-dimensional spaces; cosine similarity between them is meaningless. Either (a) Bedrock must be used for BOTH document AND query embeddings and the existing corpus must be re-embedded/backfilled, or (b) Voyage moves to a paid tier (simpler, safe, no re-embedding). The planner must surface this to the user before Wave 1 planning locks PROD-06 scope.

**Finding 2 — ALB idle timeout vs SSE stream:** The SSE generator fires `ServerSentEvent(comment="keepalive")` every 3s (`POLL_INTERVAL_S = 3.0`), which does reset the ALB idle timer. However, ALB must be configured with idle timeout >= 125s (≥ SSE hard cap of 120s + buffer) to guarantee the stream is never force-closed before the agent response arrives. The default ALB idle timeout is 60s, which is insufficient if keepalives stop.

**Primary recommendation:** Plan the four waves in strict dependency order: Wave 1 (infra) → Wave 2 (widget + CDN) → Wave 3 (S3 uploads) → Wave 4 (ContextVar + concurrency). PROD-06 scope must be resolved with the user before Wave 1 begins.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| TLS termination | ALB (AWS) | — | ACM cert on ALB; Fargate containers speak HTTP internally |
| FastAPI routing | API Fargate task | — | Uvicorn on :8000 inside VPC; ALB forwards |
| SSE streaming | API Fargate task | ElastiCache Redis (pub/sub wakeup) | event_generator runs in API; Redis is signal channel |
| Celery task dispatch | API Fargate task | ElastiCache Redis (broker) | FastAPI publishes to Redis queue; workers consume |
| Agent turns (SDK) | Runtime worker Fargate | Neon tenant DB (tool calls) | `run_agent_turn` in runtime queue |
| Document ingestion | Pipeline Spot Fargate | S3 (source bytes) | `parse→chunk→embed` chain in pipeline queue |
| Query embeddings | Bedrock (or Voyage paid) | — | `embed_query()` in retrieval_service |
| Document embeddings | Same as query embeddings | — | MUST match query embedding space |
| Vector storage | Neon per-tenant DB | — | `embeddings.vector VECTOR(1024)` in tenant schema |
| File upload staging | S3 | — | Replaces `UPLOADS_DIR` local disk |
| Widget bundle CDN | CloudFront + S3 | — | Static files; ACM cert must be in us-east-1 |
| Stable API domain | Route53 + ACM on ALB | — | `data-api` value in embed snippet |
| Secrets | Secrets Manager / SSM | ECS task definition `secrets` block | No `.env` file on Fargate |
| Job health supervision | ECS service auto-replacement | — | Replaces `Restart=always` from systemd |

---

## Standard Stack

### Core AWS Services
| Service | Purpose | Why Standard |
|---------|---------|-------------|
| ECS Fargate | Compute — serverless containers | No instance management; AWS-native uptime SLA; existing Dockerfiles ready |
| Application Load Balancer + ACM | TLS termination + HTTP routing | HTTP/1.1 chunked (required for SSE); ACM auto-renews certs |
| Amazon ElastiCache Redis (Serverless or `cache.t3.micro`) | Celery broker + SSE pub/sub | Drop-in Upstash replacement; in-VPC, no egress fees on pub/sub |
| Amazon S3 | Widget bundle + document uploads | Origin for CloudFront; durable object storage |
| Amazon CloudFront | Widget CDN | Global edge network; custom domain; cache invalidation API |
| Amazon ECR | Container registry | Pull from Fargate without egress; IAM-gated |
| Route53 | DNS for API domain + CloudFront domain | AWS-native; alias records; no TTL propagation delays on CNAME |
| AWS Secrets Manager | Store ANTHROPIC_API_KEY, NEON_ENCRYPTION_KEY, JWT_SECRET, etc. | Auto-rotation support; injected into ECS task def |
| IAM (task execution role + task role) | ECR pull, Secrets Manager read, S3 read/write, Bedrock invoke | Least-privilege per Fargate service |

### Python / Bedrock embedding client
| Library | Purpose | Notes |
|---------|---------|-------|
| `boto3` | S3 + Bedrock + Secrets Manager client | Already an indirect dep; must add explicit dep if not present |

### IaC
| Tool | Recommendation | Rationale |
|------|---------------|-----------|
| Terraform | PRIMARY [ASSUMED] | Declarative HCL; strong AWS provider; easy state inspection; no CDK compile step; solo-dev friendly documentation coverage |

**Installation (embedding service swap):**
```bash
# If Bedrock path chosen — boto3 is already available in most AWS runtimes;
# verify it is in pyproject.toml
uv pip install --system boto3
```

---

## LANDMINES (Read before planning)

### Landmine 1 — Bedrock embedding space mismatch (PROD-06 SCOPE CHANGE REQUIRED)

**What the code says:**
- `apps/api/app/services/embedding_service.py` line 57: `EMBEDDING_MODEL = "voyage-3"` — pinned constant, NOT a floating alias.
- `apps/api/app/services/embedding_service.py` lines 111-112: `embed_chunks` calls `_get_vo().embed(texts, model=EMBEDDING_MODEL, input_type="document")`.
- `apps/api/app/services/retrieval_service.py` line 78: `embed_query` calls `_get_vo().embed([query_text], model="voyage-3", input_type="query").embeddings[0]`.
- `embed.py` comment (line 3-10): "EMBEDDING_MODEL = 'voyage-3' is pinned permanently... The tenant DB schema stores embeddings in a VECTOR(1024) column — voyage-3 produces exactly 1024-dimensional vectors. If the model were changed to a different dimension, all existing embeddings would become incompatible and retrieval would silently return wrong results until schema migration." [VERIFIED: codebase]

**The constraint:** Document embeddings already stored in `embeddings.vector VECTOR(1024)` were produced by `voyage-3`. A Bedrock embedding model produces vectors in a different embedding space. Cosine similarity between a Bedrock-embedded query and a Voyage-embedded document is meaningless — it computes angle between vectors in different coordinate systems. Retrieval would silently return wrong results. This is not a performance degradation; it is a correctness break.

**Required resolution — choose one before PROD-06 is planned:**

**Option A (Full Bedrock move — removes Voyage dependency entirely):**
- Move BOTH `embed_query` (retrieval_service.py) AND `embed_chunks` (embedding_service.py) to boto3 bedrock-runtime.
- Run a one-time backfill Celery task that re-embeds all existing chunk rows per tenant using the Bedrock model and upserts into `embeddings.vector`.
- The existing `ON CONFLICT (chunk_id) DO UPDATE` in `embed_and_migrate` handles the upsert safely.
- After backfill, run `REINDEX INDEX CONCURRENTLY embeddings_vector_hnsw_idx` on each tenant DB (same autocommit pattern already in embed.py).
- Choose: `amazon.titan-embed-text-v2:0` (1024-dim configurable) or `cohere.embed-english-v3` / `cohere.embed-multilingual-v3` (1024-dim fixed).
- The `_retrieve_call_count` cap (2/turn guard in agent_tools.py) was specifically for the Voyage 3 RPM limit. After Bedrock migration, the cap can be raised or removed.

**Option B (Voyage paid tier — no re-embedding):**
- Upgrade `VOYAGE_API_KEY` to a paid Voyage account. The code needs no changes.
- Remove (or raise the ceiling of) the `_RETRIEVE_CALLS_PER_TURN_MAX = 2` cap in `agent_tools.py`.
- This is the simplest path if the re-embedding effort is undesirable.

**Bedrock model selection (for Option A):**

| Model | Bedrock modelId | Dim | Input types | Batch limit | Notes |
|-------|----------------|-----|-------------|-------------|-------|
| Amazon Titan Text Embeddings v2 | `amazon.titan-embed-text-v2:0` | 256/512/1024 (configurable) | single text per call | 1 text/call | Body: `{"inputText": "...", "dimensions": 1024, "normalize": true}` [ASSUMED] |
| Cohere Embed v3 English | `cohere.embed-english-v3` | 1024 | `search_document` / `search_query` | 96 texts/call | Body: `{"texts": [...], "input_type": "search_document"}` [ASSUMED] |

**Recommendation:** Cohere Embed v3 on Bedrock is preferred because (a) the project already has `COHERE_API_KEY` in settings as a fallback for reranking, making Cohere a familiar vendor, (b) the `input_type: "search_document"` / `"search_query"` distinction maps directly to the current Voyage `input_type="document"` / `"query"` distinction, and (c) its batch API reduces per-chunk API calls. [ASSUMED]

---

### Landmine 2 — ALB idle timeout vs SSE stream

**What the code says:**
- `apps/api/app/api/v1/widget.py` line 507: SSE stream has `asyncio.timeout(120)` hard cap.
- `apps/api/app/services/sse.py` line 44: `POLL_INTERVAL_S = 3.0` — keepalive emitted every 3s.
- `apps/api/app/services/sse.py` line 152: `yield ServerSentEvent(comment="keepalive")` — a SSE comment (not an event) sent each poll cycle.
- `apps/api/app/worker/tasks/runtime/agent.py` line 589: `asyncio.wait_for(..., timeout=90)` — agent SDK task timeout.
- `apps/api/app/api/v1/widget.py` line 519: `sse_response.headers["X-Accel-Buffering"] = "no"` — anti-nginx-buffering header (ALB ignores this header). [VERIFIED: codebase]

**The constraint:** ALB default idle timeout is 60 seconds. An SSE stream that sends no data for > 60s will be force-closed by ALB. However, the keepalive comment fires every 3s — so the connection is never idle for > 3s during an active stream. The real risk is:
1. If the agent task stalls (e.g., stuck waiting for the SDK subprocess) and no keepalives are emitted, 60s elapses and ALB closes the stream.
2. During the Phase 1 replay or the first poll tick before the agent task starts, there may be a gap where no SSE events are emitted.

**Required fix:** Set ALB idle timeout to 4000s (the maximum; this is a load balancer attribute, not a target group attribute). This costs nothing and protects against any future gap in keepalive delivery. [ASSUMED]

**Additional ALB SSE concern — response buffering:** ALB operates at layer 7 and supports chunked transfer encoding for HTTP/1.1 targets. It does NOT buffer SSE responses as long as the target sends chunks in HTTP/1.1 chunked encoding mode. `sse_starlette` uses chunked encoding correctly. The Phase 12 problem was cloudflared quick tunnels buffering SSE — not ALB. With a proper ALB + Fargate setup, SSE works if the idle timeout is set correctly. [ASSUMED]

---

### Landmine 3 — `worker_pool="solo"` is a Windows workaround; must change on Linux Fargate

**What the code says:**
- `apps/api/app/worker/celery_app.py` line 204: `worker_pool="solo"` — explicitly set as a Windows billiard fix.
- The pipeline Dockerfile CMD (line 35): `celery -A app.worker.celery_app worker --queues=pipeline --hostname=pipeline@%h --loglevel=info` — no `--pool` flag specified; uses `worker_pool` config. [VERIFIED: codebase]

**Required fix for Fargate:** Remove (or override) `worker_pool="solo"` in the Fargate context. On Linux Fargate containers, prefork is safe and correct. The ECS task definition CMD or entrypoint should pass `--pool=prefork --concurrency=N` explicitly. The solo pool runs tasks in the main process, preventing PROD-15 (concurrency > 1). For the always-on runtime worker, use concurrency ≥ 2. For the pipeline Spot worker, concurrency ≥ 1 (pipeline tasks are heavy; Docling/torch uses multiple CPU cores per task).

---

### Landmine 4 — Hardcoded `/vrd-uploads` in `embed.py`

**What the code says:**
- `apps/api/app/worker/tasks/pipeline/embed.py` line 269: `Path("/vrd-uploads") / agent_id / f"{doc_id}{ext}"` — hardcoded, NOT using `settings.UPLOADS_DIR`. [VERIFIED: codebase]
- `apps/api/app/worker/tasks/pipeline/parse.py` line 257-261: `Path(settings.UPLOADS_DIR) / agent_id / f"{doc_id}{ext}"` — uses settings correctly.

**Required fix:** The embed task's temp-file cleanup logic hardcodes `/vrd-uploads`. When S3 replaces local disk (PROD-12/13), this cleanup code becomes a no-op (no local file exists). The plan must update `embed.py` to delete from S3 instead of local path — or simply remove the cleanup call (since S3 files are not ephemeral temp files and should be retained for idempotent re-ingestion).

---

### Landmine 5 — `EMBED_SNIPPET` is missing `data-api`

**What the code says:**
- `apps/admin/app/agents/[id]/deploy/page.tsx` line 114-115: [VERIFIED: codebase]
```typescript
function EMBED_SNIPPET(id: string): string {
  return '<script src="https://widget.wchats.app/widget.js" data-agent="' + id + '" async></script>'
}
```
- `apps/widget/embed/widget.js` lines 39-47: API base resolution: `data-api` → `window.WCHATS_API_BASE` → empty string (logs warn). [VERIFIED: codebase]

**Required fix:** `EMBED_SNIPPET` must include `data-api="https://<stable-api-domain>"`. Without `data-api`, the widget silently uses `window.WCHATS_API_BASE` (portfolio site workaround) or logs a warning and makes no API calls. The snippet pasted on a third-party site has no `window.WCHATS_API_BASE` — it will fail silently. Also, the `src` is currently `https://widget.wchats.app/widget.js` (placeholder); must be replaced with the real CloudFront URL once CDN is live. Disclaimer at line 522 must also be removed.

---

## Findings by Requirement Cluster

### Cluster 1: ECS Fargate Compute (PROD-01, PROD-02, PROD-04, PROD-07)

**Three Fargate services required:**

| Service | Dockerfile | CMD | Fargate Type | Min Tasks |
|---------|------------|-----|-------------|-----------|
| API | `apps/api/Dockerfile` | `uvicorn app.main:app --host 0.0.0.0 --port 8000` | Standard | 1 (desired: 2 for HA) |
| Runtime worker | `apps/api/Dockerfile` (reuse) | `celery -A app.worker.celery_app worker --queues=runtime --pool=prefork --concurrency=2` | Standard, always-on | 1 |
| Pipeline worker | `apps/api/Dockerfile.pipeline` | `celery -A app.worker.celery_app worker --queues=pipeline --pool=prefork --concurrency=1` | Fargate Spot | 1 (scale to 0 when idle is optional) |

**CPU/memory sizing [ASSUMED]:**
- API: 0.5 vCPU / 1 GB (uvicorn is async I/O; not CPU-heavy)
- Runtime worker: 2 vCPU / 4 GB (claude-agent-sdk spawns a Node.js subprocess per turn; `asyncio.run` + SDK binary import)
- Pipeline worker: 2 vCPU / 8 GB (Docling loads torch + ML models into memory; benchmark for specific models)

**claude-agent-sdk cold-start (PROD-02 warm requirement):** [ASSUMED based on STATE.md note: "cold `import app.main` ≈108-144s on 4GB"]
The 108-144s delay is Node.js subprocess startup + SDK binary import + model loading. On the always-on runtime Fargate task, this penalty is paid once at container startup, not per user request. Strategy: ECS health check marks the task healthy only after `GET /health` returns 200 AND the runtime worker has completed its first task. Alternatively, use a startup task or ECS container health check with a generous `startPeriod` (240s). The key is that Fargate keeps the runtime task running — it does not scale to 0.

**Health check wiring (PROD-04):**
- `GET /health` (health.py line 20) always returns HTTP 200 with JSON; probes Redis and DB but never returns non-200.
- ALB target group: protocol HTTP, health check path `/health`, healthy threshold 2, unhealthy threshold 3, interval 30s.
- ECS service: health check grace period 120s (gives uvicorn time to start).

**Secrets injection (PROD-07):** Map all `Settings` fields from Secrets Manager/SSM into the ECS task definition `secrets` block. The full env surface from `apps/api/app/core/config.py` [VERIFIED: codebase]:

```
NEON_API_KEY, NEON_REGION, NEON_ENCRYPTION_KEY
CONTROL_DB_URL, CONTROL_DB_SYNC_URL
REDIS_URL
ADMIN_KEY
CORS_ORIGINS
ENVIRONMENT=production
UPLOADS_DIR (set to empty or removed — replaced by S3_UPLOADS_BUCKET)
ANTHROPIC_API_KEY
VOYAGE_API_KEY (or AWS_REGION for Bedrock path)
COHERE_API_KEY (optional)
JWT_SECRET
CLERK_JWKS_URL, CLERK_WEBHOOK_SIGNING_SECRET
SMTP_* (optional)
TENANT_DAILY_BUDGET_USD
LANGFUSE_* (optional)
EVAL_*, VERIFIED_QA_*, ALERT_*, DIGEST_ENABLED
RED_TEAM_*, DEP_BLOCK_ON_HIGH_RED_TEAM
AGENT_MAX_BUDGET_USD
MAX_UPLOAD_SIZE_MB
```

`_find_env_file()` in config.py walks parent directories for a `.env` file; on Fargate where no `.env` exists, `pydantic_settings` falls back to environment variables directly — this works correctly without code changes. [VERIFIED: config.py line 15-21]

**ECR build and push:**
```bash
# API image (runtime + API)
aws ecr create-repository --repository-name wchats-api
docker buildx build --platform linux/amd64 -t <account>.dkr.ecr.<region>.amazonaws.com/wchats-api:latest \
  -f apps/api/Dockerfile apps/api/
docker push <account>.dkr.ecr.<region>.amazonaws.com/wchats-api:latest

# Pipeline image
aws ecr create-repository --repository-name wchats-pipeline
docker buildx build --platform linux/amd64 -t <account>.dkr.ecr.<region>.amazonaws.com/wchats-pipeline:latest \
  -f apps/api/Dockerfile.pipeline apps/api/
docker push <account>.dkr.ecr.<region>.amazonaws.com/wchats-pipeline:latest
```

Note: build must use `--platform linux/amd64` — the dev machine is Windows x86_64 and Fargate default is x86_64. [ASSUMED]

---

### Cluster 2: ElastiCache Redis (PROD-03)

**Drop-in replacement:** Change `REDIS_URL` from `rediss://upstash-host:6380/0` to `rediss://<elasticache-endpoint>:6379/0`. No application code changes.

**Current SSL config in celery_app.py [VERIFIED: codebase]:**
```python
_ssl_opts = (
    {"ssl_cert_reqs": ssl.CERT_NONE} if _redis_url_clean.startswith("rediss://") else None
)
```

**TLS note:** The current code uses `ssl.CERT_NONE` (skip certificate verification) — this was acceptable for Upstash (no mTLS). For ElastiCache in-VPC, certificate verification with `ssl.CERT_REQUIRED` and the AWS CA bundle is the correct approach. However, `ssl.CERT_NONE` will also work in-VPC since network-level isolation is the real security boundary. The planner may choose to keep `CERT_NONE` for simplicity or switch to `CERT_REQUIRED` with the AmazonRootCA bundle. [ASSUMED]

**ElastiCache sizing:** `cache.t3.micro` (0.5 GB) is sufficient for a portfolio-scale deployment. Serverless ElastiCache (pay-per-use) is also available. [ASSUMED]

**Security group:** Inbound TCP 6379 from ECS task security group only. No public internet access.

**Kombu/Celery transport options:** The existing `broker_transport_options` (socket_keepalive, socket_timeout, retry_on_timeout) are correct for ElastiCache. No changes needed to transport options. [VERIFIED: celery_app.py lines 145-160]

---

### Cluster 3: Bedrock Embeddings (PROD-06) — CRITICAL

**Headline:** See Landmine #1. Full re-embed/backfill is required if Bedrock is used. The planner must surface this scope question to the user before locking PROD-06 tasks.

**If Option A (Bedrock) is chosen — boto3 call signatures [ASSUMED]:**

For Cohere Embed v3 (recommended):
```python
import boto3, json

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

# Document embedding (replaces embed_chunks)
def embed_chunks_bedrock(texts: list[str]) -> list[list[float]]:
    result = bedrock.invoke_model(
        modelId="cohere.embed-english-v3",
        body=json.dumps({"texts": texts, "input_type": "search_document"}),
    )
    return json.loads(result["body"].read())["embeddings"]

# Query embedding (replaces embed_query)
def embed_query_bedrock(query_text: str) -> list[float]:
    result = bedrock.invoke_model(
        modelId="cohere.embed-english-v3",
        body=json.dumps({"texts": [query_text], "input_type": "search_query"}),
    )
    return json.loads(result["body"].read())["embeddings"][0]
```

For Titan Text v2:
```python
# Single text per call — must loop (unlike Cohere batch)
def embed_query_titan(query_text: str) -> list[float]:
    result = bedrock.invoke_model(
        modelId="amazon.titan-embed-text-v2:0",
        body=json.dumps({"inputText": query_text, "dimensions": 1024, "normalize": True}),
    )
    return json.loads(result["body"].read())["embedding"]
```

**IAM:** Fargate task role needs `bedrock:InvokeModel` on the embedding model ARN. Example policy:
```json
{
  "Effect": "Allow",
  "Action": "bedrock:InvokeModel",
  "Resource": "arn:aws:bedrock:us-east-1::foundation-model/cohere.embed-english-v3"
}
```

**Backfill plan:** A one-time Celery task (or script) that iterates all agents, fetches their chunks from the tenant DB, re-embeds with Bedrock, and upserts via the existing `ON CONFLICT (chunk_id) DO UPDATE` pattern in `embed_and_migrate`. Then `REINDEX INDEX CONCURRENTLY embeddings_vector_hnsw_idx` per tenant (existing autocommit pattern in embed.py). [ASSUMED]

**If Option B (Voyage paid tier) is chosen:**
- Change `VOYAGE_API_KEY` to a paid key.
- Raise `_RETRIEVE_CALLS_PER_TURN_MAX` in `agent_tools.py` (current value: 2, line 132). Remove the D-10 system-prompt cap instruction in `agent.py` line 541-544 (or expand it).
- No re-embedding, no schema changes.

---

### Cluster 4: Neon Connection Pooling (PROD-05)

**Current state [VERIFIED: provision.py, codebase]:**
- `agent.neon_connection_string` = pooled endpoint (Neon PgBouncer transaction-mode). Stored as `pooled_encrypted`. [VERIFIED: provision.py lines 279-284]
- `agent.neon_direct_connection_string` = direct endpoint. Used by Alembic migrations only.
- `agent.py` `run_agent_turn`: opens a new `psycopg2.connect(conn_str, connect_timeout=5)` for EACH of: `_create_conversation_row`, `_validate_conversation_owner`, `_set_sdk_session_id`, `_persist_messages` — 4 separate connections per turn. [VERIFIED: agent.py lines 106, 134, 150, 194]

**PgBouncer transaction-mode compatibility:** psycopg2 default mode does NOT use named prepared statements (it uses unnamed, which are safe in transaction mode). `SET` session variables and server-side prepared statements are incompatible with PgBouncer transaction mode, but the current code does not use either. [ASSUMED]

**Recommended fix (PROD-05):** Batch the 4 psycopg2 calls in `run_agent_turn` into a single connection object opened at the start of the task and closed in a `finally` block. This reduces connection churn from 4 opens/closes per turn to 1. Also consolidates the `_mark_conversation_escalated` call in `agent_tools.py` (another separate connection).

**Pattern:**
```python
# In run_agent_turn, open once:
with psycopg2.connect(conn_str, connect_timeout=5) as tenant_conn:
    local_conversation_id = _create_conversation_row(tenant_conn, agent_id)
    # ... pass tenant_conn to subsequent helpers instead of reopening
```

This requires refactoring the helper functions to accept a connection argument rather than a connection string. The refactor is contained within `agent.py` and `agent_tools.py`.

---

### Cluster 5: S3 Uploads (PROD-12, PROD-13)

**Current upload path [VERIFIED: documents.py line 172-185]:**
```python
upload_dir = Path(settings.UPLOADS_DIR) / str(agent.id)
upload_dir.mkdir(parents=True, exist_ok=True)
local_path = upload_dir / f"{doc_id}{ext}"
local_path.write_bytes(cached_contents[idx])
```

**Current parse read path [VERIFIED: parse.py lines 254-263]:**
```python
# File source:
file_path = (Path(settings.UPLOADS_DIR) / agent_id / f"{doc_id}{ext}")
computed_hash = _compute_source_hash(file_path)
doc = parse_document(file_path)  # Docling — needs a file path or bytes

# URL source (already uses bytes):
content = response.content
doc = parse_document_from_bytes(content, source_uri)  # already exists!
```

**S3 migration strategy:**

1. **Upload (documents.py):** Replace `local_path.write_bytes(contents)` with `boto3.client('s3').put_object(Bucket=S3_UPLOADS_BUCKET, Key=f"{agent_id}/{doc_id}{ext}", Body=contents)`.

2. **Parse (parse.py):** Replace local file read with `boto3.client('s3').get_object(Bucket=S3_UPLOADS_BUCKET, Key=f"{agent_id}/{doc_id}{ext}")['Body'].read()` → pass bytes to `parse_document_from_bytes(content, source_uri)`. This function already exists at import line 53 of parse.py. No Docling API changes needed.

3. **Hash computation:** `computed_hash = hashlib.sha256(content).hexdigest()` (already done for URL sources; replace file-hash path with bytes-hash).

4. **Embed.py cleanup:** Remove or replace the local `/vrd-uploads` delete with `boto3.client('s3').delete_object(Bucket=..., Key=...)`. Or, prefer to retain S3 files for idempotent re-ingestion (delete is optional).

5. **New config variable:** Add `S3_UPLOADS_BUCKET: str` to `Settings` in `config.py`. Store bucket name in Secrets Manager / SSM. `UPLOADS_DIR` can be removed from `Settings` or kept with a sentinel value.

**Tenant-scoped S3 key prefix:** `{agent_id}/{doc_id}{ext}` — agent UUID naturally scopes keys without requiring explicit tenant prefix (agent UUIDs are globally unique UUIDs from the control DB).

**S3 vs presigned URL:** Server-side boto3 `get_object` is simpler and safer (no URL expiry concerns). The parse task runs inside Fargate with IAM access to S3; no presigned URL needed.

---

### Cluster 6: Widget + CDN + Stable API Domain (PROD-08..PROD-11)

**Widget bundle location [VERIFIED: codebase]:**
- `apps/admin/public/wchats/` — 4 files (widget.js, index.html, iife.js, config.json)
- Gzip size verified at 8,087 B (<20 KB gate)

**CloudFront + S3 setup:**
- S3 bucket for widget: `wchats-widget` (or similar), versioned
- CloudFront origin: S3 bucket with OAC (Origin Access Control)
- Custom domain: `widget.wchats.app` or similar; ACM cert in `us-east-1` (CloudFront requirement)
- Cache policy: default TTL 86400s; cache-control headers on S3 objects
- Invalidation on deploy: `aws cloudfront create-invalidation --distribution-id ... --paths "/*"`

**EMBED_SNIPPET fix (PROD-09):** [VERIFIED: deploy/page.tsx line 114]

Current (broken):
```typescript
function EMBED_SNIPPET(id: string): string {
  return '<script src="https://widget.wchats.app/widget.js" data-agent="' + id + '" async></script>'
}
```

Required (correct):
```typescript
const WIDGET_CDN = "https://<cloudfront-domain>/widget.js"
const API_HOST = "https://<alb-domain-or-route53-alias>"

function EMBED_SNIPPET(id: string): string {
  return `<script src="${WIDGET_CDN}" data-agent="${id}" data-api="${API_HOST}" async></script>`
}
```

Note: `data-api` is the stable production API domain (Route53 A record aliasing the ALB), NOT a per-agent subdomain. A single shared API host with `agent_id` in the path already satisfies "stable" — per-tenant subdomains are out of scope.

**Disclaimer removal (PROD-09):** Remove the `<p>` element at deploy/page.tsx line 514-523 once CDN is live.

**Stable API domain (PROD-10):** Route53 record set: A alias → ALB DNS name. ACM cert on the ALB listener covers `api.wchats.app` (or chosen domain). The ALB domain must be stable across redeployments — an ALB alias satisfies this.

---

### Cluster 7: ContextVar Refactor + Horizontal Workers (PROD-14, PROD-15)

**Current globals [VERIFIED: agent_tools.py lines 120-133]:**
```python
_conn_str: str = ""
_agent_id: str = ""
_agent_name: str = ""
_strategy: RetrievalStrategy | None = None
_conversation_id: str = ""
_notify_fn = None
_RETRIEVE_CALLS_PER_TURN_MAX: int = 2
_retrieve_call_count: int = 0
```

All are set by `build_tool_server()` (line 541-552) at the start of each `run_agent_turn` invocation.

**Why safe now:** `worker_pool="solo"` means the worker's main process executes one task at a time, sequentially. There is no concurrent access to the module globals.

**Why unsafe for concurrency > 1 with threads:** If the pool is eventlet or gevent (cooperative multitasking within a single process), multiple coroutines could interleave reads/writes to the same module globals, causing cross-request state bleed (e.g., `_conn_str` from tenant A being used in a retrieve call for tenant B).

**With prefork (recommended for Fargate):** Each worker process has its own memory. Module globals are process-local. `--concurrency=2` spawns 2 worker processes, each handling tasks sequentially. Module globals are safe. No ContextVar refactor is strictly required for correctness with prefork. However, the CONTEXT.md locks ContextVar as the approach — and it is the correct, future-proof design.

**ContextVar refactor pattern:**
```python
from contextvars import ContextVar

_conn_str_var: ContextVar[str] = ContextVar("_conn_str", default="")
_agent_id_var: ContextVar[str] = ContextVar("_agent_id", default="")
_retrieve_call_count_var: ContextVar[int] = ContextVar("_retrieve_call_count", default=0)
# ... etc for all globals

def build_tool_server(...):
    _conn_str_var.set(conn_str)
    _agent_id_var.set(agent_id)
    _retrieve_call_count_var.set(0)  # reset for this turn
    # ... etc
```

**ContextVar propagation across asyncio.run():** [ASSUMED from Python 3.7+ spec]
`asyncio.run(coro)` creates a new event loop and runs `coro` in the current context. ContextVars set before `asyncio.run()` ARE visible inside the coroutine and its callees (including tool functions). This is guaranteed by the Python data model — `asyncio.run` copies the current context into the new thread. The ContextVar refactor is therefore correct across the `asyncio.run` boundary in `agent.py` line 589. [ASSUMED]

**Tool function access pattern:**
```python
async def retrieve_tool(args: dict) -> dict:
    count = _retrieve_call_count_var.get()
    count += 1
    _retrieve_call_count_var.set(count)
    if count > _RETRIEVE_CALLS_PER_TURN_MAX:
        # ... rate cap logic
    conn_str = _conn_str_var.get()
    # ... use conn_str
```

**`--pool=prefork` Celery command for Fargate:**
```
celery -A app.worker.celery_app worker --queues=runtime --pool=prefork --concurrency=2 --hostname=runtime@%h --loglevel=info
```
Also: remove or override `worker_pool="solo"` in `celery_app.conf.update(...)` for the Fargate deployment. The simplest approach is to set `worker_pool` in `celery_app.py` as the default but allow the CMD override.

---

## Architecture Patterns

### System Architecture Diagram

```
[Business Owner Browser]
        |
        | HTTPS (ACM cert)
        v
[Route53: api.wchats.app] ──> [ALB (idle timeout: 4000s)]
                                        |
                              ┌─────────┴──────────┐
                              |                     |
                    [ECS Fargate: API]    [ECS Fargate: API]
                    uvicorn :8000         (2nd task for HA)
                              |
                    ┌─────────┴──────────────────────────────┐
                    |                    |                    |
         [POST /widget/chat]  [GET /jobs/events SSE]  [POST /agents/docs]
                    |                    |                    |
                    v                    v                    v
         [ElastiCache Redis] <── pub/sub │         [S3: wchats-uploads]
                    |         (wakeup)   │                    |
                    | (Celery broker)    │                    |
            ┌───────┴──────┐   SSE read │              ┌─────┘
            |              |   from DB  │              |
  [ECS Fargate:     [ECS Fargate:       │    [ECS Fargate:
   Runtime Worker]  Pipeline Spot]      │     Pipeline Spot]
   always-on        acks_late+idem.     │     parse_documents
   run_agent_turn   ingest chain        │     reads S3 bytes
            |              |             │           |
            v              v             │           v
   [Neon per-tenant DB] [Neon tenant DB] │ [Neon tenant DB]
   (embeddings, chunks) (documents)      │ (chunks, embeddings)
                                        │
[Widget embed on 3rd-party site]        │
        |                               │
        | src= CloudFront URL           │
        v                               │
[CloudFront + S3: widget bundle]        │
  widget.wchats.app                     │
  ← static widget.js / index.html      │
                                        │
[Bedrock: cohere.embed-english-v3] <────┘  (query + doc embedding)
[Anthropic API] ← agent turns, validators, red-team (unchanged)
```

### Recommended Project Structure (new files only)

```
deploy/
├── terraform/                   # IaC (new)
│   ├── main.tf                  # provider, VPC, ECR, ECS cluster
│   ├── fargate.tf               # task definitions, services, ALB
│   ├── elasticache.tf           # Redis cluster
│   ├── s3.tf                    # widget + uploads buckets
│   ├── cloudfront.tf            # CDN distribution
│   ├── route53.tf               # DNS records
│   └── variables.tf             # environment-specific vars
├── systemd/                     # retained as AWS-VM reference (DO NOT DELETE)
│   ├── wchats-api.service
│   └── wchats-celery-runtime.service
└── README.md                    # update with Fargate runbook
scripts/
└── smoke_vm.sh                  # reuse for ALB smoke test (rename or alias)
```

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| TLS certificate management | Manual cert renewal scripts | ACM | Auto-renewal; zero ops overhead |
| Redis connection pooling | Custom pool manager | kombu (Celery's built-in, already in use) | Already configured in celery_app.py |
| S3 multipart upload | Manual chunked PUT | `boto3.client('s3').upload_fileobj()` with multipart | Handles files > 5 GB automatically |
| CloudFront cache invalidation | Custom HTTP invalidation | `aws cloudfront create-invalidation` API | Single API call; atomic |
| Embedding batching for Cohere Bedrock | Custom batch splitter | boto3 call with 96-text limit per request | Cohere Embed v3 on Bedrock accepts up to 96 texts/call |
| HNSW index rebuild | Custom maintenance job | Existing `REINDEX INDEX CONCURRENTLY` pattern in embed.py | Already implemented with autocommit safety |
| Health check endpoint | Custom liveness handler | Existing `GET /health` (health.py) | Already probes Redis + DB |
| Secret rotation | Manual env var updates | Secrets Manager rotation + ECS task replacement | No downtime required |

---

## Common Pitfalls

### Pitfall 1: Building Docker images for wrong architecture

**What goes wrong:** Dev machine is Windows x86_64. If `docker build` runs without `--platform linux/amd64`, the resulting image is for the host platform. On Fargate (default: x86_64), the task fails with a `exec format error` if the image is arm64 or vice versa.

**How to avoid:** Always use `docker buildx build --platform linux/amd64 -f <Dockerfile>`.

**Warning signs:** ECS task goes to STOPPED immediately with exit code 1 and `exec format error` in the container logs.

### Pitfall 2: ACM certificate in wrong region for CloudFront

**What goes wrong:** CloudFront requires the ACM certificate for custom domains to be in `us-east-1` (N. Virginia), regardless of the CloudFront distribution's origin region. If the cert is in another region (e.g., `ap-southeast-1`), CloudFront will not accept it.

**How to avoid:** Always provision the widget domain ACM cert in `us-east-1`. The ALB cert for the API domain can be in any region.

**Warning signs:** CloudFront console shows "No certificate found" when attaching a custom domain.

### Pitfall 3: Embedding space mismatch — retrieval silently breaks

**What goes wrong:** Switching `embed_query` to Bedrock without re-embedding documents causes cosine similarity to return garbage — the top-k retrieved chunks will be randomly wrong. The agent appears to answer but gives ungrounded responses. No exception is raised.

**How to avoid:** See Landmine #1. Either re-embed ALL documents using Bedrock before switching the query embedder, or keep Voyage for both.

**Warning signs:** Retrieval smoke test returns chunks with unexpected content; eval faithfulness scores drop significantly.

### Pitfall 4: ALB idle timeout not set; SSE stream cut at 60s

**What goes wrong:** Long agent turns (>60s) would cause ALB to close the SSE connection at the default 60s idle timeout, even though the keepalive fires every 3s. If the agent task is slow to respond (SDK cold import, slow Neon query), keepalives may not fire for >60s.

**How to avoid:** Set ALB idle timeout to 4000s via Terraform `aws_lb` attribute `idle_timeout = 4000`.

**Warning signs:** Widget shows loading spinner; SSE stream disconnects at ~60s with no `agent.response` event.

### Pitfall 5: ElastiCache Redis not in same VPC/subnet as Fargate tasks

**What goes wrong:** Fargate tasks cannot reach ElastiCache if they are in different VPCs, or if the security group on ElastiCache does not allow TCP 6379 from the Fargate task security group.

**How to avoid:** Deploy ElastiCache and all Fargate tasks in the same VPC and subnet group. Security group: inbound TCP 6379 from the Fargate tasks' security group only.

**Warning signs:** `redis.exceptions.ConnectionError` in Fargate task logs on startup; Celery worker hangs and accepts no tasks.

### Pitfall 6: `worker_pool=solo` left in place on Fargate

**What goes wrong:** The Celery worker runs in solo mode, executing tasks in the main process sequentially. PROD-15 concurrency verification will pass trivially (single task runs fine) but the worker is not actually concurrent.

**How to avoid:** Explicitly set `--pool=prefork --concurrency=N` in the Fargate task CMD. Remove `worker_pool="solo"` from `celery_app.py` conf or override it via environment variable.

**Warning signs:** `celery inspect active` shows only 1 worker; concurrent requests queue up behind each other.

### Pitfall 7: Neon pooled endpoint incompatible with REINDEX CONCURRENTLY

**What goes wrong:** `embed_and_migrate.py` runs `REINDEX INDEX CONCURRENTLY` on a connection with `ISOLATION_LEVEL_AUTOCOMMIT`. If this uses the pooled endpoint (PgBouncer transaction mode), the autocommit mode change may not propagate correctly through the pooler.

**How to avoid:** The existing code already opens a separate connection `reindex_conn = psycopg2.connect(conn_str)` for the REINDEX. Verify that `conn_str` here is the **direct** connection string (not pooled) for DDL operations. Currently `embed_and_migrate.py` uses `agent.neon_connection_string` (pooled). The REINDEX connection should use `agent.neon_direct_connection_string` instead. This is a bug in the current code that surfaces under load. [ASSUMED — should be verified]

---

## IaC Recommendation

**Recommended: Terraform** [ASSUMED]

Rationale: For a solo developer managing a mixed AWS resource set (VPC, ECS, ElastiCache, S3, CloudFront, Route53, ACM, IAM), Terraform provides:
- Declarative HCL that reads cleanly without a compile step
- The AWS provider covers all resources in this phase
- `terraform plan` shows exact changes before apply
- State file (`terraform.tfstate`) stored in S3 for team/session continuity
- Comprehensive official documentation and examples for every resource in this phase

AWS CDK requires TypeScript or Python boilerplate with higher cognitive overhead for straightforward infra. AWS Copilot is excellent for ECS-first workflows but less flexible for non-ECS resources (CloudFront, Route53, ElastiCache). Console-based setup is not reproducible.

Estimated Terraform files: `main.tf`, `fargate.tf`, `elasticache.tf`, `s3_cloudfront.tf`, `route53.tf`, `variables.tf`, `outputs.tf`.

---

## Package Legitimacy Audit

No new external Python packages are required for this phase if Option B (Voyage paid tier) is chosen. If Option A (Bedrock) is chosen, `boto3` is the only new dependency — it is an AWS first-party package, not third-party.

| Package | Registry | Age | Downloads | Verdict | Disposition |
|---------|----------|-----|-----------|---------|-------------|
| boto3 | PyPI | 10+ yrs | >100M/month | OK | Approved — AWS first-party SDK |

No packages removed, no suspicious packages.

---

## Validation Architecture

> `nyquist_validation: true` confirmed in `.planning/config.json` — section required.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (existing; no new framework needed) |
| Config file | `apps/api/pytest.ini` or `pyproject.toml [tool.pytest]` |
| Quick run command | `pytest apps/api/tests/ -x -q` |
| Full suite command | `pytest apps/api/tests/ --cov=app --cov-report=term-missing` |
| Integration gate | `INTEGRATION_TESTS_ENABLED=1 pytest apps/api/tests/` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PROD-01 | API reachable at stable HTTPS URL; returns 200 | smoke | `bash scripts/smoke_vm.sh --target https://api.wchats.app` | Partial — smoke_vm.sh exists; needs ALB target |
| PROD-02 | Runtime worker warm; agent turn completes without 108s cold start | integration | `INTEGRATION_TESTS_ENABLED=1 pytest -k test_agent_turn_warm` | No — Wave 0 gap |
| PROD-03 | ElastiCache Redis reachable; `GET /health` returns `redis: ok` | smoke | `curl https://api.wchats.app/health` | Yes (health.py) |
| PROD-04 | Task failure triggers ECS replacement within 90s | manual/infra | ECS service replacement observation | Manual only |
| PROD-05 | No `psycopg2.connect()` per DB op; connection reused per task | unit | `pytest -k test_agent_turn_single_connection` | No — Wave 0 gap |
| PROD-06 | Re-embedded vectors return correct chunks; faithfulness ≥ 0.9 | integration | `EVAL_E2E_ENABLED=1 pytest -k test_retrieval_quality_after_reembed` | No — Wave 0 gap |
| PROD-07 | App reads config from env vars; no `.env` file on Fargate | unit | `pytest -k test_settings_from_env_no_dotenv` | No — Wave 0 gap |
| PROD-08 | Widget files accessible at CloudFront URL with correct Content-Type | smoke | `curl -I https://<cloudfront>/widget.js` | No — manual |
| PROD-09 | `EMBED_SNIPPET` contains both `data-agent` and `data-api` attributes | unit | `pytest apps/admin/tests/ -k test_embed_snippet_has_data_api` | No — Wave 0 gap |
| PROD-10 | Stable API domain responds; cert valid | smoke | `curl -I https://api.wchats.app/health` | No — smoke target |
| PROD-11 | Widget embeds on external site; `agent.response` event received | manual/e2e | Human: paste snippet on test HTML page hosted on different domain | Manual only |
| PROD-12 | Upload saves to S3; file not on local disk | unit | `pytest -k test_upload_writes_to_s3` | No — Wave 0 gap |
| PROD-13 | Parse task reads bytes from S3; no local UPLOADS_DIR read | unit | `pytest -k test_parse_reads_from_s3` | No — Wave 0 gap |
| PROD-14 | Concurrent tasks on same worker have no `_conn_str` bleed | unit | `pytest -k test_contextvar_isolation_across_tasks` | No — Wave 0 gap |
| PROD-15 | Two concurrent agent turns complete correctly; both emit `agent.response` | integration | `INTEGRATION_TESTS_ENABLED=1 pytest -k test_concurrent_agent_turns` | No — Wave 0 gap |

### ALB SSE Survival Smoke Test

**Reuse `scripts/smoke_vm.sh` §5** (already has the 95s SSE curl pattern from Phase 12) against the ALB target:
```bash
# In smoke_vm.sh or a new scripts/smoke_fargate.sh:
curl -N --max-time 125 https://api.wchats.app/widget/jobs/<job_id>/events
# Must receive agent.response event before 120s
```

### Embedding Regression Test

After re-embedding (if Option A chosen), run the existing eval suite:
```bash
EVAL_E2E_ENABLED=1 pytest apps/api/tests/test_eval_service.py -v
```
Expected: faithfulness and relevance metrics ≥ `EVAL_FAITHFULNESS_THRESHOLD=0.90` (settings.py).

### Sampling Rate
- **Per task commit:** `pytest apps/api/tests/ -x -q`
- **Per wave merge:** `pytest apps/api/tests/ --cov=app --cov-report=term-missing`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps (test files to create before Wave 1)

- [ ] `apps/api/tests/test_s3_upload.py` — mocks boto3, asserts `put_object` called with correct key (PROD-12)
- [ ] `apps/api/tests/test_s3_parse.py` — mocks boto3 `get_object`, asserts `parse_document_from_bytes` called (PROD-13)
- [ ] `apps/api/tests/test_contextvar_isolation.py` — two tasks set different ContextVar values; assert no bleed (PROD-14)
- [ ] `apps/api/tests/test_agent_turn_connection_batch.py` — asserts single psycopg2.connect per task (PROD-05)
- [ ] `apps/api/tests/test_settings_env.py` — monkeypatch env vars without `.env` file; assert `Settings()` succeeds (PROD-07)

---

## Environment Availability

| Dependency | Required By | Available Locally | Notes |
|------------|------------|-------------------|-------|
| AWS CLI (v2) | Terraform, ECR push, CloudFront invalidation | Unknown — check `aws --version` | Install from aws.amazon.com/cli |
| Docker (Desktop or Engine) | ECR image build | Unknown — check `docker --version` | Required for `docker buildx build` |
| Terraform | IaC | Unknown — check `terraform --version` | Install from terraform.io |
| Python 3.12 | Tests | Yes (dev machine) | |
| Node.js | Admin UI build | Unknown | Needed only for Next.js `pnpm build` |
| ElastiCache Redis endpoint | Celery broker | No (AWS provisioned) | Provided by Terraform output |
| Bedrock API access | PROD-06 (Option A) | No (requires AWS account + model access request) | Must request model access in Bedrock console before deploying |
| Neon API access | Already provisioned | Yes | Existing `NEON_API_KEY` |

**Missing dependencies with no fallback:**
- AWS account with billing enabled (Phase 13 explicitly resolves the no-credit-card constraint from Phase 12)
- Bedrock model access (if Option A): must be requested in AWS console before Wave 1 tasks begin

---

## Security Domain

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | Yes | Clerk JWKS (existing); widget JWT HS256 (existing) |
| V3 Session Management | Yes | SDK session_id in tenant DB; JWT 15min expiry (existing) |
| V4 Access Control | Yes | X-API-Key tenant auth (existing); IAM task roles for AWS resources |
| V5 Input Validation | Yes | Pydantic settings; file type whitelist in documents.py (existing) |
| V6 Cryptography | Yes | Fernet for connection strings (existing); ACM for TLS; never hand-roll |

### Phase 13 Specific Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| S3 bucket public access | Information Disclosure | Block public access; CloudFront OAC for widget bucket; IAM role for uploads bucket |
| Cross-tenant S3 key guessing | Elevation of Privilege | Tenant-scoped key prefix `{agent_id}/`; agent UUIDs are UUIDv4 (~122 bit entropy) |
| Secrets in ECS task environment (plaintext in console) | Information Disclosure | Use Secrets Manager ARN references in task def `secrets` block; not `environment` block |
| CORS widcard on widget routes | Spoofing | Already locked to `*` for widget routes only (existing design decision); non-widget routes locked to `CORS_ORIGINS` |
| Bedrock IAM over-permission | Elevation of Privilege | Scope `bedrock:InvokeModel` to specific model ARN only |
| ElastiCache TLS cert bypass | Tampering | In-VPC isolation is primary control; consider `ssl.CERT_REQUIRED` for defense-in-depth |

---

## Recommended Wave Sequencing

**Wave 1 — Compute + Broker (PROD-01..PROD-07)**
1. Provision VPC, security groups, ECR repositories (Terraform)
2. Build and push API + pipeline Docker images to ECR
3. Provision ElastiCache Redis; validate `REDIS_URL` endpoint
4. Create ECS cluster, task definitions (API, runtime worker, pipeline worker)
5. Create ALB + target group + listener; ACM cert for `api.wchats.app`; Route53 A alias
6. ECS services: API (desired=2), runtime worker (desired=1), pipeline Spot (desired=1)
7. Secrets Manager: populate all secrets; wire task definition `secrets` block
8. Validate: `GET /health` → 200; `redis: ok`; `db: ok`
9. PROD-05: Batch psycopg2 connections in `agent.py`
10. PROD-06: **Resolve Bedrock vs Voyage scope with user**; implement chosen option; run backfill if Option A

**Wave 2 — Widget + CDN + Stable Embed (PROD-08..PROD-11)**
1. S3 bucket for widget; upload `apps/admin/public/wchats/` files
2. CloudFront distribution with S3 origin + OAC; ACM cert in us-east-1
3. Route53 CNAME for `widget.wchats.app` → CloudFront domain
4. Fix `EMBED_SNIPPET` in `deploy/page.tsx`: real CloudFront `src` + `data-api`
5. Remove "CDN not yet live" disclaimer (line 522)
6. Smoke: paste snippet on external HTML page; verify agent.response SSE event arrives

**Wave 3 — S3 Uploads (PROD-12..PROD-13)**
1. S3 bucket for uploads; IAM policy for Fargate task role
2. Add `S3_UPLOADS_BUCKET` to `Settings` (config.py); populate in Secrets Manager
3. Replace local write in `documents.py` with `boto3 put_object`
4. Replace local file read in `parse.py` with `boto3 get_object` → `parse_document_from_bytes`
5. Fix hardcoded `/vrd-uploads` in `embed.py` cleanup section
6. Smoke: upload a document; verify it appears in S3; verify parse task reads from S3

**Wave 4 — ContextVar + Concurrency (PROD-14..PROD-15)**
1. Refactor `agent_tools.py` module-level globals to `ContextVar`
2. Update `celery_app.py`: remove `worker_pool="solo"` or make it conditional on `ENVIRONMENT`
3. Update runtime worker Fargate task CMD to `--pool=prefork --concurrency=2`
4. Concurrent load test: two simultaneous agent turns on different tenants; verify no state bleed

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Cohere Embed v3 on Bedrock accepts 96 texts per call | Landmine #1, Don't Hand-Roll | Batching logic must change if limit differs |
| A2 | Titan Text v2 accepts one text per call (no batch) | Landmine #1 | Batching approach changes if batch is supported |
| A3 | `asyncio.run()` propagates ContextVars into the new event loop | Cluster 7 | If wrong, ContextVars set before asyncio.run() would be invisible to tools; must use `contextvars.copy_context().run()` instead |
| A4 | ALB idle timeout ≥ 125s prevents SSE cut-off even if keepalives stop | Landmine #2 | SSE may still be cut if ALB has other connection limits |
| A5 | `REINDEX CONCURRENTLY` fails silently on pooled endpoint | Pitfall 7 | REINDEX may work on pooled; verify before changing to direct conn |
| A6 | Fargate default architecture is x86_64 | Cluster 1 | Must specify `--platform linux/amd64` on build |
| A7 | Bedrock Claude model versions match what's available on direct Anthropic API | ADR consequence | Validators/red-team using specific model IDs may need changes if routed through Bedrock later |
| A8 | Terraform is the appropriate IaC for this project's context | IaC Recommendation | CDK may be preferred if the team is TypeScript-first |

---

## Open Questions (RESOLVED)

> All three resolved in `13-CONTEXT.md` (gathered after this research) and implemented in the plans.

1. **PROD-06 scope — Bedrock Option A or Option B?** — **RESOLVED: Option A** (Bedrock Titan v2 on both paths + full per-tenant re-embed). Implemented by 13-02 + 13-04.
   - What we knew: full re-embed is required if Bedrock is used; Voyage paid tier avoids it.
   - Decision: user chose Bedrock + re-embed for an all-AWS, no-third-party-embedder stack.

2. **Bedrock region — us-east-1 or ap-southeast-1?** — **RESOLVED: us-east-1** (aligns with `NEON_REGION = "aws-us-east-1"`; widest Bedrock model availability). Implemented by 13-01 `variables.tf` default.

3. **Pipeline Spot worker: always-on or scale-to-0?** — **RESOLVED: always-on (1 desired task)**. Implemented by 13-01.
   - `acks_late=True` + idempotency protects against Spot termination. Scale-to-0 (CloudWatch/queue-depth trigger) is out of scope for this phase.

---

## Sources

### Primary (HIGH confidence — code verified)
- `apps/api/app/services/embedding_service.py` — EMBEDDING_MODEL pinning, 1024-dim constraint
- `apps/api/app/services/retrieval_service.py` — embed_query signature and Voyage call
- `apps/api/app/services/agent_tools.py` — module-level globals, _retrieve_call_count, build_tool_server
- `apps/api/app/worker/tasks/runtime/agent.py` — run_agent_turn, multiple psycopg2.connect(), asyncio.run
- `apps/api/app/worker/tasks/pipeline/embed.py` — embed_and_migrate, hardcoded /vrd-uploads
- `apps/api/app/worker/tasks/pipeline/parse.py` — UPLOADS_DIR path, parse_document_from_bytes pattern
- `apps/api/app/api/v1/documents.py` — UPLOADS_DIR local write, S3 migration target
- `apps/api/app/api/v1/widget.py` — SSE handler, asyncio.timeout(120), X-Accel-Buffering
- `apps/api/app/services/sse.py` — POLL_INTERVAL_S=3.0, keepalive comment pattern
- `apps/api/app/api/v1/health.py` — GET /health route (ALB health check target)
- `apps/api/app/core/config.py` — full env surface; _find_env_file() seam
- `apps/api/app/worker/celery_app.py` — worker_pool=solo, broker_transport_options
- `apps/api/Dockerfile`, `apps/api/Dockerfile.pipeline` — build recipes confirmed
- `apps/admin/app/agents/[id]/deploy/page.tsx` — EMBED_SNIPPET (line 114), CDN disclaimer (line 522)
- `apps/widget/embed/widget.js` — data-api resolution logic
- `apps/api/app/worker/tasks/pipeline/provision.py` — neon_connection_string = pooled, neon_direct = DDL
- `docs/adr/0001-cloud-native-cutover.md` — target architecture, flip mechanism
- `.planning/phases/13-production-hosting-and-durable-deployment/13-CONTEXT.md` — locked decisions
- `.planning/ROADMAP.md` — PROD-01..PROD-15 requirements
- `.planning/STATE.md` — Phase 12 demo findings (108-144s cold start; localhost.run SSE proof)

### Secondary (ASSUMED — training knowledge, not verified in this session)
- AWS ECS Fargate task definition schema, Secrets Manager injection pattern
- boto3 bedrock-runtime invoke_model request/response shapes for Cohere Embed v3 and Titan v2
- ALB idle timeout default (60s) and maximum (4000s)
- CloudFront ACM certificate us-east-1 requirement
- Terraform AWS provider coverage for all Phase 13 resources
- ContextVar propagation across asyncio.run() (Python 3.7+ spec)
- ElastiCache Redis TLS behavior in-VPC

---

## Metadata

**Confidence breakdown:**
- Standard stack (AWS services): MEDIUM — AWS service choices are the locked decision; specifics are ASSUMED
- Code findings (current behavior, globals, SSE timing, UPLOADS_DIR): HIGH — verified from source
- Bedrock correctness gate (embedding space mismatch): HIGH — mathematically certain from code inspection
- Architecture patterns: MEDIUM — patterns are standard; specific Terraform details ASSUMED
- Pitfalls: MEDIUM-HIGH — most derived from code inspection; ALB/ElastiCache specifics ASSUMED

**Research date:** 2026-06-28
**Valid until:** 2026-07-28 (Bedrock model availability subject to change; verify model IDs in AWS console before implementation)
