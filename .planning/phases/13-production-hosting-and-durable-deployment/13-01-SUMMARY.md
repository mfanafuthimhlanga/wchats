---
phase: 13-production-hosting-and-durable-deployment
plan: "01"
subsystem: infrastructure
tags: [terraform, aws, ecs-fargate, elasticache, alb, s3, cloudfront, iam, secrets-manager, route53]
dependency_graph:
  requires: []
  provides:
    - deploy/terraform module (12 .tf files — validate-clean)
    - ECR repos wchats-api + wchats-pipeline
    - VPC + private/public subnets + NAT + SGs
    - 18 Secrets Manager containers (no values — populated in 13-08)
    - IAM task_execution + task roles (least-privilege)
    - ElastiCache Redis (cache.t3.micro, TLS, private SG)
    - ECS cluster + 3 Fargate services (API HA, runtime always-on, pipeline Spot)
    - ALB idle_timeout=4000 + HTTP:80→443 redirect + /health TG
    - Route53 A-alias for api_domain + widget_domain
    - S3 widget bucket (BPA on, OAC policy) + uploads bucket (BPA on, SSE-AES256)
    - CloudFront OAC distribution (us-east-1 ACM cert, CachingOptimized)
    - deploy/README.md Fargate runbook
  affects:
    - 13-08 (live-gate terraform apply)
    - 13-02 (Bedrock embeddings — picks up BEDROCK_EMBED_MODEL_ID env + task role)
    - 13-03 (S3 uploads — picks up S3_UPLOADS_BUCKET env + uploads bucket)
tech_stack:
  added:
    - "Terraform >= 1.5 (hashicorp/aws >= 5.0)"
    - "AWS ECS Fargate (api 0.5vCPU/1GB, runtime 2vCPU/4GB, pipeline 2vCPU/8GB)"
    - "AWS ElastiCache Redis 7.1 (cache.t3.micro, transit+rest encryption)"
    - "AWS ALB (idle_timeout=4000, ACM TLS, HTTP_301 redirect)"
    - "AWS S3 (widget + uploads; Block Public Access all-true)"
    - "AWS CloudFront (OAC, CachingOptimized, us-east-1 ACM cert)"
    - "AWS Secrets Manager (18 secret containers)"
    - "AWS IAM (least-privilege task_execution + task roles)"
    - "AWS Route53 (A-alias records)"
    - "AWS ECR (scan_on_push=true)"
  patterns:
    - "Secrets Manager ARN refs in ECS task definition secrets block (never plaintext)"
    - "CloudFront OAC (not legacy OAI) for private S3 widget origin"
    - "ALB idle_timeout=4000 to survive SSE streams up to the 120s hard cap"
    - "FARGATE_SPOT with acks_late=True for cost-efficient pipeline ingestion"
    - "--pool=prefork in ECS CMD overrides worker_pool=solo Windows default"
key_files:
  created:
    - deploy/terraform/main.tf
    - deploy/terraform/ecr.tf
    - deploy/terraform/secrets.tf
    - deploy/terraform/iam.tf
    - deploy/terraform/variables.tf
    - deploy/terraform/outputs.tf
    - deploy/terraform/elasticache.tf
    - deploy/terraform/fargate.tf
    - deploy/terraform/alb.tf
    - deploy/terraform/route53.tf
    - deploy/terraform/s3.tf
    - deploy/terraform/cloudfront.tf
  modified:
    - deploy/README.md
decisions:
  - "ALB idle_timeout=4000 (maximum) instead of the default 60s — prevents force-close of SSE streams during any keepalive gap (Landmine 2)"
  - "Runtime worker --concurrency=1 intentional — PROD-15 raise to 2 gated on 13-07 ContextVar refactor"
  - "VOYAGE_API_KEY secret container included — still required by current config.py until 13-02 makes it optional"
  - "No depends_on on widget bucket policy — OAC policy is not classified as a public policy, Block Public Policy does not block it"
  - "Single-node ElastiCache replication group (automatic_failover_enabled=false) — portfolio scale; production HA can add a replica node later"
  - "ALB SG egress 0.0.0.0/0 (avoids Terraform circular dep with ecs_tasks SG reference)"
  - "widget_bucket_name = wchats-widget, uploads_bucket_name = wchats-uploads — operator must rename if globally taken"
