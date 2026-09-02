"""
storage_service — S3 upload/download helpers for document byte storage.

PROD-12 (document uploads to S3) and PROD-13 (ingestion chain reads from S3).
Replaces local-disk UPLOADS_DIR with a private S3 bucket scoped by tenant UUIDv4.

Design:
    _get_s3(): Lazy boto3 S3 client — mirrors _get_bedrock() in
        bedrock_embedding_service.py.  Late import avoids loading boto3 at
        module level so the module can be imported in unit tests without real
        AWS credentials.

    upload_key(agent_id, doc_id, ext): Tenant-scoped S3 key.
        Format: "{agent_id}/{doc_id}{ext}" (e.g., "abc-123/def-456.pdf").
        The agent_id UUIDv4 prefix provides ~122-bit cross-tenant isolation
        without a separate tenant-level prefix (UUIDs are globally unique).
        No public URL is ever issued; every read is server-side get_object.

    put_bytes(key, data): Upload raw bytes to S3 (server-side, explicit S3 credential pair;
        no presigned public URL exposed — T-13-06-02).

    get_bytes(key): Download raw bytes from S3 (server-side; not exposed
        publicly — T-13-06-02).

    delete_object(key): Best-effort S3 delete.  Never raises; never reverses
        a committed DB delete.

Security (T-13-06-01..04):
    - Bucket name from settings.S3_UPLOADS_BUCKET (env seam; never hardcoded).
    - Credentials come from settings.S3_ACCESS_KEY_ID and
      settings.S3_SECRET_ACCESS_KEY and reach boto3 as explicit arguments.
      boto3's default credential chain is not read, so no environment variable,
      shared credentials file or instance metadata role can supply an identity
      this process did not configure by name.
    - Key bytes (file content) are NEVER logged; only key path (agent_id / doc_id).
    - No presigned URL is ever generated; server-side get_object only, under the
      explicit S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY pair named above.
"""

import structlog

from app.core.config import settings

log = structlog.get_logger(__name__)

# Module-level lazy-initialized boto3 S3 client.
# Starts as None; created on first call to _get_s3().
_s3 = None


class StorageNotConfigured(RuntimeError):
    """S3_UPLOADS_BUCKET is unset, so there is nowhere to put document bytes.

    Raised instead of letting the empty string reach botocore, which reports it
    as ``Invalid bucket name ""`` from inside a 500 — indistinguishable from a
    code defect. A missing configuration is an unavailable service, and callers
    translate this to 503 (BACKLOG 1.24).
    """


def _bucket() -> str:
    """The uploads bucket name, or raise if storage was never configured.

    S3_UPLOADS_BUCKET defaults to "" so that local-dev imports work without
    real S3 (config.py:166). That default must not be allowed to travel as far
    as an API call.
    """
    bucket = settings.S3_UPLOADS_BUCKET
    if not bucket:
        raise StorageNotConfigured(
            "S3_UPLOADS_BUCKET is not set, so uploaded documents have nowhere "
            "to go. Set it in the environment (and S3_ENDPOINT_URL too if you "
            "are pointing at a local S3-compatible store)."
        )
    return bucket


#: The S3-compatible hosts a production process may write customer documents
#: to, by hostname suffix (decision #14.6: R2 or B2, chosen when the owner
#: creates the keys). Everything else, MinIO included, stays refused there.
PRODUCTION_ENDPOINT_SUFFIXES: tuple[str, ...] = (
    ".r2.cloudflarestorage.com",
    ".backblazeb2.com",
)


