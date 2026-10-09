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

    # Comma-separated IPs of reverse proxies/load balancers the gateway sits behind.
    # `allowed_ips` enforcement (PRD §7.7) only trusts the `X-Forwarded-For` header's
    # original-client entry when the direct TCP peer is one of these - otherwise any
    # caller could set that header itself to spoof its way past the allowlist, and with
    # this left empty (the default) `request.client.host` is used as-is.
    TRUSTED_PROXY_IPS: str = ""

    # --- Envelope encryption (PRD §7.7) ---
    # Base64-encoded raw AES key bytes (32 bytes -> AES-256, matching app/core/crypto.py's
    # AESGCM usage). Generate with:
    #   python -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"
    # Required outside ENVIRONMENT=development (see _derive_and_check below) - optional in
    # development so a local setup that never touches Connections doesn't need one, but if
    # present even in development it must still be well-formed (fail fast at startup,
    # never silently wrap/unwrap garbage at request time).
    CONSOLE_MASTER_KEY: str | None = None

    # --- Runtime limits (PRD §6.2) ---
    MAX_CONCURRENT_RUNS: int = 16
    DEFAULT_MAX_LOOPS: int = 8
    MAX_MAX_LOOPS: int = 20
    RUN_TIMEOUT_SECONDS: int = 120
    CONVERSATION_TTL_DAYS: int = 30

    # --- Object storage (PRD §6.6/§7.7: OKF files on disk under kb/{company_id}/{kb_id}/...)
    # - a plain local/PVC-mounted directory, not an object store (no licensing/cost concern,
    # no extra service to run) - see app.core.storage for the read/write/delete API.
    KB_STORAGE_ROOT: str = ".data/kb-storage"

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
            _decode_master_key(self.CONSOLE_MASTER_KEY, required=True)
        elif self.CONSOLE_MASTER_KEY is not None:
            _decode_master_key(self.CONSOLE_MASTER_KEY, required=False)

        return self

    def trusted_proxy_ips(self) -> set[str]:
        return {ip.strip() for ip in self.TRUSTED_PROXY_IPS.split(",") if ip.strip()}


def _decode_master_key(value: str | None, *, required: bool) -> bytes | None:
    """Shared by Settings validation (fail fast at startup) and master_key_bytes()
    (the actual decode used at encryption time) - see app/services/company_keys.py."""
    if value is None:
        if required:
            raise ValueError("CONSOLE_MASTER_KEY must be set outside development (PRD §7.7)")
        return None

    import base64
    import binascii

    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("CONSOLE_MASTER_KEY must be valid base64") from exc

    if len(decoded) not in (16, 24, 32):
        raise ValueError(
            f"CONSOLE_MASTER_KEY must decode to 16, 24 or 32 bytes (AES-128/192/256), got {len(decoded)}"
        )

    return decoded


def master_key_bytes() -> bytes:
    """Decoded CONSOLE_MASTER_KEY, for first-use-time call sites
    (app/services/company_keys.py) rather than Settings-construction time - lets
    development environments construct Settings() with no key at all until something
    actually needs encryption (Settings itself already validated the format if one is
    present, per _derive_and_check above, so this only re-raises the "not set" case)."""
    settings = get_settings()
    decoded = _decode_master_key(settings.CONSOLE_MASTER_KEY, required=True)
    assert decoded is not None  # required=True always either returns bytes or raises
    return decoded


@lru_cache
def get_settings() -> Settings:
    return Settings()
