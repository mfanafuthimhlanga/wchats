"""
Pydantic v2 schemas for widget endpoints (M4).

WidgetConfigResponse  — response for GET /widget/{id}/config
WidgetChatRequest     — body for POST /widget/{id}/chat (mirrors AgentChatRequest)
WidgetChatResponse    — response for POST /widget/{id}/chat (mirrors AgentChatResponse)
OtpRequestBody        — body for POST /widget/{id}/identity/request (Phase 17)
OtpVerifyBody         — body for POST /widget/{id}/identity/verify (Phase 17)
OtpVerifyResponse     — response for POST /widget/{id}/identity/verify (Phase 17)
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
    verified_session_token: str | None = None  # IDV-05: forwarded to run_agent_turn as 5th arg


class WidgetChatResponse(BaseModel):
    """202 Accepted response for POST /widget/{agent_id}/chat — mirrors AgentChatResponse."""

    job_id: UUID
    status: str = "pending"
    events_url: str
    conversation_id: UUID | None = None


# ---------------------------------------------------------------------------
# Phase 17 — OTP identity verification schemas (IDV-02, IDV-03, IDV-05)
# ---------------------------------------------------------------------------


class OtpRequestBody(BaseModel):
    """Body for POST /widget/{agent_id}/identity/request.

    ASVS V5: external_id is bounded at 320 chars (max email length per RFC 5321).
    method must be "email" or "sms" (regex validation).
    """

    external_id: str = Field(..., min_length=1, max_length=320)
    method: str = Field(..., pattern=r"^(email|sms)$")


class OtpVerifyBody(BaseModel):
    """Body for POST /widget/{agent_id}/identity/verify.

    ASVS V5: otp_code is exactly 6 numeric digits (pattern=r"^\\d{6}$") — prevents
    injection via non-digit characters and enforces the 6-digit challenge length.
    method must be "email" or "sms" (regex validation).
    """

    external_id: str = Field(..., min_length=1, max_length=320)
    otp_code: str = Field(..., pattern=r"^\d{6}$")  # ASVS V5 — numeric 6-digit only
    method: str = Field(..., pattern=r"^(email|sms)$")


class OtpVerifyResponse(BaseModel):
    """200 OK response for POST /widget/{agent_id}/identity/verify.

    verified_session_token is returned ONCE to the client after correct OTP verification.
    The token is never logged (T-17-11). The raw token is never stored — only its
    SHA-256 hash is persisted in the tenant DB (T-17-08).
    """

    verified_session_token: str
