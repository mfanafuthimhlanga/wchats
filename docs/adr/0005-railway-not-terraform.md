# 0005: Railway, not the Terraform stack

Status: accepted. Decided with the owner 2026-08-23 on issue #14, built in #55
(config-as-code in #122, the rest with this ADR).

## What was decided

The stack serves from Railway: `api` (uvicorn), two Celery workers (`runtime` and
`pipeline` queues), `beat` (one replica, never scaled), and Railway's managed Redis plugin
for the broker and SSE pub/sub. One Dockerfile per image under `apps/api` (`Dockerfile`,
and `Dockerfile.pipeline` carrying the ~3 GB docling extra only), built by Railway on
push; CI builds no images and keeps running the gates. Staging and production are two
environments in one project, with a **hard spend limit of $20** at the first deploy,
raised only from the unit-economics number (#60). Object storage is Cloudflare R2 or
Backblaze B2 through the existing `S3_ENDPOINT_URL` seam, which production now honours
for exactly those two hosts and refuses for every other.

`deploy/terraform/`, `deploy/systemd/` and `deploy/caddy/` are deleted. Git keeps them.

## Why

- **Cost floor, measured.** Idle floor $5 to $10 on Railway against Fly $12, Render $14
  to $31, Fargate plus ALB about $60, and the Terraform stack as written about $204 with
  a $32 NAT gateway ("The cost floor of running the stack on a public URL").
- **The hard cap is the property.** Railway's user-set spend cap takes workloads offline
  when hit, which is what the local-emulator detour was reaching for.
- **The deleted trees described stacks the map does not build.** Twelve Terraform files
  never applied (no state, no backend block, no `~/.aws` on the build machine); the
  systemd units and Caddyfile are the Phase 12 single-box era.

## What this costs

AWS would buy scale and a larger service catalogue; the Bantuson finish line does not
test either. Reversing this after services and environments exist on Railway means
re-provisioning, so the trade is real and this record is the warning to a reader who
finds the archived runbook's Fargate section and assumes it describes the deployment.

## What arms when

Schedules arm per Agent at deploy (decision #6): every beat fan-out selects
`Agent.is_deployed == True`, whose only writer is POST /approve-deployment. The nightly
eval beat selected `status='ready'` until #32 closed with ticket 18.
