# ---------------------------------------------------------------------------
# Secrets Manager — container declarations only (T-13-01-01)
# Values are populated OUT OF BAND in 13-08 (live gate).
# NO secret_string is set here — no literal value ever appears in HCL.
# ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "neon_api_key" {
  name        = "wchats/neon-api-key"
  description = "Neon API key for per-tenant database provisioning and management"
}

resource "aws_secretsmanager_secret" "neon_encryption_key" {
  name        = "wchats/neon-encryption-key"
  description = "Base64url-encoded Fernet key for encrypting Neon connection strings at rest"
}

resource "aws_secretsmanager_secret" "control_db_url" {
  name        = "wchats/control-db-url"
  description = "AsyncPG URL (postgresql+asyncpg://) for the W Chats control database"
}

resource "aws_secretsmanager_secret" "control_db_sync_url" {
  name        = "wchats/control-db-sync-url"
  description = "Synchronous psycopg2 URL (postgresql://) for Celery and Alembic CLI"
}

resource "aws_secretsmanager_secret" "redis_url" {
  name        = "wchats/redis-url"
  description = "ElastiCache Redis TLS URL (rediss://) for Celery broker and SSE pub/sub"
}

resource "aws_secretsmanager_secret" "admin_key" {
  name        = "wchats/admin-key"
  description = "X-Admin-Key secret for POST /tenants admin endpoint"
}

resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name        = "wchats/anthropic-api-key"
  description = "Anthropic API key for agent turns, validators, and red-team calls (direct API, not Bedrock)"
}

# VOYAGE_API_KEY: still required by config.py until 13-02 (Bedrock migration) makes it optional.
# Include the secret container so the task definition can inject it; 13-02 will make it optional.
resource "aws_secretsmanager_secret" "voyage_api_key" {
  name        = "wchats/voyage-api-key"
  description = "Voyage AI API key (required by config.py until 13-02 Bedrock migration; set to a placeholder after migration)"
}

resource "aws_secretsmanager_secret" "jwt_secret" {
  name        = "wchats/jwt-secret"
  description = "HS256 JWT signing secret for widget session tokens"
}

resource "aws_secretsmanager_secret" "clerk_webhook_signing_secret" {
  name        = "wchats/clerk-webhook-signing-secret"
  description = "Clerk webhook signing secret for validating incoming webhook events"
}

# Optional secrets — containers declared here; values set if the feature is enabled
resource "aws_secretsmanager_secret" "cohere_api_key" {
  name        = "wchats/cohere-api-key"
  description = "Cohere API key (optional — reranker fallback; set to a placeholder if unused)"
}

resource "aws_secretsmanager_secret" "langfuse_public_key" {
  name        = "wchats/langfuse-public-key"
  description = "Langfuse public key for observability tracing (optional)"
}

resource "aws_secretsmanager_secret" "langfuse_secret_key" {
  name        = "wchats/langfuse-secret-key"
  description = "Langfuse secret key for observability tracing (optional)"
}

resource "aws_secretsmanager_secret" "smtp_host" {
  name        = "wchats/smtp-host"
  description = "SMTP host for escalation email delivery (optional)"
}

resource "aws_secretsmanager_secret" "smtp_from" {
  name        = "wchats/smtp-from"
  description = "SMTP sender address for escalation emails (optional)"
}

resource "aws_secretsmanager_secret" "smtp_user" {
  name        = "wchats/smtp-user"
  description = "SMTP authentication username (optional)"
}

resource "aws_secretsmanager_secret" "smtp_password" {
  name        = "wchats/smtp-password"
  description = "SMTP authentication password (optional)"
}

resource "aws_secretsmanager_secret" "owner_email" {
  name        = "wchats/owner-email"
  description = "Tenant owner email for escalation notifications (optional)"
}

# ---------------------------------------------------------------------------
# Local: consolidated list of all secret ARNs
# Referenced by iam.tf (policy resources) and fargate.tf (task def secrets block)
# ---------------------------------------------------------------------------

locals {
  all_secret_arns = [
    aws_secretsmanager_secret.neon_api_key.arn,
    aws_secretsmanager_secret.neon_encryption_key.arn,
    aws_secretsmanager_secret.control_db_url.arn,
    aws_secretsmanager_secret.control_db_sync_url.arn,
    aws_secretsmanager_secret.redis_url.arn,
    aws_secretsmanager_secret.admin_key.arn,
    aws_secretsmanager_secret.anthropic_api_key.arn,
    aws_secretsmanager_secret.voyage_api_key.arn,
    aws_secretsmanager_secret.jwt_secret.arn,
    aws_secretsmanager_secret.clerk_webhook_signing_secret.arn,
    aws_secretsmanager_secret.cohere_api_key.arn,
    aws_secretsmanager_secret.langfuse_public_key.arn,
    aws_secretsmanager_secret.langfuse_secret_key.arn,
    aws_secretsmanager_secret.smtp_host.arn,
    aws_secretsmanager_secret.smtp_from.arn,
    aws_secretsmanager_secret.smtp_user.arn,
    aws_secretsmanager_secret.smtp_password.arn,
    aws_secretsmanager_secret.owner_email.arn,
  ]
}
