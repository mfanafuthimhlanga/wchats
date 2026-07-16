"""
prompt_version_service — OPS-16 non-destructive, canary-able soul editing.

Object model (DOMAIN-NOTES §2): prompt -> version -> label
(production/canary/draft/archived). Deploy/rollback moves a label; the four
soul fields (soul_role/soul_voice/soul_do_list/soul_donot_list) on an existing
row are NEVER mutated after INSERT (must_haves prohibition: "history is never
overwritten" / T-21-09-02).

Async functions (create_version_from_agent, diff_versions, set_canary,
rollback, list_versions) run on the async control-DB session (get_async_db) —
used by the FastAPI routes (app/api/v1/prompt_versions.py) and by patch_agent
(app/api/v1/agents.py).

resolve_prompt_version is SYNC — it runs inside run_agent_turn (Celery task,
sync control-DB session via get_sync_db), at turn dispatch, immediately before
build_system_prompt(). T-21-09-01: it filters label IN ('production',
'canary') only, so a 'draft'/unapproved version can never be selected for
production traffic. The caller (agent.py) wraps every call to this function
in its own try/except per the must_haves prohibition ("resolve wrapped so it
never fails the turn") — resolve_prompt_version itself performs a single
indexed SELECT with no side effects.
"""

from __future__ import annotations

import random
from uuid import UUID

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.prompt_version import PromptVersion

log = structlog.get_logger(__name__)

SOUL_FIELDS = ("soul_role", "soul_voice", "soul_do_list", "soul_donot_list")


class PromptVersionNotFoundError(Exception):
    """Raised when a version_id does not exist or does not belong to agent_id (IDOR)."""


def _soul_snapshot(row) -> dict:
    """Extract the 4 soul fields from an Agent or PromptVersion row."""
    return {field: getattr(row, field) for field in SOUL_FIELDS}


# ---------------------------------------------------------------------------
# Async — FastAPI routes (prompt_versions.py) + patch_agent (agents.py)
# ---------------------------------------------------------------------------


async def create_version_from_agent(db: AsyncSession, agent: Agent) -> PromptVersion:
    """Append an immutable prompt_versions row snapshotting agent's current soul.

    Called by patch_agent AFTER the soul fields are applied to the live `agent`
    ORM object (but this function only ever INSERTs a brand-new row — no prior
    row's soul fields are ever touched). The new row becomes the 'production'
    pointer: any row previously labeled 'production' for this agent_id is
    relabeled 'archived' first (a label move only, history-preserving).

    Returns the newly created PromptVersion (flushed, not committed — caller
    owns the transaction/commit, matching patch_agent's existing single
    db.commit() at the end of the route).
    """
    result = await db.execute(
        select(func.max(PromptVersion.version_number)).where(
            PromptVersion.agent_id == agent.id
        )
    )
    next_version = (result.scalar() or 0) + 1

    await db.execute(
        update(PromptVersion)
        .where(PromptVersion.agent_id == agent.id, PromptVersion.label == "production")
        .values(label="archived")
    )

    version = PromptVersion(
        agent_id=agent.id,
        version_number=next_version,
        label="production",
        canary_percent=0,
        **_soul_snapshot(agent),
    )
    db.add(version)
    await db.flush()
    await db.refresh(version)

    log.info(
        "prompt_version.created",
        agent_id=str(agent.id),
        version_id=str(version.id),
        version_number=next_version,
    )
    return version


async def list_versions(db: AsyncSession, agent_id: UUID) -> list[PromptVersion]:
    """Return all versions for an agent, newest first."""
    result = await db.execute(
        select(PromptVersion)
        .where(PromptVersion.agent_id == agent_id)
        .order_by(PromptVersion.version_number.desc())
    )
    return list(result.scalars().all())


async def diff_versions(
    db: AsyncSession, agent_id: UUID, version_a: UUID, version_b: UUID
) -> dict:
    """Field-by-field comparison of the 4 soul fields between two versions.

    Don't-hand-roll (RESEARCH.md): this is a 10-line dict comparison, not a
    generic text-diff — the "prompt" here is 4 structured fields.

    IDOR: both versions must belong to agent_id or PromptVersionNotFoundError
    is raised (mapped to 404 at the route layer).
    """
    a = await _get_owned_version(db, agent_id, version_a)
    b = await _get_owned_version(db, agent_id, version_b)

    fields = {}
    for field in SOUL_FIELDS:
        val_a = getattr(a, field)
        val_b = getattr(b, field)
        fields[field] = {"a": val_a, "b": val_b, "changed": val_a != val_b}

    return {
        "version_a": {"id": str(a.id), "version_number": a.version_number},
        "version_b": {"id": str(b.id), "version_number": b.version_number},
        "fields": fields,
    }