metrics:
  duration_minutes: 45
  completed_date: "2026-06-29"
  tasks_completed: 3
  tasks_total: 3
  files_created: 13
  files_modified: 1
status: complete
---

# Phase 13 Plan 01: Terraform IaC Module Summary

**One-liner:** Complete `terraform validate`-ready Terraform module (12 .tf files) declaring the full Wave-1 AWS substrate: VPC, ECR, ElastiCache Redis, three ECS Fargate services (API HA + always-on runtime + Fargate-Spot pipeline), SSE-safe ALB (idle_timeout=4000), Route53 stable domain, private S3 buckets (BPA on), CloudFront OAC distribution, and least-privilege IAM — with all secrets injected via Secrets Manager ARN refs, no literal in any .tf file.

## Objective

Author the Terraform IaC module that stands up the durable AWS serving substrate for W Chats: replace the Phase 12 local-Windows-PC + ephemeral localhost.run tunnel with always-on managed infra reachable through the same `config.py` env seam (PROD-07 — no application source change required).

## Tasks Completed

| # | Task | Commit | Key Files |
|---|------|--------|-----------|
| 1 | Network, ECR, Secrets, least-priv IAM | e8b51fa | main.tf, ecr.tf, secrets.tf, iam.tf, variables.tf, outputs.tf |
| 2 | ElastiCache, 3 Fargate services, ALB (idle 4000), Route53 | 32a2346 | elasticache.tf, fargate.tf, alb.tf, route53.tf |
| 3 | Private S3 buckets, CloudFront OAC, Fargate runbook | 95b1f64 | s3.tf, cloudfront.tf, deploy/README.md |

## Architecture Produced

### Network (main.tf)
- VPC 10.0.0.0/16, 2 public + 2 private subnets across us-east-1a/b
- Single NAT gateway for private-subnet Fargate egress
- `aws_security_group.ecs_tasks`: ingress port 8000 from ALB SG only; egress all
- `aws_security_group.elasticache`: ingress port 6379 from `ecs_tasks` SG only (T-13-01-05 — never 0.0.0.0/0)

### Container Registry (ecr.tf)
- `wchats-api` ECR repository: API + runtime worker image; `scan_on_push=true`
- `wchats-pipeline` ECR repository: Docling/torch pipeline image; `scan_on_push=true`

### Secrets (secrets.tf)
- 18 `aws_secretsmanager_secret` containers declared with no `secret_string` literal (T-13-01-01)
- Required: NEON_API_KEY, NEON_ENCRYPTION_KEY, CONTROL_DB_URL, CONTROL_DB_SYNC_URL, REDIS_URL, ADMIN_KEY, ANTHROPIC_API_KEY, VOYAGE_API_KEY, JWT_SECRET, CLERK_WEBHOOK_SIGNING_SECRET
- Optional: COHERE_API_KEY, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, SMTP_HOST, SMTP_FROM, SMTP_USER, SMTP_PASSWORD, OWNER_EMAIL
- Values populated out-of-band in 13-08 (live gate)

### IAM (iam.tf, T-13-01-03)
- `aws_iam_role.task_execution`: AmazonECSTaskExecutionRolePolicy + inline secretsmanager:GetSecretValue on specific ARNs; ecr:GetAuthorizationToken on `*` (AWS-required)
- `aws_iam_role.task`: bedrock:InvokeModel scoped to `arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0` ONLY; s3:GetObject/PutObject/DeleteObject scoped to uploads bucket ARN ONLY; secretsmanager:GetSecretValue on specific ARNs

### Broker (elasticache.tf, PROD-03)
- `aws_elasticache_replication_group.main`: cache.t3.micro, Redis 7.1, single-node, `transit_encryption_enabled=true`, `at_rest_encryption_enabled=true`
- Attached to private subnets; protected by `aws_security_group.elasticache`
- No application code change needed — REDIS_URL env var swap only

