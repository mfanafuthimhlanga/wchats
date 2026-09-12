# Staging Redis is out of requests, and this box cannot tell you when it comes back

Staging's Upstash Redis (`firm-calf-277244.upstash.io`) has spent its request allowance.
Every command fails, so every Celery worker, the beat schedule and the SSE fan-out on
staging fail with it. This note says how to check the state without opening a browser, and
which single fact still needs one.

## Check it from here

`REDIS_URL` on any staging service is the connection string. Pipe it into a probe rather
than printing it:

```bash
railway variables -s worker-runtime -e staging --kv \
  | apps/api/.venv/Scripts/python.exe -c "
import sys, redis
url = next(l.split('=',1)[1].strip() for l in sys.stdin if l.startswith('REDIS_URL='))
try:
    print('PING ->', redis.Redis.from_url(url, socket_connect_timeout=10).ping())
except Exception as e:
    print(type(e).__name__, e)
"
```

Exhausted looks like this, observed 2026-09-10 19:31 SAST:

```
ResponseError: max requests limit exceeded. Limit: 500000, Usage: 500000.
```

One `PING` costs one request, and the quota rejects it rather than serving it, so the probe
is safe to repeat.

Service state is a separate read and touches no deployment:

```bash
railway status --json
```

On 2026-09-10 that reported `beat` and `api-service` with no active deployment, and
`worker-pipeline` and `worker-runtime` with one `CRASHED` instance each, stopped.

## What the limit is

Upstash counts requests **monthly**
(https://upstash.com/docs/redis/troubleshooting/max_requests_limit). The doc gives no reset
date and no way to read the current period, so the reset window is only visible in the
Upstash console.

## Why this box cannot read the reset date

The staging services carry `REDIS_URL` and nothing else Redis-shaped. Upstash's management
API needs an account email and an API key, and neither exists in this environment, so there
is no CLI route to the usage figure or the billing period. Checking the reset date means
opening the Upstash console.

## What burns the allowance

Idle Celery polling, but slowly, and the thirty minute reading below is wrong.

One idle `runtime` worker sends **87 Redis commands a minute**, measured 2026-09-11 against
a local `redis-server` with the same app and the same start command: 57 `brpop` at one per
second plus 30 `publish` from the 2 second worker heartbeat. Two workers spend 500,000 in
about two days. Thirty minutes of two idle workers is roughly 5,000 commands, so the
2026-09-09 deploy cannot have spent the allowance on its own; it finished an allowance that
was already nearly gone, and the eleven hours `beat` and `api-service` then ran unattended
are the larger part of the bill. Issue #237 carries the full table and the two settings that
cut it by an order of magnitude.

What actually happened on 2026-09-09: a merge to `main` redeployed the four staging services
from `EXITED` to `RUNNING` at 19:09, both workers crashed terminally on the quota error by
19:42, and `beat` and `api-service` ran on until 06:18.

A merge to `main` is a deploy, and a deploy restarts every parked service. Even with both
settings in #237 applied, two idle workers spend 29 days of a 30 day allowance, so staging
stays parked when nobody is using it.

## Reset observed

`PING` returned True on 2026-09-11 at 08:00 SAST, where the same probe returned the quota
error on 2026-09-10 at 19:31. The allowance resets; the date it resets on is still only
visible in the console.

```bash
railway down -y -s <service> -e staging     # stop one; says "No deployments found" if already crashed
railway up --detach -y -s <service> -e staging   # bring one back
```

## Parking after a merge takes two passes, and one pass looks like it worked

`railway down` removes the deployment that exists at the moment it runs. A merge to `main`
leaves builds in flight, so the first pass removes the OLD deployments and the new ones go
live behind you.

Observed 2026-09-12, after merging four PRs and parking all four services:

```
first pass:   all four reported down
two minutes later:
  beat            active=1  stopped=False  status=SUCCESS
  worker-runtime  active=1  stopped=False  status=SUCCESS
```

Those two are the Redis burners. Park again, then verify with a gap:

```bash
for svc in worker-runtime beat worker-pipeline api-service; do
  railway down -s "$svc" -e staging -y
done
sleep 120
railway status --json          # every service active=0
railway deployment list -s worker-runtime -e staging --json   # recent entries REMOVED
```

`railway status --json` immediately after `railway down` cannot see a build that has not
finished, so a single check is not evidence. The deployment list is the stronger read: a
parked service shows `REMOVED`, and anything `BUILDING` or `SUCCESS` is about to bill you.

One `PING` confirms the allowance survived the window. On 2026-09-12 it returned True after
about twenty minutes of uptime across four deploys, which is roughly 5,000 of the 500,000.
