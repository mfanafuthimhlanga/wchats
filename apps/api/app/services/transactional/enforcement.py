"""
transactional.enforcement — Capability envelope enforcement (CAP-02).

Provides:
    _parse_rate_limit(rate_str) -> tuple[int, int] | None
        Parse "N/<unit>" (e.g. "5/hour", "10/day") to (max_calls, window_secs).

    check_capability_access(agent_id, skill) -> tuple[dict, str | None]
        Side-effect-free authorization gate (14-07 split).
        Reads the envelope row, coerces to JSON-safe snapshot, applies only the
        fail-closed existence/enabled checks. NO Redis, NO INCR, NO side effects.
        Returns (snapshot_dict, denial_reason).

    apply_rate_and_constraint_checks(agent_id, skill, snapshot, args) -> str | None
        Side-effecting half (14-07 split).
        Executes the rate-limit Redis INCR+EXPIRE pipeline (IN-01) and the
        max_amount_cents constraint check (IN-02). Returns denial_reason or None.

    check_capability_envelope(agent_id, skill, args) -> tuple[dict, str | None]
        Fail-closed access-control gate — RETAINED FACADE (14-07).
        Calls check_capability_access first; on denial returns immediately.
        Then calls apply_rate_and_constraint_checks. Returns (snapshot, denial_reason).
        Same return contract as the monolithic function it replaces; the 14-04 dispatcher
        and all existing tests call this function unchanged.

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
    Redis client verifies the server TLS certificate by default (WR-04); relaxation
    requires REDIS_TLS_INSECURE=True in settings plus an explicit warning log.

capability_snapshot isolation (T-14-03-03):
    The snapshot is the agent's own envelope row, scoped by agent_id + skill,
    converted to a plain dict. No ORM objects, no other agent's data.

WR-03 offload:
    Blocking get_sync_db reads and Redis pipeline calls are executed via
    asyncio.to_thread so they do not stall the event loop.

IN-01 (TTL-less key prevention):
    INCR and EXPIRE are issued atomically in a single Redis pipeline.

IN-02 (falsy-zero amount):
    amount_cents=0 is a valid real amount; explicit None-check prevents the
    falsy fallthrough to refund_amount_cents.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime
from typing import Any
from uuid import UUID

import redis as redis_lib
import structlog
from sqlalchemy import text as sa_text

from app.core.config import settings
from app.core.database import get_sync_db
from app.core.redis_tls import redis_ssl_kwargs

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Redis client for rate-limit counters (lazy, module-level singleton)
# ---------------------------------------------------------------------------

_rate_limit_redis: redis_lib.Redis | None = None


def _get_redis() -> redis_lib.Redis:
    """Return (and lazily create) the module-level sync Redis client for rate limiting.

    WR-04: For rediss:// URLs, certificate verification is ON by default
    (ssl_cert_reqs=ssl.CERT_REQUIRED, ssl_check_hostname=True). Disabling
    verification requires REDIS_TLS_INSECURE=True in settings AND emits a
    warning log on every factory call so the exposure is visible in logs.

    Issue #144: this factory was the only one of fourteen that read the setting,
    and app.core.redis_tls now holds that decision for all of them.
    """
    global _rate_limit_redis
    if _rate_limit_redis is None:
        url_clean = (
            settings.REDIS_URL.split("?")[0]
            if "?" in settings.REDIS_URL
            else settings.REDIS_URL
        )
        ssl_opts: dict = redis_ssl_kwargs(url_clean)
        _rate_limit_redis = redis_lib.from_url(url_clean, **ssl_opts)
    return _rate_limit_redis


# ---------------------------------------------------------------------------
# JSON-safe snapshot coercion
# ---------------------------------------------------------------------------


def _json_safe(value: Any) -> Any:
    """Coerce a DB row value to a JSON-serializable form for the capability_snapshot JSONB column.

    A text() SELECT returns native Python types: UUID columns as uuid.UUID and TIMESTAMPTZ
    as datetime — neither of which stock json.dumps can serialise. The snapshot is written to
    the tool_calls_audit.capability_snapshot JSONB column, so any non-serializable value would
    raise TypeError at db.commit() (CR-01). Coerce UUID/datetime to strings; pass everything
    else (str, bool, int, dict, None) through unchanged.
    """
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


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
# Split: check_capability_access — side-effect-free authorization
# ---------------------------------------------------------------------------


async def check_capability_access(
    agent_id: Any,
    skill: str,
) -> tuple[dict, str | None]:
    """Side-effect-free authorization gate (14-07 split).

    Reads the capability_envelopes row for (agent_id, skill) from the control DB,
    coerces to a JSON-safe snapshot, and applies only the fail-closed checks:
      no_envelope_row and disabled.

    NO Redis, NO INCR, NO rate-limit side effects. The caller (14-08 dispatcher)
    can run this first and defer apply_rate_and_constraint_checks to avoid
    incrementing the rate counter on idempotent replays (WR-01 substrate).

    WR-03: The blocking get_sync_db call is offloaded to asyncio.to_thread.

    Args:
        agent_id: UUID of the calling agent.
        skill: Name of the tool/skill being invoked.

    Returns:
        ({}, "no_envelope_row") when the row is absent (fail-closed).
        (snapshot, "disabled") when enabled=False (fail-closed).
        (snapshot, None) when the row exists and is enabled.
    """
    agent_id_str = str(agent_id)

    # ------------------------------------------------------------------
    # 1. Read envelope row (blocking DB call — offloaded to thread, WR-03)
    # ------------------------------------------------------------------
    def _read_envelope() -> Any:
        with get_sync_db() as db:
            return db.execute(
                sa_text(
                    "SELECT id, agent_id, skill, enabled, rate_limit, constraints, "
                    "requires_confirmation, requires_identity_verification, updated_at "
                    "FROM capability_envelopes "
                    "WHERE agent_id = :a AND skill = :s "
                    "LIMIT 1"
                ),
                {"a": agent_id_str, "s": skill},
            ).mappings().first()

    row = await asyncio.to_thread(_read_envelope)

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
    # Coerce UUID/datetime values to JSON-safe forms (CR-01): the snapshot is stored in the
    # tool_calls_audit.capability_snapshot JSONB column, and stock json.dumps cannot serialise
    # the UUID id/agent_id or the TIMESTAMPTZ updated_at this SELECT returns from a real DB.
    snapshot: dict = {k: _json_safe(v) for k, v in dict(row).items()}

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

    return snapshot, None


# ---------------------------------------------------------------------------
# Who shares a rate counter, and who gets one of their own
# ---------------------------------------------------------------------------


def rate_limit_namespace() -> str:
    """What separates one caller's rate counter from another's, "" in production.

    D1/P1b: the rate counter is SHARED STATE, and the eval drives this same
    dispatcher. Keyed only on (agent, skill, window) an overnight eval with six
    refund-shaped scenarios exhausts an envelope that allows five refunds an
    hour, and the next REAL customer refund in that window comes back "Request
    denied by rate or constraint check (reason: rate_limit)" — silent from the
    eval's side, and reading as an ordinary envelope denial from the customer's
    side.

    Namespacing rather than suppressing: the eval still measures the ceiling, on
    its own counter. Suppressing the INCR would make "the agent kept refunding
    past its limit" unfalsifiable, which is the same mistake as handing the eval
    a read-only tool subset. Rolling the INCR back is not an option either — the
    pipeline is not transactional against a concurrent real caller.

    Ticket 15 (#52) is that same argument one level down. `attempt_scope`, in
    agent_tools, gives each of a vector's k red-team attempts its own counter,
    so attempt 1's chain of refunds cannot leave attempts 2 and 3 denied at their
    first call instead of at the ceiling they exist to cross. The block comment
    over `_attempt_scope_var` carries the rest of it.

    Returns:
        "" for a customer turn, which keys exactly as it did before either
        namespace existed. "recorded:" under the eval's side-effect mode, an
        "attempt:<vector>:<n>:" fragment inside a red-team attempt, and the two
        together when a red-team attempt drives a recorded victim turn.
    """
    # Lazy import: agent_tools imports transactional.tools (which imports this
    # module) inside agent_tool_definitions, so a module-level import here would
    # close that loop.
    from app.services.agent_tools import (  # noqa: PLC0415
        _attempt_scope_var,
        _side_effects_var,
    )

    mode_prefix = "recorded:" if _side_effects_var.get() == "recorded" else ""
    return f"{mode_prefix}{_attempt_scope_var.get()}"


# ---------------------------------------------------------------------------
# Split: apply_rate_and_constraint_checks — side-effecting checks
# ---------------------------------------------------------------------------


async def apply_rate_and_constraint_checks(
    agent_id: Any,
    skill: str,
    snapshot: dict,
    args: Any,
) -> str | None:
    """Side-effecting rate-limit and constraint gate (14-07 split).

    Executes the rate-limit Redis INCR+EXPIRE pipeline and the max_amount_cents
    constraint check against the supplied snapshot and args.

    IN-01: INCR and EXPIRE are issued atomically in a single Redis pipeline so
    the key can never exist without a TTL (no key leak on process death between
    the two commands).

    IN-02: amount_cents=0 is a real amount; explicit None-check prevents the
    falsy `or` fallthrough to refund_amount_cents.

    WR-03: The blocking Redis pipeline call is offloaded to asyncio.to_thread.

    Args:
        agent_id: UUID of the calling agent.
        skill: Name of the tool/skill being invoked.
        snapshot: JSON-safe dict snapshot from check_capability_access.
        args: Validated Pydantic input model. Fields amount_cents and
              refund_amount_cents are read for max_amount_cents enforcement.

    Returns:
        denial_reason string on denial; None on full pass.
    """
    agent_id_str = str(agent_id)

    # ------------------------------------------------------------------
    # 4. Rate-limit check (Redis INCR+EXPIRE via pipeline, offloaded — WR-03, IN-01)
    # ------------------------------------------------------------------
    rate_limit_str: str | None = snapshot.get("rate_limit")
    parsed = _parse_rate_limit(rate_limit_str)
    if parsed is not None:
        max_calls, window_secs = parsed
        window_key = int(time.time()) // window_secs
        redis_key = f"ratelimit:{rate_limit_namespace()}{agent_id_str}:{skill}:{window_key}"

        def _do_rate_limit_pipeline() -> tuple[int, Any]:
            client = _get_redis()
            pipe = client.pipeline()
            pipe.incr(redis_key)
            pipe.expire(redis_key, window_secs + 1)
            count, expire_result = pipe.execute()  # [count, expire_result]
            return (int(count), expire_result)

        results = await asyncio.to_thread(_do_rate_limit_pipeline)
        count = results[0]

        if count > max_calls:
            log.warning(
                "capability.denial",
                agent_id=agent_id_str,
                skill=skill,
                reason="rate_limit",
                count=count,
                max_calls=max_calls,
            )
            return "rate_limit"

    # ------------------------------------------------------------------
    # 5. Constraint check: max_amount_cents (IN-02: explicit None-check)
    # ------------------------------------------------------------------
    constraints: dict = snapshot.get("constraints") or {}
    max_amount_cents = constraints.get("max_amount_cents")
    if max_amount_cents is not None:
        # IN-02: Use explicit None-check so amount_cents=0 is treated as a real
        # amount (not as "missing"), preventing fallthrough to refund_amount_cents.
        # The old `or`-based selection treated 0 as falsy and wrongly consulted
        # refund_amount_cents instead.
        amount = getattr(args, "amount_cents", None)
        if amount is None:
            amount = getattr(args, "refund_amount_cents", None)
        if amount is not None and amount > max_amount_cents:
            log.warning(
                "capability.denial",
                agent_id=agent_id_str,
                skill=skill,
                reason="max_amount_cents",
                amount=amount,
                limit=max_amount_cents,
            )
            return "max_amount_cents"

    # ------------------------------------------------------------------
    # 6. scope_filters — Phase-16 no-op (documented, not enforced yet)
    # ------------------------------------------------------------------
    # scope_filters in constraints are a Phase-16 feature. The field is
    # accepted and stored but not yet validated. Phase 16 will add allowlist
    # enforcement here.

    return None


# ---------------------------------------------------------------------------
# Facade: check_capability_envelope — retained for backward compatibility
# ---------------------------------------------------------------------------


async def check_capability_envelope(
    agent_id: Any,
    skill: str,
    args: Any,
) -> tuple[dict, str | None]:
    """Fail-closed capability envelope check — RETAINED FACADE (14-07).

    Calls check_capability_access first (side-effect-free: exists + enabled).
    If denied, returns immediately without touching Redis.
    Otherwise calls apply_rate_and_constraint_checks (rate limit + constraints).

    Same return contract as the pre-14-07 monolithic function:
      - (snapshot_dict, None)           — all checks passed
      - ({} or snapshot_dict, reason)   — denied; reason is a short string

    The 14-04 dispatcher (tools.py) calls this function; it is not modified
    until the 14-08 reorder plan. This facade keeps every current test green
    during the transition.

    Args:
        agent_id: UUID of the calling agent (any uuid-like; serialised to str for the query).
        skill: Name of the tool/skill being invoked.
        args: Validated Pydantic input model. Fields amount_cents and
              refund_amount_cents are read for max_amount_cents enforcement.

    Returns:
        (snapshot_dict, denial_reason) — denial_reason is None on pass-through.

    Security:
        - Missing or disabled envelope → fail-closed (denial, never pass).
        - snapshot is always scoped to the calling agent_id only.
        - Redis is used ONLY for the rate-limit counter, never for idempotency.
    """
    snapshot, denial = await check_capability_access(agent_id, skill)
    if denial is not None:
        return snapshot, denial
    denial = await apply_rate_and_constraint_checks(agent_id, skill, snapshot, args)
    return snapshot, denial
