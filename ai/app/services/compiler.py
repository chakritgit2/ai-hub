"""Agent spec compiler (PRD §4.4-A, §6.1, `/internal/v1/agents/compile`).

Slice 1: Identity + Model only (see `app.services.agent_spec` for the exact spec shape).
Validates the spec, resolves and checks company-ownership of `model.connection_id`,
enforces the role-based node-type allowlist (`app.services.node_allowlist`), and proves
`compiled_definition` is actually constructible by building a real (never run, never
network-calling) `dynamiq.nodes.agents.Agent` from it — not just shape-checking.

Compile *failures* (bad spec, cross-company reference, disallowed node type, unsupported
connection type) are a normal, successful result (`ok: False` + `errors`), never an
exception — matching `ai-internal.yaml`'s `CompileResult` contract, which is always a 200.
Only a genuine infrastructure failure (e.g. the DB unreachable) propagates as an exception,
for the route to turn into a 502.
"""
import importlib.metadata
import logging

from pydantic import ValidationError

from app.integrations.dynamiq_adapter import build_llm, llm_type_for_connection_type
from app.integrations.skill_registry import ConsoleSkillRegistry
from app.services.agent_spec import AgentSpecDoc, render_identity_prompt
from app.services.connections import ConnectionRow, resolve_connection
from app.services.node_allowlist import find_disallowed_types
from app.services.skills import list_published_skills
from app.services.tools import ToolRow, resolve_tool

logger = logging.getLogger(__name__)

COMPILER_VERSION = "1"
DEFAULT_MAX_LOOPS = 8

# Never persisted, never logged, never used to make a real API call — OpenAILLM/the
# underlying openai.Client only raise if api_key is falsy at construction time; this just
# satisfies that without pretending to be a real credential (PRD §7.4: real secrets live
# in runtime.connection_secrets, resolved at run time, not compile time — unimplemented,
# see app/core/crypto.py).
_COMPILE_TIME_PLACEHOLDER_API_KEY = "compile-time-placeholder-not-a-real-key"


async def compile_spec(spec: dict, role: str, company_id: str) -> dict:
    try:
        doc = AgentSpecDoc.model_validate(spec)
    except ValidationError as exc:
        return _failure(_pydantic_errors(exc))

    connection = await resolve_connection(company_id, doc.model.connection_id)
    if connection is None:
        return _failure(
            [{"path": "model.connection_id", "message": "no connection with this id for this company"}]
        )

    tools, tool_errors = await _resolve_tools(doc, company_id)
    skill_errors = await _validate_skills(doc, company_id)
    if tool_errors or skill_errors:
        return _failure(tool_errors + skill_errors)

    compiled_definition = _build_compiled_definition(doc, connection, tools)
    compiled_definition["guardrails"] = doc.guardrails.model_dump()
    compiled_definition["skills"] = doc.skills

    disallowed = find_disallowed_types(compiled_definition, role)
    if disallowed:
        return _failure(
            [
                {"path": "model.connection_id", "message": f"node type not allowed for role {role!r}: {node_type}"}
                for node_type in disallowed
            ]
        )

    try:
        llm_type = llm_type_for_connection_type(connection.type)
    except ValueError as exc:
        return _failure([{"path": "model.connection_id", "message": str(exc)}])
    compiled_definition["agent"]["llm"]["type"] = llm_type

    try:
        build_agent(compiled_definition, _COMPILE_TIME_PLACEHOLDER_API_KEY, company_id)
    except Exception as exc:  # proves compiled_definition is actually buildable
        logger.exception("agent spec compilation failed to construct an Agent")
        return _failure([{"path": "model", "message": f"failed to construct agent: {exc}"}])

    return {
        "ok": True,
        "compiled_definition": compiled_definition,
        "compiler_version": COMPILER_VERSION,
        "dynamiq_version": importlib.metadata.version("dynamiq"),
        "errors": [],
    }


_HTTP_TOOL_TYPE = "dynamiq.nodes.tools.HttpApiCall"


async def _resolve_tools(doc: AgentSpecDoc, company_id: str) -> tuple[list[ToolRow], list[dict]]:
    """Resolves + cross-company-validates every `doc.tools` reference, collecting *all*
    errors rather than stopping at the first (PRD §7.7: every tool referenced by an agent
    spec must belong to the agent's company).

    Only `kind: "http"` tools can actually be built into a real node this round — no
    tool-secret storage exists yet (`kind: builtin` needs its own connection/API-key model;
    `kind: python` has no execution story at all) — so any other kind fails compilation
    outright, for every role including admin, rather than being silently accepted and then
    doing nothing at run time.
    """
    resolved: list[ToolRow] = []
    errors: list[dict] = []
    for index, ref in enumerate(doc.tools):
        tool = await resolve_tool(company_id, ref.tool_id)
        if tool is None:
            errors.append({"path": f"tools[{index}].tool_id", "message": "no tool with this id for this company"})
            continue
        if not tool.enabled:
            # `console.resolve_tool` returns disabled rows too (company-ownership is its
            # only filter) - disabled must be rejected here, or disabling a tool in the
            # console (e.g. as a kill switch) would have no effect on agents already
            # referencing it.
            errors.append({"path": f"tools[{index}].tool_id", "message": "tool is disabled"})
            continue
        if tool.kind != "http":
            errors.append({"path": f"tools[{index}].tool_id", "message": f"tool kind {tool.kind!r} not yet supported"})
            continue
        if not tool.config.get("url"):
            errors.append({"path": f"tools[{index}].tool_id", "message": "tool config has no url"})
            continue
        resolved.append(tool)
    return resolved, errors


