"""Async SQLAlchemy engine + session factory.

`runtime.*` and `logs.*` tables are owned by this service (Alembic-managed);
`console.*` is owned by the Phalcon side (see PRD §8.1). Company isolation is
enforced with `SET LOCAL app.company_id` inside each transaction plus
PostgreSQL RLS (PRD §7.3) - callers are expected to set that GUC themselves
per request, this module only provides the engine/session plumbing.
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(settings.DATABASE_URL, pool_pre_ping=True, future=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped AsyncSession."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session


@asynccontextmanager
async def get_company_session(company_id: str) -> AsyncIterator[AsyncSession]:
    """`async with get_company_session(company_id) as session:` for service-layer
    code (not a FastAPI route dependency - use `get_session`/`Depends` for
    that; this is an `@asynccontextmanager`, which FastAPI's `yield`-dependency
    machinery doesn't drive the same way). Issues `SET LOCAL app.company_id`
    inside the transaction first (PRD §7.3).

    Every `runtime.*`/`logs.*` table is `FORCE ROW LEVEL SECURITY`; without
    this GUC set, RLS policies evaluate `current_setting('app.company_id',
    true)` as NULL and every row is invisible/unwritable - not an error, just
    silently zero rows affected. `SET LOCAL` (not `SET`) scopes the value to
    the current transaction only, required for correctness under connection
    pooling where the same physical connection is reused across requests.
    Call sites must do their work inside this `with` block - once it exits,
    the GUC reverts and the transaction commits.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        # `set_config(..., is_local=true)` is the parameterizable equivalent of
        # `SET LOCAL app.company_id = <value>` - the bare `SET LOCAL` statement's
        # grammar doesn't accept a bind parameter for the value, only a literal.
        await session.execute(
            sa.text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": company_id},
        )
        yield session
