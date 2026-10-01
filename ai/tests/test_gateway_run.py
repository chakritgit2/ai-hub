from types import SimpleNamespace

import httpx
import pytest
from dynamiq.runnables.base import RunnableStatus
from fastapi.testclient import TestClient

from app.main_gateway import app as gateway_app
from app.services import compiler
from app.services.gateway_auth import mint_session_token

from .markers import requires_postgres, requires_redis


class _FakeAgent:
    """Stands in for `dynamiq.nodes.agents.Agent` - same convention as
    test_playground_run.py's fake, so the run path is exercised without a real OpenAI
    key or network access."""

    def __init__(self, **kwargs) -> None:
        self.memory = kwargs.get("memory")

    def run(self, input_data: dict, **_kwargs) -> SimpleNamespace:
        return SimpleNamespace(output={"content": "fake gateway reply"}, status=RunnableStatus.SUCCESS)


@pytest.fixture
def fake_agent(monkeypatch):
    import dynamiq.nodes.agents

    monkeypatch.setattr(dynamiq.nodes.agents, "Agent", _FakeAgent)
    monkeypatch.setattr(compiler, "build_llm", lambda *_args, **_kwargs: (None, None))


@pytest.fixture
def gateway_client() -> TestClient:
    return TestClient(gateway_app)


@pytest.fixture
async def gateway_async_client():
    """A DB-backed test using async fixtures (company_ids, make_deployment, ...) must
    issue its HTTP calls on the *same* event loop those fixtures ran on - the sync
    `TestClient` drives the ASGI app from its own internal loop via an anyio portal,
    which asyncpg's connections (bound to pytest-asyncio's loop) can't cross. Same fix
    test_playground_run.py already uses for this exact problem."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app), base_url="http://test") as client:
        yield client


def test_run_requires_authorization_header(gateway_client) -> None:
    resp = gateway_client.post("/v1/deployments/some-slug/run", json={"input": "hi"})
    assert resp.status_code == 401


def test_run_with_unknown_api_key_is_404(gateway_client) -> None:
    resp = gateway_client.post(
        "/v1/deployments/some-slug/run",
        json={"input": "hi"},
        headers={"Authorization": "Bearer ak_test_doesnotexist"},
    )
    assert resp.status_code == 404


def test_info_with_garbage_bearer_token_is_401(gateway_client) -> None:
    """Doesn't start with the gateway key prefix, so it's treated as a session token -
    and fails to even parse as one."""
    resp = gateway_client.get("/v1/deployments/some-slug/info", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


@requires_postgres
async def test_run_happy_path_via_api_key(
    fake_agent, gateway_async_client, company_ids, resolvable_agent_version, make_deployment, make_api_key
) -> None:
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    deployment_id, slug = make_deployment(company_id, version_id)
    _key_id, full_key = make_api_key(company_id, [deployment_id])

    resp = await gateway_async_client.post(
        f"/v1/deployments/{slug}/run", json={"input": "hi"}, headers={"Authorization": f"Bearer {full_key}"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["output"] == "fake gateway reply"
    assert body["trace_id"]


@requires_postgres
async def test_run_rejects_api_key_scoped_to_a_different_deployment(
    fake_agent, gateway_async_client, company_ids, resolvable_agent_version, make_deployment, make_api_key
) -> None:
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    scoped_deployment_id, _scoped_slug = make_deployment(company_id, version_id)
    _other_deployment_id, other_slug = make_deployment(company_id, version_id)
    _key_id, full_key = make_api_key(company_id, [scoped_deployment_id])

    resp = await gateway_async_client.post(
        f"/v1/deployments/{other_slug}/run", json={"input": "hi"}, headers={"Authorization": f"Bearer {full_key}"}
    )

    assert resp.status_code == 404


@requires_postgres
async def test_run_rejects_disallowed_client_ip(
    fake_agent, gateway_async_client, company_ids, resolvable_agent_version, make_deployment, make_api_key
) -> None:
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    deployment_id, slug = make_deployment(company_id, version_id)
    _key_id, full_key = make_api_key(company_id, [deployment_id], allowed_ips=["203.0.113.5"])

    resp = await gateway_async_client.post(
        f"/v1/deployments/{slug}/run", json={"input": "hi"}, headers={"Authorization": f"Bearer {full_key}"}
    )

    assert resp.status_code == 404  # the test client's host never matches the allowlist


@requires_postgres
@requires_redis
async def test_run_returns_429_when_rate_limited(
    fake_agent, gateway_async_client, company_ids, resolvable_agent_version, make_deployment, make_api_key
) -> None:
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    deployment_id, slug = make_deployment(company_id, version_id, rate_limit_per_min=1)
    _key_id, full_key = make_api_key(company_id, [deployment_id])
    headers = {"Authorization": f"Bearer {full_key}"}

    first = await gateway_async_client.post(f"/v1/deployments/{slug}/run", json={"input": "hi"}, headers=headers)
    assert first.status_code == 200

    second = await gateway_async_client.post(f"/v1/deployments/{slug}/run", json={"input": "hi"}, headers=headers)
    assert second.status_code == 429
    assert second.json()["error"] == "quota_exceeded"
    assert second.json()["reason"] == "rate_limit"


@requires_postgres
async def test_run_disabled_deployment_is_404(
    fake_agent, gateway_async_client, company_ids, resolvable_agent_version, make_deployment, make_api_key
) -> None:
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    deployment_id, slug = make_deployment(company_id, version_id, enabled=False)
    _key_id, full_key = make_api_key(company_id, [deployment_id])

    resp = await gateway_async_client.post(
        f"/v1/deployments/{slug}/run", json={"input": "hi"}, headers={"Authorization": f"Bearer {full_key}"}
    )

    assert resp.status_code == 404


@requires_postgres
async def test_info_returns_identity_fields_and_never_instructions(
    gateway_async_client, company_ids, resolvable_agent_version, make_deployment, make_api_key
) -> None:
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    deployment_id, slug = make_deployment(company_id, version_id)
    _key_id, full_key = make_api_key(company_id, [deployment_id])

    resp = await gateway_async_client.get(
        f"/v1/deployments/{slug}/info", headers={"Authorization": f"Bearer {full_key}"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"display_name": "Test Agent", "role": "answers test questions", "languages": ["en"]}
    assert "instructions" not in body


@requires_postgres
async def test_create_session_then_run_with_it(
    fake_agent, gateway_async_client, company_ids, resolvable_agent_version, make_deployment, make_api_key
) -> None:
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    deployment_id, slug = make_deployment(company_id, version_id, allowed_origins=["https://example.com"])
    _key_id, full_key = make_api_key(company_id, [deployment_id])

    session_resp = await gateway_async_client.post(
        f"/v1/deployments/{slug}/session",
        json={"external_user_id": "browser-user-1"},
        headers={"Authorization": f"Bearer {full_key}"},
    )
    assert session_resp.status_code == 200
    session_token = session_resp.json()["session_token"]
    assert session_resp.json()["expires_in"] == 300

    run_resp = await gateway_async_client.post(
        f"/v1/deployments/{slug}/run",
        json={"input": "hi"},
        headers={"Authorization": f"Bearer {session_token}", "Origin": "https://example.com"},
    )
    assert run_resp.status_code == 200
    assert run_resp.headers["access-control-allow-origin"] == "https://example.com"


@requires_postgres
async def test_session_token_cannot_mint_another_session(
    gateway_async_client, company_ids, resolvable_agent_version, make_deployment, make_api_key
) -> None:
    """createDeploymentSession is API-key-only (PRD §7.5) - a session token itself must
    not be accepted here, even though it's accepted on run/info."""
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    deployment_id, slug = make_deployment(company_id, version_id)
    make_api_key(company_id, [deployment_id])
    session_token, _ = mint_session_token(company_id, deployment_id, "user-1")

    resp = await gateway_async_client.post(
        f"/v1/deployments/{slug}/session",
        json={"external_user_id": "user-1"},
        headers={"Authorization": f"Bearer {session_token}"},
    )

    assert resp.status_code == 404  # starts with "ey", not the api key prefix - treated as (and rejected as) an api key


