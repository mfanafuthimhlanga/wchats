"""Capability-envelope routes for W Chats Phase 18 (CAP-03).

Routes:
    GET   /agents/{agent_id}/capability-envelopes           — read every platform skill's state
    PATCH /agents/{agent_id}/capability-envelopes/{skill}    — tighten-only partial update

Security:
    Both routes require X-API-Key or Bearer auth via get_current_tenant.
    IDOR is prevented by verifying agent.tenant_id == tenant.id on every
    route, 404 (not 403) on both the missing-agent and the foreign-agent
    branch — the house convention copied verbatim from prompt_versions.py's
    _get_owned_agent, so a foreign agent is indistinguishable from a missing
    one.

    The tighten-only comparator (capability_service.validate_tighten_only) is
    the only write gate. CAP-02's check_capability_access (enforcement.py)
    reads the live envelope row at call time with no caching layer, so this
    route is the single place a looser row must be stopped from being
    written — plan 18-10's admin UI makes loosening physically unexpressible
    as a usability affordance, but that is not the control. A caller that
    never loaded the UI is rejected identically here (T-18-CAP-02).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_tenant
from app.core.database import get_async_db
from app.models.agent import Agent
from app.models.capability_envelope import CapabilityEnvelope
from app.models.tenant import Tenant
from app.schemas.capability import (
    CapabilityEnvelopeListResponse,
    CapabilityEnvelopeResponse,
    CapabilityEnvelopeUpdate,
)
from app.services.capability_service import PLATFORM_CAPABILITY_DEFAULTS, validate_tighten_only

router = APIRouter(tags=["capability-envelopes"])
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Shared IDOR guard (copied from prompt_versions.py's _get_owned_agent)
# ---------------------------------------------------------------------------


async def _get_owned_agent(agent_id: UUID, db: AsyncSession, tenant: Tenant) -> Agent:
    """Fetch agent and enforce IDOR (404, not 403, on mismatch — no existence leak)."""
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


def _envelope_to_dict(row: CapabilityEnvelope | dict, platform_default: dict) -> dict:
    """Serialise a CapabilityEnvelope ORM row (or a synthesised dict, for a
    skill with no stored row) into the CapabilityEnvelopeResponse shape.

    mutating defaults to True for an unknown skill — a skill the platform
    defaults table does not describe must be treated as mutating, the
    conservative direction.
    """
    if isinstance(row, dict):
        get = row.get
    else:
        get = lambda field, default=None: getattr(row, field, default)  # noqa: E731

    return {
        "skill": get("skill"),
        "enabled": get("enabled", False),
        "rate_limit": get("rate_limit"),
        "constraints": get("constraints") or {},
        "requires_confirmation": get("requires_confirmation", False),
        "requires_identity_verification": get("requires_identity_verification", False),
        "actor_mode": get("actor_mode", "always-on"),
        "updated_at": get("updated_at"),
        "platform_default": platform_default,
        "mutating": platform_default.get("mutating", True),
    }


# ---------------------------------------------------------------------------
# Route 1: GET /agents/{agent_id}/capability-envelopes — read every skill
# ---------------------------------------------------------------------------


@router.get("/agents/{agent_id}/capability-envelopes")
async def list_capability_envelopes(
    agent_id: UUID,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> CapabilityEnvelopeListResponse:
    """Return one entry per PLATFORM_CAPABILITY_DEFAULTS skill (7 entries).

    A skill with no stored row is synthesised from the platform default with
    enabled=False and updated_at=None — a stable, complete response shape so
    the UI's zero-envelope-rows empty state has a definite server contract
    instead of being invented at render time (UI-SPEC § UI Considerations).
    """
    await _get_owned_agent(agent_id, db, tenant)

    result = await db.execute(
        select(CapabilityEnvelope)
        .where(CapabilityEnvelope.agent_id == agent_id)
        .order_by(CapabilityEnvelope.skill)
    )
    rows_by_skill: dict[str, CapabilityEnvelope] = {row.skill: row for row in result.scalars().all()}

    envelopes = []
    for skill, platform_default in PLATFORM_CAPABILITY_DEFAULTS.items():
        row = rows_by_skill.get(skill)
        if row is not None:
            envelopes.append(_envelope_to_dict(row, platform_default))
        else:
            envelopes.append(
                _envelope_to_dict(
                    {
                        "skill": skill,
                        "enabled": False,
                        "rate_limit": platform_default.get("rate_limit"),
                        "constraints": platform_default.get("constraints") or {},
                        "requires_confirmation": platform_default.get("requires_confirmation", False),
                        "requires_identity_verification": platform_default.get(
                            "requires_identity_verification", False
                        ),
                        "actor_mode": platform_default.get("actor_mode", "always-on"),
                        "updated_at": None,
                    },
                    platform_default,
                )
            )

    log.info(
        "list_capability_envelopes.ok",
        agent_id=str(agent_id),
        tenant_id=str(tenant.id),
        row_count=len(rows_by_skill),
    )
    return CapabilityEnvelopeListResponse(
        envelopes=[CapabilityEnvelopeResponse(**e) for e in envelopes]
    )


# ---------------------------------------------------------------------------
# Route 2: PATCH /agents/{agent_id}/capability-envelopes/{skill} — write gate
# ---------------------------------------------------------------------------


@router.patch("/agents/{agent_id}/capability-envelopes/{skill}")
async def patch_capability_envelope(
    agent_id: UUID,
    skill: str,
    body: CapabilityEnvelopeUpdate,
    db: AsyncSession = Depends(get_async_db),
    tenant: Tenant = Depends(get_current_tenant),
) -> CapabilityEnvelopeResponse:
    """Tighten-only PATCH. Order of operations matters:

    1. IDOR first — before anything else touches the skill or the body.
    2. Reject an unknown skill — the seven PLATFORM_CAPABILITY_DEFAULTS
       entries are the closed set.
    3. Load the existing row; when absent, the platform default for this
       skill IS the current state for comparison, so a first write is
       compared against the platform ceiling rather than against nothing
       ("never loosen beyond platform defaults").
    4. An empty proposed body is a 200 no-op — it must not touch updated_at.
    5. validate_tighten_only runs BEFORE any ORM mutation. A non-None reason
       is a 422 and the function returns immediately — no db.add, no
       attribute assignment, no db.commit. Plan 18-10's UI makes loosening
       unexpressible as an affordance; this check is the actual control for
       a caller that never loaded the UI (UI-SPEC D1's server-side backstop
       framing).
    6. Only on None does the row get created/updated, updated_at stamped,
       and the transaction committed.
    """
    await _get_owned_agent(agent_id, db, tenant)

    platform_default = PLATFORM_CAPABILITY_DEFAULTS.get(skill)
    if platform_default is None:
        raise HTTPException(status_code=404, detail="Skill not found")

    result = await db.execute(
        select(CapabilityEnvelope).where(
            CapabilityEnvelope.agent_id == agent_id,
            CapabilityEnvelope.skill == skill,
        )
    )
    row = result.scalar_one_or_none()

    if row is not None:
        current = {
            "skill": row.skill,
            "enabled": row.enabled,
            "rate_limit": row.rate_limit,
            "constraints": row.constraints or {},
            "requires_confirmation": row.requires_confirmation,
            "requires_identity_verification": row.requires_identity_verification,
            "actor_mode": row.actor_mode,
        }
    else:
        current = {
            "skill": skill,
            **{
                k: (dict(v) if isinstance(v, dict) else v)
                for k, v in platform_default.items()
                if k != "mutating"
            },
        }

    proposed = body.model_dump(exclude_unset=True)

    if not proposed:
        current_entry = row if row is not None else current
        return CapabilityEnvelopeResponse(**_envelope_to_dict(current_entry, platform_default))

    # The comparator call must precede every mutation below: a 422 path must
    # leave the transaction untouched, and a route that wrote the row and
    # then raised would still return 422 while silently persisting the
    # loosened row — that is the exact bug this ordering forecloses.
    reason = validate_tighten_only(current=current, proposed=proposed, platform_defaults=PLATFORM_CAPABILITY_DEFAULTS)
    if reason is not None:
        raise HTTPException(status_code=422, detail=f"Capability envelope change rejected: {reason}")

    if row is None:
        row = CapabilityEnvelope(
            agent_id=agent_id,
            skill=skill,
            enabled=current["enabled"],
            rate_limit=current["rate_limit"],
            constraints=current["constraints"],
            requires_confirmation=current["requires_confirmation"],
            requires_identity_verification=current["requires_identity_verification"],
            actor_mode=current["actor_mode"],
        )
        db.add(row)

    for field, value in proposed.items():
        setattr(row, field, value)
    row.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(row)

    log.info(
        "patch_capability_envelope.ok",
        agent_id=str(agent_id),
        tenant_id=str(tenant.id),
        skill=skill,
        fields=sorted(proposed.keys()),
    )
    return CapabilityEnvelopeResponse(**_envelope_to_dict(row, platform_default))
