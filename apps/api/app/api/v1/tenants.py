"""
POST /tenants — bootstrap a new tenant (admin operation).

Requires X-Admin-Key header (not X-API-Key).
Returns 201 Created with the plaintext API key (only time it is ever visible).

Security:
    - Plaintext key is generated and returned once; hash stored in DB.
    - admin key validated via constant-time compare in get_admin dependency.
    - T-04-03: no DB errors surfaced in HTTP response detail.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin
from app.core.database import get_async_db
from app.core.security import generate_api_key, hash_api_key
from app.models.tenant import Tenant
from app.schemas.tenant import TenantCreate, TenantResponse

router = APIRouter(tags=["tenants"])


@router.post("/tenants", status_code=201, response_model=TenantResponse)
async def create_tenant(
    body: TenantCreate,
    db: AsyncSession = Depends(get_async_db),
    _: bool = Depends(get_admin),
) -> TenantResponse:
    """Create a new tenant and return its plaintext API key.

    The plaintext key is only returned here — subsequent requests use the
    hashed value stored in the DB for verification.
    """
    raw_key = generate_api_key()
    tenant = Tenant(
        name=body.name,
        api_key_hash=hash_api_key(raw_key),
    )
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)

    # Build response manually — tenant.api_key_hash contains the hash,
    # but the response schema expects plaintext api_key (on creation only).
    return TenantResponse(
        id=tenant.id,
        name=tenant.name,
        api_key=raw_key,
        created_at=tenant.created_at,
    )
