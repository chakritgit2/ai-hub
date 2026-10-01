"""Role-based node-type allowlist (PRD §6.1/§12) — the compiler only emits node/connection
types permitted for the publisher's role; anything else fails compilation.

There's no closed enum of valid "type" strings anywhere in dynamiq itself —
`WorkflowYAMLLoader` resolves an arbitrary `"module.ClassName"` string via
`importlib`, so this allowlist is necessarily our own hand-maintained list,
checked against the literal dotted-path strings this console ever writes into
a `compiled_definition`.

Slice 1 (Identity + Model) never puts a tool-type string into a
`compiled_definition` — there's no Tools section/table yet — so
`ADMIN_ONLY_NODE_TYPES` can't actually be hit via the compiler's normal path
yet. The mechanism is still real and tested directly (see
`tests/test_node_allowlist.py`), ready for Tools to start populating `type`
entries without this function needing to change.
"""

# Verified importable from the installed dynamiq==0.65.0.
ALWAYS_ALLOWED_NODE_TYPES: frozenset[str] = frozenset(
    {
        "dynamiq.nodes.agents.Agent",
        "dynamiq.connections.OpenAI",
        "dynamiq.nodes.llms.OpenAI",
    }
)

# Code execution — admin only (PRD §6.1, Non-goal #3, §6.6a).
ADMIN_ONLY_NODE_TYPES: frozenset[str] = frozenset(
    {
        "dynamiq.nodes.tools.python.Python",
        "dynamiq.nodes.tools.e2b_sandbox.E2BInterpreterTool",
        "dynamiq.nodes.tools.daytona_sandbox.DaytonaInterpreterTool",
    }
)


def allowed_node_types(role: str) -> frozenset[str]:
    if role == "admin":
        return ALWAYS_ALLOWED_NODE_TYPES | ADMIN_ONLY_NODE_TYPES
    return ALWAYS_ALLOWED_NODE_TYPES


def find_disallowed_types(compiled_tree: dict, role: str) -> list[str]:
    """Walks `compiled_tree` (dicts/lists, arbitrary depth), collects every string value
    found under a `"type"` key, and returns the ones not permitted for `role` — generalizes
    to any future node kind for free, since Tools/Knowledge/Skills just need to put a
    `"type"` key wherever they appear in the compiled tree."""
    allowed = allowed_node_types(role)
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "type" and isinstance(value, str):
                    found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(compiled_tree)

    return [node_type for node_type in found if node_type not in allowed]
