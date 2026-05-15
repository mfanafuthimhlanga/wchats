"""
Pydantic v2 schemas for query endpoints (M3).

QueryRequest       — body for POST /agents/{id}/query
QueryJobResponse   — response for POST /agents/{id}/query (202 Accepted)
QueryJobItem       — single item in GET /agents/{id}/queries list
QueryListResponse  — response for GET /agents/{id}/queries
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class QueryRequest(BaseModel):
    query: str = Field(min_length=1)
    filters: list[dict] = []  # M4+ use; accepted but not applied in M3


class QueryJobResponse(BaseModel):
    job_id: UUID
    status: str
    events_url: str


class QueryJobItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: UUID
    status: str
    kind: str
    created_at: datetime
    finished_at: datetime | None = None


class QueryListResponse(BaseModel):
    jobs: list[QueryJobItem]
