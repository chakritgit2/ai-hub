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


class _FailingAgentWithUsage:
    """Like test_playground_guardrails.py's `_RecordingAgent`: fires a fake usage_data
    callback (as a real LLM node would, mid-run) and then fails - used to regression-test
    that a deployment run burning real usage before failing still counts that spend
    against the deployment's daily quota (app.services.runtime.run_deployment_agent's
    finally block used to refund the whole reservation instead, as if nothing ran)."""

    _USAGE = {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "total_tokens_cost_usd": 0.01,
    }

    def __init__(self, **kwargs) -> None:
        self.memory = kwargs.get("memory")

    def run(self, input_data: dict, config=None, **_kwargs) -> SimpleNamespace:
        if config is not None:
            for callback in config.callbacks:
                callback.on_node_execute_run({}, usage_data=self._USAGE)
        raise RuntimeError("boom after partial usage")


@pytest.fixture
def failing_agent_with_usage(monkeypatch):
    import dynamiq.nodes.agents

    monkeypatch.setattr(dynamiq.nodes.agents, "Agent", _FailingAgentWithUsage)
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
@requires_redis
async def test_run_returns_429_when_daily_token_limit_exceeded(
    fake_agent, gateway_async_client, company_ids, resolvable_agent_version, make_deployment, make_api_key
) -> None:
    """End-to-end through the real HTTP route, not just unit-tested against
    app.services.quotas directly - the daily_token_limit/daily_cost_limit_usd branches of
    QuotaExceededError had no test exercising their wiring through runDeployment's actual
    429 response before this. A deployment with no max_tokens configured reserves the
    1000-token default estimate (app.services.quotas._DEFAULT_MAX_TOKENS_ESTIMATE), so a
    limit below that blocks on the very first call."""
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    deployment_id, slug = make_deployment(company_id, version_id, daily_token_limit=500)
    _key_id, full_key = make_api_key(company_id, [deployment_id])

    resp = await gateway_async_client.post(
        f"/v1/deployments/{slug}/run", json={"input": "hi"}, headers={"Authorization": f"Bearer {full_key}"}
    )

    assert resp.status_code == 429
    assert resp.json()["error"] == "quota_exceeded"
    assert resp.json()["reason"] == "daily_token_limit"


@requires_postgres
@requires_redis
async def test_quota_counts_real_usage_even_when_run_fails_after_partial_usage(
    failing_agent_with_usage,
    gateway_async_client,
    company_ids,
    resolvable_agent_version,
    make_deployment,
    make_api_key,
) -> None:
    """Regression test for the quota-refund-on-partial-failure bug: a run that burns real
    LLM usage (fired via the usage_data callback, see _FailingAgentWithUsage) before
    failing must still count that spend against the deployment's daily quota, not be
    refunded in full as if nothing ran at all."""
    from app.core.redis import get_redis
    from app.services.quotas import _bangkok_today

    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    # Limit set well above the 1000-token default reservation estimate so the reservation
    # itself succeeds and the run actually gets to execute (and fail).
    deployment_id, slug = make_deployment(company_id, version_id, daily_token_limit=100_000)
    _key_id, full_key = make_api_key(company_id, [deployment_id])

    resp = await gateway_async_client.post(
        f"/v1/deployments/{slug}/run", json={"input": "hi"}, headers={"Authorization": f"Bearer {full_key}"}
    )

    assert resp.status_code == 502  # the agent run itself failed, not a quota rejection

    redis_client = get_redis()
    tokens_key = f"gw:quota:tokens:{deployment_id}:{_bangkok_today()}"
    # Reserved the 1000-token default estimate, actually used 150 (100 + 50 from
    # _FailingAgentWithUsage._USAGE) - the bug being regression-tested refunded the whole
    # reservation (would leave this counter at 0); the fix must leave it reflecting the
    # 150 tokens that were actually spent before the failure.
    assert int(await redis_client.get(tokens_key)) == 150


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
