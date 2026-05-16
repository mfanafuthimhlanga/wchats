"""
Pydantic v2 schemas for widget endpoints (M4).

WidgetConfigResponse  — response for GET /widget/{id}/config
WidgetChatRequest     — body for POST /widget/{id}/chat (mirrors AgentChatRequest)
WidgetChatResponse    — response for POST /widget/{id}/chat (mirrors AgentChatResponse)
"""

from uuid import UUID

from pydantic import BaseModel, Field


class WidgetConfigResponse(BaseModel):
    """Response for GET /widget/{agent_id}/config — consumed by the embedded widget."""

    agent_id: UUID
    name: str
    theming: dict
    jwt: str


class WidgetChatRequest(BaseModel):
    """Request body for POST /widget/{agent_id}/chat — mirrors AgentChatRequest."""

    message: str = Field(..., min_length=1, max_length=2000)
    conversation_id: UUID | None = None


class WidgetChatResponse(BaseModel):
    """202 Accepted response for POST /widget/{agent_id}/chat — mirrors AgentChatResponse."""

    job_id: UUID
    status: str = "pending"
    events_url: str
    conversation_id: UUID | None = None