async def set_canary(
    db: AsyncSession, agent_id: UUID, version_id: UUID, percent: int
) -> PromptVersion:
    """Set label='canary' + canary_percent on version_id; demotes any other canary.

    IDOR: version_id must belong to agent_id or PromptVersionNotFoundError.
    Percent bounds (0-100) are enforced by the Pydantic request schema at the
    route layer (422 on out-of-range) — this function trusts the caller.

    At most one active canary per agent — a stale canary from a previous
    experiment is relabeled 'archived' (label move only, soul fields intact).
    """
    version = await _get_owned_version(db, agent_id, version_id)

    await db.execute(
        update(PromptVersion)
        .where(
            PromptVersion.agent_id == agent_id,
            PromptVersion.label == "canary",
            PromptVersion.id != version_id,
        )
        .values(label="archived", canary_percent=0)
    )

    version.label = "canary"
    version.canary_percent = percent
    await db.flush()
    await db.refresh(version)

    log.info(
        "prompt_version.canary_set",
        agent_id=str(agent_id),
        version_id=str(version_id),
        percent=percent,
    )
    return version


async def rollback(db: AsyncSession, agent_id: UUID, version_id: UUID) -> PromptVersion:
    """Restore a prior version WITHOUT deleting or mutating any history row.

    T-21-09-02: appends a brand-new version (version_number = max+1) copying
    version_id's soul fields, labeled 'production' — the target row itself is
    never edited. Any current 'production' row is relabeled 'archived' (label
    move, not a soul-field mutation). Also updates the live `agents` row's
    soul_* columns to match, so GET /agents/{id} / the soul editor reflect the
    rollback immediately — the same effect a fresh patch_agent edit would have.

    IDOR: version_id must belong to agent_id or PromptVersionNotFoundError.
    """
    target = await _get_owned_version(db, agent_id, version_id)

    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise PromptVersionNotFoundError(f"agent {agent_id} not found")

    for field in SOUL_FIELDS:
        setattr(agent, field, getattr(target, field))

    result = await db.execute(
        select(func.max(PromptVersion.version_number)).where(
            PromptVersion.agent_id == agent_id
        )
    )
    next_version = (result.scalar() or 0) + 1

    await db.execute(
        update(PromptVersion)
        .where(PromptVersion.agent_id == agent_id, PromptVersion.label == "production")
        .values(label="archived")
    )

    new_version = PromptVersion(
        agent_id=agent_id,
        version_number=next_version,
        label="production",
        canary_percent=0,
        **_soul_snapshot(target),
    )
    db.add(new_version)
    await db.flush()
    await db.refresh(new_version)

    log.info(
        "prompt_version.rolled_back",
        agent_id=str(agent_id),
        restored_from_version_id=str(version_id),
        new_version_id=str(new_version.id),
    )
    return new_version


async def _get_owned_version(
    db: AsyncSession, agent_id: UUID, version_id: UUID
) -> PromptVersion:
    """IDOR guard: fetch a version and verify it belongs to agent_id."""
    version = await db.get(PromptVersion, version_id)
    if version is None or version.agent_id != agent_id:
        raise PromptVersionNotFoundError(
            f"version {version_id} not found for agent {agent_id}"
        )
    return version


# ---------------------------------------------------------------------------
# Sync — run_agent_turn (Celery task, sync control-DB session via get_sync_db)
# ---------------------------------------------------------------------------


def resolve_prompt_version(db: Session, agent_id: str) -> tuple[str | None, dict | None]:
    """Weighted-random pick between the production and canary versions of agent_id.

    T-21-09-01: filters label IN ('production', 'canary') only — a 'draft' or
    'archived' row is never a candidate, so canary routing can never serve an
    unapproved persona to production traffic.

    Weighting: if a canary row exists with canary_percent > 0, that share of
    calls resolve to the canary version; the remainder resolve to production.
    If only one of the two exists, it is always chosen.

    Returns:
        (prompt_version_id, soul_override) as strings/dicts — both None if the
        agent has no production/canary prompt_versions rows yet. The caller
        (agent.py) treats (None, None) as "fall back to the agent's live
        soul_* columns, unchanged" (T-21-09-05: never fails a turn).
    """
    rows = (
        db.execute(
            select(PromptVersion).where(
                PromptVersion.agent_id == agent_id,
                PromptVersion.label.in_(("production", "canary")),
            )
        )
        .scalars()
        .all()
    )

    production = next((v for v in rows if v.label == "production"), None)
    canary = next((v for v in rows if v.label == "canary"), None)

    if production is None and canary is None:
        return None, None

    chosen = production
    if canary is not None and canary.canary_percent > 0:
        if random.random() * 100 < canary.canary_percent:
            chosen = canary
    if chosen is None:
        chosen = canary

    if chosen is None:
        return None, None

    return str(chosen.id), _soul_snapshot(chosen)
