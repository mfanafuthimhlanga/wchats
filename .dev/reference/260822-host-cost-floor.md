# Cost floor of the public stack on four hosts

What it costs to keep uvicorn, one Celery worker, Celery beat and Redis on a public URL,
with S3-compatible object storage and Neon external. Prices are the providers' own list
prices on 2026-08-22, US East or equivalent, in USD. A month is 730 hours.

## The floor per host

| Host | Sizes (api / worker / beat / redis) | Idle $/month | 1-hour proof | Hard spend cap |
|---|---|---|---|---|
| Railway, Hobby | metered on actual RSS and CPU, not allocation | **$5 to $10** (fee $5, includes $5 of usage) | $0 on the trial, about $0.01 to $0.03 on Hobby | yes, user-set, minimum $10 |
| Fly.io | shared-cpu-1x: 256 MB / 1 GB / 256 MB / 256 MB + 1 GB volume | **$12.13** | $0.017 (trial covers 2 VM-hours, so 30 min of four machines) | none, and no billing alerts |
| Render, Hobby workspace | Starter 0.5 CPU 512 MB x3 + Key Value Starter 256 MB | **$31.00** | $0.042 | none for compute (pipeline minutes only) |
| AWS Fargate + ALB + S3 | 4 tasks at 0.25 vCPU / 0.5 GB, x86 | **$59.77** ($52.56 on ARM) | $0.08 (no NAT), $0.13 with NAT | free plan only: account closes when credits end |

Render can run cheaper ($14/month) with the Free web service and the Free Key Value, at
the price of a 15-minute idle spin-down, a one-minute cold start, and a 25 MB Redis that
loses state on restart. The M4 Terraform stack as written costs about $204/month; the
table below shows why.

## Per-unit rates

| Unit | AWS (us-east-1) | Railway | Fly.io | Render |
|---|---|---|---|---|
| vCPU-hour | $0.04048 x86, $0.03238 ARM, $0.01268 Spot | $0.02778 ($20/vCPU/month, metered) | bundled in presets; shared-cpu-1x 256 MB is $0.0028/h | bundled in instance: Starter $7/month = $0.0096/h |
| GB-hour memory | $0.004445 x86, $0.00356 ARM, $0.00139 Spot | $0.01386 ($10/GB/month, metered) | about $5 per 30 days per extra GB | bundled in instance |
| Smallest compute unit | 0.25 vCPU / 512 MiB task | no minimum; billed per minute of use | shared-cpu-1x 256 MB, $2.02/month | Starter 0.5 CPU / 512 MB, $7/month |
| Load balancer / public entry | ALB $0.0225/h + $0.008 per LCU-hour; public IPv4 $0.005/h each | included | shared IPv4 and IPv6 included; dedicated IPv4 $2/month | included; dedicated IP set $100/month |
| Redis | ElastiCache cache.t3.micro $0.017/h ($12.41/month), or a Fargate task | run as a service, metered | run as a Machine; Upstash pay-as-you-go $0.20 per 100k commands | Key Value Free 25 MB, Starter 256 MB $10/month |
| Object storage, GB-month | S3 Standard $0.023 | Buckets $0.015, S3-compatible | Tigris $0.02, S3-compatible, 5 GB free | none offered; use Tigris or S3 |
| Object requests | PUT $0.005 per 1k, GET $0.0004 per 1k | free | Tigris Class A $0.005 per 1k, Class B $0.0005 per 1k | n/a |
| Persistent disk, GB-month | EBS or EFS, not needed for this stack | Volumes $0.15 | Volumes $0.15 | $0.25 |
| Egress, per GB | $0.09 after the first 100 GB/month free | $0.05 per GB from services; bucket egress free | $0.02 (NA and EU); Tigris egress free | 5 GB/month included on Hobby, then $0.15 |
| NAT gateway | $0.045/h + $0.045/GB processed | none | none | none |
| Billing granularity | per second, 1-minute minimum per task | per minute | per second | per second |

## Fargate arithmetic

Fargate x86 at 0.25 vCPU / 0.5 GB costs $0.0123/h, $9.01/month per task. The floor row
above is four such tasks ($36.04) plus one ALB ($16.43) plus two public IPv4 addresses
for the ALB subnets ($7.30). Tasks in public subnets with `assignPublicIp` pull images
without a NAT gateway, which removes the single largest idle line.

`deploy/terraform/` as written sizes the stack for production, not for the floor:

| Resource in the plan | Size | $/month |
|---|---|---|
| `api` x2 | 0.5 vCPU / 1 GB each | $36.04 |
| `runtime_worker` | 2 vCPU / 4 GB | $72.08 |
| `pipeline_worker` on Spot | 2 vCPU / 8 GB | $26.64 |
| ALB + 2 public IPv4 | | $23.73 |
| NAT gateway | one, hourly only | $32.85 |
| ElastiCache `cache.t3.micro` | | $12.41 |
| Total before CloudFront, CloudWatch Logs, ECR, S3 | | **$203.74** |

