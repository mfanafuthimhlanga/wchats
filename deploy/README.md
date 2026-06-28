# W Chats — Deployment Runbook

---

## Fargate production deployment (Phase 13)

This section documents the durable AWS serving substrate introduced in Phase 13
(plan 13-01). It replaces the Phase 12 local-PC + ephemeral tunnel with always-on
ECS Fargate services behind a stable Route53 domain.

### Prerequisites

- AWS account with billing enabled (resolves the Phase 12 no-credit-card constraint)
- Terraform >= 1.5 installed: https://developer.hashicorp.com/terraform/downloads
- AWS CLI v2 installed and configured (`aws configure`)
- Docker Desktop (or Engine) with buildx support
- Bedrock model access: in the AWS console, request access to
  `amazon.titan-embed-text-v2:0` in us-east-1 before deploying

### Required Terraform variables

Create a `deploy/terraform/terraform.tfvars` file (do NOT commit — it contains domain names):

```hcl
aws_region                    = "us-east-1"
api_domain                    = "api.wchats.app"            # your Route53 domain
widget_domain                 = "widget.wchats.app"         # your Route53 domain
route53_zone_id               = "Z0123456789ABCDEFGHIJK"    # your hosted zone ID
acm_certificate_arn           = "arn:aws:acm:us-east-1:..."  # covers api.wchats.app
acm_certificate_arn_us_east_1 = "arn:aws:acm:us-east-1:..."  # covers widget.wchats.app (MUST be us-east-1)
api_image_tag                 = "latest"
pipeline_image_tag            = "latest"
```

> **Note on ACM certs:** `acm_certificate_arn` is for the ALB (any region).
> `acm_certificate_arn_us_east_1` is for CloudFront — AWS requires this cert to be
> in `us-east-1` regardless of your deployment region (Pitfall 2 in RESEARCH.md).

### Step 1 — Build and push Docker images to ECR

Both images must be built for `linux/amd64` (Fargate default architecture).
Building without `--platform linux/amd64` on Windows produces an incompatible image.

```bash
# Authenticate with ECR (run after terraform apply to get the repo URLs)
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin <account_id>.dkr.ecr.us-east-1.amazonaws.com

# API image (used for both API service and runtime worker)
docker buildx build --platform linux/amd64 \
  -t <account_id>.dkr.ecr.us-east-1.amazonaws.com/wchats-api:latest \
  -f apps/api/Dockerfile apps/api/
docker push <account_id>.dkr.ecr.us-east-1.amazonaws.com/wchats-api:latest

# Pipeline image (Docling + torch CPU-only)
docker buildx build --platform linux/amd64 \
  -t <account_id>.dkr.ecr.us-east-1.amazonaws.com/wchats-pipeline:latest \
  -f apps/api/Dockerfile.pipeline apps/api/
docker push <account_id>.dkr.ecr.us-east-1.amazonaws.com/wchats-pipeline:latest
```

### Step 2 — Provision infrastructure with Terraform

```bash
cd deploy/terraform

# Initialize providers (no backend = local state; configure S3 backend for production)
terraform init

# Review the plan (no live changes yet)
terraform plan -out=tfplan

# Apply (provisions VPC, ECR, ELB, ElastiCache, ECS, S3, CloudFront, Route53)
terraform apply tfplan
```

Terraform outputs after apply:

| Output | Description |
|--------|-------------|
| `alb_dns_name` | ALB DNS (also the Route53 alias target) |
| `api_url` | Stable API URL (use as `data-api` in embed snippet) |
| `widget_cdn_url` | CloudFront CDN URL (use as script `src`) |
| `ecr_api_repository_url` | ECR URL for Step 1 push |
| `ecr_pipeline_repository_url` | ECR URL for Step 1 push |
| `elasticache_primary_endpoint` | Redis endpoint (for the `wchats/redis-url` secret) |
| `uploads_bucket_name` | S3 bucket name for `S3_UPLOADS_BUCKET` |
| `widget_bucket_name` | S3 bucket for widget bundle upload |

