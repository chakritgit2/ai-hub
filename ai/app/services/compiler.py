"""Agent spec compiler (PRD §4.4-A, §6.1, `/internal/v1/agents/compile`).

Identity + Model + Tools + Skills + Knowledge (see `app.services.agent_spec` for the exact
spec shape). Validates the spec, resolves and checks company-ownership of every reference
(`model.connection_id`, `tools[].tool_id`, `skills` names, `knowledge[].kb_id`), enforces
the role-based node-type allowlist (`app.services.node_allowlist`), and proves
`compiled_definition` is actually constructible by building a real `dynamiq.nodes.agents.Agent`
from it — not just shape-checking. Never makes a real call to an LLM/embedding *provider*
(LLM and knowledge-base embedders are built with a compile-time placeholder key, never a
real one); internal DB round-trips (resolving connections/tools/skills, and opening a
knowledge base's own pgvector table to prove it's actually indexed) are not considered
"network calls" in that sense and do happen for real during compilation.

Compile *failures* (bad spec, cross-company reference, disallowed node type, unsupported
connection type) are a normal, successful result (`ok: False` + `errors`), never an
exception — matching `ai-internal.yaml`'s `CompileResult` contract, which is always a 200.
Only a genuine infrastructure failure (e.g. the DB unreachable) propagates as an exception,
for the route to turn into a 502.
"""
import asyncio
import importlib.metadata
import logging

from pydantic import ValidationError

from app.integrations.dynamiq_adapter import build_embedder, build_llm, llm_type_for_connection_type
from app.integrations.skill_registry import ConsoleSkillRegistry
from app.services.agent_spec import AgentSpecDoc, render_identity_prompt
from app.services.connections import ConnectionRow, resolve_connection
from app.services.egress_allowlist import EgressAllowlistEntry
from app.services.knowledge_bases import (
    KnowledgeBaseRow,
    kb_vector_table_name,
    open_kb_vector_store,
    resolve_knowledge_base,
)
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
    knowledge, knowledge_errors = await _resolve_knowledge(doc, company_id)
    if tool_errors or skill_errors or knowledge_errors:
        return _failure(tool_errors + skill_errors + knowledge_errors)

    compiled_definition = _build_compiled_definition(doc, connection, tools, knowledge)
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
        await build_agent(
            compiled_definition,
            _COMPILE_TIME_PLACEHOLDER_API_KEY,
            company_id,
            # Same placeholder-key philosophy as the LLM above - no real embedder secret is
            # ever touched at compile time. The per-KB vector store is still opened for
            # real (see _build_tools's retrieval branch), which is what proves a published
            # agent's knowledge bases are actually indexed - that's an internal DB check,
            # not a network call to a provider, consistent with resolve_connection/
            # resolve_tool/list_published_skills already hitting the DB during compile.
            knowledge_api_keys={kb.id: _COMPILE_TIME_PLACEHOLDER_API_KEY for kb, _ in knowledge},
        )
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
RETRIEVAL_TOOL_TYPE = "dynamiq.nodes.retrievers.retriever.VectorStoreRetriever"
_KNOWLEDGE_TOP_K = 5  # matches app.services.kb_search's own default; no per-agent override yet


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


async def _resolve_knowledge(
    doc: AgentSpecDoc, company_id: str
) -> tuple[list[tuple[KnowledgeBaseRow, ConnectionRow]], list[dict]]:
    """Resolves + cross-company-validates every `doc.knowledge` reference, same shape and
    reasoning as `_resolve_tools` (collects *all* errors, not just the first).

    Only `retrieval_mode == "vector"` KBs can be attached today - Dynamiq's native
    `VectorStoreRetriever` node (the only agent-attachable retrieval node this project has)
    is vector-only; the project's own `hybrid` mode (`app.services.kb_hybrid`'s hand-rolled
    RRF fusion) has no equivalent node, so a `hybrid` KB fails compilation outright, for
    every role, rather than being silently accepted and behaving like a vector-only KB at
    run time.
    """
    resolved: list[tuple[KnowledgeBaseRow, ConnectionRow]] = []
    errors: list[dict] = []
    for index, ref in enumerate(doc.knowledge):
        kb = await resolve_knowledge_base(company_id, ref.kb_id)
        if kb is None:
            errors.append(
                {"path": f"knowledge[{index}].kb_id", "message": "no knowledge base with this id for this company"}
            )
            continue
        if kb.retrieval_mode != "vector":
            errors.append(
                {
                    "path": f"knowledge[{index}].kb_id",
                    "message": f"knowledge bases with retrieval_mode {kb.retrieval_mode!r} can't be attached to an "
                    "agent yet - only 'vector' is supported",
                }
            )
            continue
        embedder_connection = await resolve_connection(company_id, kb.embedder_connection_id)
        if embedder_connection is None:
            errors.append(
                {
                    "path": f"knowledge[{index}].kb_id",
                    "message": "this knowledge base's embedder connection no longer exists for this company",
                }
            )
            continue
        resolved.append((kb, embedder_connection))
    return resolved, errors