async def _validate_skills(doc: AgentSpecDoc, company_id: str) -> list[dict]:
    if not doc.skills:
        return []
    published_names = {skill.name for skill in await list_published_skills(company_id)}
    return [
        {"path": "skills", "message": f"no published skill named {name!r} for this company"}
        for name in doc.skills
        if name not in published_names
    ]


def _build_compiled_definition(doc: AgentSpecDoc, connection: ConnectionRow, tools: list[ToolRow]) -> dict:
    return {
        "version": 1,
        "agent": {
            "type": "dynamiq.nodes.agents.Agent",
            "name": doc.identity.name,
            "role": render_identity_prompt(doc.identity),
            "max_loops": DEFAULT_MAX_LOOPS,
            "tools": [
                {"type": _HTTP_TOOL_TYPE, "id": tool.id, "name": tool.name, "config": tool.config}
                for tool in tools
            ],
            "llm": {
                # Filled in once the allowlist check has passed — see compile_spec().
                "type": None,
                "model": doc.model.model,
                "temperature": doc.model.temperature,
                "max_tokens": doc.model.max_tokens,
                "connection": {
                    "type": connection.type,
                    "connection_id": connection.id,
                    "name": connection.name,
                    "api_base": connection.api_base,
                },
            },
        },
    }


def _build_tools(tool_defs: list[dict], company_id: str) -> list:
    """Constructs real Dynamiq tool node instances from `compiled_definition["agent"]
    ["tools"]`. `_resolve_tools` already guarantees only `_HTTP_TOOL_TYPE` entries ever
    reach here - the explicit check below is a defensive backstop (matches this
    codebase's general defense-in-depth style), not the primary gate.

    Uses `SafeHttpApiCall`, not Dynamiq's own `HttpApiCall`, so every real request this
    tool makes is checked against the company's egress allowlist - see that class's
    docstring for why the bare Dynamiq node can't be used as-is here.
    """
    from dynamiq.connections.connections import Http

    from app.integrations.safe_http_tool import SafeHttpApiCall

    tools = []
    for tool_def in tool_defs:
        if tool_def["type"] != _HTTP_TOOL_TYPE:
            raise ValueError(f"unsupported tool type for execution: {tool_def['type']!r}")
        config = tool_def["config"]
        connection = Http(
            url=config.get("url", ""),
            method=config.get("method", "GET"),
            headers=config.get("headers") or {},
        )
        tools.append(
            SafeHttpApiCall(
                name=tool_def["name"],
                description=config.get("description", ""),
                connection=connection,
                company_id=company_id,
            )
        )
    return tools


def build_agent(
    compiled_definition: dict,
    api_key: str,
    company_id: str,
    memory=None,
    skill_registry: ConsoleSkillRegistry | None = None,
):
    """Constructs a real `dynamiq.nodes.agents.Agent` from a `compiled_definition`
    (PRD §6.1) - used two ways: `compile_spec`'s own proof-check above, with
    `_COMPILE_TIME_PLACEHOLDER_API_KEY`, no memory, no skill_registry, and the result
    discarded (only proving it's buildable), and Playground's agent-version resolution
    (`app.services.runtime`), with the connection's real decrypted secret, real
    conversation memory and skill registry when available, and the returned Agent
    actually run.
    """
    from dynamiq.nodes.agents import Agent
    from dynamiq.skills.config import SkillsConfig

    agent_def = compiled_definition["agent"]
    llm_def = agent_def["llm"]
    connection_def = llm_def["connection"]

    _connection, llm = build_llm(
        connection_def["type"],
        {
            "api_key": api_key,
            "model": llm_def["model"],
            "temperature": llm_def.get("temperature"),
            "max_tokens": llm_def.get("max_tokens"),
            # A connection's api_base (PRD §7.4) was only ever enforced at save time
            # (console-api's egress allowlist check) - it never reached the real LLM
            # construction here, so every agent silently called the provider's default
            # endpoint (api.openai.com) regardless of what api_base was configured,
            # making any OpenAI-compatible-but-not-OpenAI endpoint (OpenRouter, a local
            # proxy, Azure OpenAI, ...) unreachable in practice. _build_openai already
            # reads this as "url" (see app/integrations/dynamiq_adapter.py).
            "url": connection_def.get("api_base"),
        },
    )

    return Agent(
        name=agent_def["name"],
        llm=llm,
        role=agent_def["role"],
        max_loops=agent_def["max_loops"],
        tools=_build_tools(agent_def.get("tools", []), company_id),
        memory=memory,
        skills=SkillsConfig(enabled=True, source=skill_registry) if skill_registry is not None else SkillsConfig(),
    )


def _pydantic_errors(exc: ValidationError) -> list[dict]:
    return [
        {"path": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
        for error in exc.errors()
    ]


def _failure(errors: list[dict]) -> dict:
    return {
        "ok": False,
        "compiled_definition": None,
        "compiler_version": None,
        "dynamiq_version": None,
        "errors": errors,
    }