### Step 3 — Populate Secrets Manager values

For each secret in `deploy/terraform/secrets.tf`, set its value via the AWS CLI or console.
The Terraform apply created the secret containers; values are populated out-of-band.

```bash
# Example: set the ANTHROPIC_API_KEY secret
aws secretsmanager put-secret-value \
  --secret-id wchats/anthropic-api-key \
  --secret-string "sk-ant-..."

# Set all required secrets before starting ECS services:
# wchats/neon-api-key              — Neon API key
# wchats/neon-encryption-key       — Fernet key (base64url 32 bytes)
# wchats/control-db-url            — postgresql+asyncpg://...
# wchats/control-db-sync-url       — postgresql://...
# wchats/redis-url                 — rediss://<elasticache_primary_endpoint>:6379/0
# wchats/admin-key                 — random secret for X-Admin-Key header
# wchats/anthropic-api-key         — sk-ant-...
# wchats/voyage-api-key            — voyage API key (until 13-02 Bedrock migration)
# wchats/jwt-secret                — random 32-byte hex for widget JWT HS256
# wchats/clerk-webhook-signing-secret — from Clerk dashboard
```

### Step 4 — Upload widget bundle to S3

```bash
# Upload all widget files to the S3 bucket (CloudFront OAC serves these)
aws s3 sync apps/admin/public/wchats/ \
  s3://$(terraform -chdir=deploy/terraform output -raw widget_bucket_name)/ \
  --delete

# Invalidate CloudFront cache after upload
aws cloudfront create-invalidation \
  --distribution-id <distribution_id> \
  --paths "/*"
```

### Step 5 — Post-apply smoke test

```bash
# API health check (validates Redis + DB probes)
curl -I https://api.wchats.app/health

# SSE stream survival check (must stay alive for >125s; agent.response before 120s)
curl -N --max-time 125 https://api.wchats.app/widget/jobs/<job_id>/events

# Widget bundle accessible from CDN
curl -I https://widget.wchats.app/widget.js
```

### ECS service management

```bash
# Force new deployment (after image push)
aws ecs update-service --cluster wchats --service wchats-api --force-new-deployment

# Scale the runtime worker to zero (for maintenance)
aws ecs update-service --cluster wchats --service wchats-runtime-worker --desired-count 0

# View running tasks
aws ecs list-tasks --cluster wchats --service-name wchats-api

# Tail logs
aws logs tail /ecs/wchats-api --follow
```

### Architecture reference

```
[Browser / Embed snippet]
        |
        | HTTPS (ACM cert)
        v
[Route53: api.wchats.app] → [ALB idle_timeout=4000s]
                                      |
                         ┌────────────┴────────────┐
                         |                         |
               [ECS Fargate: API #1]  [ECS Fargate: API #2]
               uvicorn :8000           (HA — desired=2)
                         |
              [ElastiCache Redis] ← Celery broker + SSE pub/sub
                         |
            ┌────────────┴───────────┐
            |                       |
  [ECS Fargate:            [ECS Fargate Spot:
   Runtime Worker]          Pipeline Worker]
   always-on                acks_late + idempotent
   runtime queue            pipeline queue
            |                       |
            v                       v
   [Neon per-tenant DB]    [S3 wchats-uploads → parse → embed]

[widget.wchats.app] → [CloudFront OAC] → [S3 wchats-widget]
```

---

## AWS-VM reference runbook (superseded for Phase 13)

> **Retained for reference only.** The Oracle Cloud Always Free ARM VM + Caddy + systemd
> deployment below was the Phase 05–12 production host. Phase 13 replaces it with the
> ECS Fargate setup above. The VM runbook remains useful for understanding the secrets
> and process model that Fargate inherits.

Ordered checklist for deploying the W Chats API and runtime Celery worker to an
Oracle Cloud Always Free ARM (aarch64) VM. Plan 05 executes these steps.