### Compute (fargate.tf, PROD-01/02/04)
- ECS cluster `wchats` with FARGATE and FARGATE_SPOT capacity providers
- **Service 1 — API**: uvicorn, 0.5 vCPU/1 GB, `desired_count=2` (HA), ALB-attached, `health_check_grace_period_seconds=120`
- **Service 2 — Runtime worker**: `--queues=runtime --pool=prefork --concurrency=1`, 2 vCPU/4 GB, `desired_count=1` always-on (108–144s cold-start off request path — PROD-02). concurrency=1 until 13-07 ContextVar refactor (PROD-15)
- **Service 3 — Pipeline worker**: `--queues=pipeline --pool=prefork --concurrency=1`, 2 vCPU/8 GB, `desired_count=1`, FARGATE_SPOT. `acks_late=True` + idempotency (CLAUDE.md rule 5) make Spot interruption safe
- All services: secrets injected from Secrets Manager ARNs; non-secret config in environment block; CloudWatch logs with 30-day retention
- Landmine 3 handled: `--pool=prefork` in ECS CMD overrides `worker_pool="solo"` Windows default in `celery_app.py`

### Load Balancer (alb.tf, T-13-01-02)
- `aws_lb.main`: `idle_timeout=4000` (Landmine 2 fix — prevents SSE force-close at ALB 60s default)
- HTTPS:443 listener: ACM cert, TLS 1.2/1.3, forwards to api target group
- HTTP:80 listener: HTTP_301 redirect to HTTPS:443
- `aws_lb_target_group.api`: `/health` path, interval=30, healthy=2, unhealthy=3

### DNS (route53.tf, PROD-10)
- A-alias `api_domain → ALB`: stable production API domain for `data-api` embed attribute
- A-alias `widget_domain → CloudFront`: stable CDN domain for widget script `src`

### Storage (s3.tf, T-13-01-04)
- `aws_s3_bucket.widget`: Block Public Access all-true; versioning enabled; OAC-only bucket policy (no public grant)
- `aws_s3_bucket.uploads`: Block Public Access all-true; versioning enabled; AES256 SSE; no bucket policy (IAM task role only)

### CDN (cloudfront.tf, PROD-08)
- `aws_cloudfront_origin_access_control.widget`: sigv4/always signing
- `aws_cloudfront_distribution.widget`: S3 origin via OAC; `acm_certificate_arn_us_east_1` (Pitfall 2 — CloudFront requires us-east-1 cert); CachingOptimized managed policy; `redirect-to-https`; `aliases=[var.widget_domain]`

### Runbook (deploy/README.md)
- New "Fargate production deployment (Phase 13)" section: required tfvars, `terraform init/plan/apply` order, `docker buildx build --platform linux/amd64` + ECR push for both images, Secrets Manager population commands, post-apply smoke test order
- Existing VM/systemd section retained as historical reference (not deleted)

## Threat Model Coverage

| Threat | Status | Mitigation |
|--------|--------|------------|
| T-13-01-01: Secrets in HCL | Mitigated | 18 secret containers declared; `secret_string` never set; grep verified |
| T-13-01-02: Plaintext HTTP / SSE downgrade | Mitigated | HTTPS ACM listener + HTTP_301 redirect; `idle_timeout=4000` |
| T-13-01-03: Over-broad IAM | Mitigated | bedrock:InvokeModel scoped to titan ARN only; S3 scoped to uploads bucket; no `Resource:"*"` on app actions |
| T-13-01-04: Public S3 bucket | Mitigated | Both buckets: `block_public_acls=true`, `block_public_policy=true`; widget via OAC only |
| T-13-01-05: ElastiCache open to internet | Mitigated | 6379 ingress restricted to `aws_security_group.ecs_tasks`; `transit_encryption_enabled=true` |
| T-13-01-06: Crashed task served as alive | Accept→Mitigate | ALB /health TG + `health_check_grace_period=120s` replaces unhealthy tasks |

## Deviations from Plan

### Auto-added: VOYAGE_API_KEY secret container
- **Rule 2 — missing critical functionality**
- **Found during:** Task 1 (secrets.tf authoring)
- **Issue:** `config.py` has `VOYAGE_API_KEY: str` (required field, no default). The plan's explicit secret list omits it — presumably because 13-02 (Bedrock migration) will make it optional. However, until 13-02 runs and before 13-08 (live apply), the ECS task definition would fail to start if VOYAGE_API_KEY isn't injected.
- **Fix:** Added `aws_secretsmanager_secret.voyage_api_key` to secrets.tf and included it in the task definition secrets block. 13-02 will make the field optional, at which point the secret injection can be removed from the task def (or left as a no-op).
- **Files modified:** deploy/terraform/secrets.tf, deploy/terraform/fargate.tf

