import os
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.crypto import decrypt_with_dek
from app.core.db import get_company_session
from app.db.tables import company_keys_table
from app.main_runtime import app as runtime_app
from app.main_runtime import require_internal_auth
from app.services.company_keys import get_or_create_company_dek
from app.services.connection_secrets import get_connection_secret

from .markers import fake_internal_claims, requires_openai_key, requires_postgres


@pytest.fixture
async def _cleanup_company_keys():
    """Deletes any company_keys row the test's DEK lookups create, keyed to whichever
    company id(s) the test registers via the returned function."""
    issued: list[str] = []
    yield issued.append

    for company_id in issued:
        async with get_company_session(company_id) as session:
            await session.execute(
                company_keys_table.delete().where(company_keys_table.c.company_id == uuid.UUID(company_id))
            )


@pytest.fixture
async def internal_client(company_ids):
    """An httpx.AsyncClient (not TestClient) wired to require_internal_auth via
    dependency_overrides, like test_playground_run.py's test_route_persists_conversation_id
    — async so it shares this test's own event loop with get_company_session-based
    fixtures/services, avoiding the asyncpg "different loop" pitfall."""
    company_id = company_ids()
    runtime_app.dependency_overrides[require_internal_auth] = fake_internal_claims(company_id)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime_app), base_url="http://test") as client:
            yield client, company_id
    finally:
        runtime_app.dependency_overrides.pop(require_internal_auth, None)


def test_put_secret_route_requires_internal_token():
    resp = TestClient(runtime_app).put(
        "/internal/v1/connections/00000000-0000-4000-8000-000000000000/secret",
        json={"secret": "sk-whatever"},
        headers={"X-Company-Id": "company-1"},
    )
    assert resp.status_code == 401


def test_test_connection_route_requires_internal_token():
    resp = TestClient(runtime_app).post(
        "/internal/v1/connections/00000000-0000-4000-8000-000000000000/test",
        headers={"X-Company-Id": "company-1"},
    )
    assert resp.status_code == 401


@requires_postgres
async def test_malformed_connection_id_is_404(internal_client):
    client, _company_id = internal_client
    resp = await client.put("/internal/v1/connections/not-a-uuid/secret", json={"secret": "sk-whatever"})
    assert resp.status_code == 404


@requires_postgres
async def test_unknown_connection_id_is_404(internal_client):
    client, _company_id = internal_client
    resp = await client.put(f"/internal/v1/connections/{uuid.uuid4()}/secret", json={"secret": "sk-whatever"})
    assert resp.status_code == 404


@requires_postgres
async def test_cross_company_connection_is_404(internal_client, make_connection):
    client, company_id = internal_client
    other_company_id = str(uuid.uuid4())
    connection_id = make_connection(other_company_id)

    resp = await client.put(f"/internal/v1/connections/{connection_id}/secret", json={"secret": "sk-whatever"})

    assert resp.status_code == 404
    assert company_id != other_company_id


@requires_postgres
async def test_test_connection_returns_ok_false_when_no_secret_stored(internal_client, make_connection):
    client, company_id = internal_client
    connection_id = make_connection(company_id)

    resp = await client.post(f"/internal/v1/connections/{connection_id}/test")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False


@requires_postgres
async def test_test_connection_returns_egress_blocked_when_host_not_allowlisted(internal_client, make_connection):
    """No egress_allowlist row exists for this company/host, so SafeHttpClient must reject
    it before any network call is attempted — proven by pointing api_base at a reserved,
    non-routable test address (RFC 5737 TEST-NET-1) that would hang/fail if actually
    dialed, and asserting the response still comes back fast with no secret stored."""
    client, company_id = internal_client
    connection_id = make_connection(company_id, api_base="https://192.0.2.1")

    resp = await client.post(f"/internal/v1/connections/{connection_id}/test")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["detail"].startswith("egress_blocked")


@requires_postgres
async def test_put_then_test_with_garbage_key_is_ok_false(internal_client, make_connection, _cleanup_company_keys):
    """A true negative: OpenAI deterministically rejects a malformed key, so this needs
    no live OPENAI_API_KEY to be a meaningful assertion."""
    client, company_id = internal_client
    _cleanup_company_keys(company_id)
    connection_id = make_connection(company_id)

    put_resp = await client.put(
        f"/internal/v1/connections/{connection_id}/secret", json={"secret": "sk-not-a-real-key"}
    )
    assert put_resp.status_code == 204

    test_resp = await client.post(f"/internal/v1/connections/{connection_id}/test")
    assert test_resp.status_code == 200
    body = test_resp.json()
    assert body["ok"] is False


@requires_postgres
async def test_put_secret_round_trips_through_real_encryption(internal_client, make_connection, _cleanup_company_keys):
    """Proves the stored ciphertext actually decrypts back to the exact secret via the
    company's real DEK - not just that the route returns 204."""
    client, company_id = internal_client
    _cleanup_company_keys(company_id)
    connection_id = make_connection(company_id)
    secret = "sk-round-trip-test-secret"

    put_resp = await client.put(f"/internal/v1/connections/{connection_id}/secret", json={"secret": secret})
    assert put_resp.status_code == 204

    stored = await get_connection_secret(company_id, connection_id)
    assert stored is not None
    company_dek = await get_or_create_company_dek(company_id)
    assert decrypt_with_dek(stored.ciphertext, stored.nonce, company_dek.dek).decode() == secret


@requires_openai_key
@requires_postgres
async def test_put_then_test_with_real_key_is_ok_true(internal_client, make_connection, _cleanup_company_keys):
    client, company_id = internal_client
    _cleanup_company_keys(company_id)
    connection_id = make_connection(company_id)

    put_resp = await client.put(
        f"/internal/v1/connections/{connection_id}/secret", json={"secret": os.environ["OPENAI_API_KEY"]}
    )
    assert put_resp.status_code == 204

    test_resp = await client.post(f"/internal/v1/connections/{connection_id}/test")
    assert test_resp.status_code == 200
    assert test_resp.json()["ok"] is True
