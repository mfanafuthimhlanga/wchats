"""
Pydantic v2 schemas for job endpoints.

JobEventResponse — nested event within a job response
JobResponse      — response body for GET /jobs/{job_id}
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class JobEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    payload: dict | None
    created_at: datetime


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID | None
    kind: str
    status: str
    error: str | None
    created_at: datetime
    events: list[JobEventResponse] = []
