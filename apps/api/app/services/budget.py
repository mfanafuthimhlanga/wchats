"""
Per-tenant daily budget tracking service.

F4 (CRITICAL) — Cross-Phase Security Review.
Without a tenant daily ceiling, an attacker can create a fresh conversation_id
per request (each capped at the per-conversation limit) and exhaust Anthropic
spend at ~$3/min = ~$4,320/day per agent.

This service uses Redis INCRBYFLOAT to track cumulative spend per tenant per
calendar day (UTC). The key expires after 86400 seconds (24 hours), so the
budget resets automatically the following day.

Note on atomicity: The check-then-increment pattern is not fully atomic (TOCTOU
race). For M4.1 this is acceptable because ESTIMATED_TURN_COST_USD is a
conservative upper bound ($0.01 vs typical $0.001–$0.003 actual spend), so mild
overspend under a race remains within safe bounds. A fully atomic WATCH/MULTI/EXEC
pipeline can be introduced in M5 if needed.
"""

import time

from redis.asyncio import Redis

# F4: Estimated per-turn Anthropic cost (conservative upper bound for Haiku 4.5).
# It lives beside the ceiling it is charged against rather than on a route, because
# both chat routes charge the same tenant budget and a second copy of this number
# would be a second, silently different ceiling (BACKLOG 7.4).
ESTIMATED_TURN_COST_USD = 0.01


async def check_and_increment_budget(
    tenant_id: str, cost_usd: float, redis: Redis, ceiling_usd: float
) -> bool:
    """Check whether the tenant has budget remaining and record the spend if so.

    Args:
        tenant_id:   Tenant UUID string — used as part of the Redis key.
        cost_usd:    Estimated cost of the current turn in USD.
        redis:       Async Redis client.
        ceiling_usd: Daily spend ceiling in USD (from settings.TENANT_DAILY_BUDGET_USD).

    Returns:
        True  — spend is within ceiling; cost_usd has been recorded.
        False — ceiling already reached; cost_usd NOT recorded; caller must return 429.
    """
    key = f"budget:{tenant_id}:{_today()}"
    current = float(await redis.get(key) or 0.0)
    if current >= ceiling_usd:
        return False
    await redis.incrbyfloat(key, cost_usd)
    await redis.expire(key, 86400)
    return True


def _today() -> str:
    """Return today's date as YYYY-MM-DD (UTC) for use in Redis key partitioning."""
    return time.strftime("%Y-%m-%d")
