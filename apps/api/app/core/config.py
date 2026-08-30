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


# Where the calibration harness's answer is read from, resolved off this file the
# same way, so it does not move with the working directory either. This file sits
# at apps/api/app/core/config.py, so parents[2] is apps/api.
#
# The path is absolute and the file need not exist. `load_calibration_status`
# returns `no_artifact` when it is missing, which is a real and correct answer
# rather than a failure. `apps/api/.dockerignore` excludes `tests/`, so a
# container has no calibration directory at all, and no Judge has been shown
# calibrated there.
def _calibration_artifact_file() -> str:
    return str(
        Path(__file__).resolve().parents[2]
        / "tests"
        / "evals"
        / "calibration"
        / "calibration.json"
    )


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

    # Per-tenant Neon projects scale to zero. A suspended endpoint takes roughly
    # 8-20s to wake, so at the previous hardcoded 5s the FIRST message after
    # about five idle minutes could not reach a woken endpoint at all: psycopg2
    # spends this budget on each address the host resolves to, every one of them
    # timed out, and the turn died (observed on three live jobs, 2026-08-16).
    # 30s covers the wake. Note the per-address multiplication when tuning it:
    # a genuinely unreachable host costs this value once per resolved address.
    TENANT_DB_CONNECT_TIMEOUT_S: int = 30

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

    # Where `calibration_service.load_calibration_status` reads the harness's
    # answer from. The default points at the calibration directory beside the
    # harness that writes it (ticket #53, slice 2). Override it to move the
    # artifact somewhere a deployed process can read.
    CALIBRATION_ARTIFACT_PATH: str = _calibration_artifact_file()

    # Ingestion pipeline, M2 additions (T-02-01-01, keys suppressed by __repr__)
    #
    # REVOKED 2026-08-27, and required until that day. The owner revoked the
    # Anthropic and DeepSeek credentials once ADR 0008 put every model call on
    # OpenAI, so the name is gone from every env file in this repo. A required
    # field would now stop the API, both workers and the whole test suite at
    # import over a credential nothing on the customer path uses.
    #
    # Empty rather than deleted, because `resolve_credentials` still has an
    # Anthropic branch for the nine `messages` call sites #76 moves.
    #
    # WHERE AN EMPTY KEY ACTUALLY FAILS, measured against anthropic 0.101.0 on
    # 2026-08-27 rather than assumed. `Anthropic(api_key="")` constructs, and so
    # does `api_key=None`. The refusal comes at the CALL, as
    # `TypeError: Could not resolve authentication method`, raised while the SDK
    # resolves auth and therefore before any request leaves this machine. So a
    # revoked credential fails closed and silently costs nothing, but it fails
    # at the call site rather than where the client is built. Delete the field
    # when #76 lands.
    ANTHROPIC_API_KEY: str = ""
    # Decision #34 routes every direct-API purpose to OpenAI gpt-5.6-luna. The
    # default is empty rather than absent, and after #47 slice B it still is:
    # the five Judge purposes reach OpenAI through the instructor seam, and the
    # nine sites that send Anthropic-shaped `messages` bodies still resolve
    # Anthropic credentials, so a process that never scores an eval needs no
    # OpenAI key. A required field here would stop all of them at import.
    # `make_client` and `make_async_client` pass this value straight to the SDK,
    # which refuses an empty key at construction, so the failure lands where the
    # key is used. Make it required once those nine sites speak OpenAI too.
    OPENAI_API_KEY: str = ""
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
    # k (ticket 15, issue #52). How many times each of the seven vectors runs its
    # whole probe, from the top, with nothing carried between runs. It is NOT
    # RED_TEAM_ATTACK_SEQUENCES above, which is the shape of ONE conversational
    # attempt: three sequences inside one attacker loop under one shared
    # ATTACKER_LOOP_TIMEOUT_S budget, which the deterministic probes ignore
    # entirely. The two multiply, so a run costs k times what it cost before.
    RED_TEAM_ATTEMPTS_PER_VECTOR: int = 3

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

    # Local-development override for the S3 endpoint (E2E-2, BACKLOG 1.24).
    # None (default) => boto3 resolves real AWS exactly as it always has; no
    # deployed environment changes behaviour by this field existing.
    # Set to e.g. http://127.0.0.1:9000 to point document storage at a local
    # S3-compatible process (MinIO) so the ingestion chain can be exercised
    # without an AWS account.
    #
    # THIS IS A REDIRECT PRIMITIVE ON THE BOUNDARY THAT DECIDES WHERE CUSTOMER
    # DOCUMENTS ARE WRITTEN AND READ. It is therefore refused outright when
    # ENVIRONMENT == "production" — see storage_service._get_s3(), which raises
    # rather than warning or silently ignoring it. A production process
    # configured to send customer documents to a non-AWS endpoint must fail to
    # serve that path, not serve it quietly.
    S3_ENDPOINT_URL: str | None = None

    # The owned loop's per-turn USD ceiling (#48). Between model calls
    # `agent_loop._over_budget` prices this turn's `model_calls` rows against the
    # versioned book and stops the turn once the total reaches this number, so
    # what it guards is a runaway turn (T-04-03-06) and not a monthly bill.
    #
    # 0.50 was derived for a Haiku extended-thinking turn under ClaudeAgentOptions
    # and NOBODY HAS RE-DERIVED IT for gpt-5.6-luna at reasoning effort none.
    # Issue #82 carries that decision. The value stands until #82 lands.
    # Set AGENT_MAX_BUDGET_USD in .env to override, tighter in production.
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

    # The two hosts the embed snippet names (BACKLOG 7.1). The snippet is the
    # one artifact a customer pastes into their own site, so both halves of it
    # are deployment configuration, not code:
    #   WIDGET_CDN_BASE  where widget.js and its index.html are served from
    #   PUBLIC_API_BASE  the origin the loader's data-api points the widget at
    # PUBLIC_API_BASE defaults to the local uvicorn address because that is the
    # only value that is true on a fresh checkout.
    #
    # THE DEFAULT IS REFUSED WHEN ENVIRONMENT == "production" — see
    # deployment_service._make_iframe_snippet, which raises rather than emitting
    # a snippet, exactly as storage_service._get_s3() refuses S3_ENDPOINT_URL
    # there. The loader only warns when the API base is EMPTY
    # (apps/widget/embed/widget.js), so a non-empty localhost value is silent:
    # the snippet renders on the customer's site and every visitor's browser
    # calls its own machine, with nothing in the console to say so, and
    # http://localhost is a potentially-trustworthy origin so an https page does
    # not even mixed-content block it. A base that cannot be reached from a
    # visitor's browser must fail to issue a snippet, not issue a dead one.
    WIDGET_CDN_BASE: str = "https://widget.wchats.app"
    PUBLIC_API_BASE: str = "http://localhost:8000"

    def __repr__(self) -> str:  # T-01-01, T-01-02: never leak field values
        return f"Settings(LOG_LEVEL={self.LOG_LEVEL!r})"

    __str__ = __repr__


# Module-level singleton — imported by every module that needs config
settings = Settings()


# ---------------------------------------------------------------------------
# Pinned model identifiers (constants, deliberately NOT Settings fields)
# ---------------------------------------------------------------------------
# AGENT_TURN_MODEL names the model that serves a customer turn, and an eval score
# is an assertion about that model. It is a constant rather than a Settings field
# because it is not an operational knob. Changing it changes what every recorded
# score means, so it moves by code review and lands in the eval run's
# configuration tuple (eval_runs.config.model_id, migration 0013), never by an
# environment variable no run record would notice.
#
# Single source of truth for three readers. The agent-turn purpose route in
# app.core.model_client sends it on the wire, the Langfuse turn trace names it,
# and eval_service.build_eval_run_config stamps it on the run. A second literal
# anywhere else is a drift bug, because a score would then name a model that did
# not produce it.
AGENT_TURN_MODEL = "gpt-5.6-luna"
