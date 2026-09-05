"""
Application configuration loaded from environment variables.

T-01-01: NEON_ENCRYPTION_KEY read from env only; Settings.__repr__ is suppressed
          to prevent accidental value leakage in logs.
T-01-02: CONTROL_DB_SYNC_URL read from env only; same repr suppression applies.
"""

from pathlib import Path

from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import ArgumentError


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
    return str(Path(__file__).resolve().parents[2] / "tests" / "evals" / "calibration" / "calibration.json")


# The two control engines take different drivers, and pasting one into the other's
# variable is a live staging failure mode. asyncpg for the FastAPI async engine;
# psycopg for the Celery/Alembic sync engine (scripts/probe_environment.py already
# treats postgresql+psycopg as a sync form, so it is accepted here too).
_CONTROL_DSN_DRIVERS: dict[str, tuple[str, ...]] = {
    "CONTROL_DB_URL": ("postgresql+asyncpg",),
    "CONTROL_DB_SYNC_URL": ("postgresql", "postgresql+psycopg2", "postgresql+psycopg"),
}

# Measured 2026-09-01 against the installed drivers: asyncpg 0.31.0 accepts ssl= and
# raises TypeError on sslmode=/channel_binding=; psycopg2 2.9.12 is the mirror image,
# accepting those two and refusing ssl= as an invalid dsn option. SQLAlchemy passes
# query params through untranslated, so the wrong one parses, boots, then kills every
# connection. Neon's copied string suits the sync URL; the async URL needs rewriting.
_CONTROL_DSN_REJECTED_QUERY: dict[str, tuple[str, ...]] = {
    "CONTROL_DB_URL": ("sslmode", "channel_binding"),
    "CONTROL_DB_SYNC_URL": ("ssl",),
}


def _refuse_unusable_dsn_text(field: str, value: str, expected: tuple[str, ...]):
    """Reject a control DSN on the text itself, then hand back the parsed URL.

    make_url raises ValueError, not ArgumentError, on a non-numeric port, so both
    are caught. A line break is checked separately because make_url ACCEPTS one,
    folding the remainder into the database name rather than failing.
    """
    if len(value.splitlines()) > 1:
        raise ValueError(
            f"{field} contains a line break. SQLAlchemy parses the first line and "
            f"folds the rest into the database name instead of failing, so paste "
            f"the DSN as a single line."
        )
    if value != value.strip():
        raise ValueError(
            f"{field} has leading or trailing whitespace. Paste the DSN as one "
            f"bare line, with no surrounding space or newline."
        )
    try:
        return make_url(value)
    except (ArgumentError, ValueError):
        if "://" in value:
            detail = f"it begins {value.split('://', 1)[0][:40]!r}"
        else:
            detail = f"it has no '://' at all ({len(value)} characters)"
        raise ValueError(
            f"{field} is not a parseable database URL: {detail}. Expected one "
            f"bare line like '{expected[0]}://USER:PASSWORD@HOST/DBNAME'. A "
            f"wrapping quote, a 'KEY=' prefix, a 'psql ...' wrapper or an "
            f"unresolved '${{{{Service.VAR}}}}' reference all do this."
        ) from None


