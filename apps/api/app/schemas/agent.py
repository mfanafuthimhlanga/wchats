"""
Pydantic v2 schemas for agent endpoints.

SoulSchema         — nested soul object (voice, do, do_not lists)
AgentCreate        — request body for POST /agents
AgentResponse      — response body for GET /agents/{id}
AgentCreateResponse — response body for POST /agents (202 Accepted)
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


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
