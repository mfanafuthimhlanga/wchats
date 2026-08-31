# 0005: Railway, not the Terraform stack

Status: accepted. Decided with the owner 2026-08-23 on issue #14, built in #55
(config-as-code in #122, the rest with this ADR).

## What was decided

The stack serves from Railway: `api` (uvicorn), two Celery workers (`runtime` and
`pipeline` queues), `beat` (one replica, never scaled), and Railway's managed Redis plugin
for the broker and SSE pub/sub. One Dockerfile per image under `apps/api` (`Dockerfile`,
and `Dockerfile.pipeline` adding the ~3 GB docling extra), built by Railway on
push; CI builds no images and keeps running the gates. Staging and production are two
environments in one project, with a **hard spend limit of $20** at the first deploy,
raised only from the unit-economics number (#60). Object storage is Cloudflare R2 or
Backblaze B2 through the existing `S3_ENDPOINT_URL` seam, which production now honours
for those two providers' host suffixes over https and refuses for every other endpoint
(the account within the provider is not yet pinned: #133).

`deploy/terraform/`, `deploy/systemd/` and `deploy/caddy/` are deleted, and
`scripts/smoke_vm.sh` with them (the single-box era's probe, two hostnames stale). Git
keeps them.

## Deviations from the decision as written

Two, both riding this ticket's PR for ratification. The decision said one worker process
serving both queues until the unit-economics number (#60) shows a reason to split; #122
shipped two worker services and a second Dockerfile so the 3 GB docling image is not the
price of every agent turn. And "the code does not change" for object storage did not
survive contact: the old seam refused every production endpoint, so honouring R2 or B2
required the allowlist in `storage_service.py`. Both images currently install the `dev`
extra too; slimming them is real but not this decision.

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

Two jobs the deleted tree did that nothing does yet: serving the widget bundle at
`WIDGET_CDN_BASE` (the CloudFront and S3 files were its only mechanism; #135 carries the
Railway-era story), and knowing the proxy's idle timeout (the ALB was set to 4000s for
SSE; Railway's bound is unmeasured and PRODUCTION-READINESS 3.1 records the probe owed).

## What arms when

Schedules arm per Agent at deploy (decision #6): every beat fan-out selects
`Agent.is_deployed == True`, whose only writer is POST /approve-deployment. The nightly
eval beat selected `status='ready'` until #32 closed with ticket 18.
