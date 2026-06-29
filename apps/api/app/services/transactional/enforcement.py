"""
transactional.enforcement — Capability envelope enforcement (CAP-02).

Provides:
    _parse_rate_limit(rate_str) -> tuple[int, int] | None
        Parse "N/<unit>" (e.g. "5/hour", "10/day") to (max_calls, window_secs).

    check_capability_envelope(agent_id, skill, args) -> tuple[dict, str | None]
        Fail-closed access-control gate. Returns (snapshot_dict, denial_reason).
        denial_reason is None on a full pass; non-None on any denial.

Enforcement order (T-14-03-01 mitigations):
    1. No envelope row   → denial reason "no_envelope_row"
    2. enabled = false   → denial reason "disabled"
    3. rate_limit exceeded (Redis INCR over window) → denial reason "rate_limit"
    4. constraints.max_amount_cents exceeded → denial reason "max_amount_cents"
    5. scope_filters (Phase-16 no-op, documented)
    6. All checks pass   → return (snapshot_dict, None)

FAIL-CLOSED CONTRACT (T-14-03-01):
    A missing or disabled envelope row MUST yield a denial, never a pass.
    There is no fallback path that authorizes an unconfigured skill.

Redis usage:
    Redis is used ONLY for the rate-limit counter (INCR + TTL on a window-aligned key).
    Redis loss resets the rate-limit window counter — acceptable, not catastrophic.
    Redis is NOT used for idempotency (see idempotency.py).

capability_snapshot isolation (T-14-03-03):
    The snapshot is the agent's own envelope row, scoped by agent_id + skill,
    converted to a plain dict. No ORM objects, no other agent's data.
"""

from __future__ import annotations

import ssl
import time
from typing import Any

import redis as redis_lib
import structlog
from sqlalchemy import text as sa_text

from app.core.config import settings
from app.core.database import get_sync_db

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Redis client for rate-limit counters (lazy, module-level singleton)
# ---------------------------------------------------------------------------

_rate_limit_redis: redis_lib.Redis | None = None


def _get_redis() -> redis_lib.Redis:
    """Return (and lazily create) the module-level sync Redis client for rate limiting."""
    global _rate_limit_redis
    if _rate_limit_redis is None:
        url_clean = (
            settings.REDIS_URL.split("?")[0]
            if "?" in settings.REDIS_URL
            else settings.REDIS_URL
        )
        ssl_opts: dict = (
            {"ssl_cert_reqs": ssl.CERT_NONE}
            if url_clean.startswith("rediss://")
            else {}
        )
        _rate_limit_redis = redis_lib.from_url(url_clean, **ssl_opts)
    return _rate_limit_redis


# ---------------------------------------------------------------------------
# Rate-limit parser
# ---------------------------------------------------------------------------

_UNIT_TO_SECS: dict[str, int] = {
    "minute": 60,
    "hour": 3600,
    "day": 86400,
}


def _parse_rate_limit(rate_str: str | None) -> tuple[int, int] | None:
    """Parse "N/<unit>" to (max_calls, window_secs).

    Args:
        rate_str: e.g. "5/hour", "10/day", "100/minute" — or None.

    Returns:
        (max_calls, window_secs) tuple, or None if rate_str is None/empty/malformed.
    """
    if not rate_str:
        return None
    parts = rate_str.strip().split("/")
    if len(parts) != 2:
        return None
    try:
        max_calls = int(parts[0])
    except ValueError:
        return None
    window_secs = _UNIT_TO_SECS.get(parts[1].lower())
    if window_secs is None:
        return None
    return max_calls, window_secs


# ---------------------------------------------------------------------------
# Main enforcement function
# ---------------------------------------------------------------------------


