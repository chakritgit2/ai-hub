import uuid

import pytest
from dynamiq.nodes.agents.exceptions import ToolExecutionException
from dynamiq.nodes.tools.http_api_call import HttpApiCallInputSchema
from fastapi.testclient import TestClient

from app.main_runtime import app as runtime_app
from app.services.compiler import build_agent, compile_spec

from .markers import requires_postgres

VALID_IDENTITY = {
    "name": "vending-support",
    "display_name": "Vending Helper",
    "owner": "CS team",
    "role": "Answers customer questions about using machines and refunds.",
    "languages": ["th", "en"],
}


def _valid_spec(connection_id: str) -> dict:
    return {
        "identity": VALID_IDENTITY,
        "model": {"connection_id": connection_id, "model": "gpt-4o-mini"},
    }


async def test_missing_identity_section_fails_without_db():
    spec = {"model": {"connection_id": str(uuid.uuid4()), "model": "gpt-4o-mini"}}
    result = await compile_spec(spec, "developer", str(uuid.uuid4()))

    assert result["ok"] is False
    assert result["compiled_definition"] is None
    assert any(error["path"] == "identity" for error in result["errors"])


async def test_missing_model_section_fails_without_db():
    result = await compile_spec({"identity": VALID_IDENTITY}, "developer", str(uuid.uuid4()))

    assert result["ok"] is False
    assert any(error["path"] == "model" for error in result["errors"])


def test_compile_route_requires_internal_token():
    """Real auth, no override — matches test_health.py's test_internal_route_requires_internal_token."""
    resp = TestClient(runtime_app).post(
        "/internal/v1/agents/compile",
        json={"spec": {}, "role": "developer"},
        headers={"X-Company-Id": "company-1"},
    )
    assert resp.status_code == 401


def test_compile_route_rejects_missing_role(runtime_client):
    resp = runtime_client.post("/internal/v1/agents/compile", json={"spec": {}})
    assert resp.status_code == 422


def test_compile_route_returns_200_for_a_bad_spec(runtime_client):
    """A malformed spec is a normal 200 with ok:false (ai-internal.yaml's CompileResult
    contract) — never a 4xx/5xx, even though auth and request shape are both fine."""
    resp = runtime_client.post("/internal/v1/agents/compile", json={"spec": {}, "role": "developer"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["compiled_definition"] is None
    assert len(body["errors"]) > 0


@requires_postgres
async def test_compile_happy_path_constructs_a_real_agent(make_connection):
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id)

    result = await compile_spec(_valid_spec(connection_id), "developer", company_id)

    assert result["ok"] is True, result["errors"]
    assert result["compiled_definition"]["agent"]["name"] == "vending-support"
    assert result["compiled_definition"]["agent"]["llm"]["type"] == "dynamiq.nodes.llms.OpenAI"
    assert result["compiled_definition"]["agent"]["llm"]["connection"]["connection_id"] == connection_id
    assert result["compiler_version"]
    assert result["dynamiq_version"]


@requires_postgres
async def test_compile_threads_connection_api_base_through_to_the_built_llm(make_connection):
    """Regression guard: a connection's api_base (e.g. an OpenAI-compatible endpoint like
    OpenRouter) must actually reach the real LLM construction, not just the PHP-side egress
    allowlist check at save time - otherwise every agent silently calls the provider's
    default endpoint (api.openai.com) regardless of what api_base is configured."""
    company_id = str(uuid.uuid4())
    custom_api_base = "https://openrouter.ai/api/v1"
    connection_id = make_connection(company_id, api_base=custom_api_base)

    result = await compile_spec(_valid_spec(connection_id), "developer", company_id)

    assert result["ok"] is True, result["errors"]
    assert result["compiled_definition"]["agent"]["llm"]["connection"]["api_base"] == custom_api_base

    agent = build_agent(result["compiled_definition"], "sk-test-key", company_id)
    assert agent.llm.connection.url == custom_api_base


@requires_postgres
async def test_compile_rejects_unknown_connection_id(make_connection):
    company_id = str(uuid.uuid4())
    make_connection(company_id)  # a real connection exists, just not this id

    result = await compile_spec(_valid_spec(str(uuid.uuid4())), "developer", company_id)

    assert result["ok"] is False
    assert result["errors"][0]["path"] == "model.connection_id"


@requires_postgres
async def test_compile_rejects_cross_company_connection(make_connection):
    company_a = str(uuid.uuid4())
    company_b = str(uuid.uuid4())
    connection_id = make_connection(company_b)

    result = await compile_spec(_valid_spec(connection_id), "developer", company_a)

    assert result["ok"] is False
    assert result["errors"][0]["path"] == "model.connection_id"


@requires_postgres
async def test_compile_rejects_disallowed_node_type_for_developer(make_connection):
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id, connection_type="dynamiq.nodes.tools.python.Python")

    result = await compile_spec(_valid_spec(connection_id), "developer", company_id)

    assert result["ok"] is False
    assert "not allowed for role" in result["errors"][0]["message"]