@requires_postgres
async def test_run_rejects_session_token_from_disallowed_origin(
    fake_agent, gateway_async_client, company_ids, resolvable_agent_version, make_deployment
) -> None:
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    deployment_id, slug = make_deployment(company_id, version_id, allowed_origins=["https://example.com"])
    session_token, _ = mint_session_token(company_id, deployment_id, "user-1")

    resp = await gateway_async_client.post(
        f"/v1/deployments/{slug}/run",
        json={"input": "hi"},
        headers={"Authorization": f"Bearer {session_token}", "Origin": "https://evil.example"},
    )

    assert resp.status_code == 403


@requires_postgres
async def test_run_rejects_session_token_with_no_origin_configured(
    fake_agent, gateway_async_client, company_ids, resolvable_agent_version, make_deployment
) -> None:
    """allowed_origins defaults to [] - deny by default for browser callers until an
    admin configures it, even with a perfectly valid session token."""
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    deployment_id, slug = make_deployment(company_id, version_id)  # allowed_origins=[]
    session_token, _ = mint_session_token(company_id, deployment_id, "user-1")

    resp = await gateway_async_client.post(
        f"/v1/deployments/{slug}/run",
        json={"input": "hi"},
        headers={"Authorization": f"Bearer {session_token}", "Origin": "https://example.com"},
    )

    assert resp.status_code == 403


@requires_postgres
async def test_run_ignores_body_user_when_session_token_present(
    fake_agent, gateway_async_client, company_ids, resolvable_agent_version, make_deployment
) -> None:
    """PRD §7.5: "the gateway ignores identity in the body when a session token is
    used" - a session-token call sending a spoofed body.user must not be able to
    impersonate a different end user than the one the token was minted for."""
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    deployment_id, slug = make_deployment(company_id, version_id, allowed_origins=["https://example.com"])
    session_token, _ = mint_session_token(company_id, deployment_id, "real-user")

    first = await gateway_async_client.post(
        f"/v1/deployments/{slug}/run",
        json={"input": "hi", "user": {"external_id": "spoofed-user"}},
        headers={"Authorization": f"Bearer {session_token}", "Origin": "https://example.com"},
    )
    second = await gateway_async_client.post(
        f"/v1/deployments/{slug}/run",
        json={"input": "what's my name", "conversation_id": first.json()["conversation_id"]},
        headers={"Authorization": f"Bearer {session_token}", "Origin": "https://example.com"},
    )

    # both turns resolve to the same conversation (same real_user identity), proving the
    # body's spoofed external_id on turn one was never used to key memory.
    assert first.json()["conversation_id"] == second.json()["conversation_id"]
