"""Conversation/memory service (PRD §6.2).

Wires Dynamiq's PostgreSQL memory backend for the Playground/gateway run
path. Two tables are involved, deliberately kept separate:

- `runtime.conversations` (this service's own table, `app/db/tables.py`):
  metadata only - who, when, expiry. Nothing in a run's actual content lives
  here.
- `runtime.agent_memory` (Dynamiq-managed content table, see the docstring on
  `ai/alembic/versions/0005_create_agent_memory_table.py`): the actual
  message history, scoped by a hashed `user_id`/`session_id` composite key,
  with no `company_id` column and no RLS - isolation for that table rests
  entirely on this module always deriving the key correctly.
"""
import asyncio
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from dynamiq.connections.connections import PostgreSQL as PostgreSQLConnection

from app.core.config import get_settings
from app.core.db import get_company_session
from app.core.hashing import hash_external_user_id
from app.db.tables import conversations_table

if TYPE_CHECKING:
    from dynamiq.memory.memory import Memory


class _MemoryDBConnection(PostgreSQLConnection):
    """`dynamiq.connections.PostgreSQL.connect()` opens a plain psycopg
    connection with no way to pass extra libpq options through its
    constructor, so `search_path` can't be set there directly. Setting it via
    `ALTER ROLE ai_app SET search_path` would work too, but needs a DB
    privilege (`ALTER ROLE`) neither `ai_app` nor whoever runs `alembic
    upgrade` locally is guaranteed to have, and it would apply to every
    session for that role, including the app's own async SQLAlchemy engine -
    broader blast radius than this needs. Overriding `connect()` to set
    `search_path` on just this one connection, right after it opens, is
    self-contained and needs no extra grants.
    """

    def connect(self):
        conn = super().connect()
        with conn.cursor() as cur:
            cur.execute("SET search_path TO runtime, public")
        return conn


_memory: "Memory | None" = None  # process-wide singleton, see get_memory()
_memory_lock = threading.Lock()


def _build_memory() -> "Memory":
    from dynamiq.memory.backends.postgresql import PostgreSQL as PostgresMemoryBackend
    from dynamiq.memory.memory import Memory

    settings = get_settings()
    connection = _MemoryDBConnection(
        host=settings.MEMORY_DB_HOST,
        port=settings.MEMORY_DB_PORT,
        database=settings.MEMORY_DB_NAME,
        user=settings.MEMORY_DB_USER,
        password=settings.MEMORY_DB_PASSWORD,
    )
    backend = PostgresMemoryBackend(
        connection=connection,
        # Unqualified - resolves inside `runtime` via _MemoryDBConnection's search_path.
        table_name="agent_memory",
        # The table is Alembic-owned (0005_create_agent_memory_table.py), not
        # created on first use - see that migration's docstring for why.
        create_if_not_exist=False,
    )
    return Memory(backend=backend)


def get_memory() -> "Memory":
    """Process-wide singleton.

    `PostgreSQL.model_post_init()` opens one blocking psycopg connection
    eagerly, and the backend has no connection pooling, so building it once
    and reusing it avoids opening a new blocking connection per request.
    Under concurrent runs (`Settings.MAX_CONCURRENT_RUNS`) this single
    connection serializes all memory reads/writes - acceptable for this
    slice, flagged as a fast-follow (pool multiple backend instances) rather
    than solved here.

    Blocking (the first call connects) - async callers go through
    `asyncio.to_thread`, and the lock keeps concurrent first calls from
    opening two connections.
    """
    global _memory
    if _memory is None:
        with _memory_lock:
            if _memory is None:
                _memory = _build_memory()
    return _memory


async def get_or_create_conversation(
    company_id: str,
    deployment_id: str | None,
    external_user_id: str,
    conversation_id: str | None,
    source: str = "playground",
) -> tuple[str, "Memory", str]:
    """Resolve a conversation and its Dynamiq memory scoping key.

    Returns `(conversation_id, memory, user_id)`. Callers pass
    `memory=memory` into `Agent(...)` and `user_id=user_id,
    session_id=conversation_id` into `agent.run(input_data={...})`.

    - `external_user_id` is hashed (`app/core/hashing.py`) and combined with
      `company_id` into `user_id` - never stored or used raw, and never able
      to collide across companies even for the same end-user id (PRD §13's
      cross-company memory-isolation test case).
    - A supplied `conversation_id` is only continued if it belongs to this
      company *and* this end-user and hasn't expired. Anything else (expired,
      swept, another user's or another company's id, or an id that never
      existed) gets a freshly minted id, exactly as if none had been given -
      the client's id is never reused, so clients can't choose session ids.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=settings.CONVERSATION_TTL_DAYS)
    hashed_user_id = hash_external_user_id(company_id, external_user_id)
    user_id = f"{company_id}:{hashed_user_id}"
    company_uuid = uuid.UUID(company_id)

    async with get_company_session(company_id) as session:
        resolved_id: uuid.UUID | None = None

        if conversation_id:
            result = await session.execute(
                conversations_table.update()
                .where(
                    conversations_table.c.id == uuid.UUID(conversation_id),
                    conversations_table.c.company_id == company_uuid,
                    conversations_table.c.external_user_id == hashed_user_id,
                    conversations_table.c.expires_at > now,
                )
                .values(last_message_at=now, expires_at=expires_at)
                .returning(conversations_table.c.id)
            )
            resolved_id = result.scalar_one_or_none()

        if resolved_id is None:
            resolved_id = uuid.uuid4()
            await session.execute(
                conversations_table.insert().values(
                    id=resolved_id,
                    company_id=company_uuid,
                    deployment_id=uuid.UUID(deployment_id) if deployment_id else None,
                    source=source,
                    external_user_id=hashed_user_id,
                    last_message_at=now,
                    expires_at=expires_at,
                    created_at=now,
                )
            )

    memory = await asyncio.to_thread(get_memory)
    return str(resolved_id), memory, user_id
