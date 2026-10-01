"""Agent spec schema — slice 1: Identity + Model only (PRD §6.1/§6.1a).

Tools/Knowledge/Skills/Memory/Guardrails/Advanced tabs exist in the web editor
already (every tab's data is sent in the spec regardless of which ones have a
real backing table), but none of those sections have CRUD or a schema behind
them yet — `AgentSpecDoc` only validates `identity`/`model` and silently
ignores anything else (`model_config = ConfigDict(extra="ignore")`), rather
than rejecting a spec just because of an empty `tools: []`.

`AgentIdentitySpec`'s fields mirror `web/src/lib/api/types.ts`'s
`AgentIdentity` exactly — this is the one spec shape the editor already
commits to, not a new design.
"""
from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentIdentitySpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    role: str = Field(min_length=1)
    languages: list[str] = Field(min_length=1)
    persona: str | None = None
    tags: list[str] = Field(default_factory=list)
    responsibilities: str | None = None
    in_scope: str | None = None
    out_of_scope: str | None = None
    handoff: str | None = None
    instructions: str | None = None


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    connection_id: str
    model: str = Field(min_length=1)
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, gt=0)

    @field_validator("connection_id")
    @classmethod
    def _connection_id_is_uuid(cls, value: str) -> str:
        import uuid

        uuid.UUID(value)  # raises ValueError -> wrapped as a pydantic ValidationError
        return value


class AgentSpecDoc(BaseModel):
    model_config = ConfigDict(extra="ignore")

    identity: AgentIdentitySpec
    model: ModelSpec


def render_identity_prompt(identity: AgentIdentitySpec) -> str:
    """Assembles Identity into the Dynamiq Agent's `role` (system prompt),
    per PRD §6.1a — plain string building, no templating engine needed for a
    handful of deterministic sections."""
    lines = [f"You are {identity.display_name} ({identity.name}).", identity.role]

    if identity.persona:
        lines.append(f"Persona: {identity.persona}")

    if identity.responsibilities:
        lines.append(f"Responsibilities: {identity.responsibilities}")

    if identity.in_scope:
        lines.append(f"In scope: {identity.in_scope}")

    if identity.out_of_scope:
        lines.append(
            f"Out of scope: {identity.out_of_scope} "
            "If asked about something out of scope, politely decline and explain "
            "that it's outside what you can help with."
        )

    if identity.handoff:
        lines.append(
            f"Hand off to a human when: {identity.handoff} "
            "When this condition is met, tell the user you're connecting them to a "
            "human and state the handoff channel."
        )

    lines.append(f"Respond in: {', '.join(identity.languages)} (first listed is primary).")

    if identity.instructions:
        lines.append(identity.instructions)

    return "\n\n".join(lines)
