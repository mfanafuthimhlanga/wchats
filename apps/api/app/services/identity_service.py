"""
Identity service — OTP generation, delivery, verification, and session management.

Phase 17: IDV-02 (email OTP) + IDV-03 (SMS OTP) + IDV-05 (verified session check).

Security invariants:
    T-17-04: OTP_MAX_ATTEMPTS=5 lockout; 6-digit code space; short TTL
    T-17-05: Single-use — Redis key DELETED before session issuance (Pitfall 4)
    T-17-06: hmac.compare_digest for constant-time hash comparison
    T-17-07: Session token generated server-side via secrets.token_urlsafe(32)
    T-17-08: SHA-256 hash stored at rest; raw code/token never persisted or logged
    T-17-01: check_verified_session queries the tenant conn_str only (OD-1)
    T-17-18: OTP_SEND_MAX_PER_WINDOW enforced per external_id via Redis

Architecture (OD-2/3/4/5):
    OD-2: SMS default = Twilio behind SmsProvider Protocol; swappable via SMS_PROVIDER
    OD-3: OTP challenge state in Redis with TTL (not a DB table)
    OD-4: Session TTL = VERIFIED_SESSION_TTL_SECONDS (default 3600s global)
    OD-5: external_id = delivery address (lowercased email; E.164 phone)
"""

import asyncio
import hashlib
import hmac
import json
import secrets
import smtplib
from email.mime.text import MIMEText
from typing import Protocol

import psycopg2
import structlog

from app.core.config import settings

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ProviderNotConfiguredError(Exception):
    """Raised when an SMS operation is requested but the provider is not configured."""


class OtpInvalid(Exception):
    """Raised when an OTP code is missing, expired, or does not match."""


class OtpRateLimited(Exception):
    """Raised when OTP attempt or send count exceeds the configured limit."""


# ---------------------------------------------------------------------------
# Task 1: Crypto core helpers (pure functions, stdlib only)
# ---------------------------------------------------------------------------


def generate_otp_code() -> str:
    """Generate a cryptographically-random 6-digit OTP code.

    Uses secrets.randbelow — never random.randint (T-17-08).
    Returns a zero-padded 6-character decimal string (000000..999999).
    """
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp_code(code: str) -> str:
    """Return the SHA-256 hex digest of an OTP code.

    The raw code is never stored — only this hash (T-17-08).
    Returns a 64-character lowercase hex string.
    """
    return hashlib.sha256(code.encode()).hexdigest()


def verify_otp_code(stored_hash: str, submitted_code: str) -> bool:
    """Compare a submitted OTP code against a stored SHA-256 hash.

    Uses hmac.compare_digest for constant-time comparison (T-17-06).
    Returns True only when the submitted code hashes to stored_hash.
    """
    return hmac.compare_digest(stored_hash, hash_otp_code(submitted_code))


