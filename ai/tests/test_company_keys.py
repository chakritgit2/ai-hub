import asyncio
import uuid

import pytest

from app.core.db import get_company_session
from app.db.tables import company_keys_table
from app.services.company_keys import get_or_create_company_dek

from .markers import requires_postgres


@pytest.fixture
async def company_id():
    """A fresh company id per test, cleaned up afterward through the same company-scoped
    session the service itself uses. An *async* fixture (not a sync one wrapping
    asyncio.run()), matching conftest.py's company_ids fixture — so teardown runs on the
    same event loop as the test body. get_company_session()'s engine/sessionmaker are
    @lru_cache'd module singletons whose pooled asyncpg connections bind to whichever
    event loop first created them (pytest-asyncio's per-test loop); a sync fixture
    tearing down via a separate asyncio.run() spins up a second loop and asyncpg then
    raises "attached to a different loop" when that connection is returned to the pool."""
    issued = str(uuid.uuid4())

    yield issued

    async with get_company_session(issued) as session:
        await session.execute(
            company_keys_table.delete().where(company_keys_table.c.company_id == uuid.UUID(issued))
        )


@requires_postgres
async def test_get_or_create_creates_then_reuses_same_dek(company_id):
    first = await get_or_create_company_dek(company_id)
    second = await get_or_create_company_dek(company_id)

    assert first.dek == second.dek
    assert first.dek_version == second.dek_version == 1


@requires_postgres
async def test_concurrent_get_or_create_returns_same_dek(company_id):
    results = await asyncio.gather(*(get_or_create_company_dek(company_id) for _ in range(5)))

    deks = {result.dek for result in results}
    assert len(deks) == 1


@requires_postgres  # the test body itself doesn't touch the DB, but company_id's teardown does
async def test_get_or_create_propagates_missing_master_key(company_id, monkeypatch):
    """Isolates get_or_create_company_dek's error-propagation contract from
    master_key_bytes()'s own validation (already covered by test_config.py) — and from
    pydantic-settings' real env-file/OS-env precedence, which a plain monkeypatch.delenv
    can't fully override since Settings() also reads ai/.env directly, not just os.environ."""

    def _raise() -> bytes:
        raise RuntimeError("CONSOLE_MASTER_KEY is not set - required for connection secret encryption")

    monkeypatch.setattr("app.services.company_keys.master_key_bytes", _raise)

    with pytest.raises(RuntimeError, match="CONSOLE_MASTER_KEY"):
        await get_or_create_company_dek(company_id)
