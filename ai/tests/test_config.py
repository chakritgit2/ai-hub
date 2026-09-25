import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_memory_db_fields_derived_from_database_url() -> None:
    settings = Settings(_env_file=None, DATABASE_URL="postgresql+asyncpg://u1:p1@db.example:6543/dbx")
    assert (settings.MEMORY_DB_HOST, settings.MEMORY_DB_PORT) == ("db.example", 6543)
    assert (settings.MEMORY_DB_NAME, settings.MEMORY_DB_USER, settings.MEMORY_DB_PASSWORD) == ("dbx", "u1", "p1")


def test_explicit_memory_db_field_wins() -> None:
    settings = Settings(_env_file=None, MEMORY_DB_HOST="memory-host")
    assert settings.MEMORY_DB_HOST == "memory-host"


def test_production_rejects_dev_defaults() -> None:
    with pytest.raises(ValidationError, match="HASH_PEPPER"):
        Settings(_env_file=None, ENVIRONMENT="production")


def test_production_accepts_real_secrets() -> None:
    settings = Settings(
        _env_file=None,
        ENVIRONMENT="production",
        HASH_PEPPER="a-real-pepper",
        DATABASE_URL="postgresql+asyncpg://ai_app:s3cret@db:5432/ai_console",
    )
    assert settings.MEMORY_DB_PASSWORD == "s3cret"