def generate_session_token() -> str:
    """Generate a URL-safe session token (256 bits of entropy).

    Returns approximately 43 characters of URL-safe base64
    (secrets.token_urlsafe(32) = 32 bytes = 43 chars after base64url encoding).
    """
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """Return the SHA-256 hex digest of a session token.

    Only the hash is stored in the tenant DB; the raw token is never
    persisted or logged (T-17-08).
    Returns a 64-character lowercase hex string.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def _otp_redis_key(agent_id: str, external_id: str, method: str) -> str:
    """Compute the Redis key for an OTP challenge.

    Normalizes external_id to lowercase (Pitfall 6 — case-sensitive Redis keys).
    Format: otp:{agent_id}:{external_id.lower()}:{method}
    """
    return f"otp:{agent_id}:{external_id.lower()}:{method}"


async def store_otp_challenge(
    redis,
    agent_id: str,
    external_id: str,
    method: str,
    code_hash: str,
    ttl: int,
) -> None:
    """Store an OTP challenge in Redis with a TTL (OD-3).

    Stores {"hash": code_hash, "attempts": 0} as JSON.
    The code_hash is the SHA-256 hex of the OTP code — never the plaintext (T-17-08).

    Args:
        redis:      Async Redis client.
        agent_id:   Agent UUID string.
        external_id: Customer delivery address (email or E.164 phone).
        method:     "email" or "sms".
        code_hash:  SHA-256 hex of the generated OTP code.
        ttl:        TTL in seconds (OTP_EMAIL_TTL_SECONDS or OTP_SMS_TTL_SECONDS).
    """
    key = _otp_redis_key(agent_id, external_id, method)
    payload = json.dumps({"hash": code_hash, "attempts": 0})
    await redis.set(key, payload, ex=ttl)


# ---------------------------------------------------------------------------
# Task 2: Delivery seam — email (SMTP) + SMS provider abstraction
# ---------------------------------------------------------------------------


class SmsProvider(Protocol):
    """Protocol for SMS delivery providers (OD-2 swappable seam)."""

    def send(self, to: str, body: str) -> None:  # pragma: no cover
        ...


class TwilioSmsProvider:
    """Twilio SMS provider — OD-2 default.

    Lazily imports twilio.rest.Client on first send() call
    (supply-chain safety: import only after human verification in 17-02; T-17-SC).
    """

    def __init__(self, account_sid: str, auth_token: str, from_number: str) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from_number = from_number

    def send(self, to: str, body: str) -> None:
        from twilio.rest import Client  # noqa: PLC0415
        client = Client(self._account_sid, self._auth_token)
        client.messages.create(body=body, from_=self._from_number, to=to)


class AfricasTalkingProvider:
    """Africa's Talking SMS provider."""

    def __init__(self, api_key: str, username: str, sender_id: str | None = None) -> None:
        self._api_key = api_key
        self._username = username
        self._sender_id = sender_id

    def send(self, to: str, body: str) -> None:
        import africastalking  # noqa: PLC0415
        africastalking.initialize(self._username, self._api_key)
        sms = africastalking.SMS
        kwargs: dict = {"message": body, "recipients": [to]}
        if self._sender_id:
            kwargs["senderId"] = self._sender_id
        sms.send(**kwargs)


class NullSmsProvider:
    """Null SMS provider — raises when send is called.

    Acts as the fail-safe default when no SMS provider credentials are set.
    Matches the ProviderNotConfiguredError sentinel pattern from credential_service.py.
    """

    def send(self, to: str, body: str) -> None:
        raise ProviderNotConfiguredError(
            "SMS provider is not configured. "
            "Set SMS_PROVIDER and the required credentials in the environment."
        )


def _get_sms_provider() -> SmsProvider:
    """Select the active SMS provider from SMS_PROVIDER setting and credentials.

    Falls back to NullSmsProvider (with a warning) if credentials are missing.
    OD-2: Twilio is the default.
    """
    provider_name = (settings.SMS_PROVIDER or "twilio").lower()

    if provider_name == "twilio":
        if all([settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN, settings.TWILIO_FROM_NUMBER]):
            return TwilioSmsProvider(
                settings.TWILIO_ACCOUNT_SID,  # type: ignore[arg-type]
                settings.TWILIO_AUTH_TOKEN,  # type: ignore[arg-type]
                settings.TWILIO_FROM_NUMBER,  # type: ignore[arg-type]
            )
    elif provider_name == "africastalking":
        if all([settings.AT_API_KEY, settings.AT_USERNAME]):
            return AfricasTalkingProvider(
                settings.AT_API_KEY,  # type: ignore[arg-type]
                settings.AT_USERNAME,  # type: ignore[arg-type]
                settings.AT_SENDER_ID,
            )

    log.warning("sms_provider.not_configured", provider=provider_name)
    return NullSmsProvider()


