"""
Pydantic schemas for CAP-03's per-skill capability-envelope routes
(``apps/api/app/api/v1/capability_envelopes.py``).

CapabilityEnvelopeUpdate    — PATCH body, shape-only validation
CapabilityEnvelopeResponse  — one skill's envelope state
CapabilityEnvelopeListResponse — the GET list-response envelope
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from app.services.capability_service import ACTOR_MODE_RE
from app.services.transactional.enforcement import _parse_rate_limit


class CapabilityEnvelopeUpdate(BaseModel):
    """Partial-update PATCH body for /agents/{agent_id}/capability-envelopes/{skill}.

    Every field is Optional with a None default — an absent field means "no
    change", consumed by the route handler via model_dump(exclude_unset=True).

    These validators check SHAPE ONLY. The tighten-only DIRECTION check is
    deliberately not here (OD-3): a Pydantic validator sees only the proposed
    value in isolation and cannot see the current DB row that "tighter" is
    relative to. Adding a direction check here would silently permit loosening
    whenever the current row is out of scope — that check lives in
    capability_service.validate_tighten_only, called from the route below
    every write.

    extra="forbid" — a typo'd or unknown field is a 422, not a silently
    ignored no-op. On an authorization surface, a silently dropped field is
    worse than a rejection.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    rate_limit: str | None = None
    constraints: dict | None = None
    requires_confirmation: bool | None = None
    requires_identity_verification: bool | None = None
    actor_mode: str | None = None

    @field_validator("rate_limit")
    @classmethod
    def validate_rate_limit_shape(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if _parse_rate_limit(v) is None:
            raise ValueError(f"invalid rate_limit shape: {v!r} (expected 'N/<minute|hour|day>')")
        return v

    @field_validator("constraints")
    @classmethod
    def validate_constraints_shape(cls, v: dict | None) -> dict | None:
        if v is None:
            return None
        if "max_amount_cents" in v:
            max_amount_cents = v["max_amount_cents"]
            if max_amount_cents is not None and (
                not isinstance(max_amount_cents, int) or isinstance(max_amount_cents, bool) or max_amount_cents < 0
            ):
                raise ValueError(
                    f"constraints.max_amount_cents must be null or a non-negative int, got {max_amount_cents!r}"
                )
        return v

    @field_validator("actor_mode")
    @classmethod
    def validate_actor_mode_domain(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v in {"always-on", "off"}:
            return v
        if ACTOR_MODE_RE.match(v):
            return v
        raise ValueError(f"invalid actor_mode: {v!r} (expected 'always-on', 'off', or 'sample_at_rate_N')")


class CapabilityEnvelopeResponse(BaseModel):
    """One skill's capability-envelope state as the UI needs it.

    platform_default and mutating are read-only helper fields — the UI-SPEC's
    D1/D2 controls need them to render the tighten-only ceilings without a
    second request. platform_default is the PLATFORM_CAPABILITY_DEFAULTS entry
    for this skill; mutating (True for the six mutating skills, False only for
    confirm_action) is what lets the UI compute which control positions are
    unreachable without duplicating the platform-defaults table in TypeScript.
    """

    skill: str
    enabled: bool
    rate_limit: str | None
    constraints: dict
    requires_confirmation: bool
    requires_identity_verification: bool
    actor_mode: str
    updated_at: datetime | None
    platform_default: dict
    mutating: bool


class CapabilityEnvelopeListResponse(BaseModel):
    """Response body for GET /agents/{agent_id}/capability-envelopes."""

    envelopes: list[CapabilityEnvelopeResponse]
