"""Workflow spec compiler (PRD §6.9, phase 4, `/internal/v1/workflows/compile`).

Resolves each state's `agent_version_id` against the referenced agent's own *published*
`compiled_definition` (not its live spec) - a workflow's whole premise (confirmed scope
decision) is "references an EXISTING, PUBLISHED company Agent," so there's nothing left
to recompile: `compiled_definition` already has everything `compiler.build_agent` needs
(resolved connection/tool/knowledge refs), and resolving the published snapshot instead
of the live spec means a workflow's compile step never touches a connection/tool secret
of its own and can't fail because of an unrelated, transient change to an agent's draft.

Mechanical note on the edge model (`app.services.workflow_spec`'s own module docstring
has the authoring-side half of this): reading `GraphOrchestrator.add_edge()` directly
(installed at `dynamiq/nodes/agents/orchestrators/graph.py`) shows it does
`state.next_states = [destination_id]` - this *replaces* a state's `next_states`, so only
one `add_edge` call sticks per source. A state with 2+ `next_states` and no `condition`
falls through to `GraphOrchestrator._get_next_state()`'s `get_next_state_by_manager()`
branch at run time - the `GraphAgentManager` LLM picks the next state purely from each
candidate's description, no `Callable`/`Python` condition object required. This compiler
builds that branching case by setting a state's `next_states` directly (bypassing
`add_conditional_edge`, which mandates a condition object this slice deliberately doesn't
have - no code-execution/sandboxing story exists yet, PRD Non-goal #3).

Like `compiler.compile_spec`, compile *failures* (bad spec, cross-company/unpublished
agent reference, disallowed node type) are a normal, successful result (`ok: False` +
`errors`), never an exception; only a genuine infrastructure failure propagates, for the
route to turn into a 502.
"""
import importlib.metadata
import logging

from pydantic import ValidationError

from app.integrations.dynamiq_adapter import build_llm
from app.services.agent_versions import PublishedAgentVersionRow, resolve_published_agent_version_for_workflow
from app.services.compiler import build_agent
from app.services.connections import ConnectionRow, resolve_connection
from app.services.node_allowlist import find_disallowed_types
from app.services.workflow_spec import END_STATE_ID, WorkflowSpecDoc

logger = logging.getLogger(__name__)

COMPILER_VERSION = "1"

# Never persisted, never logged, never used to make a real API call - same
# never-a-real-call philosophy as compiler.py's own placeholder of the same name.
_COMPILE_TIME_PLACEHOLDER_API_KEY = "compile-time-placeholder-not-a-real-key"

_GRAPH_ORCHESTRATOR_TYPE = "dynamiq.nodes.agents.orchestrators.graph.GraphOrchestrator"
_GRAPH_STATE_TYPE = "dynamiq.nodes.agents.orchestrators.graph_state.GraphState"
_GRAPH_MANAGER_TYPE = "dynamiq.nodes.agents.orchestrators.graph_manager.GraphAgentManager"
_RETRIEVAL_TOOL_TYPE = "dynamiq.nodes.retrievers.retriever.VectorStoreRetriever"


async def compile_workflow_spec(spec: dict, role: str, company_id: str) -> dict:
    try:
        doc = WorkflowSpecDoc.model_validate(spec)
    except ValidationError as exc:
        return _failure(_pydantic_errors(exc))

    manager_connection = await resolve_connection(company_id, doc.manager.connection_id)
    if manager_connection is None:
        return _failure(
            [{"path": "manager.connection_id", "message": "no connection with this id for this company"}]
        )

    resolved_states, state_errors = await _resolve_states(doc, company_id)
    if state_errors:
        return _failure(state_errors)

    compiled_definition = _build_compiled_definition(doc, manager_connection, resolved_states)

    disallowed = find_disallowed_types(compiled_definition, role)
    if disallowed:
        return _failure(
            [
                {"path": "states", "message": f"node type not allowed for role {role!r}: {node_type}"}
                for node_type in disallowed
            ]
        )

    try:
        await _build_workflow(compiled_definition, company_id)
    except Exception as exc:  # proves compiled_definition is actually buildable
        logger.exception("workflow spec compilation failed to construct a GraphOrchestrator")
        return _failure([{"path": "states", "message": f"failed to construct workflow: {exc}"}])

    return {
        "ok": True,
        "compiled_definition": compiled_definition,
        "compiler_version": COMPILER_VERSION,
        "dynamiq_version": importlib.metadata.version("dynamiq"),
        "errors": [],
    }