def send_otp_email(to_email: str, code: str) -> None:
    """Send an OTP code via email (fire-and-forget — NEVER raises).

    Copies the escalation.py SMTP pattern exactly:
    - Guard on SMTP_HOST + SMTP_FROM (same two-field guard as escalation.py)
    - MIMEText body with code and expiry note
    - smtplib.SMTP with starttls, timeout=5
    - try/except logs otp_email.send_failed, never re-raises

    The plaintext code goes ONLY to the delivery channel — never logged (T-17-08).
    """
    if not all([settings.SMTP_HOST, settings.SMTP_FROM]):
        log.warning("otp_email.not_configured", to=to_email)
        return

    ttl_minutes = settings.OTP_EMAIL_TTL_SECONDS // 60
    body = (
        f"Your W Chats verification code is below.\n\n"
        f"Code: {code}\n\n"
        f"This code expires in {ttl_minutes} minutes.\n"
        f"If you did not request this code, please ignore this message."
    )
    msg = MIMEText(body)
    msg["Subject"] = "Your W Chats Verification Code"
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to_email

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT or 587, timeout=5) as server:  # type: ignore[arg-type]
            server.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_FROM, [to_email], msg.as_string())  # type: ignore[arg-type]
        log.info("otp_email.sent", to=to_email)
    except Exception as exc:
        # Fire-and-forget: log warning but NEVER re-raise
        log.warning("otp_email.send_failed", error=str(exc), to=to_email)


def _deliver_otp(method: str, external_id: str, code: str) -> None:
    """Route OTP delivery to email or SMS.

    The plaintext code goes ONLY to the delivery channel — never logged (T-17-08).
    """
    if method == "email":
        send_otp_email(external_id, code)
    elif method == "sms":
        provider = _get_sms_provider()
        ttl_minutes = settings.OTP_SMS_TTL_SECONDS // 60
        body = f"Your W Chats verification code is {code}. Valid for {ttl_minutes} minutes."
        provider.send(external_id, body)
    else:
        log.warning("otp_deliver.unknown_method", method=method)


# ---------------------------------------------------------------------------
# Task 3: Orchestration — request_otp, verify_otp, check_verified_session
# ---------------------------------------------------------------------------


async def request_otp(redis, agent_id: str, external_id: str, method: str) -> None:
    """Request an OTP code — normalize, enforce send limit, store, and deliver.

    Returns None regardless of whether external_id is known to the system
    (no enumeration oracle — same response for valid and invalid addresses).

    Security (T-17-18): Per-external_id send limit enforced via Redis INCR counter
    keyed otp_sendlimit:{agent_id}:{external_id.lower()} (ceiling OTP_SEND_MAX_PER_WINDOW).
    """
    # Normalize: lowercase for email; treat as-is (E.164) for SMS
    normalized = external_id.lower() if method == "email" else external_id

    # Enforce per-external_id send rate limit
    send_limit_key = f"otp_sendlimit:{agent_id}:{normalized.lower()}"
    count = await redis.incr(send_limit_key)
    if count == 1:
        # First send in this window — set TTL on the counter key
        window_ttl = (
            settings.OTP_EMAIL_TTL_SECONDS if method == "email" else settings.OTP_SMS_TTL_SECONDS
        )
        await redis.expire(send_limit_key, window_ttl)
    if count > settings.OTP_SEND_MAX_PER_WINDOW:
        raise OtpRateLimited("Too many OTP requests for this address — try again later")

    # Generate, hash, and store the challenge in Redis (OD-3)
    code = generate_otp_code()
    code_hash = hash_otp_code(code)
    ttl = settings.OTP_EMAIL_TTL_SECONDS if method == "email" else settings.OTP_SMS_TTL_SECONDS
    await store_otp_challenge(redis, agent_id, normalized, method, code_hash, ttl)

    # Deliver via the appropriate channel (code never logged — T-17-08)
    _deliver_otp(method, normalized, code)

    return None


