"""Skip markers shared across test modules.

Kept out of conftest.py: pytest recommends against importing conftest
directly, since it can end up loaded twice under different module names.
"""
import os

import psycopg
import pytest

from app.core.config import get_settings


def _postgres_available() -> bool:
    """Best-effort reachability check, evaluated at collection time.

    Only confirms something answers with these credentials, not that
    migrations have been applied. `docker-compose.yaml` at the repo root
    brings up a plain `postgres` superuser; the full pre-migrations ->
    Alembic -> post-migrations sequence (see `db/README.md`) still has to be
    run against it for DB-backed tests to pass.
    """
    settings = get_settings()
    try:
        conn = psycopg.connect(
            host=settings.MEMORY_DB_HOST,
            port=settings.MEMORY_DB_PORT,
            dbname=settings.MEMORY_DB_NAME,
            user=settings.MEMORY_DB_USER,
            password=settings.MEMORY_DB_PASSWORD,
            connect_timeout=2,
        )
    except psycopg.Error:
        return False
    conn.close()
    return True


requires_postgres = pytest.mark.skipif(
    not _postgres_available(),
    reason="Postgres not reachable at MEMORY_DB_* settings - skipping DB-backed test",
)

requires_openai_key = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set - skipping live Dynamiq Agent call",
)