def _require_production_endpoint(endpoint: str) -> None:
    """Refuse a production endpoint outside PRODUCTION_ENDPOINT_SUFFIXES.

    The suffix check runs on the PARSED hostname, never on the raw string: a
    URL like https://evil.example/?x=.r2.cloudflarestorage.com carries the
    suffix without pointing there. Userinfo is refused outright, because an
    endpoint URL that embeds credentials puts them one log line or one
    misconfigured client away from disclosure. Scheme is https or nothing:
    customer bytes do not travel cleartext.

    THE BOUND IS THE PROVIDER, NOT THE ACCOUNT. Any R2 or B2 tenant's host
    carries these suffixes, so a mistyped or hostile S3_ENDPOINT_URL can still
    name an account that is not ours; pinning the owner's own host is #133.
    """
    from urllib.parse import urlsplit  # noqa: PLC0415

    parsed = urlsplit(endpoint)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        raise StorageNotConfigured(
            "S3_ENDPOINT_URL must be https while ENVIRONMENT=production; over "
            + (parsed.scheme or "a missing scheme")
            + " every customer document is readable in transit."
        )
    if parsed.username or parsed.password:
        raise StorageNotConfigured(
            "S3_ENDPOINT_URL embeds credentials while ENVIRONMENT=production. "
            "Move the key into the AWS credential variables and keep the URL "
            "to scheme and host."
        )
    if not any(host.endswith(suffix) for suffix in PRODUCTION_ENDPOINT_SUFFIXES):
        raise StorageNotConfigured(
            "S3_ENDPOINT_URL points at "
            + (host or "an unreadable host")
            + " while ENVIRONMENT=production. Customer documents may only be "
            "redirected to Cloudflare R2 or Backblaze B2 (decision #14); for "
            "anything else, unset it or do not run this process as production."
        )


def _require_credentials() -> tuple[str, str]:
    """Return the S3 credentials, refusing rather than letting boto3 guess.

    boto3's default chain would silently try environment variables, a shared
    credentials file and the instance metadata service. On a container with none
    of them that surfaces as NoCredentialsError inside the first upload, naming
    nothing the operator set. Refusing here names the setting instead.
    """
    key_id = settings.S3_ACCESS_KEY_ID
    secret = settings.S3_SECRET_ACCESS_KEY
    missing = [
        name
        for name, value in (
            ("S3_ACCESS_KEY_ID", key_id),
            ("S3_SECRET_ACCESS_KEY", secret),
        )
        if not value
    ]
    if missing:
        raise StorageNotConfigured(
            f"{' and '.join(missing)} unset, so document storage has no "
            f"credentials. Set them for the S3-compatible store named by "
            f"S3_ENDPOINT_URL; boto3's default credential chain is deliberately "
            f"not used."
        )
    return key_id, secret


def _get_s3():
    """Return the module-level boto3 S3 client, initializing on first call.

    Lazy init keeps boto3 out of module import (it loads on the first S3 call,
    not at module load time).  Mirrors _get_bedrock() in
    bedrock_embedding_service.py.  Credentials are no longer optional at that
    point: _require_credentials reads them from settings and names the missing
    one, so nothing falls back to boto3's default credential chain.

    S3_ENDPOINT_URL (BACKLOG 1.24) redirects every read and write of customer
    document bytes. In production it is honoured for exactly the S3-compatible
    stores decision #14 names, Cloudflare R2 and Backblaze B2, and refused for
    every other host (ticket 18). Silently ignoring a refused endpoint would be
    worse than raising: the operator who set it believes documents are going
    somewhere they are not.
    """
    global _s3
    if _s3 is None:
        import boto3  # noqa: PLC0415  — intentional lazy import

        endpoint = settings.S3_ENDPOINT_URL
        if endpoint and settings.ENVIRONMENT == "production":
            _require_production_endpoint(endpoint)

        key_id, secret = _require_credentials()
        kwargs: dict = {
            "region_name": settings.AWS_REGION,
            "aws_access_key_id": key_id,
            "aws_secret_access_key": secret,
        }
        if endpoint:
            kwargs["endpoint_url"] = endpoint
            # BACKLOG 1.33: log the HOST, never the whole URL. An endpoint URL
            # carries userinfo, and MinIO setups routinely embed the access key
            # and secret:
            #   http://AKIAEXAMPLE:s3cr3t-p4ssw0rd@minio.internal:9000
            # This line fires on every process that sets the seam, so the whole
            # URL would put those credentials in every such log.
            from urllib.parse import urlsplit  # noqa: PLC0415

            parsed = urlsplit(endpoint)
            log.warning(
                "storage_service.endpoint_override_active",
                endpoint_host=parsed.hostname,
                endpoint_port=parsed.port,
                endpoint_scheme=parsed.scheme,
                environment=settings.ENVIRONMENT,
            )
        _s3 = boto3.client("s3", **kwargs)
    return _s3


