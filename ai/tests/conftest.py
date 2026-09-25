import uuid

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.main_gateway import app as gateway_app
from app.main_runtime import app as runtime_app


@pytest.fixture
def runtime_client() -> TestClient:
    return TestClient(runtime_app)


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