@requires_postgres
async def test_compile_allows_admin_past_the_allowlist_but_still_fails_to_build(make_connection):
    """Proves the allowlist gate actually fires (admin gets past it, developer doesn't,
    see the previous test) rather than just happening to reject everything — admin still
    fails here, but for an entirely different reason (no LLM builder for this type)."""
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id, connection_type="dynamiq.nodes.tools.python.Python")

    result = await compile_spec(_valid_spec(connection_id), "admin", company_id)

    assert result["ok"] is False
    assert "not allowed for role" not in result["errors"][0]["message"]
    assert "Unsupported connection type" in result["errors"][0]["message"]


@requires_postgres
async def test_compile_includes_guardrails_in_compiled_definition(make_connection):
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id)
    spec = {
        **_valid_spec(connection_id),
        "guardrails": {
            "input": [{"check_type": "max_length", "action": "block", "max_length": 4000}],
            "output": [{"check_type": "valid_json", "action": "flag"}],
        },
    }

    result = await compile_spec(spec, "developer", company_id)

    assert result["ok"] is True, result["errors"]
    guardrails = result["compiled_definition"]["guardrails"]
    assert guardrails["input"][0]["check_type"] == "max_length"
    assert guardrails["output"][0]["check_type"] == "valid_json"


async def test_compile_rejects_mask_action_on_max_length_check():
    spec = {
        **_valid_spec(str(uuid.uuid4())),
        "guardrails": {"input": [{"check_type": "max_length", "action": "mask", "max_length": 100}]},
    }

    result = await compile_spec(spec, "developer", str(uuid.uuid4()))

    assert result["ok"] is False
    assert any(error["path"].startswith("guardrails") for error in result["errors"])


async def test_compile_rejects_regex_blocklist_without_pattern():
    spec = {
        **_valid_spec(str(uuid.uuid4())),
        "guardrails": {"input": [{"check_type": "regex_blocklist", "action": "block"}]},
    }

    result = await compile_spec(spec, "developer", str(uuid.uuid4()))

    assert result["ok"] is False
    assert any(error["path"].startswith("guardrails") for error in result["errors"])


async def test_compile_rejects_valid_json_check_in_input_list():
    spec = {
        **_valid_spec(str(uuid.uuid4())),
        "guardrails": {"input": [{"check_type": "valid_json", "action": "block"}]},
    }

    result = await compile_spec(spec, "developer", str(uuid.uuid4()))

    assert result["ok"] is False
    assert any(error["path"].startswith("guardrails") for error in result["errors"])


@requires_postgres
async def test_compile_attaches_a_real_http_tool(make_connection, make_tool):
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id)
    tool_id = make_tool(company_id, kind="http", config={"url": "https://api.example.com/orders", "method": "GET"})
    spec = {**_valid_spec(connection_id), "tools": [{"tool_id": tool_id}]}

    result = await compile_spec(spec, "developer", company_id)

    assert result["ok"] is True, result["errors"]
    compiled_tools = result["compiled_definition"]["agent"]["tools"]
    assert len(compiled_tools) == 1
    assert compiled_tools[0]["type"] == "dynamiq.nodes.tools.HttpApiCall"
    assert compiled_tools[0]["id"] == tool_id

    agent = build_agent(result["compiled_definition"], "sk-test-key", company_id)
    assert len(agent.tools) == 1
    assert agent.tools[0].connection.url == "https://api.example.com/orders"
    assert agent.tools[0].company_id == company_id


@requires_postgres
async def test_compiled_http_tool_execution_is_egress_blocked_by_default(make_connection, make_tool):
    """The attached tool must actually go through SafeHttpClient at run time, not just
    Dynamiq's own unprotected requests/httpx client - with no egress_allowlist row for
    this company, calling the tool for real must be blocked exactly like the
    /tools/{id}/test endpoint already is, even though the tool's own config is valid."""
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id)
    tool_id = make_tool(company_id, kind="http", config={"url": "https://api.example.com/orders", "method": "GET"})
    spec = {**_valid_spec(connection_id), "tools": [{"tool_id": tool_id}]}

    result = await compile_spec(spec, "developer", company_id)
    assert result["ok"] is True, result["errors"]

    agent = build_agent(result["compiled_definition"], "sk-test-key", company_id)
    tool = agent.tools[0]

    with pytest.raises(ToolExecutionException, match="egress_blocked"):
        tool.execute(HttpApiCallInputSchema())


