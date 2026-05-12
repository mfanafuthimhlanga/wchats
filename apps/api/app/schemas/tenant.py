"""
Pydantic v2 schemas for tenant endpoints.

TenantCreate  — request body for POST /tenants
TenantResponse — response body for POST /tenants (contains plaintext api_key on creation only)
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TenantCreate(BaseModel):
    name: str


class TenantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    # Plaintext key — returned ONLY on creation; never stored in plaintext
    api_key: str
    created_at: datetime
