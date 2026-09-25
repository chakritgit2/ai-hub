"""Tests for `app.services.conversations` (PRD §6.2/§13).

Require a real, fully-migrated Postgres (see `db/README.md` for the
pre-migrations -> Alembic -> post-migrations sequence) - `requires_postgres`
only checks that *something* answers on `MEMORY_DB_*`, so a schema error here
most likely means `alembic upgrade head` hasn't been run yet.
"""
import uuid

from dynamiq.prompts import MessageRole

from app.core.db import get_company_session
from app.db.tables import conversations_table
from app.services.conversations import get_or_create_conversation

from .markers import requires_postgres


async def _fetch_row(company_id: str, conversation_id: str):
    async with get_company_session(company_id) as session:
        result = await session.execute(
            conversations_table.select().where(conversations_table.c.id == uuid.UUID(conversation_id))
        )
        return result.one_or_none()


@requires_postgres
async def test_get_or_create_conversation_creates_row(company_ids) -> None:
    company_id = company_ids()
    conversation_id, memory, user_id = await get_or_create_conversation(company_id, None, "user-create", None)
    assert conversation_id
    assert memory is not None
    assert user_id.startswith(f"{company_id}:")
    assert await _fetch_row(company_id, conversation_id) is not None


@requires_postgres
async def test_get_or_create_conversation_reuses_existing_id(company_ids) -> None:
    company_id = company_ids()
    first_id, _, first_user_id = await get_or_create_conversation(company_id, None, "user-reuse", None)
    second_id, _, second_user_id = await get_or_create_conversation(company_id, None, "user-reuse", first_id)
    assert second_id == first_id
    assert second_user_id == first_user_id


@requires_postgres
async def test_unknown_conversation_id_gets_a_new_id(company_ids) -> None:
    company_id = company_ids()
    unknown_id = str(uuid.uuid4())
    resolved_id, _, _ = await get_or_create_conversation(company_id, None, "user-unknown", unknown_id)
    assert resolved_id != unknown_id


@requires_postgres
async def test_other_companys_conversation_id_gets_a_new_id(company_ids) -> None:
    """Reusing the id would collide on the primary key and surface as a 502."""
    company_a, company_b = company_ids(), company_ids()
    conv_a, _, _ = await get_or_create_conversation(company_a, None, "user-x", None)
    resolved_id, _, _ = await get_or_create_conversation(company_b, None, "user-x", conv_a)
    assert resolved_id != conv_a


@requires_postgres
async def test_other_users_conversation_id_is_not_adopted(company_ids) -> None:
    company_id = company_ids()
    conv_a, _, _ = await get_or_create_conversation(company_id, None, "user-a", None)
    expires_before = (await _fetch_row(company_id, conv_a)).expires_at

    resolved_id, _, _ = await get_or_create_conversation(company_id, None, "user-b", conv_a)

    assert resolved_id != conv_a
    assert (await _fetch_row(company_id, conv_a)).expires_at == expires_before


@requires_postgres
async def test_external_user_id_is_hashed_not_stored_raw(company_ids) -> None:
    company_id = company_ids()
    raw_external_id = "very-secret-raw-external-id"
    conversation_id, _, _ = await get_or_create_conversation(company_id, None, raw_external_id, None)
    row = await _fetch_row(company_id, conversation_id)
    assert raw_external_id not in row.external_user_id


@requires_postgres
async def test_cross_company_memory_does_not_mix(company_ids) -> None:
    """PRD §13: same external_user_id in two companies -> memories do not mix."""
    company_a, company_b = company_ids(), company_ids()
    conv_a, memory, user_id_a = await get_or_create_conversation(company_a, None, "shared-user", None)
    conv_b, _, user_id_b = await get_or_create_conversation(company_b, None, "shared-user", None)
    assert user_id_a != user_id_b

    memory.add(role=MessageRole.USER, content="company A message", metadata={"user_id": user_id_a, "session_id": conv_a})
    memory.add(role=MessageRole.USER, content="company B message", metadata={"user_id": user_id_b, "session_id": conv_b})

    contents_a = [m.content for m in memory.search(filters={"user_id": user_id_a, "session_id": conv_a})]
    assert "company A message" in contents_a
    assert "company B message" not in contents_a
