"""Agent spec compiler stub (PRD §4.4-A, §6.1, `/internal/v1/agents/compile`).

Real implementation validates an agent spec (JSON) against a role-based
node-type allowlist (`admin`/`developer`/`viewer` gate which node types can
be used), verifies every referenced connection/KB/skill/tool belongs to the
agent's company (PRD §7.7), and compiles it into a Dynamiq definition,
validating it can actually be constructed by the installed Dynamiq version.
"""


def compile_spec(spec: dict, role: str) -> dict:
    raise NotImplementedError("compile_spec: agent spec compiler not yet implemented")