def upload_key(agent_id: str, doc_id: str, ext: str) -> str:
    """Build a tenant-scoped S3 object key for an uploaded document.

    Key format: "{agent_id}/{doc_id}{ext}" (e.g., "abc-123/def-456.pdf").

    The agent_id UUIDv4 prefix provides cross-tenant isolation; no two agents
    share the same UUID, so even an adversary who guesses a document UUID cannot
    collide with another tenant's key without also knowing the agent UUID
    (~122-bit entropy — T-13-06-01).

    An S3_ENDPOINT_URL that carries a path (R2's per-bucket URL ends in the
    bucket name) makes boto3 prefix every key with that path segment, so the
    object sits at "<path>/{agent_id}/{doc_id}{ext}" in that environment and
    at the bare key where the endpoint has no path (local MinIO). Reads and
    writes share one client, so each environment agrees with itself.

    Args:
        agent_id: UUID string of the agent (tenant-scoping prefix).
        doc_id:   UUID string of the document.
        ext:      File extension including the leading dot (e.g., ".pdf", ".png").

    Returns:
        Tenant-scoped S3 key string.
    """
    return f"{agent_id}/{doc_id}{ext}"


def put_bytes(key: str, data: bytes) -> None:
    """Upload raw bytes to S3 under the given key.

    Uses server-side boto3 put_object with the explicit S3 credential pair from
    Settings.  No presigned URL is generated.

    Args:
        key:  S3 object key — use upload_key() to build a tenant-scoped key.
        data: Raw file bytes to store.  NEVER logged (T-13-06-04).

    Raises:
        botocore.exceptions.ClientError / EndpointResolutionError if the S3
        call fails.  Callers should handle this exception if the upload is
        critical to the transaction.
    """
    _get_s3().put_object(
        Bucket=_bucket(),
        Key=key,
        Body=data,
    )
    log.debug("storage_service.put_bytes", key=key, size=len(data))


def get_bytes(key: str) -> bytes:
    """Download raw bytes from S3 for the given key.

    Server-side boto3 get_object with the explicit S3 credential pair from
    Settings.  No presigned URL; the bytes are returned directly to the
    caller and NEVER logged (T-13-06-04).

    Args:
        key: S3 object key — use upload_key() to build a tenant-scoped key.

    Returns:
        Raw file bytes read from S3.

    Raises:
        botocore.exceptions.ClientError if the object does not exist or S3
        is unreachable.
    """
    response = _get_s3().get_object(
        Bucket=_bucket(),
        Key=key,
    )
    data: bytes = response["Body"].read()
    log.debug("storage_service.get_bytes", key=key, size=len(data))
    return data


def delete_object(key: str) -> None:
    """Best-effort delete an S3 object.

    Called after a document's DB row is deleted.  Never raises on failure
    (the authoritative state is the database, not S3).  A leftover S3 object
    is a benign storage-cost concern, not a correctness issue.

    Note: S3 objects are intentionally NOT deleted after ingestion completes
    (embed_and_migrate) — they are retained as durable source bytes for
    idempotent re-ingestion (e.g., backfill re-embed in 13-04).

    Args:
        key: S3 object key to delete.
    """
    try:
        _get_s3().delete_object(
            Bucket=_bucket(),
            Key=key,
        )
        log.debug("storage_service.delete_object", key=key)
    except Exception as exc:
        log.warning(
            "storage_service.delete_object_failed",
            key=key,
            error=str(exc),
        )
