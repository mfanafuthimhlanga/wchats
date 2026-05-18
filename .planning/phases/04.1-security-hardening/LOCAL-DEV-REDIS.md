# Local Dev: Redis Broker Configuration (F5 — Phase 4.1)

## Why This Matters

Phase 04 changed what the Redis broker carries. Task args for `run_agent_turn` now
include `body.message` — up to 2000 chars of customer-supplied text from anonymous
widget callers. Celery also stores task results in Redis. The original Phase 01
acceptance (AR-06) premised that Redis contained only UUID identifiers with no PII.
That premise no longer holds.

## App-Level Fix

`celery_app.conf.result_expires = 300` — task results are purged from Redis after
5 minutes. This is already applied in `apps/api/app/worker/celery_app.py`.

## Local Dev: Disable RDB Snapshots

Redis RDB persistence snapshots to disk can capture task args (including message text)
in `/var/lib/redis/dump.rdb` or the local Redis data directory. For local development,
start Redis without RDB snapshots:

```
redis-server --save ""
```

This disables all RDB snapshot triggers for the current process. No data is
persisted to disk between restarts — appropriate for a local dev broker.

## Production Note

In production, Redis should run behind mTLS with Redis ACL user restrictions.
This is deferred to M10 production hardening. Until then, ensure Redis is not
exposed on a public interface and RDB is disabled on the broker instance.

## References

- Phase 01 AR-06 (01-SECURITY.md) — original acceptance being annotated
- Phase 04 trust boundary table (04-SECURITY.md) — updated in Phase 4.1 Plan 05
- CONTEXT.md F5 section
