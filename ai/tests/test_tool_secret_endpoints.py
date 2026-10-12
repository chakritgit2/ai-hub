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
from app.services.tool_secrets import get_tool_secret

from .markers import fake_internal_claims, requires_postgres


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
    """Mirrors test_connection_secret_endpoints.py's fixture of the same name - an
    httpx.AsyncClient sharing this test's event loop, wired to require_internal_auth."""
    company_id = company_ids()
    runtime_app.dependency_overrides[require_internal_auth] = fake_internal_claims(company_id)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime_app), base_url="http://test") as client:
            yield client, company_id
    finally:
        runtime_app.dependency_overrides.pop(require_internal_auth, None)


def test_put_tool_secret_route_requires_internal_token():
    resp = TestClient(runtime_app).put(
        "/internal/v1/tools/00000000-0000-4000-8000-000000000000/secret",
        json={"secret": "sk-whatever"},
        headers={"X-Company-Id": "company-1"},
    )
    assert resp.status_code == 401


@requires_postgres
async def test_malformed_tool_id_is_404(internal_client):
    client, _company_id = internal_client
    resp = await client.put("/internal/v1/tools/not-a-uuid/secret", json={"secret": "sk-whatever"})
    assert resp.status_code == 404


@requires_postgres
async def test_unknown_tool_id_is_404(internal_client):
    client, _company_id = internal_client
    resp = await client.put(f"/internal/v1/tools/{uuid.uuid4()}/secret", json={"secret": "sk-whatever"})
    assert resp.status_code == 404


@requires_postgres
async def test_cross_company_tool_is_404(internal_client, make_tool):
    client, company_id = internal_client
    other_company_id = str(uuid.uuid4())
    tool_id = make_tool(other_company_id)

    resp = await client.put(f"/internal/v1/tools/{tool_id}/secret", json={"secret": "sk-whatever"})

    assert resp.status_code == 404
    assert company_id != other_company_id


@requires_postgres
async def test_put_tool_secret_round_trips_through_real_encryption(
    internal_client, make_tool, _cleanup_company_keys
):
    """Proves the stored ciphertext actually decrypts back to the exact secret via the
    company's real DEK - not just that the route returns 204 (mirrors
    test_connection_secret_endpoints.py's test_put_secret_round_trips_through_real_encryption)."""
    client, company_id = internal_client
    _cleanup_company_keys(company_id)
    tool_id = make_tool(company_id)
    secret = "sk-round-trip-test-secret"

    put_resp = await client.put(f"/internal/v1/tools/{tool_id}/secret", json={"secret": secret})
    assert put_resp.status_code == 204

    stored = await get_tool_secret(company_id, tool_id)
    assert stored is not None
    company_dek = await get_or_create_company_dek(company_id)
    assert decrypt_with_dek(stored.ciphertext, stored.nonce, company_dek.dek).decode() == secret


@requires_postgres
async def test_test_tool_attaches_stored_secret_as_authorization_header(
    monkeypatch, internal_client, make_tool, _cleanup_company_keys
):
    """testTool doesn't go through SafeHttpApiCall (it hand-builds its own request), so
    this proves its own header-building logic directly - SafeHttpClient's check_host/
    request are monkeypatched to avoid any real DNS/network, same reasoning
    test_safe_http_client.py's own suite always injects resolve_ips/transport instead of
    touching the network for real."""
    import app.main_runtime as main_runtime_module

    captured: dict = {}

    async def fake_check_host(self, host, port):
        return "203.0.113.10"

    async def fake_request(self, method, url, **kwargs):
        captured["headers"] = kwargs.get("headers")
        return httpx.Response(200, request=httpx.Request(method, url))

    monkeypatch.setattr(main_runtime_module.SafeHttpClient, "check_host", fake_check_host)
    monkeypatch.setattr(main_runtime_module.SafeHttpClient, "request", fake_request)

    client, company_id = internal_client
    _cleanup_company_keys(company_id)
    tool_id = make_tool(company_id, kind="http", auth_mode="service", config={"url": "https://api.example.com/orders"})
    secret = "sk-service-mode-secret"
    put_resp = await client.put(f"/internal/v1/tools/{tool_id}/secret", json={"secret": secret})
    assert put_resp.status_code == 204

    test_resp = await client.post(f"/internal/v1/tools/{tool_id}/test")

    assert test_resp.status_code == 200
    assert test_resp.json()["ok"] is True
    assert captured["headers"]["Authorization"] == f"Bearer {secret}"


@requires_postgres
async def test_test_tool_attaches_no_header_when_no_secret_stored(monkeypatch, internal_client, make_tool):
    """Fail-open: a service-mode tool with no secret ever stored must attach no
    Authorization header and still succeed, exactly like before secret storage existed."""
    import app.main_runtime as main_runtime_module

    captured: dict = {}

    async def fake_check_host(self, host, port):
        return "203.0.113.10"

    async def fake_request(self, method, url, **kwargs):
        captured["headers"] = kwargs.get("headers")
        return httpx.Response(200, request=httpx.Request(method, url))

    monkeypatch.setattr(main_runtime_module.SafeHttpClient, "check_host", fake_check_host)
    monkeypatch.setattr(main_runtime_module.SafeHttpClient, "request", fake_request)

    client, company_id = internal_client
    tool_id = make_tool(company_id, kind="http", auth_mode="service", config={"url": "https://api.example.com/orders"})

    test_resp = await client.post(f"/internal/v1/tools/{tool_id}/test")

    assert test_resp.status_code == 200
    assert test_resp.json()["ok"] is True
    assert "Authorization" not in captured["headers"]


def test_delete_tool_secret_route_requires_internal_token():
    resp = TestClient(runtime_app).delete(
        "/internal/v1/tools/00000000-0000-4000-8000-000000000000/secret",
        headers={"X-Company-Id": "company-1"},
    )
    assert resp.status_code == 401


@requires_postgres
async def test_delete_tool_secret_removes_a_stored_secret(internal_client, make_tool, _cleanup_company_keys):
    client, company_id = internal_client
    _cleanup_company_keys(company_id)
    tool_id = make_tool(company_id)
    put_resp = await client.put(f"/internal/v1/tools/{tool_id}/secret", json={"secret": "sk-to-be-deleted"})
    assert put_resp.status_code == 204

    delete_resp = await client.delete(f"/internal/v1/tools/{tool_id}/secret")

    assert delete_resp.status_code == 204
    assert await get_tool_secret(company_id, tool_id) is None


@requires_postgres
async def test_delete_tool_secret_is_a_no_op_when_nothing_stored(internal_client, make_tool):
    """Called unconditionally by ToolsController::deleteTool after the console.tools row
    is already gone - must succeed even when there was never a secret to clean up."""
    client, company_id = internal_client
    tool_id = make_tool(company_id)

    delete_resp = await client.delete(f"/internal/v1/tools/{tool_id}/secret")

    assert delete_resp.status_code == 204


@requires_postgres
async def test_delete_tool_secret_malformed_id_is_404(internal_client):
    client, _company_id = internal_client
    resp = await client.delete("/internal/v1/tools/not-a-uuid/secret")
    assert resp.status_code == 404
