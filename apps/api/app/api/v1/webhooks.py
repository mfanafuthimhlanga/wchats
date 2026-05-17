"""
Clerk webhook handler and self-healing tenant provisioning endpoint.

Routes:
    POST /webhooks/clerk   — receives Clerk user lifecycle events (user.created, user.deleted)
    POST /me/provision     — self-healing tenant provisioning from JWT sub claim (RISK-01 mitigation)

Threat mitigations:
    T-04-10-03: Svix HMAC-SHA256 signature verification on every webhook request.
    T-04-10-04: Svix timestamp window (5 min) prevents replay attacks; ON CONFLICT DO NOTHING is idempotent.
    T-04-10-05: CLERK_WEBHOOK_SIGNING_SECRET never logged; structlog context excludes request headers.
    T-04-10-07: clerk_user_id TEXT UNIQUE + ON CONFLICT DO NOTHING prevents duplicate tenant rows.
    T-04-10-10: verify_clerk_jwt validates RS256 signature + exp; ON CONFLICT prevents rogue tenant creation.
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from svix.webhooks import Webhook, WebhookVerificationError

from app.core.clerk_jwt import verify_clerk_jwt
from app.core.config import settings
from app.core.database import get_async_db
from app.core.security import generate_api_key, hash_api_key, hmac_key_prefix

log = structlog.get_logger()

router = APIRouter(tags=["webhooks"])

# Bearer scheme for /me/provision — auto_error=True raises 401 if header missing
_bearer_scheme_prov = HTTPBearer(auto_error=True)


# ---------------------------------------------------------------------------
# POST /webhooks/clerk
# ---------------------------------------------------------------------------


@router.post("/webhooks/clerk", status_code=status.HTTP_204_NO_CONTENT)
async def clerk_webhook(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_async_db),
) -> None:
    """Receive Clerk webhook events. Signature verified via Svix.

    CRITICAL: payload = await request.body() MUST be the first statement —
    Svix HMAC is computed over raw bytes, NOT parsed JSON (Pitfall 2).

    user.created:
        Idempotent INSERT with ON CONFLICT (clerk_user_id) DO NOTHING.
        Provisions tenants row with clerk_user_id + generated api_key_hash.

    user.deleted:
        Soft-delete: UPDATE tenants SET deleted_at = now() WHERE clerk_user_id = ... AND deleted_at IS NULL.

    All other event types: acknowledge with 204, no-op.
    """
    payload = await request.body()  # MUST be first — raw bytes for Svix HMAC
    headers = dict(request.headers)

    try:
        wh = Webhook(settings.CLERK_WEBHOOK_SIGNING_SECRET)
        evt = wh.verify(payload, headers)
    except WebhookVerificationError:
        log.warning("clerk_webhook.signature_invalid")
        response.status_code = status.HTTP_400_BAD_REQUEST
        return

    event_type: str = evt.get("type", "")
    data: dict = evt.get("data", {})

    if event_type == "user.created":
        clerk_user_id: str = data["id"]  # "user_xxx"
        email_addresses = data.get("email_addresses", [])
        email: str = email_addresses[0]["email_address"] if email_addresses else ""
        first_name: str = data.get("first_name") or ""
        last_name: str = data.get("last_name") or ""
        display_name = f"{first_name} {last_name}".strip() or email or clerk_user_id

        raw_key = generate_api_key()
        key_hash = hash_api_key(raw_key)
        key_prefix = hmac_key_prefix(raw_key)

        # Idempotent INSERT — Clerk may retry webhooks; ON CONFLICT is the idempotency gate
        await db.execute(
            text(
                "INSERT INTO tenants (name, api_key, api_key_prefix, clerk_user_id) "
                "VALUES (:name, :api_key, :api_key_prefix, :clerk_user_id) "
                "ON CONFLICT (clerk_user_id) DO NOTHING"
            ),
            {
                "name": display_name,
                "api_key": key_hash,
                "api_key_prefix": key_prefix,
                "clerk_user_id": clerk_user_id,
            },
        )
        await db.commit()
        log.info("tenant.provisioned", clerk_user_id=clerk_user_id, name=display_name)
        # NOTE: raw_key is NOT returned — single retrieval opportunity is /me/provision (201 response)

    elif event_type == "user.deleted":
        clerk_user_id = data["id"]
        await db.execute(
            text(
                "UPDATE tenants SET deleted_at = now() "
                "WHERE clerk_user_id = :cuid AND deleted_at IS NULL"
            ),
            {"cuid": clerk_user_id},
        )
        await db.commit()
        log.info("tenant.soft_deleted", clerk_user_id=clerk_user_id)

    # All other event types: acknowledge (204), no-op


# ---------------------------------------------------------------------------
# POST /me/provision
# ---------------------------------------------------------------------------


@router.post("/me/provision", status_code=status.HTTP_201_CREATED)
async def provision_me(
    credentials: HTTPAuthorizationCredentials = Security(_bearer_scheme_prov),
    response: Response = None,
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    """Self-healing tenant provisioning from JWT sub claim (RISK-01 mitigation).

    Called by the admin UI when the webhook-based provisioning was missed.
    If a tenant already exists for this Clerk user, returns 200.
    If not, creates a new tenant row and returns 201 with the api_key (single retrieval opportunity).

    T-04-10-10: RS256 signature + exp validation via verify_clerk_jwt;
                ON CONFLICT prevents rogue tenant creation via stolen JWT.
    """
    try:
        payload = verify_clerk_jwt(credentials.credentials)
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid session token")

    clerk_user_id: str = payload["sub"]

    # Check if tenant already exists
    from sqlalchemy import select
    from app.models.tenant import Tenant

    result = await db.execute(
        select(Tenant).where(
            Tenant.deleted_at.is_(None),
            Tenant.clerk_user_id == clerk_user_id,
        )
    )
    existing = result.scalars().first()

    if existing:
        response.status_code = status.HTTP_200_OK
        return {"status": "exists", "tenant_id": str(existing.id)}

    # Provision new tenant
    raw_key = generate_api_key()
    key_hash = hash_api_key(raw_key)
    key_prefix = hmac_key_prefix(raw_key)
    display_name = payload.get("email", clerk_user_id)  # fallback to clerk_user_id

    result = await db.execute(
        text(
            "INSERT INTO tenants (name, api_key, api_key_prefix, clerk_user_id) "
            "VALUES (:name, :api_key, :api_key_prefix, :clerk_user_id) "
            "ON CONFLICT (clerk_user_id) DO NOTHING "
            "RETURNING id"
        ),
        {
            "name": display_name,
            "api_key": key_hash,
            "api_key_prefix": key_prefix,
            "clerk_user_id": clerk_user_id,
        },
    )
    await db.commit()

    row = result.fetchone()
    if row is None:
        # Race condition: another request created the tenant between SELECT and INSERT
        re_result = await db.execute(
            select(Tenant).where(
                Tenant.deleted_at.is_(None),
                Tenant.clerk_user_id == clerk_user_id,
            )
        )
        existing = re_result.scalars().first()
        response.status_code = status.HTTP_200_OK
        return {"status": "exists", "tenant_id": str(existing.id) if existing else "unknown"}

    new_id = row[0]
    log.info("tenant.self_provisioned", clerk_user_id=clerk_user_id)
    # api_key returned only once — this is the single retrieval opportunity (T-04-10-10)
    return {"status": "created", "tenant_id": str(new_id), "api_key": raw_key}