async def _resolve_states(
    doc: WorkflowSpecDoc, company_id: str
) -> tuple[dict[str, PublishedAgentVersionRow], list[dict]]:
    """Resolves + cross-company-validates every state's `agent_version_id`, collecting
    *all* errors rather than stopping at the first (same convention as
    `compiler._resolve_tools`/`_resolve_knowledge`). Distinguishes "no agent version with
    this id for this company" from "agent version exists but is not published" - two
    different, actionable errors (see `resolve_published_agent_version_for_workflow`'s own
    docstring for why both are resolvable without leaking cross-company existence)."""
    resolved: dict[str, PublishedAgentVersionRow] = {}
    errors: list[dict] = []
    for index, state in enumerate(doc.states):
        row = await resolve_published_agent_version_for_workflow(company_id, state.agent_version_id)
        if row is None:
            errors.append(
                {
                    "path": f"states[{index}].agent_version_id",
                    "message": "no agent version with this id for this company",
                }
            )
            continue
        if not row.is_published:
            errors.append(
                {
                    "path": f"states[{index}].agent_version_id",
                    "message": "agent version exists but is not published",
                }
            )
            continue
        resolved[state.id] = row
    return resolved, errors


def _build_compiled_definition(
    doc: WorkflowSpecDoc,
    manager_connection: ConnectionRow,
    resolved_states: dict[str, PublishedAgentVersionRow],
) -> dict:
    return {
        "version": 1,
        "workflow": {
            "type": _GRAPH_ORCHESTRATOR_TYPE,
            "initial_state": doc.initial_state,
            "max_loops": doc.max_loops,
            "manager": {
                "type": _GRAPH_MANAGER_TYPE,
                "model": doc.manager.model,
                "temperature": doc.manager.temperature,
                "max_tokens": doc.manager.max_tokens,
                "connection": {
                    "type": manager_connection.type,
                    "connection_id": manager_connection.id,
                    "name": manager_connection.name,
                    "api_base": manager_connection.api_base,
                },
            },
            "states": [
                {
                    "type": _GRAPH_STATE_TYPE,
                    "id": state.id,
                    "label": state.label,
                    "agent_version_id": state.agent_version_id,
                    # The resolved agent's own published snapshot - already validated/
                    # allowlisted under whatever role published *that* agent; walked again
                    # below (find_disallowed_types over the whole tree) under *this*
                    # publish's role, so an admin-only node type embedded in a referenced
                    # agent can't leak into a workflow a non-admin publishes.
                    "agent_compiled_definition": resolved_states[state.id].compiled_definition,
                    "next_states": state.next_states or [END_STATE_ID],
                }
                for state in doc.states
            ],
        },
    }


def build_manager(manager_def: dict, api_key: str) -> "GraphAgentManager":
    """Constructs a real `GraphAgentManager` (the router LLM) from a workflow's
    `compiled_definition["workflow"]["manager"]` dict - shared by `_build_workflow`'s
    compile-time proof (a placeholder key) and `workflow_runtime.py`'s real execution
    (a real decrypted connection secret), so the two never drift apart on how the
    manager's LLM is actually constructed."""
    from dynamiq.nodes.agents.orchestrators.graph_manager import GraphAgentManager

    connection_def = manager_def["connection"]
    _connection, manager_llm = build_llm(
        connection_def["type"],
        {
            "api_key": api_key,
            "model": manager_def["model"],
            "temperature": manager_def.get("temperature"),
            "max_tokens": manager_def.get("max_tokens"),
            "url": connection_def.get("api_base"),
        },
    )
    return GraphAgentManager(llm=manager_llm)


