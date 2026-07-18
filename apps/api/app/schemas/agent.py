"""
Pydantic v2 schemas for agent endpoints.

SoulSchema         — nested soul object (voice, do, do_not lists)
AgentCreate        — request body for POST /agents
AgentResponse      — response body for GET /agents/{id}
AgentCreateResponse — response body for POST /agents (202 Accepted)
AgentSoulUpdate    — request body for PATCH /agents/{id} (partial soul update)
AgentDetailResponse — response body for PATCH /agents/{id}
AgentListResponse  — response body for GET /agents (list all tenant agents)
WidgetColorsSchema — nested colors block for widget customization (validates hex)
WidgetTypographySchema — nested typography block for widget customization
WidgetConfigUpdate — request body for POST /agents/{id}/widget-config
"""

import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.sanitize import sanitize_chunk_text


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
    soul_role: str | None = None
    soul_voice: str | None = None
    soul_do_list: list = []
    soul_donot_list: list = []
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

    @field_validator("soul_voice", "soul_role", mode="before")
    @classmethod
    def sanitise_text_field(cls, v: str | None) -> str | None:
        return sanitize_chunk_text(v) if v is not None else None

    @field_validator("soul_do_list", "soul_donot_list", mode="before")
    @classmethod
    def sanitise_list_field(cls, v: list | None) -> list | None:
        """Sanitise each item, then drop the ones left empty.

        The admin soul editor submits blank rows, and sanitise_chunk_text can
        itself reduce an item to "" by removing injection markers. Per the
        04-06 API contract these are stripped server-side rather than 422-ing
        the whole update via the per-item min_length=1 constraint, which still
        guards anything that survives.
        """
        if v is None:
            return None
        sanitised = (sanitize_chunk_text(item) for item in v)
        return [item for item in sanitised if item]


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


# ---------------------------------------------------------------------------
# M4.2: Widget config schemas
# ---------------------------------------------------------------------------


class AgentListResponse(BaseModel):
    """Response body for GET /agents — list all non-deleted agents for the tenant."""

    agents: list[AgentResponse]


class WidgetColorsSchema(BaseModel):
    """Color palette for widget customization.

    All fields are validated as 6-digit hex strings (#RRGGBB).
    Defaults match UI-SPEC §9 Design G color palette.

    Security: T-04.2-02-03 — server-side hex validation prevents
    XSS / CSS injection via widget color values rendered in the iframe.
    """

    widget_bg: str = "#FDF9F5"
    header_bg: str = "#7B1C3A"
    header_text: str = "#FFFFFF"
    agent_bubble_bg: str = "#FDF9F5"
    agent_bubble_text: str = "#4A2030"
    user_bubble_bg: str = "#7B1C3A"
    user_bubble_text: str = "#FFFFFF"
    send_button: str = "#7B1C3A"
    input_bg: str = "#F7F0EA"

    @field_validator("*", mode="after")
    @classmethod
    def validate_hex_color(cls, v: str) -> str:
        if re.match(r'^#[0-9A-Fa-f]{6}$', v) is None:
            raise ValueError(f"Invalid hex color: {v}")
        return v


class WidgetTypographySchema(BaseModel):
    """Typography settings for widget customization."""

    font_family: Literal["Inter", "System UI", "Georgia", "custom"] = "Inter"
    font_custom_url: str | None = None
    border_radius_preset: Literal["sharp", "rounded", "pill"] = "rounded"


class WidgetConfigUpdate(BaseModel):
    """Request body for POST /agents/{id}/widget-config.

    Security: T-04.2-02-04 — Literal constraints on appearance, launcher_shape,
    font_family, and border_radius_preset prevent arbitrary string injection.
    """

    appearance: Literal["floating-button", "floating-mini-modal", "slide-out-panel"] = "floating-button"
    launcher_shape: Literal["circle", "square"] = "circle"
    colors: WidgetColorsSchema = Field(default_factory=WidgetColorsSchema)
    typography: WidgetTypographySchema = Field(default_factory=WidgetTypographySchema)
