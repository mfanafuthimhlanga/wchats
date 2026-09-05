"""
Pydantic schemas for ACT-07's pending-confirmation queue and resolve routes
(``apps/api/app/api/v1/pending_confirmations.py``).

PendingConfirmationResolve      — the resolve POST body, extra=forbid, one field
PendingConfirmationResponse     — one queue row, shared by the GET and resolve responses
PendingConfirmationListResponse — the GET list-response envelope
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PendingConfirmationResolve(BaseModel):
    """POST body for /agents/{agent_id}/pending-confirmations/{confirmation_id}/resolve.

    Exactly one field: the approver's terminal decision. "expired" is
    deliberately not in the literal — expiry is a fact the database
    establishes inside the atomic claim (OD-2), never something a caller may
    assert.

    model_config = ConfigDict(extra="forbid") mirrors CapabilityEnvelopeUpdate's
    own reasoning (schemas/capability.py): on an authorization surface, a
    silently dropped field is worse than a rejection. This body carries the
    sharper reason that same posture exists here: the resolve request carries
    NO action payload of any kind, so an approver cannot alter, extend, or
    re-specify the action they are approving — the executed arguments are
    always exactly the arguments already stored on the confirmation row.
    extra="forbid" is what turns an attempt to smuggle one in into a 422
    rather than a silently ignored field.
    """

    model_config = ConfigDict(extra="forbid")

    resolution: Literal["approved", "rejected"]


class PendingConfirmationResponse(BaseModel):
    """One pending_confirmations row.

    execution_outcome / execution_error / executed_at are populated only when
    resolution == "approved", via a read-time lookup against
    tool_calls_audit (OD-3, no 0020 migration) — always None on every other
    row, and honestly None on an approved row whose resolver-driven audit row
    does not (yet, or ever) exist.

    execution_outcome carries three states, not two. "not_executed" means a
    gate refused the call and the owner can change that decision; "failed"
    means the adapter ran and broke, and someone has to fix the provider.
    The resolver has told those apart since #73; this is where the owner sees
    it.
    """

    id: UUID
    skill: str
    arguments: dict | None
    requested_at: datetime
    expires_at: datetime | None
    resolved_at: datetime | None
    resolution: str | None
    execution_outcome: Literal["executed", "not_executed", "failed"] | None
    execution_error: str | None
    executed_at: datetime | None


class PendingConfirmationListResponse(BaseModel):
    """Response body for GET /agents/{agent_id}/pending-confirmations."""

    confirmations: list[PendingConfirmationResponse]