def wire_orchestrator_states(orchestrator: "GraphOrchestrator", workflow_def: dict, agents_by_state_id: dict) -> None:
    """Adds each state (wrapping its already-built Agent) and wires edges onto an
    already-constructed `GraphOrchestrator` - shared by `_build_workflow`'s compile-time
    proof (placeholder-keyed Agents) and `workflow_runtime.py`'s real execution (real
    Agents), so the two code paths' graph-shape wiring (which this module's own docstring
    already documents the mechanics of) can't drift apart."""
    for state_def in workflow_def["states"]:
        orchestrator.add_state_by_tasks(state_def["id"], [agents_by_state_id[state_def["id"]]])

    for state_def in workflow_def["states"]:
        targets = state_def["next_states"]
        if len(targets) == 1:
            orchestrator.add_edge(state_def["id"], targets[0])
        else:
            # Manager-routed dynamic branching (see module docstring) - deliberately
            # bypasses add_conditional_edge (which mandates a Callable|Python condition);
            # leaving `condition` unset makes GraphOrchestrator._get_next_state() fall
            # through to get_next_state_by_manager() at run time. validate_states() is the
            # same existence check add_edge()/add_conditional_edge() already run.
            orchestrator.validate_states(targets)
            orchestrator._state_by_id[state_def["id"]].next_states = targets


async def _build_workflow(compiled_definition: dict, company_id: str) -> None:
    """Constructs a real `GraphOrchestrator` from `compiled_definition` and discards it -
    proves it's buildable, never executed. Reuses `compiler.build_agent` per state rather
    than duplicating its tool/LLM/knowledge-base construction logic."""
    from dynamiq.nodes.agents.orchestrators.graph import GraphOrchestrator

    workflow_def = compiled_definition["workflow"]
    manager = build_manager(workflow_def["manager"], _COMPILE_TIME_PLACEHOLDER_API_KEY)

    orchestrator = GraphOrchestrator(
        manager=manager,
        initial_state=workflow_def["initial_state"],
        max_loops=workflow_def["max_loops"],
    )

    agents_by_state_id = {}
    for state_def in workflow_def["states"]:
        agent_compiled = state_def["agent_compiled_definition"]
        has_fallback = agent_compiled.get("agent", {}).get("llm", {}).get("fallback") is not None
        agents_by_state_id[state_def["id"]] = await build_agent(
            agent_compiled,
            _COMPILE_TIME_PLACEHOLDER_API_KEY,
            company_id,
            knowledge_api_keys=_placeholder_knowledge_api_keys(agent_compiled),
            # Same never-a-real-call self-proof rule as compiler.compile_spec's own
            # build_agent call - without this, a state whose agent has a fallback model
            # configured would build it with api_key=None, falling through to
            # OpenAIConnection's own ambient-env-var default instead of staying a pure
            # placeholder.
            fallback_api_key=_COMPILE_TIME_PLACEHOLDER_API_KEY if has_fallback else None,
        )

    wire_orchestrator_states(orchestrator, workflow_def, agents_by_state_id)


def _placeholder_knowledge_api_keys(agent_compiled: dict) -> dict[str, str]:
    """`compiler.build_agent` requires a `knowledge_api_keys` entry for every attached
    knowledge-base tool in `agent_compiled` - same compile-time-placeholder-only rule as
    `compiler.compile_spec`'s own self-proof call."""
    return {
        tool["id"]: _COMPILE_TIME_PLACEHOLDER_API_KEY
        for tool in agent_compiled.get("agent", {}).get("tools", [])
        if tool.get("type") == _RETRIEVAL_TOOL_TYPE
    }


def _pydantic_errors(exc: ValidationError) -> list[dict]:
    return [{"path": ".".join(str(part) for part in error["loc"]), "message": error["msg"]} for error in exc.errors()]


def _failure(errors: list[dict]) -> dict:
    return {
        "ok": False,
        "compiled_definition": None,
        "compiler_version": None,
        "dynamiq_version": None,
        "errors": errors,
    }