@requires_postgres
async def test_compile_rejects_cross_company_tool(make_connection, make_tool):
    company_a = str(uuid.uuid4())
    company_b = str(uuid.uuid4())
    connection_id = make_connection(company_a)
    tool_id = make_tool(company_b, kind="http")
    spec = {**_valid_spec(connection_id), "tools": [{"tool_id": tool_id}]}

    result = await compile_spec(spec, "developer", company_a)

    assert result["ok"] is False
    assert result["errors"][0]["path"] == "tools[0].tool_id"


@requires_postgres
async def test_compile_rejects_unknown_tool_id(make_connection):
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id)
    spec = {**_valid_spec(connection_id), "tools": [{"tool_id": str(uuid.uuid4())}]}

    result = await compile_spec(spec, "developer", company_id)

    assert result["ok"] is False
    assert result["errors"][0]["path"] == "tools[0].tool_id"


@requires_postgres
async def test_compile_rejects_disabled_tool(make_connection, make_tool):
    """`console.resolve_tool` returns disabled rows too (only company-ownership is
    filtered in SQL) - the compiler must reject them itself, or disabling a tool in the
    console (e.g. as a kill switch) would have no effect on agents already referencing it."""
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id)
    tool_id = make_tool(company_id, kind="http", config={"url": "https://api.example.com/orders"}, enabled=False)
    spec = {**_valid_spec(connection_id), "tools": [{"tool_id": tool_id}]}

    result = await compile_spec(spec, "admin", company_id)

    assert result["ok"] is False
    assert result["errors"][0]["path"] == "tools[0].tool_id"
    assert "disabled" in result["errors"][0]["message"]


@requires_postgres
async def test_compile_rejects_http_tool_with_no_url(make_connection, make_tool):
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id)
    tool_id = make_tool(company_id, kind="http", config={})
    spec = {**_valid_spec(connection_id), "tools": [{"tool_id": tool_id}]}

    result = await compile_spec(spec, "developer", company_id)

    assert result["ok"] is False
    assert result["errors"][0]["path"] == "tools[0].tool_id"
    assert "no url" in result["errors"][0]["message"]


@requires_postgres
async def test_compile_rejects_builtin_tool_kind_even_for_admin(make_connection, make_tool):
    """kind: builtin (Tavily/Exa) has no connection/API-key model yet - fails compilation
    for every role, including admin, rather than being silently accepted and doing nothing
    at run time."""
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id)
    tool_id = make_tool(company_id, kind="builtin")
    spec = {**_valid_spec(connection_id), "tools": [{"tool_id": tool_id}]}

    result = await compile_spec(spec, "admin", company_id)

    assert result["ok"] is False
    assert "not yet supported" in result["errors"][0]["message"]


@requires_postgres
async def test_compile_attaches_published_skill(make_connection, make_skill):
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id)
    make_skill(company_id, name="refund-policy", content="# Refund Policy", is_published=True)
    spec = {**_valid_spec(connection_id), "skills": ["refund-policy"]}

    result = await compile_spec(spec, "developer", company_id)

    assert result["ok"] is True, result["errors"]
    assert result["compiled_definition"]["skills"] == ["refund-policy"]


@requires_postgres
async def test_compile_rejects_unpublished_skill_name(make_connection, make_skill):
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id)
    make_skill(company_id, name="draft-skill", is_published=False)
    spec = {**_valid_spec(connection_id), "skills": ["draft-skill"]}

    result = await compile_spec(spec, "developer", company_id)

    assert result["ok"] is False
    assert result["errors"][0]["path"] == "skills"


@requires_postgres
async def test_build_agent_scopes_skill_registry_to_only_the_declared_skills(
    company_ids, make_connection, make_skill
):
    """Two published skills exist for the company; the agent only declares one - the
    attached registry must not leak the other (PRD §6.6a: skills are attached per agent,
    not company-wide)."""
    from app.integrations.skill_registry import ConsoleSkillRegistry, get_console_skill_registry

    company_id = company_ids()
    connection_id = make_connection(company_id)
    make_skill(company_id, name="refund-policy", content="# Refund Policy", is_published=True)
    make_skill(company_id, name="shipping-policy", content="# Shipping Policy", is_published=True)
    spec = {**_valid_spec(connection_id), "skills": ["refund-policy"]}

    result = await compile_spec(spec, "developer", company_id)
    assert result["ok"] is True, result["errors"]

    full_registry = await get_console_skill_registry(company_id)
    scoped = [s for s in full_registry.skills if s.name in result["compiled_definition"]["skills"]]
    skill_registry = ConsoleSkillRegistry(skills=scoped)

    agent = build_agent(result["compiled_definition"], "sk-test-key", company_id, skill_registry=skill_registry)

    assert agent.skills.enabled is True
    names = [m.name for m in agent.skills.source.get_skills_metadata()]
    assert names == ["refund-policy"]
