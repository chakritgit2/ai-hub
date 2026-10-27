"""Agent spec schema — slice 1: Identity + Model (PRD §6.1/§6.1a); slice 2: Guardrails
(PRD §6.4); slice 3: Tools + Skills references (PRD §6.5/§6.6a); slice 4: Knowledge
references (PRD §6.6); slice 5: Memory (PRD §6.2) - resolution/cross-company
validation/node construction lives in `app.services.compiler`, not here; this module only
validates the *shape* a reference must have (a well-formed tool id, a skill name, a
well-formed KB id).

Advanced tab exists in the web editor already (every tab's data is sent in the spec
regardless of which ones have a real backing table), but still has no schema behind it —
`AgentSpecDoc` silently ignores anything else it doesn't recognize
(`model_config = ConfigDict(extra="ignore")`), rather than rejecting a spec just because of
an unrecognized key.

`AgentIdentitySpec`'s fields mirror `web/src/lib/api/types.ts`'s
`AgentIdentity` exactly — this is the one spec shape the editor already
commits to, not a new design. No committed `GuardrailsSpec` shape exists yet on the web
side (that tab isn't typed there), so this is a fresh design driven directly by PRD §6.4.
"""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    # Optional provider fallback (PRD §6.1/§11): "Provider errors (429/5xx): two retries
    # with backoff, then the fallback model if configured." Independent of connection_id -
    # may point at the same connection (different model string) or a different one
    # entirely (different provider/endpoint/secret).
    fallback_connection_id: str | None = None
    fallback_model: str | None = Field(default=None, min_length=1)

    @field_validator("connection_id")
    @classmethod
    def _connection_id_is_uuid(cls, value: str) -> str:
        import uuid

        uuid.UUID(value)  # raises ValueError -> wrapped as a pydantic ValidationError
        return value

    @field_validator("fallback_connection_id")
    @classmethod
    def _fallback_connection_id_is_uuid(cls, value: str | None) -> str | None:
        import uuid

        if value is not None:
            uuid.UUID(value)
        return value

    @model_validator(mode="after")
    def _fallback_fields_are_paired(self) -> "ModelSpec":
        if bool(self.fallback_connection_id) != bool(self.fallback_model):
            raise ValueError("fallback_connection_id and fallback_model must be set together")
        return self


GuardrailCheckType = Literal["max_length", "regex_blocklist", "pii", "valid_json", "valid_choices"]
GuardrailAction = Literal["block", "mask", "flag"]

# Checks with no identifiable "matched span" to redact - block/flag only, never mask.
_NO_MASK_CHECK_TYPES: frozenset[GuardrailCheckType] = frozenset({"max_length", "valid_json", "valid_choices"})
# Checks that only make sense against the model's output, never the user's input.
_OUTPUT_ONLY_CHECK_TYPES: frozenset[GuardrailCheckType] = frozenset({"valid_json", "valid_choices"})


class GuardrailCheckSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Named `check_type`, not `type` - `node_allowlist.find_disallowed_types()` walks
    # `compiled_definition` collecting every string under a literal `"type"` key to
    # enforce the role-based node-type allowlist; a guardrail check sharing that key name
    # would get its check kind (e.g. "max_length") misread as a disallowed dynamiq node
    # type and fail compilation for every spec that uses guardrails.
    check_type: GuardrailCheckType
    action: GuardrailAction
    on_error: Literal["block", "flag"] = "flag"
    fallback_message: str | None = None

    # check_type == "max_length"
    max_length: int | None = Field(default=None, gt=0)
    # check_type == "regex_blocklist"
    pattern: str | None = None
    # check_type == "valid_choices"
    choices: list[str] | None = None

    @model_validator(mode="after")
    def _validate_type_specific_params(self) -> "GuardrailCheckSpec":
        if self.action == "mask" and self.check_type in _NO_MASK_CHECK_TYPES:
            raise ValueError(f"{self.check_type!r} checks don't support action='mask' (nothing to redact)")

        if self.check_type == "max_length" and self.max_length is None:
            raise ValueError("max_length check requires 'max_length'")

        if self.check_type == "regex_blocklist":
            if not self.pattern:
                raise ValueError("regex_blocklist check requires 'pattern'")
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"regex_blocklist 'pattern' does not compile: {exc}") from exc

        if self.check_type == "valid_choices" and not self.choices:
            raise ValueError("valid_choices check requires a non-empty 'choices' list")

        return self


class GuardrailsSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    input: list[GuardrailCheckSpec] = Field(default_factory=list)
    output: list[GuardrailCheckSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_output_only_checks_not_in_input(self) -> "GuardrailsSpec":
        offending = [check.check_type for check in self.input if check.check_type in _OUTPUT_ONLY_CHECK_TYPES]
        if offending:
            raise ValueError(f"check types not valid for input: {offending}")
        return self


class ToolRefSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tool_id: str

    @field_validator("tool_id")
    @classmethod
    def _tool_id_is_uuid(cls, value: str) -> str:
        import uuid

        uuid.UUID(value)  # raises ValueError -> wrapped as a pydantic ValidationError
        return value


MemorySaveMode = Literal["full", "input_output"]


class MemorySpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Defaults match what every agent already does today (app.services.runtime wires
    # Dynamiq's memory unconditionally with its own defaults) - an agent that never
    # touches this tab sees no behavior change.
    enabled: bool = True
    message_limit: int | None = Field(default=None, gt=0)
    save_mode: MemorySaveMode = "full"


class PublishGateSpec(BaseModel):
    """PRD §6.7 "Publish gate (e.g. faithfulness ≥ 0.8)" - opt-in per agent version
    (`enabled` defaults to False, zero behavior change for every agent that never
    touches this tab). Never merged into `compiled_definition` (see compiler.py) -
    this is a publish-time-only check (`AgentsController::publishAgentVersion`
    reads it straight from the agent version's own `spec` column), never consulted
    by a running agent."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    metric: str | None = None
    threshold: float | None = None

    @model_validator(mode="after")
    def _require_metric_and_threshold_when_enabled(self) -> "PublishGateSpec":
        if self.enabled and (self.metric is None or self.threshold is None):
            raise ValueError("publish_gate.metric and publish_gate.threshold are required when enabled")
        return self


class KnowledgeRefSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kb_id: str

    @field_validator("kb_id")
    @classmethod
    def _kb_id_is_uuid(cls, value: str) -> str:
        import uuid

        uuid.UUID(value)  # raises ValueError -> wrapped as a pydantic ValidationError
        return value


class AgentSpecDoc(BaseModel):
    model_config = ConfigDict(extra="ignore")

    identity: AgentIdentitySpec
    model: ModelSpec
    guardrails: GuardrailsSpec = Field(default_factory=GuardrailsSpec)
    tools: list[ToolRefSpec] = Field(default_factory=list)
    # Skill *names*, not ids - SkillsTool/ConsoleSkillRegistry already address skills by
    # name (PRD §6.6a: "SkillsTool (list -> get)"), not id.
    skills: list[str] = Field(default_factory=list)
    knowledge: list[KnowledgeRefSpec] = Field(default_factory=list)
    memory: MemorySpec = Field(default_factory=MemorySpec)
    publish_gate: PublishGateSpec = Field(default_factory=PublishGateSpec)


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
