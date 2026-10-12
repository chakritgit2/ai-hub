import uuid

from app.services.tool_secrets import delete_tool_secret, get_tool_secret, upsert_tool_secret

from .markers import requires_postgres


@requires_postgres
async def test_get_tool_secret_returns_none_when_nothing_stored(make_tool):
    company_id = str(uuid.uuid4())
    tool_id = make_tool(company_id)

    assert await get_tool_secret(company_id, tool_id) is None


@requires_postgres
async def test_upsert_then_get_round_trips(make_tool):
    company_id = str(uuid.uuid4())
    tool_id = make_tool(company_id)

    await upsert_tool_secret(
        company_id=company_id, tool_id=tool_id, ciphertext=b"ciphertext-1", nonce=b"nonce-1", dek_version=1
    )

    stored = await get_tool_secret(company_id, tool_id)
    assert stored is not None
    assert stored.ciphertext == b"ciphertext-1"
    assert stored.nonce == b"nonce-1"
    assert stored.dek_version == 1


@requires_postgres
async def test_upsert_overwrites_the_previous_secret(make_tool):
    company_id = str(uuid.uuid4())
    tool_id = make_tool(company_id)

    await upsert_tool_secret(
        company_id=company_id, tool_id=tool_id, ciphertext=b"old", nonce=b"old-nonce", dek_version=1
    )
    await upsert_tool_secret(
        company_id=company_id, tool_id=tool_id, ciphertext=b"new", nonce=b"new-nonce", dek_version=2
    )

    stored = await get_tool_secret(company_id, tool_id)
    assert stored.ciphertext == b"new"
    assert stored.nonce == b"new-nonce"
    assert stored.dek_version == 2


@requires_postgres
async def test_get_tool_secret_is_invisible_from_a_different_company(make_tool):
    """RLS-enforced isolation: a secret stored under one company must not be readable
    through a session scoped to a different company, even though get_tool_secret's own
    query filters by tool_id alone (mirrors runtime.connection_secrets' same contract)."""
    owner_company_id = str(uuid.uuid4())
    other_company_id = str(uuid.uuid4())
    tool_id = make_tool(owner_company_id)

    await upsert_tool_secret(
        company_id=owner_company_id, tool_id=tool_id, ciphertext=b"secret", nonce=b"nonce", dek_version=1
    )

    assert await get_tool_secret(other_company_id, tool_id) is None
    assert await get_tool_secret(owner_company_id, tool_id) is not None


@requires_postgres
async def test_delete_tool_secret_removes_a_stored_secret(make_tool):
    company_id = str(uuid.uuid4())
    tool_id = make_tool(company_id)
    await upsert_tool_secret(
        company_id=company_id, tool_id=tool_id, ciphertext=b"secret", nonce=b"nonce", dek_version=1
    )

    await delete_tool_secret(company_id, tool_id)

    assert await get_tool_secret(company_id, tool_id) is None


@requires_postgres
async def test_delete_tool_secret_is_a_no_op_when_nothing_stored(make_tool):
    """Called unconditionally by ToolsController::deleteTool after the tool is already
    gone - must never raise just because there was nothing to clean up."""
    company_id = str(uuid.uuid4())
    tool_id = make_tool(company_id)

    await delete_tool_secret(company_id, tool_id)

    assert await get_tool_secret(company_id, tool_id) is None