def _build_compiled_definition(
    doc: AgentSpecDoc,
    connection: ConnectionRow,
    tools: list[ToolRow],
    knowledge: list[tuple[KnowledgeBaseRow, ConnectionRow]],
) -> dict:
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
            ]
            + [
                {
                    "type": RETRIEVAL_TOOL_TYPE,
                    "id": kb.id,
                    "name": kb.name,
                    "config": {
                        "embedder_connection_id": kb.embedder_connection_id,
                        "embedder_connection_type": embedder_connection.type,
                        "embedder_connection_api_base": embedder_connection.api_base,
                        "top_k": _KNOWLEDGE_TOP_K,
                    },
                }
                for kb, embedder_connection in knowledge
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


def _build_http_tool(tool_def: dict, company_id: str):
    """Uses `SafeHttpApiCall`, not Dynamiq's own `HttpApiCall`, so every real request this
    tool makes is checked against the company's egress allowlist - see that class's
    docstring for why the bare Dynamiq node can't be used as-is here."""
    from dynamiq.connections.connections import Http

    from app.integrations.safe_http_tool import SafeHttpApiCall

    config = tool_def["config"]
    connection = Http(
        url=config.get("url", ""),
        method=config.get("method", "GET"),
        headers=config.get("headers") or {},
    )
    return SafeHttpApiCall(
        name=tool_def["name"],
        description=config.get("description", ""),
        connection=connection,
        company_id=company_id,
    )


async def _build_knowledge_tool(tool_def: dict, company_id: str, api_key: str):
    """Constructs a real `VectorStoreRetriever` for one `doc.knowledge` entry.

    Opens the per-KB pgvector table for real (`open_kb_vector_store`, via
    `asyncio.to_thread` since it's a synchronous DB call) - this is what turns "this
    knowledge base has no indexed documents yet" into a real, caught exception both at
    compile-proof time (rejecting publish) and at actual agent run time, exactly like
    `app.services.kb_search.search_knowledge_base` already does for the standalone
    `/kb/search` endpoint.
    """
    from dynamiq.components.retrievers.pgvector import PGVectorDocumentRetriever
    from dynamiq.nodes.retrievers.retriever import VectorStoreRetriever

    config = tool_def["config"]
    kb_id = tool_def["id"]
    _connection, embedder = build_embedder(
        config["embedder_connection_type"],
        {"api_key": api_key, "url": config.get("embedder_connection_api_base")},
    )
    table_name = kb_vector_table_name(company_id, kb_id)
    store = await asyncio.to_thread(open_kb_vector_store, table_name)
    retriever = PGVectorDocumentRetriever(vector_store=store, top_k=config["top_k"])
    return VectorStoreRetriever(
        name=tool_def["name"],
        text_embedder=embedder,
        document_retriever=retriever,
        top_k=config["top_k"],
    )


async def _build_tools(tool_defs: list[dict], company_id: str, knowledge_api_keys: dict[str, str]) -> list:
    """Constructs real Dynamiq tool/retrieval node instances from
    `compiled_definition["agent"]["tools"]`. `_resolve_tools`/`_resolve_knowledge` already
    guarantee only `_HTTP_TOOL_TYPE`/`RETRIEVAL_TOOL_TYPE` entries ever reach here - the
    explicit check below is a defensive backstop (matches this codebase's general
    defense-in-depth style), not the primary gate.
    """
    tools = []
    for tool_def in tool_defs:
        if tool_def["type"] == _HTTP_TOOL_TYPE:
            tools.append(_build_http_tool(tool_def, company_id))
        elif tool_def["type"] == RETRIEVAL_TOOL_TYPE:
            tools.append(await _build_knowledge_tool(tool_def, company_id, knowledge_api_keys[tool_def["id"]]))
        else:
            raise ValueError(f"unsupported tool type for execution: {tool_def['type']!r}")
    return tools


async def build_agent(
    compiled_definition: dict,
    api_key: str,
    company_id: str,
    memory=None,
    skill_registry: ConsoleSkillRegistry | None = None,
    egress_allowlist: list[EgressAllowlistEntry] | None = None,
    knowledge_api_keys: dict[str, str] | None = None,
):
    """Constructs a real `dynamiq.nodes.agents.Agent` from a `compiled_definition`
    (PRD §6.1) - used two ways: `compile_spec`'s own proof-check above, with
    `_COMPILE_TIME_PLACEHOLDER_API_KEY`, no memory, no skill_registry, and the result
    discarded (only proving it's buildable), and Playground's agent-version resolution
    (`app.services.runtime`), with the connection's real decrypted secret, real
    conversation memory and skill registry when available, and the returned Agent
    actually run.

    `egress_allowlist` only matters when the connection has a custom `api_base`
    (`app.integrations.dynamiq_adapter._build_openai`/`SafeOpenAIConnection`) - omitting
    it is fail-closed, not fail-open: a custom-api_base connection with no allowlist
    passed through simply blocks every request rather than skipping the check.

    `knowledge_api_keys` maps each attached knowledge base's id to the embedder API key to
    build it with - a KB's embedder connection is independent of the agent's own LLM
    connection, so (unlike `api_key` above) this can't be a single shared value. Required
    whenever `compiled_definition` has any `RETRIEVAL_TOOL_TYPE` tool entries; the two
    callers build it differently (`compile_spec`: every KB mapped to the same compile-time
    placeholder; `app.services.runtime`: each KB's own decrypted secret).
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
            "egress_allowlist": egress_allowlist,
        },
    )

    return Agent(
        name=agent_def["name"],
        llm=llm,
        role=agent_def["role"],
        max_loops=agent_def["max_loops"],
        tools=await _build_tools(agent_def.get("tools", []), company_id, knowledge_api_keys or {}),
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
