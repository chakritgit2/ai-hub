import base64
import os

import pytest
from pydantic import ValidationError

from app.core.config import Settings

_VALID_MASTER_KEY = base64.b64encode(os.urandom(32)).decode()


def test_memory_db_fields_derived_from_database_url() -> None:
    settings = Settings(_env_file=None, DATABASE_URL="postgresql+asyncpg://u1:p1@db.example:6543/dbx")
    assert (settings.MEMORY_DB_HOST, settings.MEMORY_DB_PORT) == ("db.example", 6543)
    assert (settings.MEMORY_DB_NAME, settings.MEMORY_DB_USER, settings.MEMORY_DB_PASSWORD) == ("dbx", "u1", "p1")


def test_explicit_memory_db_field_wins() -> None:
    settings = Settings(_env_file=None, MEMORY_DB_HOST="memory-host")
    assert settings.MEMORY_DB_HOST == "memory-host"


def test_production_rejects_dev_defaults() -> None:
    with pytest.raises(ValidationError, match="HASH_PEPPER"):
        Settings(_env_file=None, ENVIRONMENT="production", CONSOLE_MASTER_KEY=_VALID_MASTER_KEY)


def test_production_accepts_real_secrets() -> None:
    settings = Settings(
        _env_file=None,
        ENVIRONMENT="production",
        HASH_PEPPER="a-real-pepper",
        DATABASE_URL="postgresql+asyncpg://ai_app:s3cret@db:5432/ai_console",
        CONSOLE_MASTER_KEY=_VALID_MASTER_KEY,
    )
    assert settings.MEMORY_DB_PASSWORD == "s3cret"


def test_production_rejects_missing_master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # `_env_file=None` only skips pydantic-settings' own .env parsing - `uv run` has
    # already loaded ai/.env into the real process environment by the time this runs, so
    # CONSOLE_MASTER_KEY must be cleared there too for this "missing" case to be real.
    monkeypatch.delenv("CONSOLE_MASTER_KEY", raising=False)
    with pytest.raises(ValidationError, match="CONSOLE_MASTER_KEY"):
        Settings(
            _env_file=None,
            ENVIRONMENT="production",
            HASH_PEPPER="a-real-pepper",
            DATABASE_URL="postgresql+asyncpg://ai_app:s3cret@db:5432/ai_console",
        )


@pytest.mark.parametrize("bad_key", ["not-valid-base64!!!", base64.b64encode(b"too-short").decode()])
def test_production_rejects_malformed_master_key(bad_key: str) -> None:
    with pytest.raises(ValidationError, match="CONSOLE_MASTER_KEY"):
        Settings(
            _env_file=None,
            ENVIRONMENT="production",
            HASH_PEPPER="a-real-pepper",
            DATABASE_URL="postgresql+asyncpg://ai_app:s3cret@db:5432/ai_console",
            CONSOLE_MASTER_KEY=bad_key,
        )


def test_development_allows_missing_master_key() -> None:
    settings = Settings(_env_file=None, CONSOLE_MASTER_KEY=None)
    assert settings.CONSOLE_MASTER_KEY is None


def test_development_still_rejects_malformed_master_key_if_present() -> None:
    with pytest.raises(ValidationError, match="CONSOLE_MASTER_KEY"):
        Settings(_env_file=None, CONSOLE_MASTER_KEY="not-valid-base64!!!")