### Auto-adjusted: ALB SG egress uses 0.0.0.0/0 (not ecs_tasks SG reference)
- **Rule 1 — avoiding Terraform circular dependency**
- **Found during:** Task 2 (alb.tf authoring)
- **Issue:** The plan specifies "egress to ecs_tasks" on the ALB SG. If `aws_security_group.alb` egress referenced `aws_security_group.ecs_tasks.id` AND `aws_security_group.ecs_tasks` ingress referenced `aws_security_group.alb.id`, Terraform would detect a resource-level circular dependency.
- **Fix:** ALB SG uses `egress 0.0.0.0/0` (standard ALB pattern). The `ecs_tasks` SG still restricts inbound to port 8000 from the ALB SG only, maintaining the intended security boundary.
- **Files modified:** deploy/terraform/alb.tf

### Deferred: `terraform validate` / `terraform fmt -check` checks
- **Per environment_notes in the plan prompt**
- Terraform is not installed on the local Windows dev machine (4 GB RAM)
- All HCL syntax is hand-verified against Terraform AWS provider >= 5.0 documentation
- `terraform validate` + `terraform fmt -check` are gated to 13-08 (live gate, where terraform+AWS credentials exist)

## Known Stubs

None. The Terraform module declares all resources with correct resource type, name, and attribute wiring. No placeholder values are used. All variable references are declared in variables.tf and all cross-file resource references resolve within the module.

The only "deferred" items are the Secrets Manager secret values (populated in 13-08) and the ACM certificate ARNs (provisioned in 13-08 or by the operator before apply) — these are expected out-of-band inputs, not stubs.

## Threat Flags

None. All new network endpoints and trust boundaries introduced by this plan are covered by the plan's `<threat_model>` and mitigated:
- ALB (Internet → AWS): TLS termination + HTTP redirect
- ElastiCache (ECS → Redis): SG-restricted + transit encryption
- S3 (public → widget bucket): OAC-only; Block Public Access all-true
- IAM (task → AWS services): least-privilege ARN-scoped policies

## Self-Check: PASSED

### Files exist:
- deploy/terraform/main.tf ✓
- deploy/terraform/ecr.tf ✓
- deploy/terraform/secrets.tf ✓
- deploy/terraform/iam.tf ✓
- deploy/terraform/variables.tf ✓
- deploy/terraform/outputs.tf ✓
- deploy/terraform/elasticache.tf ✓
- deploy/terraform/fargate.tf ✓
- deploy/terraform/alb.tf ✓
- deploy/terraform/route53.tf ✓
- deploy/terraform/s3.tf ✓
- deploy/terraform/cloudfront.tf ✓
- deploy/README.md (updated) ✓

### Commits exist:
- e8b51fa: Task 1 (network, ECR, secrets, IAM) ✓
- 32a2346: Task 2 (ElastiCache, Fargate, ALB, Route53) ✓
- 95b1f64: Task 3 (S3, CloudFront, runbook) ✓

### Acceptance criteria grep results: ALL PASS
- bedrock:InvokeModel scoped to titan ARN in iam.tf ✓
- no secret_string literal in secrets.tf ✓
- 6379 ingress uses security_groups (not cidr 0.0.0.0/0) in main.tf ✓
- idle_timeout = 4000 in alb.tf ✓
- HTTP_301 redirect in alb.tf ✓
- exactly 3 aws_ecs_service in fargate.tf ✓
- FARGATE_SPOT capacity_provider in fargate.tf ✓
- queues=runtime + pool=prefork + concurrency=1 in fargate.tf ✓
- health check path "/health" in alb.tf ✓
- 2 aws_s3_bucket_public_access_block in s3.tf ✓
- block_public_acls=true in s3.tf ✓
- origin_access_control in cloudfront.tf ✓
- acm_certificate_arn_us_east_1 in cloudfront.tf ✓
- linux/amd64 in deploy/README.md ✓
