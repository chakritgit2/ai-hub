import os
import uuid
from types import SimpleNamespace

import httpx
import pytest
from dynamiq.runnables.base import RunnableStatus
from fastapi.testclient import TestClient

from app.main_runtime import app as runtime_app
from app.main_runtime import require_runtime_auth
from app.services import compiler
from app.services.runtime import run_playground_agent

from .markers import fake_runtime_claims, requires_openai_key, requires_postgres


class _FakeAgent:
    """Stands in for `dynamiq.nodes.agents.Agent` so the run path can be
    exercised without an OpenAI key or network access."""

    def __init__(self, **kwargs) -> None:
        self.memory = kwargs.get("memory")

    def run(self, input_data: dict, **_kwargs) -> SimpleNamespace:
        return SimpleNamespace(output={"content": "fake reply"}, status=RunnableStatus.SUCCESS)


@pytest.fixture
def fake_agent(monkeypatch):
    import dynamiq.nodes.agents

    monkeypatch.setattr(dynamiq.nodes.agents, "Agent", _FakeAgent)
    # build_llm is called from app.services.compiler.build_agent now (resolution/compile
    # lives there), not directly from app.services.runtime - see compiler.py.
    monkeypatch.setattr(compiler, "build_llm", lambda *_args, **_kwargs: (None, None))


@requires_openai_key
@requires_postgres
async def test_run_playground_agent_returns_real_output(company_ids, resolvable_agent_version) -> None:
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id, secret=os.environ["OPENAI_API_KEY"])

    result = await run_playground_agent(
        "Say hi in 3 words.", agent_version_id=version_id, role="developer", company_id=company_id
    )

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
    """Even with the runtime_client fixture's default claims (empty company_id, a
    zero-UUID agent_version_id that resolves to nothing), the route must be reachable and
    fail with a clear upstream error (502), never an import/wiring error (500)."""
    resp = runtime_client.post("/ai/v1/playground/run", json={"input": "hi"})
    assert resp.status_code == 502


def test_malformed_conversation_id_is_422(runtime_client) -> None:
    resp = runtime_client.post("/ai/v1/playground/run", json={"input": "hi", "conversation_id": "not-a-uuid"})
    assert resp.status_code == 422


@requires_postgres
async def test_conversation_id_not_echoed_without_memory(fake_agent, company_ids, resolvable_agent_version) -> None:
    """A resolvable agent version with no external_user_id in the request means memory
    is never used, so a client-sent conversation_id must not come back as if the
    conversation had been continued."""
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    runtime_app.dependency_overrides[require_runtime_auth] = fake_runtime_claims(
        company_id, agent_version_id=version_id
    )
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime_app), base_url="http://test") as client:
            resp = await client.post(
                "/ai/v1/playground/run",
                json={"input": "hi", "conversation_id": "3f2b8c1e-0000-4000-8000-000000000000"},
            )
        assert resp.status_code == 200
        assert resp.json()["conversation_id"] is None
    finally:
        runtime_app.dependency_overrides.pop(require_runtime_auth, None)


@requires_postgres
async def test_route_persists_conversation_id(fake_agent, company_ids, resolvable_agent_version) -> None:
    # httpx.AsyncClient rather than TestClient, so requests run on this test's event loop -
    # the same one the company_ids fixture tears down on.
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    body = {"input": "hi", "user": {"external_id": "route-memory-test-user"}}
    runtime_app.dependency_overrides[require_runtime_auth] = fake_runtime_claims(
        company_id, agent_version_id=version_id
    )
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime_app), base_url="http://test") as client:
            resp1 = await client.post("/ai/v1/playground/run", json=body)
            assert resp1.status_code == 200
            conversation_id = resp1.json()["conversation_id"]
            assert conversation_id

            resp2 = await client.post("/ai/v1/playground/run", json={**body, "conversation_id": conversation_id})
        assert resp2.status_code == 200
        assert resp2.json()["conversation_id"] == conversation_id
    finally:
        runtime_app.dependency_overrides.pop(require_runtime_auth, None)


@requires_openai_key
@requires_postgres
async def test_agent_remembers_previous_turn(company_ids, resolvable_agent_version) -> None:
    """End-to-end with a real LLM: memory from turn one is visible on turn two (PRD §6.2)."""
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id, secret=os.environ["OPENAI_API_KEY"])

    first = await run_playground_agent(
        "My favorite color is teal. Just acknowledge briefly.",
        agent_version_id=version_id,
        role="developer",
        company_id=company_id,
        external_user_id="playground-memory-test-user",
    )
    second = await run_playground_agent(
        "What's my favorite color? Answer with just the color.",
        agent_version_id=version_id,
        role="developer",
        company_id=company_id,
        external_user_id="playground-memory-test-user",
        conversation_id=first["conversation_id"],
    )
    assert second["conversation_id"] == first["conversation_id"]
    assert "teal" in (second["output"] or "").lower()


@requires_postgres
async def test_unknown_agent_version_id_raises(company_ids) -> None:
    company_id = company_ids()

    with pytest.raises(ValueError, match="not found"):
        await run_playground_agent("hi", agent_version_id=str(uuid.uuid4()), role="developer", company_id=company_id)


@requires_postgres
async def test_cross_company_agent_version_id_is_indistinguishable_from_not_found(
    company_ids, resolvable_agent_version
) -> None:
    """A version belonging to another company must behave exactly like an unknown one
    (PRD §12) - not a different error that would reveal it exists."""
    owner_company_id = company_ids()
    other_company_id = company_ids()
    version_id = await resolvable_agent_version(owner_company_id)

    with pytest.raises(ValueError, match="not found"):
        await run_playground_agent("hi", agent_version_id=version_id, role="developer", company_id=other_company_id)
