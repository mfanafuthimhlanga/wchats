"""
Pydantic v2 schemas for agent endpoints.

SoulSchema         — nested soul object (voice, do, do_not lists)
AgentCreate        — request body for POST /agents
AgentResponse      — response body for GET /agents/{id}
AgentCreateResponse — response body for POST /agents (202 Accepted)
AgentSoulUpdate    — request body for PATCH /agents/{id} (partial soul update)
AgentDetailResponse — response body for PATCH /agents/{id}
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SoulSchema(BaseModel):
    voice: str
    do: list[str]
    do_not: list[str]


class AgentCreate(BaseModel):
    name: str
    soul: SoulSchema
    role: Literal["support", "sales", "helpdesk"]


class AgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    role: str
    status: str
    neon_project_id: str | None
    schema_version: str | None
    created_at: datetime


class AgentCreateResponse(BaseModel):
    agent_id: UUID
    job_id: UUID
    status: str
    events_url: str


class AgentSoulUpdate(BaseModel):
    """Partial update schema for PATCH /agents/{id}.

    All fields are optional — only fields present in the body are updated
    (use model_dump(exclude_unset=True) in the route handler).

    Threat mitigation T-04-06-01: Pydantic enforces size constraints server-side.
    """

    name: str | None = Field(None, min_length=1, max_length=60)
    soul_role: str | None = Field(None, max_length=120)
    soul_voice: str | None = Field(None, max_length=500)
    soul_do_list: list[Annotated[str, Field(min_length=1, max_length=200)]] | None = None
    soul_donot_list: list[Annotated[str, Field(min_length=1, max_length=200)]] | None = None


class AgentDetailResponse(BaseModel):
    """Response body for GET /agents/{id} and PATCH /agents/{id}."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    soul_role: str | None = None
    soul_voice: str | None = None
    soul_do_list: list = []
    soul_donot_list: list = []
    status: str
    created_at: datetime
