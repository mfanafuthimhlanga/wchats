"""
Pydantic v2 schemas for agent chat endpoints (M4).

AgentChatRequest       — body for POST /agents/{id}/chat
AgentChatResponse      — response for POST /agents/{id}/chat (202 Accepted)
ConversationListItem   — single item in GET /agents/{id}/conversations list
ConversationListResponse — response for GET /agents/{id}/conversations
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentChatRequest(BaseModel):
    """Request body for POST /agents/{agent_id}/chat."""

    message: str = Field(..., min_length=1, max_length=2000)
    conversation_id: UUID | None = None


class AgentChatResponse(BaseModel):
    """202 Accepted response for POST /agents/{agent_id}/chat."""

    job_id: UUID
    status: str = "pending"
    events_url: str
    conversation_id: UUID | None = None


class ConversationListItem(BaseModel):
    """Single conversation record in the list endpoint."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    escalated: bool
    message_count: int


class ConversationListResponse(BaseModel):
    """Response for GET /agents/{agent_id}/conversations."""

    conversations: list[ConversationListItem]
