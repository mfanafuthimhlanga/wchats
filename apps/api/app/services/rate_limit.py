"""
Per-agent request rate limiting, shared by every route that dispatches an agent turn.

Lifted out of app/api/v1/widget.py (BACKLOG 7.4). The widget chat route has enforced a
60/min per-agent ceiling inline since T-04-04-06 while POST /agents/{agent_id}/chat —
the tenant-facing product route, authenticated with the tenant's own API key — enforced
nothing. One implementation, two call sites, so the two ceilings cannot drift.

Returns a bool rather than raising: HTTP status codes belong to app.api, and the
import-linter layers contract puts app.services below it.
"""

import time

from redis.asyncio import Redis

# Turns per agent per 60-second window — the ceiling the widget route has enforced
# since T-04-04-06.
AGENT_TURN_RATE_LIMIT_PER_MIN = 60


async def check_agent_turn_rate_limit(
    key_prefix: str,
    agent_id: str,
    redis: Redis,
    limit: int = AGENT_TURN_RATE_LIMIT_PER_MIN,
) -> bool:
    """Record one request against *agent_id* and report whether it is within *limit*.

    Key: {key_prefix}:{agent_id}:{bucket}  (bucket = 60-second window)
    TTL: 60 seconds — the key dies with its window.

    Args:
        key_prefix: Redis key namespace. Each route passes its OWN prefix so one
                    route's traffic can never starve — or be starved by — another
                    route's traffic on the same agent. This is the same reason the
                    feedback route already keys `rate:feedback:...` separately.
        agent_id:   Agent UUID string.
        redis:      Async Redis client.
        limit:      Requests allowed per 60-second window.

    Returns:
        True  — within the ceiling; this request has been counted.
        False — over the ceiling; caller must return 429 with Retry-After: 60.
    """
    bucket = str(int(time.time()) // 60)
    key = f"{key_prefix}:{agent_id}:{bucket}"
    await redis.set(key, 0, nx=True, ex=60)
    count = await redis.incr(key)
    return count <= limit