async def verify_otp(
    redis,
    agent_id: str,
    external_id: str,
    otp_code: str,
    method: str,
    conn_str: str,
) -> str:
    """Verify an OTP code and issue a short-lived verified session token.

    Security invariants (threat model T-17-04..T-17-08):
    - Missing/expired challenge → OtpInvalid (same error as wrong code — no oracle)
    - Challenge attempts already at max → OtpRateLimited (fast path)
    - Wrong code → increment attempts in Redis + raise OtpInvalid (NOT OtpRateLimited
      until the Nth failed attempt hits OTP_MAX_ATTEMPTS)
    - Correct code → DELETE Redis key FIRST (single-use, T-17-05), then UPSERT session
    - session_token_hash (SHA-256 of raw_token) stored in tenant DB (T-17-08)
    - Constant-time compare via hmac.compare_digest (T-17-06)

    Returns:
        The raw session token string (returned ONCE to the client; never logged or stored).

    Raises:
        OtpInvalid: code expired, absent, or does not match.
        OtpRateLimited: attempt count exceeded OTP_MAX_ATTEMPTS.
    """
    normalized = external_id.lower() if method == "email" else external_id
    key = _otp_redis_key(agent_id, normalized, method)

    raw = await redis.get(key)
    if raw is None:
        raise OtpInvalid("Code expired or not found")

    if isinstance(raw, bytes):
        raw = raw.decode()

    data = json.loads(raw)
    attempts = data.get("attempts", 0)

    # Fast path: already at or above max — lock before doing any crypto
    if attempts >= settings.OTP_MAX_ATTEMPTS:
        raise OtpRateLimited("Challenge locked — too many failed attempts")

    if not verify_otp_code(data["hash"], otp_code):
        # Increment attempts; DO NOT delete the key — allow retries up to the cap
        new_attempts = attempts + 1
        data["attempts"] = new_attempts
        await redis.set(key, json.dumps(data))
        if new_attempts >= settings.OTP_MAX_ATTEMPTS:
            # Nth attempt hit the wall — signal lockout on this attempt
            raise OtpRateLimited("Too many failed attempts — challenge locked")
        raise OtpInvalid("Invalid code")

    # Correct code: DELETE Redis key FIRST (single-use, Pitfall 4, T-17-05)
    await redis.delete(key)

    # Generate a server-side session token — client cannot inject its own (T-17-07)
    raw_token = generate_session_token()
    token_hash = hash_session_token(raw_token)

    # UPSERT customer_identities via blocking psycopg2 wrapped in asyncio.to_thread.
    # Parameterized SQL only — no f-strings (Pitfall 1).
    def _upsert() -> None:
        conn = psycopg2.connect(conn_str, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO customer_identities
                        (external_id, verification_method, session_token_hash, session_expires_at)
                    VALUES
                        (%s, %s, %s, now() + (%s || ' seconds')::interval)
                    ON CONFLICT (external_id) DO UPDATE SET
                        verified_at         = now(),
                        verification_method = %s,
                        session_token_hash  = %s,
                        session_expires_at  = now() + (%s || ' seconds')::interval,
                        updated_at          = now()
                    """,
                    (
                        normalized,
                        method,
                        token_hash,
                        str(settings.VERIFIED_SESSION_TTL_SECONDS),
                        method,
                        token_hash,
                        str(settings.VERIFIED_SESSION_TTL_SECONDS),
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    await asyncio.to_thread(_upsert)
    return raw_token


async def check_verified_session(agent_id: str, raw_token: str, conn_str: str) -> bool:
    """Check if a verified session token is valid and non-expired.

    Security (T-17-01, OD-1):
    - Hashes the presented token before querying (T-17-08)
    - Queries the tenant conn_str only (per-tenant scope — no cross-tenant reuse)
    - agent_id accepted for call-site symmetry but NOT used in SQL WHERE clause
      (OD-1: uniqueness is enforced on external_id alone across the whole tenant)

    Returns:
        True if a non-expired row exists with matching session_token_hash.
        False if the session is absent, expired, or the token is incorrect.
    """
    token_hash = hash_session_token(raw_token)

    def _query() -> bool:
        conn = psycopg2.connect(conn_str, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 FROM customer_identities
                    WHERE session_token_hash = %s AND session_expires_at > NOW()
                    LIMIT 1
                    """,
                    (token_hash,),
                )
                return cur.fetchone() is not None
        finally:
            conn.close()

    return await asyncio.to_thread(_query)
