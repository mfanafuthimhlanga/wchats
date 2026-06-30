"""
Application configuration loaded from environment variables.

T-01-01: NEON_ENCRYPTION_KEY read from env only; Settings.__repr__ is suppressed
          to prevent accidental value leakage in logs.
T-01-02: CONTROL_DB_SYNC_URL read from env only; same repr suppression applies.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Walk up from this file to find .env — works whether CWD is the project root,
# apps/api/, or anywhere else. Stops at the first .env found.
def _find_env_file() -> str | None:
    here = Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        candidate = parent / ".env"
        if candidate.exists():
            return str(candidate)
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_find_env_file(), extra="ignore")

    # Neon
    NEON_API_KEY: str
    NEON_REGION: str = "aws-us-east-1"
    # Base64url-encoded 32 bytes; kept as str because Fernet accepts str
    NEON_ENCRYPTION_KEY: str
    # Base64url-encoded 32 bytes; key material for HKDF per-tenant credential derivation (INT-01)
    # Same encoding convention as NEON_ENCRYPTION_KEY. Set PLATFORM_CREDENTIAL_KEY in .env.
    PLATFORM_CREDENTIAL_KEY: str

    # Database
    # postgresql+asyncpg:// — for FastAPI async engine
    CONTROL_DB_URL: str
    # postgresql:// — for Celery sync engine and Alembic CLI
    CONTROL_DB_SYNC_URL: str

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    # WR-04: disable TLS certificate verification for rediss:// connections.
    # False (default) = verify the server certificate (ssl.CERT_REQUIRED + hostname check).
    # True = allow CERT_NONE — exposes the connection to MITM attacks and MUST only be set
    # for a documented local/dev exception (e.g. self-signed cert in a test environment).
    # Never set True in production.
    REDIS_TLS_INSECURE: bool = False

    # Observability
    LOG_LEVEL: str = "INFO"
    SENTRY_DSN: str | None = None

    # Auth
    ADMIN_KEY: str  # for X-Admin-Key on POST /tenants

    # CORS — locked to known origins; widget CORS added in M4 only (T-04-06)
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # Deployment environment — "production" disables OpenAPI docs (WR-04)
    ENVIRONMENT: str = "development"

    # Upload staging directory — shared between API and pipeline worker
    # Override via UPLOADS_DIR env var; default works for both Linux containers
    # (/vrd-uploads) and Windows native runs (C:/vrd-uploads or any writable path)
    UPLOADS_DIR: str = "/vrd-uploads"

    # Ingestion pipeline — M2 additions (T-02-01-01: keys suppressed by __repr__)
    ANTHROPIC_API_KEY: str
    VOYAGE_API_KEY: str
    # M3: Cohere reranker fallback (RET-05); optional so existing envs are not broken
    COHERE_API_KEY: str | None = None

    # M4: Widget JWT auth — required; no default; must be set in every deployment environment
    JWT_SECRET: str

    # M4.1: Clerk authentication
    CLERK_JWKS_URL: str = "https://api.clerk.com/v1/jwks"
    CLERK_WEBHOOK_SIGNING_SECRET: str  # required — must be set from Clerk dashboard

    # M4: Escalation email (all optional — fallback to structlog WARNING when unset)
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_FROM: str | None = None
    OWNER_EMAIL: str | None = None
    # M10: SMTP authentication credentials (required by all production SMTP providers)
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None

    # M4.1: Tenant daily budget ceiling (global default; per-tenant override in M5 admin UI)
    TENANT_DAILY_BUDGET_USD: float = 5.0

    # M5: Langfuse observability (optional — validation chain still runs when unset)
    LANGFUSE_PUBLIC_KEY: str | None = None
    LANGFUSE_SECRET_KEY: str | None = None
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    # M5: Verified-QA confidence threshold — auditor confidence must meet this to enqueue candidate
    VERIFIED_QA_CONFIDENCE_THRESHOLD: float = 0.90

    # M6: Eval system thresholds — Ragas metric promotion gates + retrieval cache
    EVAL_FAITHFULNESS_THRESHOLD: float = 0.90
    EVAL_RELEVANCY_THRESHOLD: float = 0.90
    VERIFIED_QA_HIT_THRESHOLD: float = 0.93

    # M7: Red team configuration
    RED_TEAM_MAX_TURNS: int = 5        # max turns per attack sequence per agent
    RED_TEAM_ATTACK_SEQUENCES: int = 3  # number of distinct attack sequences per agent

    # M8: Deployment checklist configuration
    DEP_BLOCK_ON_HIGH_RED_TEAM: bool = True  # when True, high_count > 0 triggers block (DEP-03)

    # M10: Maintenance + Observability thresholds and flags
    ALERT_FAITHFULNESS_THRESHOLD: float = 0.6
    ALERT_RED_TEAM_CRITICAL_COUNT: int = 1
    DIGEST_ENABLED: bool = True

    MAX_UPLOAD_SIZE_MB: int = 50

    # P13-02: Bedrock embedding provider seam (D-14 env-selectable; "bedrock" | "voyage")
    # EMBEDDING_PROVIDER selects the embedding backend at startup:
    #   "bedrock" → Amazon Bedrock Titan Text Embeddings v2 (IAM-authed, no RPM cap)
    #   "voyage"  → Voyage AI voyage-3 (legacy; retained as fallback behind the seam)
    EMBEDDING_PROVIDER: str = "bedrock"
    AWS_REGION: str = "us-east-1"
    BEDROCK_EMBED_MODEL_ID: str = "amazon.titan-embed-text-v2:0"

    # P13-06: S3 uploads bucket (PROD-12, PROD-13).
    # Empty string default keeps local-dev imports working without real S3;
    # set S3_UPLOADS_BUCKET in .env or ECS task definition for production.
    # UPLOADS_DIR is retained below for backward-compat but is no longer on
    # the upload/parse hot path after the S3 migration.
    S3_UPLOADS_BUCKET: str = ""

    # M4 Runtime agent budget — per-turn USD ceiling for ClaudeAgentOptions.
    # D-10 fix phase 2: raised from 0.05 (too low for thinking+retrieve+synthesis).
    # 0.50 gives headroom for a Sonnet extended-thinking+retrieve+synthesis turn
    # while still acting as a DoS guardrail (T-04-03-06).
    # Set AGENT_MAX_BUDGET_USD in .env to override (e.g. tighter in production).
    AGENT_MAX_BUDGET_USD: float = 0.50

    # M15: Actor Validator skip threshold
    # 500 = $5.00 — any skill whose envelope constraints.max_amount_cents ceiling is
    # strictly below this value skips the Actor judge entirely (ACT-03 cost control).
    # Rationale: a booking fee capped at $1.00 needs no Haiku security review;
    # a purchase up to $50.00 should always be reviewed.
    # Override via env var ACTOR_SKIP_MAX_AMOUNT_CENTS.
    ACTOR_SKIP_MAX_AMOUNT_CENTS: int = 500

    def __repr__(self) -> str:  # T-01-01, T-01-02: never leak field values
        return f"Settings(LOG_LEVEL={self.LOG_LEVEL!r})"

    __str__ = __repr__


# Module-level singleton — imported by every module that needs config
settings = Settings()
