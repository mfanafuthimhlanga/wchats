"""
Application configuration loaded from environment variables.

T-01-01: NEON_ENCRYPTION_KEY read from env only; Settings.__repr__ is suppressed
          to prevent accidental value leakage in logs.
T-01-02: CONTROL_DB_SYNC_URL read from env only; same repr suppression applies.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Neon
    NEON_API_KEY: str
    NEON_REGION: str = "aws-us-east-1"
    # Base64url-encoded 32 bytes; kept as str because Fernet accepts str
    NEON_ENCRYPTION_KEY: str

    # Database
    # postgresql+asyncpg:// — for FastAPI async engine
    CONTROL_DB_URL: str
    # postgresql:// — for Celery sync engine and Alembic CLI
    CONTROL_DB_SYNC_URL: str

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Observability
    LOG_LEVEL: str = "INFO"
    SENTRY_DSN: str | None = None

    # Auth
    ADMIN_KEY: str  # for X-Admin-Key on POST /tenants

    # CORS — locked to known origins; widget CORS added in M4 only (T-04-06)
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    def __repr__(self) -> str:  # T-01-01, T-01-02: never leak field values
        return f"Settings(LOG_LEVEL={self.LOG_LEVEL!r})"

    __str__ = __repr__


# Module-level singleton — imported by every module that needs config
settings = Settings()
