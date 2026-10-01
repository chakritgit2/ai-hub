import uuid

import psycopg
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main_gateway import app as gateway_app
from app.main_runtime import app as runtime_app
from app.main_runtime import require_internal_auth, require_runtime_auth

from .markers import fake_internal_claims, fake_runtime_claims


@pytest.fixture
def runtime_client(request) -> TestClient:
    """`request.param`, if set (via `@pytest.mark.parametrize` or
    `pytest.fixture(params=...)`), is a company_id to bake into the fake verified
    runtime-token claims (see markers.fake_runtime_claims); otherwise company_id
    defaults to "" (no memory). Also bypasses the internal-call auth (markers.
    fake_internal_claims) so existing stub-wiring tests don't all need their own
    internal token — tests that care about enforcement use a bare TestClient instead."""
    company_id = getattr(request, "param", "")
    runtime_app.dependency_overrides[require_runtime_auth] = fake_runtime_claims(company_id)
    runtime_app.dependency_overrides[require_internal_auth] = fake_internal_claims()
    try:
        yield TestClient(runtime_app)
    finally:
        runtime_app.dependency_overrides.pop(require_runtime_auth, None)
        runtime_app.dependency_overrides.pop(require_internal_auth, None)


@pytest.fixture
def gateway_client() -> TestClient:
    return TestClient(gateway_app)


@pytest.fixture(autouse=True)
async def _fresh_engine():
    """`get_engine` is lru_cached, but each async test runs on its own event loop and
    asyncpg connections are bound to the loop that opened them - rebuild the engine per
    test and dispose it on the same loop afterwards."""
    from app.core.db import get_engine, get_sessionmaker

    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    yield
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


@pytest.fixture
async def company_ids():
    """Hands out fresh company ids and, after the test, deletes every
    runtime.conversations / runtime.agent_memory row created under them.

    Only touches the DB during teardown, and only if an id was handed out,
    so tests that never use it don't need Postgres.
    """
    from app.core.db import get_company_session
    from app.db.tables import conversations_table

    issued: list[str] = []

    def new_company_id() -> str:
        company_id = str(uuid.uuid4())
        issued.append(company_id)
        return company_id

    yield new_company_id

    for company_id in issued:
        async with get_company_session(company_id) as session:
            result = await session.execute(
                conversations_table.delete()
                .where(conversations_table.c.company_id == uuid.UUID(company_id))
                .returning(conversations_table.c.id)
            )
            session_ids = [str(row[0]) for row in result.fetchall()]
            if session_ids:
                await session.execute(
                    sa.text("DELETE FROM runtime.agent_memory WHERE metadata ->> 'session_id' = ANY(:ids)"),
                    {"ids": session_ids},
                )


@pytest.fixture
def make_connection():
    """Inserts a `console.connections` row as `console_app` (console-api's own write
    role — `ai_app` has no write grant on this table by design, PRD §8.1) and deletes it
    afterward. Local-dev-only credentials, matching the `changeme_local_dev_only`
    convention used everywhere else in this repo's local setup. Shared by
    test_compiler.py and test_connection_secret_endpoints.py."""
    settings = get_settings()
    created: list[tuple[str, str]] = []  # (company_id, connection_id)

    def _connect() -> psycopg.Connection:
        return psycopg.connect(
            host=settings.MEMORY_DB_HOST,
            port=settings.MEMORY_DB_PORT,
            dbname=settings.MEMORY_DB_NAME,
            user="console_app",
            password="changeme_local_dev_only",
        )

    def _make(
        company_id: str,
        connection_type: str = "dynamiq.connections.OpenAI",
        name: str = "test-connection",
    ) -> str:
        connection_id = str(uuid.uuid4())
        with _connect() as conn:
            # SET LOCAL doesn't accept a bound parameter — company_id here is always our
            # own freshly generated uuid4, never external input.
            conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
            conn.execute(
                "INSERT INTO console.connections (id, company_id, name, type) VALUES (%s, %s, %s, %s)",
                (connection_id, company_id, name, connection_type),
            )
            conn.commit()
        created.append((company_id, connection_id))
        return connection_id

    yield _make

    for company_id, connection_id in created:
        with _connect() as conn:
            conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
            conn.execute("DELETE FROM console.connections WHERE id = %s", (connection_id,))
            conn.commit()
