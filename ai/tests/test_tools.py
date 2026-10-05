import uuid

import pytest
from fastapi.testclient import TestClient

from app.main_runtime import app as runtime_app
from app.main_runtime import require_internal_auth
from app.services.tools import resolve_tool

from .markers import fake_internal_claims, requires_postgres


@pytest.fixture
async def internal_client(company_ids):
    """Same convention as test_connection_secret_endpoints.py's internal_client fixture —
    an httpx.AsyncClient wired to require_internal_auth via dependency_overrides, sharing
    this test's event loop with get_company_session-based fixtures/services."""
    import httpx

    company_id = company_ids()
    runtime_app.dependency_overrides[require_internal_auth] = fake_internal_claims(company_id)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime_app), base_url="http://test") as client:
            yield client, company_id
    finally:
        runtime_app.dependency_overrides.pop(require_internal_auth, None)


@requires_postgres
async def test_resolve_tool_returns_companys_own_row(company_ids, make_tool):
    company_id = company_ids()
    tool_id = make_tool(company_id, name="orders-lookup", config={"url": "https://api.example.com/orders"})

    tool = await resolve_tool(company_id, tool_id)

    assert tool is not None
    assert tool.name == "orders-lookup"
    assert tool.config == {"url": "https://api.example.com/orders"}


@requires_postgres
async def test_resolve_tool_is_none_for_another_companys_tool(company_ids, make_tool):
    company_id = company_ids()
    other_company_id = str(uuid.uuid4())
    tool_id = make_tool(other_company_id)

    tool = await resolve_tool(company_id, tool_id)

    assert tool is None
    assert company_id != other_company_id


def test_test_tool_route_requires_internal_token():
    resp = TestClient(runtime_app).post(
        "/internal/v1/tools/00000000-0000-4000-8000-000000000000/test",
        headers={"X-Company-Id": "company-1"},
    )
    assert resp.status_code == 401


@requires_postgres
async def test_malformed_tool_id_is_404(internal_client):
    client, _company_id = internal_client
    resp = await client.post("/internal/v1/tools/not-a-uuid/test")
    assert resp.status_code == 404


@requires_postgres
async def test_unknown_tool_id_is_404(internal_client):
    client, _company_id = internal_client
    resp = await client.post(f"/internal/v1/tools/{uuid.uuid4()}/test")
    assert resp.status_code == 404


@requires_postgres
async def test_cross_company_tool_is_404(internal_client, make_tool):
    client, company_id = internal_client
    other_company_id = str(uuid.uuid4())
    tool_id = make_tool(other_company_id)

    resp = await client.post(f"/internal/v1/tools/{tool_id}/test")

    assert resp.status_code == 404
    assert company_id != other_company_id


@requires_postgres
async def test_builtin_tool_is_not_supported(internal_client, make_tool):
    client, company_id = internal_client
    tool_id = make_tool(company_id, kind="builtin")

    resp = await client.post(f"/internal/v1/tools/{tool_id}/test")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "not supported" in body["detail"]


@requires_postgres
async def test_http_tool_with_no_url_is_ok_false(internal_client, make_tool):
    client, company_id = internal_client
    tool_id = make_tool(company_id, kind="http", config={})

    resp = await client.post(f"/internal/v1/tools/{tool_id}/test")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "no url" in body["detail"]


@requires_postgres
async def test_http_tool_with_non_allowlisted_host_is_egress_blocked(internal_client, make_tool):
    """No egress_allowlist row exists for this company/host, so SafeHttpClient must reject
    it before any network call is attempted — proven by pointing the tool's url at a
    reserved, non-routable test address (RFC 5737 TEST-NET-1) that would hang/fail if
    actually dialed, and asserting the response still comes back fast."""
    client, company_id = internal_client
    tool_id = make_tool(company_id, kind="http", config={"url": "https://192.0.2.1/"})

    resp = await client.post(f"/internal/v1/tools/{tool_id}/test")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["detail"].startswith("egress_blocked")
