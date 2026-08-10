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
    # hide_input_in_errors: T-01-01/T-01-02 again, on the path __repr__ does not
    # cover. `__repr__` below suppresses field values, but a pydantic
    # ValidationError is raised BEFORE any instance exists, and its default
    # rendering includes `input_value=` — for BaseSettings that input is the
    # whole assembled settings dict, so one missing or malformed field prints
    # truncated fragments of every real secret beside it. Observed 2026-08-11
    # while probing env precedence: omitting PLATFORM_CREDENTIAL_KEY produced
    #   PLATFORM_CREDENTIAL_KEY
    #     Field required [type=missing, input_value={'NEON_API_KEY': 'stub-ke...<tail of a real key>'}]
    # A misconfigured worker, a CI job with an absent secret, or any Celery task
    # traceback would write that to stderr and into the job log. Pinned by
    # tests/unit/test_config_error_redaction.py.
    model_config = SettingsConfigDict(
        env_file=_find_env_file(), extra="ignore", hide_input_in_errors=True
    )

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

    # M17: OTP identity verification — TTL and rate-limit settings (OD-4 global TTL lock)
    VERIFIED_SESSION_TTL_SECONDS: int = 3600   # OD-4: 1 hour verified-session lifetime
    OTP_EMAIL_TTL_SECONDS: int = 600           # 10 min email OTP window
    OTP_SMS_TTL_SECONDS: int = 300             # 5 min SMS OTP window
    OTP_MAX_ATTEMPTS: int = 5                  # max verify attempts before challenge expires
    OTP_SEND_MAX_PER_WINDOW: int = 3           # max sends per external_id per 10-min window

    # M17: SMS OTP provider — OD-2 Twilio default; NullSmsProvider used when creds unset
    # All credentials default to None (fail-safe: unset = SMS not configured).
    SMS_PROVIDER: str = "twilio"               # "twilio" | "africastalking"
    TWILIO_ACCOUNT_SID: str | None = None
    TWILIO_AUTH_TOKEN: str | None = None
    TWILIO_FROM_NUMBER: str | None = None      # E.164 format, e.g. +27XXXXXXXXX
    AT_API_KEY: str | None = None              # Africa's Talking API key
    AT_USERNAME: str | None = None             # Africa's Talking username
    AT_SENDER_ID: str | None = None            # Africa's Talking sender ID (optional)

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
    # 0.50 gives headroom for a Haiku extended-thinking+retrieve+synthesis turn
    # while still acting as a DoS guardrail (T-04-03-06).
    # Set AGENT_MAX_BUDGET_USD in .env to override (e.g. tighter in production).
    AGENT_MAX_BUDGET_USD: float = 0.50

    # Phase 21 (OPS-07): sampled Ragas 0.4.x faithfulness + citation-coverage rate.
    # Online-scoring norm is 1-10% of live traffic (DOMAIN-NOTES §2) + 100% of
    # Auditor-flagged ungrounded/partial turns (gated inside run_retrieval_faithfulness,
    # not at dispatch time — see 21-04-SUMMARY.md). 0.1 = 10%, the top of that range,
    # chosen as a conservative default; override via env var for a tighter/looser rate.
    RETRIEVAL_FAITHFULNESS_SAMPLE_RATE: float = 0.1

    # M15: Actor Validator skip threshold
    # 500 = $5.00 — any skill whose envelope constraints.max_amount_cents ceiling is
    # strictly below this value skips the Actor judge entirely (ACT-03 cost control).
    # Rationale: a booking fee capped at $1.00 needs no Haiku security review;
    # a purchase up to $50.00 should always be reviewed.
    # Override via env var ACTOR_SKIP_MAX_AMOUNT_CENTS.
    ACTOR_SKIP_MAX_AMOUNT_CENTS: int = 500

    # Phase 18: blast-radius platform-default warning thresholds (BLR-01).
    # Used when tenants.blast_radius_warn_single_cents /
    # blast_radius_warn_hourly_cents is NULL (mirrors the tenants.daily_budget_usd
    # + global-default convention from migration 0008). Both are configured-side
    # ceilings, distinct from the observed-history figures computed over
    # BLAST_RADIUS_OBSERVED_WINDOW_DAYS — Open Decision 1 keeps the two kinds
    # of number separately labelled, never conflated.
    BLAST_RADIUS_WARN_SINGLE_CENTS: int = 50000    # R500.00 platform-default single-action warning
    BLAST_RADIUS_WARN_HOURLY_CENTS: int = 200000   # R2000.00 platform-default hourly-aggregate warning
    BLAST_RADIUS_OBSERVED_WINDOW_DAYS: int = 7     # rolling window the observed blast-radius figures cover

    def __repr__(self) -> str:  # T-01-01, T-01-02: never leak field values
        return f"Settings(LOG_LEVEL={self.LOG_LEVEL!r})"

    __str__ = __repr__


# Module-level singleton — imported by every module that needs config
settings = Settings()


# ---------------------------------------------------------------------------
# Pinned model identifiers (constants, deliberately NOT Settings fields)
# ---------------------------------------------------------------------------
# AGENT_TURN_MODEL is the model that serves a customer turn — the model an eval
# score is actually an assertion ABOUT. It is a constant rather than a Settings
# field because it is not an operational knob: changing it changes what every
# recorded score means, so it must move by code review and land in the eval
# run's configuration tuple (eval_runs.config.model_id, migration 0013), never
# by an environment variable that no run record would notice.
#
# Single source of truth for run_agent_turn's ClaudeAgentOptions(model=...),
# its Langfuse generation trace, and eval_service.build_eval_run_config. A
# second literal anywhere else is a drift bug: the score would be attributed to
# a model that did not produce it.
AGENT_TURN_MODEL = "claude-haiku-4-5-20251001"
