"""Proves PRD §6.2's Memory tab actually controls whether/how memory is wired per agent -
the gap this feature closed was `app.services.runtime` always calling
`get_or_create_conversation` with Dynamiq's hardcoded defaults whenever an
`external_user_id` was present, with no way for an agent spec to opt out or tune
`message_limit`/`save_mode`. Mirrors `test_runtime_identity.py`'s
`_capture_identity`/`_FakeAgent` pattern - monkeypatches `build_agent` itself since
`memory` is a kwarg passed straight through to it."""
from types import SimpleNamespace

from dynamiq.memory.memory import MemorySaveMode
from dynamiq.runnables.base import RunnableStatus

from app.services import runtime as runtime_module
from app.services.runtime import run_deployment_agent, run_playground_agent

from .markers import requires_postgres


class _FakeAgent:
    def __init__(self) -> None:
        self.memory = None

    def run(self, input_data: dict, **_kwargs) -> SimpleNamespace:
        return SimpleNamespace(output={"content": "fake reply"}, status=RunnableStatus.SUCCESS)


def _capture_memory(monkeypatch) -> dict:
    captured: dict = {}

    async def _fake_build_agent(compiled_definition, api_key, company_id, **kwargs):
        captured["memory"] = kwargs.get("memory")
        return _FakeAgent()

    monkeypatch.setattr(runtime_module, "build_agent", _fake_build_agent)
    return captured


@requires_postgres
async def test_disabled_memory_spec_keeps_memory_none_even_with_external_user_id(
    monkeypatch, company_ids, resolvable_agent_version, make_deployment
) -> None:
    captured = _capture_memory(monkeypatch)
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id, memory={"enabled": False})
    deployment_id, _slug = make_deployment(company_id, version_id)

    await run_deployment_agent(company_id, deployment_id, "developer", "hi", external_user_id="ext-1")

    assert captured["memory"] is None


@requires_postgres
async def test_default_memory_spec_wires_memory_with_external_user_id(
    monkeypatch, company_ids, resolvable_agent_version, make_deployment
) -> None:
    captured = _capture_memory(monkeypatch)
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    deployment_id, _slug = make_deployment(company_id, version_id)

    await run_deployment_agent(company_id, deployment_id, "developer", "hi", external_user_id="ext-1")

    assert captured["memory"] is not None


@requires_postgres
async def test_memory_overrides_reach_the_built_agent(
    monkeypatch, company_ids, resolvable_agent_version
) -> None:
    captured = _capture_memory(monkeypatch)
    company_id = company_ids()
    version_id = await resolvable_agent_version(
        company_id, memory={"enabled": True, "message_limit": 7, "save_mode": "input_output"}
    )

    await run_playground_agent(
        "hi",
        agent_version_id=version_id,
        role="developer",
        company_id=company_id,
        identity_sub="runtime-user-1",
        external_user_id="ext-2",
    )

    memory = captured["memory"]
    assert memory is not None
    assert memory.message_limit == 7
    assert memory.save_mode == MemorySaveMode.INPUT_OUTPUT
