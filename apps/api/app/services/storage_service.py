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
        No public URL; bucket is private with Block Public Access (13-01
        Terraform module).

    put_bytes(key, data): Upload raw bytes to S3 (server-side IAM task role;
        no presigned public URL exposed — T-13-06-02).

    get_bytes(key): Download raw bytes from S3 (server-side; not exposed
        publicly — T-13-06-02).

    delete_object(key): Best-effort S3 delete.  Never raises; never reverses
        a committed DB delete.

Security (T-13-06-01..04):
    - Bucket name from settings.S3_UPLOADS_BUCKET (env seam; never hardcoded).
    - IAM task role provides credentials at runtime — no static AWS key.
    - Key bytes (file content) are NEVER logged; only key path (agent_id / doc_id).
    - No presigned URL is ever generated — server-side get_object only (Fargate
      IAM task role has the required s3:GetObject permission on the uploads bucket).
"""

import structlog

from app.core.config import settings

log = structlog.get_logger(__name__)

# Module-level lazy-initialized boto3 S3 client.
# Starts as None; created on first call to _get_s3().
_s3 = None


def _get_s3():
    """Return the module-level boto3 S3 client, initializing on first call.

    Lazy init keeps this module importable in unit tests without real AWS
    credentials (boto3 is only imported when the first S3 call is made, not
    at module load time).  Mirrors _get_bedrock() in bedrock_embedding_service.py.
    """
    global _s3
    if _s3 is None:
        import boto3  # noqa: PLC0415  — intentional lazy import
        _s3 = boto3.client("s3", region_name=settings.AWS_REGION)
    return _s3


def upload_key(agent_id: str, doc_id: str, ext: str) -> str:
    """Build a tenant-scoped S3 object key for an uploaded document.

    Key format: "{agent_id}/{doc_id}{ext}" (e.g., "abc-123/def-456.pdf").

    The agent_id UUIDv4 prefix provides cross-tenant isolation; no two agents
    share the same UUID, so even an adversary who guesses a document UUID cannot
    collide with another tenant's key without also knowing the agent UUID
    (~122-bit entropy — T-13-06-01).

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

    Uses server-side boto3 put_object; the Fargate task's IAM role provides
    credentials.  No presigned URL is generated — the bucket is private
    (Block Public Access enforced at the Terraform level, 13-01).

    Args:
        key:  S3 object key — use upload_key() to build a tenant-scoped key.
        data: Raw file bytes to store.  NEVER logged (T-13-06-04).

    Raises:
        botocore.exceptions.ClientError / EndpointResolutionError if the S3
        call fails.  Callers should handle this exception if the upload is
        critical to the transaction.
    """
    _get_s3().put_object(
        Bucket=settings.S3_UPLOADS_BUCKET,
        Key=key,
        Body=data,
    )
    log.debug("storage_service.put_bytes", key=key, size=len(data))


def get_bytes(key: str) -> bytes:
    """Download raw bytes from S3 for the given key.

    Server-side boto3 get_object inside Fargate; IAM task role provides
    credentials.  No presigned URL; the bytes are returned directly to the
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
        Bucket=settings.S3_UPLOADS_BUCKET,
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
            Bucket=settings.S3_UPLOADS_BUCKET,
            Key=key,
        )
        log.debug("storage_service.delete_object", key=key)
    except Exception as exc:
        log.warning(
            "storage_service.delete_object_failed",
            key=key,
            error=str(exc),
        )
