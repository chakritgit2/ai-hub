"""Workflow spec schema (PRD §6.9, Phase 4) - slice 1: CRUD + compile/validate only, no
execution yet. Resolution/cross-company validation/node construction lives in
`app.services.workflow_compiler`, not here; this module only validates the *shape* a spec
must have (well-formed state ids, a well-formed agent_version_id reference, a graph that's
actually walkable).

Each state references an *existing, published* company Agent by `agent_version_id` - no
inline agent definitions inside a workflow spec (confirmed scope decision). The graph
engine is `dynamiq.nodes.agents.orchestrators.graph.GraphOrchestrator` (states + edges +
an LLM-backed `GraphAgentManager` router), not the lower-level `Flow`/`NodeDependency` DAG.

Edge model (see `app.services.workflow_compiler`'s module docstring for the full mechanical
reasoning): each state's `next_states` is 0, 1, or many declared state ids.
- 0 entries -> the compiler auto-wires this state straight to dynamiq's own `END` sentinel
  (a state with no edges would otherwise crash at run time - `GraphOrchestrator` always
  needs somewhere to go next).
- 1 entry -> a deterministic transition (`GraphOrchestrator.add_edge`).
- 2+ entries, no condition -> real branching with zero code-execution surface: dynamiq's
  `GraphAgentManager` (an LLM) decides at run time which declared candidate to take next,
  based on each state's `label`. Deterministic/code-based conditional edges
  (`add_conditional_edge`'s `Callable | Python` condition) are explicitly out of scope this
  slice (PRD Non-goal #3, no code-execution/sandboxing story here yet).

Slice 1 also rejects any spec whose graph contains a cycle outright (no "stop after N
visits" authoring concept beyond the orchestrator's own blunt `max_loops` ceiling) - a
deliberate simplification, not a permanent limitation.
"""
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Reserved sentinel matching dynamiq's own GraphOrchestrator.END - a terminal sink, never
# a state the author declares. Always a valid `next_states` target even though it's not
# in `id_set`; never expanded further during reachability/cycle checks (it has no
# outgoing edges by definition).
END_STATE_ID = "END"


def _validate_uuid(value: str) -> str:
    uuid.UUID(value)  # raises ValueError -> wrapped as a pydantic ValidationError
    return value


class WorkflowStateSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    label: str | None = None  # shown to the manager LLM when routing between candidates
    agent_version_id: str
    next_states: list[str] = Field(default_factory=list)

    @field_validator("agent_version_id")
    @classmethod
    def _agent_version_id_is_uuid(cls, value: str) -> str:
        return _validate_uuid(value)


class WorkflowManagerSpec(BaseModel):
    """Identical shape to `agent_spec.ModelSpec` - the router's LLM connection is a
    required, independently-configured block, not borrowed from any one state's agent.
    States can reference agents on different connections/providers; picking one state's
    connection for the router would be arbitrary and would break if that state is later
    removed."""

    model_config = ConfigDict(extra="ignore")

    connection_id: str
    model: str = Field(min_length=1)
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, gt=0)

    @field_validator("connection_id")
    @classmethod
    def _connection_id_is_uuid(cls, value: str) -> str:
        return _validate_uuid(value)


class WorkflowSpecDoc(BaseModel):
    model_config = ConfigDict(extra="ignore")

    states: list[WorkflowStateSpec] = Field(min_length=1)
    initial_state: str
    manager: WorkflowManagerSpec
    max_loops: int = Field(default=15, gt=0)  # matches GraphOrchestrator's own default

    @model_validator(mode="after")
    def _validate_graph(self) -> "WorkflowSpecDoc":
        errors: list[str] = []

        ids = [state.id for state in self.states]
        seen: set[str] = set()
        dupes: set[str] = set()
        for state_id in ids:
            if state_id in seen:
                dupes.add(state_id)
            seen.add(state_id)
        if dupes:
            errors.append(f"duplicate state id(s): {sorted(dupes)}")

        id_set = set(ids)
        if self.initial_state not in id_set:
            errors.append(f"initial_state {self.initial_state!r} does not match any declared state")

        states_by_id = {state.id: state for state in self.states}
        for state in self.states:
            for target in state.next_states:
                if target != END_STATE_ID and target not in id_set:
                    errors.append(
                        f"state {state.id!r} has an edge to undeclared state {target!r}"
                    )

        if errors:
            raise ValueError("; ".join(errors))

        if self.initial_state in id_set:
            reachable = _bfs_reachable(self.initial_state, states_by_id)
            unreachable = id_set - reachable
            if unreachable:
                raise ValueError(f"unreachable state(s) from initial_state: {sorted(unreachable)}")

            cycle = _find_cycle(self.initial_state, states_by_id)
            if cycle is not None:
                raise ValueError(f"cycle detected in workflow graph: {' -> '.join(cycle)}")

        return self


def _bfs_reachable(start: str, states_by_id: dict[str, WorkflowStateSpec]) -> set[str]:
    seen = {start}
    queue = [start]
    while queue:
        current = queue.pop()
        if current == END_STATE_ID:
            continue  # a terminal sink, never expanded further
        for target in states_by_id[current].next_states:
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return seen


def _find_cycle(start: str, states_by_id: dict[str, WorkflowStateSpec]) -> list[str] | None:
    """DFS cycle detection over the (possibly branching) `next_states` graph - a state
    revisited while still on the current path (not just previously visited overall) is a
    real cycle."""
    path: list[str] = []
    on_path: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> list[str] | None:
        if node == END_STATE_ID:
            return None  # a terminal sink, never expanded further
        path.append(node)
        on_path.add(node)
        visited.add(node)
        for target in states_by_id[node].next_states:
            if target not in states_by_id and target != END_STATE_ID:
                continue  # dangling edges already reported separately
            if target in on_path:
                return path[path.index(target):] + [target]
            if target not in visited:
                found = visit(target)
                if found is not None:
                    return found
        path.pop()
        on_path.remove(node)
        return None

    return visit(start)