---

## Prerequisites

- Oracle Cloud account (credit card required at signup; $1 hold, no ongoing charge for Always Free)
- DuckDNS account (free, no credit card) — register a subdomain, note your token
- Repo cloned locally with all secrets in `.env`

---

## Step A: Provision the VM (OCI A1.Flex, Ubuntu 22.04 aarch64)

1. Log in to the OCI Console → Compute → Instances → Create Instance.
2. Choose shape: **VM.Standard.A1.Flex**, 2 OCPU / 12 GB RAM (from the 4 OCPU / 24 GB Always Free pool).
3. Choose image: **Canonical Ubuntu 22.04 (aarch64 Minimal)**.
4. Upload your SSH public key.
5. Click Create.

**Capacity note:** OCI ARM capacity errors (`Out of host capacity`) are common in US regions.
If the instance fails to provision, use the retry loop:

```bash
# Run as a cron job (every 5 min) until the instance is created:
# https://github.com/hitrov/oci-arm-host-capacity
# EU-Frankfurt-1 and EU-Amsterdam-1 tend to have better availability.
```

6. Once the instance is `RUNNING`, note the **public IP address**.

---

## Step B: Open Port 443 (HTTPS only — port 80 is NOT required)

DNS-01 ACME (used by Caddy + DuckDNS) does not need port 80. Only 443 must be open.

### OCI VCN Security List (cloud firewall)

OCI Console → Networking → Virtual Cloud Networks → your VCN →
Security Lists → Default Security List → Add Ingress Rule:

- Source CIDR: `0.0.0.0/0`
- IP Protocol: TCP
- Destination Port: `443`

**Do NOT open port 80** — DNS-01 never uses it, and keeping it closed reduces attack surface.

### Host-level iptables (Ubuntu on Oracle images blocks everything except SSH by default)

```bash
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
sudo apt install -y iptables-persistent
sudo netfilter-persistent save
```

Verify: `sudo iptables -L INPUT -n | grep 443` — should show ACCEPT.

---

## Step C: Clone Repo + Install Python

```bash
sudo useradd -m -s /bin/bash wchats
sudo mkdir -p /opt/wchats
sudo chown wchats:wchats /opt/wchats

sudo -u wchats bash -c "
  git clone https://github.com/bantuson/wchats.git /opt/wchats/apps/api
  cd /opt/wchats/apps/api
  python3 -m venv /opt/wchats/venv
  /opt/wchats/venv/bin/pip install uv
  /opt/wchats/venv/bin/uv pip install '.[dev]'
"
```

**Note:** `claude-agent-sdk==0.1.81` bundles the aarch64 Claude Code binary — no separate
Node.js install required. The Linux aarch64 wheel is available on PyPI.

---

## Step D: Place the .env File

