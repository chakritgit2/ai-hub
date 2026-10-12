"""Proves PRD §7.5 per-run identity (sub/claims/act/run_id) actually reaches
`build_agent` from both entry points - the gap this feature closed was `claims` being
resolved at the gateway/runtime HTTP layer and then silently dropped before reaching
`app.services.runtime`'s build_agent call. Monkeypatches `build_agent` itself rather than
going through a fake Dynamiq Agent, since `identity` is consumed by `build_agent`/
`_build_tools` to construct tools, never passed to `Agent()` itself."""
from types import SimpleNamespace

from dynamiq.runnables.base import RunnableStatus

from app.services import runtime as runtime_module
from app.services.runtime import run_deployment_agent, run_playground_agent

from .markers import requires_postgres


class _FakeAgent:
    def __init__(self) -> None:
        self.memory = None

    def run(self, input_data: dict, **_kwargs) -> SimpleNamespace:
        return SimpleNamespace(output={"content": "fake reply"}, status=RunnableStatus.SUCCESS)


def _capture_identity(monkeypatch) -> dict:
    captured: dict = {}

    async def _fake_build_agent(compiled_definition, api_key, company_id, **kwargs):
        captured["identity"] = kwargs.get("identity")
        return _FakeAgent()

    monkeypatch.setattr(runtime_module, "build_agent", _fake_build_agent)
    return captured


@requires_postgres
async def test_run_deployment_agent_threads_claims_into_identity_context(
    monkeypatch, company_ids, resolvable_agent_version, make_deployment
) -> None:
    captured = _capture_identity(monkeypatch)
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    deployment_id, _slug = make_deployment(company_id, version_id)

    await run_deployment_agent(
        company_id,
        deployment_id,
        "developer",
        "hi",
        external_user_id="ext-1",
        claims={"tier": "pro"},
    )

    identity = captured["identity"]
    assert identity.sub == "ext-1"
    assert identity.claims == {"tier": "pro"}
    assert identity.act == f"deployment:{deployment_id}"
    assert identity.run_id


@requires_postgres
async def test_run_playground_agent_marks_playground_claim_and_falls_back_sub(
    monkeypatch, company_ids, resolvable_agent_version
) -> None:
    captured = _capture_identity(monkeypatch)
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)

    await run_playground_agent(
        "hi", agent_version_id=version_id, role="developer", company_id=company_id, identity_sub="runtime-user-1"
    )

    identity = captured["identity"]
    assert identity.sub == "runtime-user-1"
    assert identity.claims == {"playground": True}
    assert identity.act == f"agent_version:{version_id}"
    assert identity.run_id
