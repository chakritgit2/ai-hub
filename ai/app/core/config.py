"""Application settings, read from the environment (and `.env` in local dev).

PRD refs: §6.2 (runtime limits), §7.1/§7.6 (auth), §7.7 (envelope encryption).
"""
from functools import lru_cache
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

_DEV_HASH_PEPPER = "changeme_local_dev_only"
_DEV_DB_PASSWORD = "changeme"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # --- Database / Redis ---
    DATABASE_URL: str = "postgresql+asyncpg://ai_app:changeme@localhost:5432/ai_console"
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Dynamiq memory backend (PRD §6.2) ---
    # Dynamiq's `dynamiq.connections.PostgreSQL` opens its own synchronous psycopg
    # connection and takes discrete host/port/etc fields, not a DSN. Each one left
    # unset is filled from DATABASE_URL (see _derive_and_check), so memory lands in
    # the same database as runtime.conversations unless deliberately overridden.
    MEMORY_DB_HOST: str | None = None
    MEMORY_DB_PORT: int | None = None
    MEMORY_DB_NAME: str | None = None
    MEMORY_DB_USER: str | None = None
    MEMORY_DB_PASSWORD: str | None = None

    # HMAC pepper for hashing end-user identifiers (app/core/hashing.py) before
    # they're ever persisted (PRD §6.2/§7.5). Rotating this makes every
    # existing runtime.agent_memory/runtime.conversations row for every
    # company unreachable by its original external_user_id - treat rotation
    # as a deliberate, disruptive action, not a routine one.
    HASH_PEPPER: str = _DEV_HASH_PEPPER

    # --- LLM providers ---
    OPENAI_API_KEY: str | None = None
    OPENAI_URL: str = "https://api.openai.com/v1"

    # --- Auth (PRD §7.1/§7.6) ---
    CONSOLE_INTERNAL_JWT_AUD: str = "ai-internal"
    CONSOLE_INTERNAL_JWT_ISSUER: str = "console-api"
    CONSOLE_JWKS_URL: str = "http://console-api.internal/admin/.well-known/jwks.json"
    RUNTIME_TOKEN_AUD: str = "ai-runtime"
    GATEWAY_API_KEY_PREFIX: str = "ak_"
    GATEWAY_JWKS_PRIVATE_KEY_PATH: str | None = None
    GATEWAY_JWKS_KID: str = "gw-2026-09"

    # --- Envelope encryption (PRD §7.7) ---
    CONSOLE_MASTER_KEY: str | None = None

    # --- Runtime limits (PRD §6.2) ---
    MAX_CONCURRENT_RUNS: int = 16
    DEFAULT_MAX_LOOPS: int = 8
    MAX_MAX_LOOPS: int = 20
    RUN_TIMEOUT_SECONDS: int = 120
    CONVERSATION_TTL_DAYS: int = 30

    # --- Observability ---
    OTEL_EXPORTER_OTLP_ENDPOINT: str | None = None

    @model_validator(mode="after")
    def _derive_and_check(self) -> Self:
        url = make_url(self.DATABASE_URL)
        self.MEMORY_DB_HOST = self.MEMORY_DB_HOST or url.host or "localhost"
        self.MEMORY_DB_PORT = self.MEMORY_DB_PORT or url.port or 5432
        self.MEMORY_DB_NAME = self.MEMORY_DB_NAME or url.database
        self.MEMORY_DB_USER = self.MEMORY_DB_USER or url.username
        self.MEMORY_DB_PASSWORD = self.MEMORY_DB_PASSWORD or url.password

        if self.ENVIRONMENT != "development":
            insecure = []
            if self.HASH_PEPPER == _DEV_HASH_PEPPER:
                insecure.append("HASH_PEPPER")
            if self.MEMORY_DB_PASSWORD == _DEV_DB_PASSWORD:
                insecure.append("MEMORY_DB_PASSWORD (or the password in DATABASE_URL)")
            if insecure:
                raise ValueError(
                    f"ENVIRONMENT={self.ENVIRONMENT!r} but still using local-dev defaults for: "
                    + ", ".join(insecure)
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