The worker sizes are not padding. The pipeline image carries docling, transformers and
torch, about 3 GB installed, so a 0.5 GB worker task cannot import the pipeline. On every
host the worker's real memory is the term that moves the bill; the floors above use the
smallest size the host sells and the reader should add the worker's measured RSS.

## Spend caps and alarms

| Host | Mechanism | What happens at the limit |
|---|---|---|
| Railway | Usage Limits, all plans. Hard limit (minimum $10) and a soft limit email. Reminders at 75%, 90%, 100%. | "all your workloads will be taken offline". Recovery next cycle is automatic unless it fails, then redeploy by hand. |
| Fly.io | None. "We don't support billing alerts (yet), so budget accordingly." The dashboard shows month-to-date spend. | Nothing stops. |
| Render | Spend limit applies to build pipeline minutes only. Email when approaching and when exceeding an included amount (bandwidth, free hours). | Builds stop at the pipeline limit. Compute keeps billing. Free web services suspend after 750 free hours per workspace per month. |
| AWS | AWS Budgets alerts by email or SNS on actual or forecast spend; data updates up to three times a day, 8 to 12 hours apart. Budget actions apply an IAM deny policy or SCP, or stop EC2 and RDS instances. | Actions cannot stop ECS services or the ALB, so a running stack keeps billing through a breached budget. The Free account plan is the only hard cap: "The account closes on its own 6 months after you open it or when your credits run out, whichever comes first." |

## Free tiers

| Host | Free allowance | Limits |
|---|---|---|
| AWS Free plan | $100 credit at sign-up, up to $200 by completing activities, 6 months | No charges possible on the Free plan. Account closes at 6 months or when credits run out. New customers get 750 ALB hours and 15 LCUs per month. |
| Railway | Trial: $5 one-time credit, 30 days, no card. Free plan after: $1/month credit. | Trial caps each service at 1 GB RAM and shared vCPU, 5 services per project. Free plan caps a service at 1 vCPU / 0.5 GB, one replica, and $1 covers 0.1 GB-month of RAM, so four services exhaust it in days. Buckets: 10 GB-month free. |
| Fly.io | Trial: 2 VM-hours total, 7 days, 10 machines, 20 GB volumes, no card; machines auto-stop after 5 minutes. | Adding a card ends the trial. Tigris: 5 GB storage, 10k Class A and 100k Class B requests per month. |
| Render | Free web service (0.1 CPU, 512 MB), one Free Key Value (25 MB, no persistence), Free Postgres (30-day limit). 750 Free instance hours per workspace per month. | Free web services spin down after 15 minutes idle, cold start about one minute. Background workers have no free tier, so beat and the worker cost $7 each at minimum. |

## The one-hour proof

| Host | What runs | Cost |
|---|---|---|
| Railway | four services, idle | $0.008 at 0.5 GB total RSS, $0.03 at 2 GB; $0 on the trial credit |
| Fly.io | three 256 MB machines plus a 1 GB worker | $0.0166; the trial's 2 VM-hours cover 30 minutes of four machines |
| Render | three Starter instances plus Key Value Starter, prorated to the second | $0.042 |
| AWS | four 0.25/0.5 tasks, ALB, two IPv4, no NAT | $0.082; $0.127 with the plan's NAT gateway; $0 against Free plan credits |

Tear-down is `terraform destroy` on AWS and deleting the project or app on the others.
Fly keeps charging $0.15 per GB of rootfs per month for stopped machines and $0.15 per GB
for volumes until they are deleted, not merely stopped.

## Sources fetched on 2026-08-22

- https://aws.amazon.com/fargate/pricing/ and its price files
  `b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ecs/USD/current/ecs.json`,
  `dftu77xade0tc.cloudfront.net/fargate-spot-prices.json`
- https://aws.amazon.com/elasticloadbalancing/pricing/
- https://aws.amazon.com/s3/pricing/ and `meteredUnitMaps/s3/USD/current/s3-standard.json`,
  `meteredUnitMaps/datatransfer/USD/current/datatransfer.json`
- https://aws.amazon.com/vpc/pricing/ and `meteredUnitMaps/vpc/USD/current/vpc.json`
- `meteredUnitMaps/elasticache/USD/current/elasticache.json`
- https://aws.amazon.com/free/
- https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-tasks-services.html
- https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html
- https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-controls.html
- https://railway.com/pricing
- https://docs.railway.com/reference/pricing/plans
- https://docs.railway.com/reference/pricing/free-trial
- https://docs.railway.com/reference/pricing/faqs
- https://docs.railway.com/reference/usage-limits
- https://docs.railway.com/storage-buckets/billing
- https://fly.io/docs/about/pricing/
- https://fly.io/docs/about/billing/
- https://fly.io/docs/about/free-trial/
- https://fly.io/docs/about/cost-management/
- https://fly.io/docs/upstash/redis/
- https://www.tigrisdata.com/pricing/
- https://render.com/pricing
- https://render.com/docs/free
- https://render.com/docs/build-pipeline
