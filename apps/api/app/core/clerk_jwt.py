"""Clerk JWT verification using PyJWKClient (PyJWT 2.12.1).

The _get_jwks_client singleton caches signing keys for 3600 seconds
and refreshes automatically on unknown kid. verify_clerk_jwt() wraps
all jwt exceptions into jwt.InvalidTokenError so callers can catch a
single exception type.

Security: azp claim NOT validated per 04-9-RESEARCH.md Pitfall 3
(azp absent in some Clerk token configurations).
"""

from functools import lru_cache
from typing import Any

import jwt
from jwt import InvalidTokenError, PyJWKClient

from app.core.config import settings


@lru_cache(maxsize=1)
def _get_jwks_client() -> PyJWKClient:
    """Module-level singleton. PyJWKClient caches keys internally."""
    return PyJWKClient(settings.CLERK_JWKS_URL, cache_keys=True, lifespan=3600)


def verify_clerk_jwt(token: str) -> dict[str, Any]:
    """Verify a Clerk session JWT.

    Returns the decoded payload on success.
    Raises jwt.InvalidTokenError on any failure (wraps all exception types).

    Claims validated:
    - Signature (RS256 via JWKS)
    - exp / nbf (PyJWT handles automatically with verify_exp=True, verify_nbf=True)
    - azp NOT validated — azp may be absent in some token configurations
      (see 04-9-RESEARCH.md Pitfall 3); skipping is an accepted risk (T-04-10-06).
    """
    try:
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={
                "verify_exp": True,
                "verify_nbf": True,
                # Do not require audience — Clerk session tokens have no aud by default
                "verify_aud": False,
            },
        )
        return payload
    except Exception as exc:
        raise InvalidTokenError(str(exc)) from exc
