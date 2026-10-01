from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main_runtime import app as runtime_app, require_runtime_auth
from app.services import runtime
from app.services.runtime import run_playground_agent

from .markers import fake_runtime_claims, requires_openai_key, requires_postgres


class _FakeAgent:
    """Stands in for `dynamiq.nodes.agents.Agent` so the run path can be
    exercised without an OpenAI key or network access."""

    def __init__(self, **kwargs) -> None:
        self.memory = kwargs.get("memory")

    def run(self, input_data: dict) -> SimpleNamespace:
        return SimpleNamespace(output={"content": "fake reply"})


@pytest.fixture
def fake_agent(monkeypatch):
    import dynamiq.nodes.agents

    monkeypatch.setattr(dynamiq.nodes.agents, "Agent", _FakeAgent)
    monkeypatch.setattr(runtime, "build_llm", lambda *_args, **_kwargs: (None, None))


@requires_openai_key
async def test_run_playground_agent_returns_real_output() -> None:
    result = await run_playground_agent("Say hi in 3 words.")
    assert result["output"]
    assert isinstance(result["output"], str)
    assert result["trace_id"]
    assert isinstance(result["latency_ms"], int)


def test_playground_run_requires_runtime_token() -> None:
    """Real auth, no override — a request with no Authorization header is 401,
    never reaching run_playground_agent (PRD §4.4-B/§7.6)."""
    resp = TestClient(runtime_app).post("/ai/v1/playground/run", json={"input": "hi"})
    assert resp.status_code == 401


def test_playground_run_route_wired(runtime_client) -> None:
    """Even without a live key, the route must be reachable and fail with a
    clear provider/auth error (502), never an import/wiring error (500)."""
    resp = runtime_client.post("/ai/v1/playground/run", json={"input": "hi"})
    assert resp.status_code in (200, 502)


def test_malformed_conversation_id_is_422(runtime_client) -> None:
    resp = runtime_client.post("/ai/v1/playground/run", json={"input": "hi", "conversation_id": "not-a-uuid"})
    assert resp.status_code == 422


def test_conversation_id_not_echoed_without_memory(runtime_client, fake_agent) -> None:
    """With an empty company_id claim (the runtime_client fixture's default) no memory
    is used, so the client's id must not come back as if the conversation had been
    continued."""
    resp = runtime_client.post(
        "/ai/v1/playground/run",
        json={"input": "hi", "conversation_id": "3f2b8c1e-0000-4000-8000-000000000000"},
    )
    assert resp.status_code == 200
    assert resp.json()["conversation_id"] is None


@requires_postgres
async def test_route_persists_conversation_id(fake_agent, company_ids) -> None:
    # httpx.AsyncClient rather than TestClient, so requests run on this test's event loop -
    # the same one the company_ids fixture tears down on. Doesn't use the runtime_client
    # fixture (which only takes a company_id via request.param), so the dependency
    # override is installed directly here instead.
    company_id = company_ids()
    body = {"input": "hi", "user": {"external_id": "route-memory-test-user"}}
    runtime_app.dependency_overrides[require_runtime_auth] = fake_runtime_claims(company_id)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime_app), base_url="http://test"
        ) as client:
            resp1 = await client.post("/ai/v1/playground/run", json=body)
            assert resp1.status_code == 200
            conversation_id = resp1.json()["conversation_id"]
            assert conversation_id

            resp2 = await client.post(
                "/ai/v1/playground/run", json={**body, "conversation_id": conversation_id}
            )
        assert resp2.status_code == 200
        assert resp2.json()["conversation_id"] == conversation_id
    finally:
        runtime_app.dependency_overrides.pop(require_runtime_auth, None)


@requires_openai_key
@requires_postgres
async def test_agent_remembers_previous_turn(company_ids) -> None:
    """End-to-end with a real LLM: memory from turn one is visible on turn two (PRD §6.2)."""
    company_id = company_ids()
    first = await run_playground_agent(
        "My favorite color is teal. Just acknowledge briefly.",
        company_id=company_id,
        external_user_id="playground-memory-test-user",
    )
    second = await run_playground_agent(
        "What's my favorite color? Answer with just the color.",
        company_id=company_id,
        external_user_id="playground-memory-test-user",
        conversation_id=first["conversation_id"],
    )
    assert second["conversation_id"] == first["conversation_id"]
    assert "teal" in (second["output"] or "").lower()