Copy your local `.env` to the VM. The file must contain all secrets:
`ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `NEON_API_KEY`, `NEON_ENCRYPTION_KEY`,
`CONTROL_DB_URL`, `CONTROL_DB_SYNC_URL`, `REDIS_URL` (Upstash TLS URL),
`JWT_SECRET`, `CLERK_WEBHOOK_SIGNING_SECRET`, `CLERK_JWKS_URL`.

```bash
# From your local machine:
scp .env ubuntu@<vm-ip>:/tmp/app.env
ssh ubuntu@<vm-ip> "sudo mv /tmp/app.env /opt/wchats/apps/api/.env && sudo chown wchats:wchats /opt/wchats/apps/api/.env && sudo chmod 600 /opt/wchats/apps/api/.env"
```

Verify: `sudo -u wchats stat /opt/wchats/apps/api/.env` — permissions must be `600`.

**DUCKDNS_TOKEN is NOT placed in .env** — it goes in Caddy's systemd drop-in (Step F).

---

## Step E: Install and Enable the Two systemd Units

```bash
sudo cp /opt/wchats/apps/api/deploy/systemd/wchats-api.service /etc/systemd/system/
sudo cp /opt/wchats/apps/api/deploy/systemd/wchats-celery-runtime.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wchats-api wchats-celery-runtime
```

Verify:

```bash
systemctl is-active wchats-api         # should print: active
systemctl is-active wchats-celery-runtime  # should print: active
curl http://127.0.0.1:8000/health      # should return: {"status":"ok"}
```

If a service fails, inspect logs: `journalctl -u wchats-api -n 50 --no-pager`

---

## Step F: Install Caddy with the DuckDNS Plugin + Configure TLS

### 1. Register your DuckDNS subdomain

Go to https://www.duckdns.org — log in, create a subdomain (e.g. `wchats-api`),
and point it to the VM public IP. Note your **DuckDNS token**.

### 2. Update the Caddyfile subdomain

Edit `deploy/caddy/Caddyfile` (in the repo) and replace `wchats-api.duckdns.org`
with your actual subdomain. Commit the change, then `git pull` on the VM.

### 3. Build Caddy with the DuckDNS DNS provider plugin

Standard `apt install caddy` does NOT include the DuckDNS plugin. Build it with xcaddy:

```bash
# Download xcaddy (Go binary, ARM64 pre-built)
curl -fsSL https://github.com/caddyserver/xcaddy/releases/latest/download/xcaddy_linux_arm64.tar.gz \
    | sudo tar -xz -C /usr/local/bin xcaddy

# Build Caddy with the DuckDNS DNS provider
xcaddy build --with github.com/caddy-dns/duckdns --output /usr/local/bin/caddy

# Verify the plugin is included
caddy list-modules | grep duckdns
```

Alternatively, visit https://caddyserver.com/download, select the
`github.com/caddy-dns/duckdns` DNS provider, and download the ARM64 binary.

### 4. Install the Caddyfile

```bash
sudo mkdir -p /etc/caddy
sudo cp /opt/wchats/apps/api/deploy/caddy/Caddyfile /etc/caddy/Caddyfile
```

### 5. Set DUCKDNS_TOKEN in Caddy's systemd environment

The token goes in Caddy's own drop-in, NOT the app `.env`:

```bash
sudo mkdir -p /etc/systemd/system/caddy.service.d
sudo tee /etc/systemd/system/caddy.service.d/override.conf <<EOF
[Service]
Environment=DUCKDNS_TOKEN=<your-duckdns-token>
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now caddy
```

### 6. Verify TLS

```bash
# From the VM (loopback):
curl http://127.0.0.1:8000/health

# From an external machine or your local terminal:
curl -I https://wchats-api.duckdns.org/health
# Expected: HTTP/2 200 with a valid Let's Encrypt certificate
```

**Caddy auto-renews the TLS certificate via DuckDNS DNS-01.** No cron job needed.

---

## Step G: Verification Commands

```bash
# Systemd service status
systemctl is-active wchats-api
systemctl is-active wchats-celery-runtime

# API health (internal)
curl http://127.0.0.1:8000/health

# API health (external, validates TLS)
curl -I https://wchats-api.duckdns.org/health

# Run deployment smoke test (plan 06 gate — runs against live host)
API_HOST=https://wchats-api.duckdns.org bash /opt/wchats/apps/api/scripts/smoke_vm.sh
```

---

## Security Notes

- Port 80 is NOT open. DNS-01 ACME never needs it.
- Port 443 only: OCI Security List + host iptables.
- uvicorn binds `127.0.0.1` only — never `0.0.0.0`. Caddy is the only public listener.
- `.env` is `chmod 600 wchats:wchats`. Not checked into git.
- `DUCKDNS_TOKEN` is in Caddy's drop-in, isolated from app secrets.
- systemd units use `EnvironmentFile=` only — no secret values are inlined in unit files.
- The pipeline Celery worker is NOT hosted (D-03). Ingestion runs locally on-demand.