def _refuse_wrong_engine(field: str, value: str, url, expected: tuple[str, ...]) -> None:
    """Reject a parsed control DSN aimed at the other engine.

    All three of these PARSE. A missing host and the other engine's ssl query
    param both survive to connect time, costing a live request instead of a boot.
    """
    if not url.host:
        raise ValueError(
            f"{field} has no host: {value.split('://', 1)[0][:40]!r} followed by "
            f"nothing usable. Expected "
            f"'{expected[0]}://USER:PASSWORD@HOST/DBNAME'."
        )
    if url.drivername not in expected:
        raise ValueError(
            f"{field} uses driver {url.drivername!r}, which belongs to the other "
            f"control engine. Expected one of {list(expected)}."
        )
    rejected = sorted(set(url.query) & set(_CONTROL_DSN_REJECTED_QUERY[field]))
    if rejected:
        wanted = "ssl=require" if field == "CONTROL_DB_URL" else "sslmode=require"
        raise ValueError(
            f"{field} carries {rejected} in its query string, which its driver "
            f"({url.drivername}) refuses at connect time. Use {wanted} instead."
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
    model_config = SettingsConfigDict(env_file=_find_env_file(), extra="ignore", hide_input_in_errors=True)

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

    @field_validator("ENVIRONMENT")
    @classmethod
    def _environment_is_a_known_word(cls, value: str) -> str:
        # Four fail-open guards key off the exact string "production": the
        # storage endpoint allowlist, the /docs and /redoc routes, the embed
        # snippet's loopback refusal, and the session-token error redaction.
        # A typo ("Production", "prod", a trailing space) would disable all
        # four silently, so an unknown word refuses to boot instead.
        known = ("development", "test", "staging", "production")
        if value not in known:
            raise ValueError(
                f"ENVIRONMENT={value!r} is not one of {', '.join(known)}. "
                "A typo here silently disables every production-only guard."
            )
        return value

    @field_validator("CONTROL_DB_URL", "CONTROL_DB_SYNC_URL")
    @classmethod
    def _control_dsn_is_a_bare_url(cls, value: str, info: ValidationInfo) -> str:
        """Refuse a control DSN the engine cannot use, naming the field.

        Without this the first thing to touch the value is create_async_engine at
        import time, and its ArgumentError names neither the variable nor the fault,
        so a mispasted Railway value costs a crash-loop and a dashboard hunt.
        model_config sets hide_input_in_errors=True, so the message has to carry the
        diagnosis itself. Credentials sit after "://", so the prefix is safe to echo.
        """
        field = info.field_name or "CONTROL_DB_URL"
        expected = _CONTROL_DSN_DRIVERS[field]
        url = _refuse_unusable_dsn_text(field, value, expected)
        _refuse_wrong_engine(field, value, url, expected)
        return value

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
    VERIFIED_SESSION_TTL_SECONDS: int = 3600  # OD-4: 1 hour verified-session lifetime
    OTP_EMAIL_TTL_SECONDS: int = 600  # 10 min email OTP window
    OTP_SMS_TTL_SECONDS: int = 300  # 5 min SMS OTP window
    OTP_MAX_ATTEMPTS: int = 5  # max verify attempts before challenge expires
    OTP_SEND_MAX_PER_WINDOW: int = 3  # max sends per external_id per 10-min window

    # M17: SMS OTP provider — OD-2 Twilio default; NullSmsProvider used when creds unset
    # All credentials default to None (fail-safe: unset = SMS not configured).
    SMS_PROVIDER: str = "twilio"  # "twilio" | "africastalking"
    TWILIO_ACCOUNT_SID: str | None = None
    TWILIO_AUTH_TOKEN: str | None = None
    TWILIO_FROM_NUMBER: str | None = None  # E.164 format, e.g. +27XXXXXXXXX
    AT_API_KEY: str | None = None  # Africa's Talking API key
    AT_USERNAME: str | None = None  # Africa's Talking username
    AT_SENDER_ID: str | None = None  # Africa's Talking sender ID (optional)

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
    RED_TEAM_MAX_TURNS: int = 5  # max turns per attack sequence per agent
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

    # THE CHECKLIST SEQUENCES THE TWO JOBS IT GRADES (#54, decision 19 rule 5).
    # It dispatches an eval chain and a red-team run, then waits for both to
    # reach a terminal status before it reads a single signal, so every summary
    # in the report describes the run this checklist started. The first checklist
    # ever run read eval_signal=no_runs seconds after starting the eval it was
    # asking about; that shape is what these two numbers close.
    #
    # THE CHECKLIST DOES NOT HOLD A WORKER SLOT WHILE IT WAITS, and that is what
    # lets this number be large enough to be useful (#54 review). It used to sleep
    # inside the task on the same `runtime` queue as the two jobs it had just
    # dispatched; on the documented local topology — one worker, `-Q
    # pipeline,runtime`, solo pool, so one execution slot — those jobs could not
    # start until the checklist returned, and the wait could never be satisfied.
    # The task now polls once, re-queues itself with CHECKLIST_WAIT_POLL_S of
    # countdown and returns, so the slot is free between looks.
    #
    # 2700s is 45 minutes, which covers the red-team k=3 bound rather than
    # sitting below it. At 1500s a full red-team run was EXPECTED to outlast the
    # ceiling, so the report's security half was routinely decided on a job that
    # never finished.
    #
    # It no longer has to stay under a 60-minute idempotency window, because
    # there is not one: #129 keyed the checklist's guard on a live chain's
    # heartbeat instead of a row's age, and deployment._stale_after_s derives how
    # long a chain may go quiet from this ceiling and the two job bounds. Raising
    # this raises that with it.
    #
    # A half that does not reach terminal inside this still reads as an ABSENT
    # record and blocks. The ceiling bounds how long the platform is willing to
    # wait; it never decides what the report may claim.
    CHECKLIST_WAIT_CEILING_S: int = 2700

    # The countdown between one poll of the tenant DB and the next. Each poll
    # opens one short psycopg2 connection per job still in flight and costs one
    # Celery message, so at the 2700s ceiling the worst case is 270 polls.
    # Lowering it buys latency at the cost of connections against a per-tenant
    # Neon project and messages on the broker.
    CHECKLIST_WAIT_POLL_S: int = 10

    # M10: Maintenance + Observability thresholds and flags
    ALERT_FAITHFULNESS_THRESHOLD: float = 0.6
    ALERT_RED_TEAM_CRITICAL_COUNT: int = 1
    DIGEST_ENABLED: bool = True

    MAX_UPLOAD_SIZE_MB: int = 50

    # Parse a bundled one-page PDF once, when the pipeline worker reports ready
    # (#24). Docling's DocLayNet and TableFormer models load on the FIRST
    # conversion, not when the converter is built, and that load was measured at
    # 3m43s before a 500-byte file produced its first detection. Somebody pays
    # it either way; the choice is the worker at boot, where nobody is waiting
    # and the deploy log records the number, or whichever upload happens to
    # arrive first after a deploy, where the owner watches `parsing.started`
    # with no way to tell a model load from a hung worker.
    #
    # True everywhere a pipeline worker runs for real. tests/conftest.py sets it
    # false, because a test process that touched this would load two gigabytes
    # of model weights to assert on a log line.
    DOCLING_WARMUP_ON_BOOT: bool = True

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

    # S3-compatible credentials, passed EXPLICITLY to boto3 (see
    # storage_service._get_s3). They are named for the protocol, not the vendor,
    # because the deployed store is Cloudflare R2 and the AWS_* names invited a
    # standing misreading that this project runs on AWS. Nothing reads boto3's
    # default credential chain any more: an unset credential is refused by name
    # instead of failing inside the SDK on the first upload.
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""

    # Local-development override for the S3 endpoint (E2E-2, BACKLOG 1.24).
    # None (default) => boto3 resolves real AWS exactly as it always has; no
    # deployed environment changes behaviour by this field existing.
    # Set to e.g. http://127.0.0.1:9000 to point document storage at a local
    # S3-compatible process (MinIO) so the ingestion chain can be exercised
    # without an AWS account.
    #
    # THIS DECIDES WHERE CUSTOMER DOCUMENTS ARE WRITTEN AND READ. In
    # production, storage_service._require_production_endpoint honours it for
    # exactly the object stores decision #14 names (Cloudflare R2, Backblaze
    # B2) and raises for every other host, so a process configured to send
    # customer documents somewhere unvetted fails to serve that path rather
    # than serving it quietly. Everywhere else it is the local-dev seam
    # (MinIO) it always was.
    S3_ENDPOINT_URL: str | None = None

    # The ONE object-store host a production process may write customer
    # documents to (#133), as a bare hostname:
    #   S3_EXPECTED_ENDPOINT_HOST=8f2c1d....r2.cloudflarestorage.com
    #
    # S3_ENDPOINT_URL above bounds the PROVIDER. Every R2 tenant on earth
    # carries `.r2.cloudflarestorage.com` and every B2 tenant carries
    # `.backblazeb2.com`, so the suffix list admits any other customer's
    # account as readily as ours: a mistyped or hostile endpoint sends every
    # uploaded document into a bucket somebody else holds the keys to, with
    # the guard's blessing. This field is the account bound, and
    # storage_service._require_production_endpoint compares the parsed
    # hostname against it for equality.
    #
    # Empty is the local-development value, where S3_ENDPOINT_URL points at
    # MinIO and no owner account exists. In production empty refuses to boot,
    # because a process that was never told which account is ours must not
    # reach the point where it can write a document to one.
    #
    # Under ENVIRONMENT=staging no host check applies at all, in this validator
    # or in storage_service, and railway_staging_wizard.sh sets `production` on
    # Railway's staging environment for exactly that reason: the staging deploy
    # is where these guards are meant to be armed and observed.
    S3_EXPECTED_ENDPOINT_HOST: str = ""

    @field_validator("S3_EXPECTED_ENDPOINT_HOST")
    @classmethod
    def _expected_endpoint_host_is_a_bare_host(
        cls, value: str, info: ValidationInfo
    ) -> str:
        """Normalise the host, and require one in production.

        The operator reaches this variable with the endpoint URL already in the
        clipboard, so a pasted URL is the shape to expect and the one to refuse
        by name. A URL would never equal a parsed hostname, and the resulting
        failure would name S3_ENDPOINT_URL on every upload while the fault sat
        in this field.

        A port and an embedded space are that same failure in shapes the scheme
        and path checks let through: `_require_production_endpoint` compares
        against `urlsplit(...).hostname`, which carries neither, so
        `<id>.r2.cloudflarestorage.com:443` booted green and then refused every
        upload. Boot is where the operator is still reading the deploy log.
        """
        host = value.strip().lower()
        if "://" in host or "/" in host or "@" in host:
            raise ValueError(
                "S3_EXPECTED_ENDPOINT_HOST is a bare hostname, not a URL. Give "
                "the host on its own, as in "
                "'8f2c1d.r2.cloudflarestorage.com', with no scheme, no path "
                "and no credentials."
            )
        if ":" in host:
            raise ValueError(
                "S3_EXPECTED_ENDPOINT_HOST carries a port. The endpoint check "
                "compares it against the parsed hostname, which never has one, "
                "so this value would refuse every upload. Give the host alone, "
                "as in '8f2c1d.r2.cloudflarestorage.com'."
            )
        if any(character.isspace() for character in host):
            raise ValueError(
                "S3_EXPECTED_ENDPOINT_HOST contains whitespace inside the host. "
                "A hostname has none, so this value would refuse every upload. "
                "Give the host alone, as in '8f2c1d.r2.cloudflarestorage.com'."
            )
        if not host and info.data.get("ENVIRONMENT") == "production":
            raise ValueError(
                "S3_EXPECTED_ENDPOINT_HOST is unset while ENVIRONMENT=production. "
                "It names the one object-store host this deployment may write "
                "customer documents to. Without it the endpoint check bounds the "
                "provider and not the account, so any R2 or B2 bucket would pass. "
                "Set it to the host inside S3_ENDPOINT_URL."
            )
        return host

    # The owned loop's per-turn USD ceiling (#48). Between model calls
    # `agent_loop._over_budget` prices this turn's `model_calls` rows against the
    # versioned book and stops the turn once the total reaches this number, so
    # what it guards is a runaway turn (T-04-03-06) and not a monthly bill.
    #
    # 0.40 IS TWICE THE WORST TURN THE CONFIGURED LIMITS PERMIT, measured
    # 2026-09-05 by driving the real `run_agent_loop` and pricing every ModelCall
    # row through app.domain.pricing, exactly as `_over_budget` does. #182 carries
    # the derivation; #82 asked for it. Two earlier numbers, 0.0038 and 0.04, both
    # cut ordinary turns off with an empty answer and were reverted, because both
    # were measured against a turn smaller than the configuration allows.
    #
    # THE ARITHMETIC. The worst turn is MAX_MODEL_CALLS_PER_TURN model calls, each
    # re-sending everything before it, carrying all of:
    #
    #   agent_tools._RETRIEVE_CALLS_PER_TURN_MAX   8 retrieves, FRONT-LOADED into
    #                                              call 1 so all eight ride on
    #                                              calls 2 to 6
    #   agent_tools.MAX_CHUNKS                     5 chunks per retrieve
    #   agent_tools.CHUNK_CONTENT_CHAR_LIMIT       2000 characters per chunk
    #   agent.TURN_HISTORY_MAX_MESSAGES            40 rows, on every call
    #   agent.TURN_HISTORY_MAX_ROW_CHARS           4000 characters per row
    #   agent_prompt.SYSTEM_PROMPT_MAX_CHARS       the soul at both list caps
    #   the eleven tool schemas                    on every call
    #
    # priced at CJK, the densest content the product can carry (1.37 characters
    # per token against English prose's 5.67), and billed with tiktoken o200k_base
    # over the real request body rather than at characters over four:
    #
    #   input tokens per call   127,749 / 191,699 / 194,696 / 197,693 / 200,690 / 203,687
    #   whole turn              $0.244260
    #   through call 5          $0.200019   <- what the guard compares against
    #
    # 0.40 is 2.00 times that $0.200019 and 1.64 times the whole turn.
    #
    # THE CHECK READS THE PREVIOUS CALL. `_over_budget` runs at the TOP of a call
    # against rows recorded through the one before it, so the effective ceiling is
    # this number plus one full call (#82, measured live 2026-08-27). That is why
    # the headroom is stated over the through-call-5 figure and not the whole turn.
    #
    # EVERY CONSTANT NAMED ABOVE MAKES THIS NUMBER STALE WHEN IT MOVES, and so
    # does the price book. tests/unit/test_turn_budget_ceiling.py drives both
    # directions against this constant: the worst permitted turn has to reach its
    # answer, the fixture has to still cost what the derivation says, and a
    # context beyond every configured limit has to be stopped.
    #
    # ONE GLOBAL CONSTANT CANNOT BE TIGHT FOR EVERY TENANT. Derived at CJK, this
    # ceiling sits roughly four times above an English tenant's own worst turn.
    # Derived at English prose it would cut every Chinese turn off mid-answer,
    # which is what the reverted numbers did. A per-tenant or per-token bound is
    # the real fix; this is the honest setting for one number.
    #
    # NOT BOUNDED, ASSUMED: the loop sends no `max_tokens`, so the OUTPUT side of
    # a turn is bounded only by the provider default. The measurement prices
    # output at TURN_HISTORY_MAX_ROW_CHARS of the densest content per call, which
    # is what the product treats as a maximal assistant message.
    #
    # The measurement live against gpt-5.6-luna on 2026-08-27 with the owner's key
    # (#82) still stands beside this: a two-call grounded turn with retrieval ran
    # $0.000114 to $0.000227, and a turn under a $0.00002 ceiling stopped after
    # one call with stop_reason='budget_exceeded', priced from real ModelCall rows
    # through the real book. The mechanism works; the constant was the problem.
    #
    # The same run closed this field's other half. The response reports
    # `gpt-5.6-luna`, the alias, not a dated snapshot, so cost_usd raises no
    # UnknownPrice here today. What happens WHEN it does is #178's: `_over_budget`
    # catches UnknownPrice and returns False, so one unpriced model id turns this
    # ceiling off for the whole turn.
    #
    # Set AGENT_MAX_BUDGET_USD in .env to override, tighter in production.
    AGENT_MAX_BUDGET_USD: float = 0.40

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
    BLAST_RADIUS_WARN_SINGLE_CENTS: int = 50000  # R500.00 platform-default single-action warning
    BLAST_RADIUS_WARN_HOURLY_CENTS: int = 200000  # R2000.00 platform-default hourly-aggregate warning
    BLAST_RADIUS_OBSERVED_WINDOW_DAYS: int = 7  # rolling window the observed blast-radius figures cover

    # The two hosts the embed snippet names (BACKLOG 7.1). The snippet is the
    # one artifact a customer pastes into their own site, so both halves of it
    # are deployment configuration, not code:
    #   WIDGET_CDN_BASE  where widget.js and its index.html are served from.
    #                    Empty (the default) means "this API serves them at
    #                    /wchats": the api image carries the synced bundle
    #                    (apps/api/static/wchats, SHA-gated by
    #                    apps/widget/scripts/sync-embed.mjs) and the snippet
    #                    derives PUBLIC_API_BASE + "/wchats". Set it only when
    #                    a real CDN fronts the bundle, and then add that
    #                    origin to CORS_ORIGINS too: the iframe's runtime
    #                    calls to the API become cross-origin the moment the
    #                    page loads from the CDN. #135: the CloudFront
    #                    origin went with ADR 0005, and the old default,
    #                    https://widget.wchats.app, named a host nothing
    #                    served, so every emitted snippet was dead on arrival.
    #   PUBLIC_API_BASE  the origin the loader's data-api points the widget at.
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
    WIDGET_CDN_BASE: str = ""
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
