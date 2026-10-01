import pytest
from pydantic import ValidationError

from app.services.agent_spec import AgentIdentitySpec, AgentSpecDoc, ModelSpec, render_identity_prompt

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
