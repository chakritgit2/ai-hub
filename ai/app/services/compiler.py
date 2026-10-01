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
from app.services.agent_spec import AgentSpecDoc, render_identity_prompt
from app.services.connections import ConnectionRow, resolve_connection
from app.services.node_allowlist import find_disallowed_types

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

    compiled_definition = _build_compiled_definition(doc, connection)
    compiled_definition["guardrails"] = doc.guardrails.model_dump()

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
        build_agent(compiled_definition, _COMPILE_TIME_PLACEHOLDER_API_KEY)
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


def _build_compiled_definition(doc: AgentSpecDoc, connection: ConnectionRow) -> dict:
    return {
        "version": 1,
        "agent": {
            "type": "dynamiq.nodes.agents.Agent",
            "name": doc.identity.name,
            "role": render_identity_prompt(doc.identity),
            "max_loops": DEFAULT_MAX_LOOPS,
            "tools": [],
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
                },
            },
        },
    }


def build_agent(compiled_definition: dict, api_key: str, memory=None):
    """Constructs a real `dynamiq.nodes.agents.Agent` from a `compiled_definition`
    (PRD §6.1) - used two ways: `compile_spec`'s own proof-check above, with
    `_COMPILE_TIME_PLACEHOLDER_API_KEY`, no memory, and the result discarded (only
    proving it's buildable), and Playground's agent-version resolution
    (`app.services.runtime`), with the connection's real decrypted secret, real
    conversation memory when available, and the returned Agent actually run.
    """
    from dynamiq.nodes.agents import Agent

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
        },
    )

    return Agent(
        name=agent_def["name"],
        llm=llm,
        role=agent_def["role"],
        max_loops=agent_def["max_loops"],
        tools=[],
        memory=memory,
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
