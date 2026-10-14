import pytest
from pydantic import ValidationError

from app.services.agent_spec import (
    AgentIdentitySpec,
    AgentSpecDoc,
    MemorySpec,
    ModelSpec,
    PublishGateSpec,
    render_identity_prompt,
)

VALID_IDENTITY = {
    "name": "vending-support",
    "display_name": "Vending Helper",
    "owner": "CS team",
    "role": "Answers customer questions about using machines and refunds.",
    "languages": ["th", "en"],
}

VALID_MODEL = {
    "connection_id": "3f2b8c1e-0000-4000-8000-000000000000",
    "model": "gpt-4o-mini",
}


def test_valid_spec_parses():
    doc = AgentSpecDoc.model_validate({"identity": VALID_IDENTITY, "model": VALID_MODEL})
    assert doc.identity.name == "vending-support"
    assert doc.model.model == "gpt-4o-mini"


def test_unknown_top_level_sections_are_ignored():
    doc = AgentSpecDoc.model_validate(
        {"identity": VALID_IDENTITY, "model": VALID_MODEL, "tools": [], "guardrails": {"whatever": True}}
    )
    assert doc.identity.name == "vending-support"


def test_missing_identity_name_is_rejected():
    identity = {**VALID_IDENTITY}
    del identity["name"]
    with pytest.raises(ValidationError) as exc_info:
        AgentSpecDoc.model_validate({"identity": identity, "model": VALID_MODEL})
    locs = [error["loc"] for error in exc_info.value.errors()]
    assert ("identity", "name") in locs


def test_empty_languages_is_rejected():
    identity = {**VALID_IDENTITY, "languages": []}
    with pytest.raises(ValidationError):
        AgentSpecDoc.model_validate({"identity": identity, "model": VALID_MODEL})


def test_non_uuid_connection_id_is_rejected():
    model = {**VALID_MODEL, "connection_id": "not-a-uuid"}
    with pytest.raises(ValidationError):
        AgentSpecDoc.model_validate({"identity": VALID_IDENTITY, "model": model})


def test_render_identity_prompt_includes_decline_and_handoff_instructions():
    identity = AgentIdentitySpec.model_validate(
        {
            **VALID_IDENTITY,
            "out_of_scope": "Never grants discounts.",
            "handoff": "Refund above 500 THB -> call center.",
        }
    )
    prompt = render_identity_prompt(identity)
    assert "Never grants discounts." in prompt
    assert "politely decline" in prompt
    assert "Refund above 500 THB -> call center." in prompt
    assert "hand off" in prompt.lower() or "connecting them to a human" in prompt
    assert "th, en" in prompt


def test_model_spec_rejects_non_positive_max_tokens():
    with pytest.raises(ValidationError):
        ModelSpec.model_validate({**VALID_MODEL, "max_tokens": 0})


def test_memory_spec_defaults_match_todays_always_on_behavior():
    memory = MemorySpec()
    assert memory.enabled is True
    assert memory.message_limit is None
    assert memory.save_mode == "full"


def test_agent_spec_doc_defaults_memory_when_omitted():
    doc = AgentSpecDoc.model_validate({"identity": VALID_IDENTITY, "model": VALID_MODEL})
    assert doc.memory == MemorySpec()


def test_memory_spec_rejects_non_positive_message_limit():
    with pytest.raises(ValidationError):
        MemorySpec.model_validate({"message_limit": 0})


def test_memory_spec_rejects_unknown_save_mode():
    with pytest.raises(ValidationError):
        MemorySpec.model_validate({"save_mode": "summarized"})


def test_publish_gate_spec_defaults_to_disabled():
    gate = PublishGateSpec()
    assert gate.enabled is False
    assert gate.metric is None
    assert gate.threshold is None


def test_agent_spec_doc_defaults_publish_gate_when_omitted():
    doc = AgentSpecDoc.model_validate({"identity": VALID_IDENTITY, "model": VALID_MODEL})
    assert doc.publish_gate == PublishGateSpec()


def test_publish_gate_spec_rejects_enabled_without_metric_and_threshold():
    with pytest.raises(ValidationError):
        PublishGateSpec.model_validate({"enabled": True})


def test_publish_gate_spec_rejects_enabled_with_only_metric():
    with pytest.raises(ValidationError):
        PublishGateSpec.model_validate({"enabled": True, "metric": "faithfulness"})


def test_publish_gate_spec_accepts_enabled_with_metric_and_threshold():
    gate = PublishGateSpec.model_validate({"enabled": True, "metric": "faithfulness", "threshold": 0.8})
    assert gate.enabled is True
    assert gate.metric == "faithfulness"
    assert gate.threshold == 0.8


def test_publish_gate_spec_allows_disabled_with_stale_metric_and_threshold():
    """A gate the user just unchecked still carries its old metric/threshold in the
    spec (AgentSpecTabs.svelte's buildPublishGateSpec() clears them on build, but
    this model itself must not reject a disabled gate with leftover values, same as
    enabling it again later should restore them for free)."""
    gate = PublishGateSpec.model_validate({"enabled": False, "metric": "faithfulness", "threshold": 0.8})
    assert gate.enabled is False