async def check_capability_envelope(
    agent_id: Any,
    skill: str,
    args: Any,
) -> tuple[dict, str | None]:
    """Fail-closed capability envelope check.

    Reads the capability_envelopes row for (agent_id, skill) from the control DB,
    validates rate limits and constraints, and returns either:
      - (snapshot_dict, None)           — all checks passed
      - ({} or snapshot_dict, reason)   — denied; reason is a short string

    On every denial, logs structlog.warning("capability.denial", ...).

    Args:
        agent_id: UUID of the calling agent (any uuid-like; serialised to str for the query).
        skill: Name of the tool/skill being invoked.
        args: Validated Pydantic input model. Fields `amount_cents` and
              `refund_amount_cents` are read for max_amount_cents enforcement.

    Returns:
        (snapshot_dict, denial_reason) — denial_reason is None on pass-through.

    Security:
        - Missing or disabled envelope → fail-closed (denial, never pass).
        - snapshot is always scoped to the calling agent_id only.
        - Redis is used ONLY for the rate-limit counter, never for idempotency.
    """
    agent_id_str = str(agent_id)

    # ------------------------------------------------------------------
    # 1. Read envelope row from control DB (scoped to this agent + skill)
    # ------------------------------------------------------------------
    with get_sync_db() as db:
        row = db.execute(
            sa_text(
                "SELECT id, agent_id, skill, enabled, rate_limit, constraints, "
                "requires_confirmation, requires_identity_verification, updated_at "
                "FROM capability_envelopes "
                "WHERE agent_id = :a AND skill = :s "
                "LIMIT 1"
            ),
            {"a": agent_id_str, "s": skill},
        ).mappings().first()

    # ------------------------------------------------------------------
    # 2. Fail-closed: no row → denial
    # ------------------------------------------------------------------
    if row is None:
        log.warning(
            "capability.denial",
            agent_id=agent_id_str,
            skill=skill,
            reason="no_envelope_row",
        )
        return {}, "no_envelope_row"

    # Convert to plain dict immediately — never pass ORM/mapping object downstream.
    snapshot: dict = dict(row)

    # ------------------------------------------------------------------
    # 3. Fail-closed: disabled envelope → denial
    # ------------------------------------------------------------------
    if not snapshot.get("enabled", False):
        log.warning(
            "capability.denial",
            agent_id=agent_id_str,
            skill=skill,
            reason="disabled",
        )
        return snapshot, "disabled"

    # ------------------------------------------------------------------
    # 4. Rate-limit check (Redis INCR with window-aligned key)
    # ------------------------------------------------------------------
    rate_limit_str: str | None = snapshot.get("rate_limit")
    parsed = _parse_rate_limit(rate_limit_str)
    if parsed is not None:
        max_calls, window_secs = parsed
        window_key = int(time.time()) // window_secs
        redis_key = f"ratelimit:{agent_id_str}:{skill}:{window_key}"
        redis_client = _get_redis()
        count = redis_client.incr(redis_key)
        redis_client.expire(redis_key, window_secs + 1)
        if count > max_calls:
            log.warning(
                "capability.denial",
                agent_id=agent_id_str,
                skill=skill,
                reason="rate_limit",
                count=count,
                max_calls=max_calls,
            )
            return snapshot, "rate_limit"

    # ------------------------------------------------------------------
    # 5. Constraint check: max_amount_cents
    # ------------------------------------------------------------------
    constraints: dict = snapshot.get("constraints") or {}
    max_amount_cents = constraints.get("max_amount_cents")
    if max_amount_cents is not None:
        # Support both amount_cents (place_order) and refund_amount_cents (issue_refund)
        amount = getattr(args, "amount_cents", None) or getattr(args, "refund_amount_cents", None)
        if amount is not None and amount > max_amount_cents:
            log.warning(
                "capability.denial",
                agent_id=agent_id_str,
                skill=skill,
                reason="max_amount_cents",
                amount=amount,
                limit=max_amount_cents,
            )
            return snapshot, "max_amount_cents"

    # ------------------------------------------------------------------
    # 6. scope_filters — Phase-16 no-op (documented, not enforced yet)
    # ------------------------------------------------------------------
    # scope_filters in constraints are a Phase-16 feature. The field is
    # accepted and stored but not yet validated. Phase 16 will add allowlist
    # enforcement here.

    # ------------------------------------------------------------------
    # All checks passed — return snapshot and no denial
    # ------------------------------------------------------------------
    return snapshot, None
