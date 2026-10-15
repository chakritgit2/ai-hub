import uuid

import pytest
from pydantic import ValidationError

from app.services.workflow_spec import WorkflowSpecDoc


def _manager(connection_id: str | None = None) -> dict:
    return {"connection_id": connection_id or str(uuid.uuid4()), "model": "gpt-4o-mini"}


def _state(state_id: str, next_states: list[str] | None = None, agent_version_id: str | None = None) -> dict:
    return {
        "id": state_id,
        "agent_version_id": agent_version_id or str(uuid.uuid4()),
        "next_states": next_states or [],
    }


def test_minimal_single_state_spec_is_valid():
    doc = WorkflowSpecDoc.model_validate(
        {"states": [_state("state1")], "initial_state": "state1", "manager": _manager()}
    )
    assert doc.states[0].id == "state1"
    assert doc.max_loops == 15


def test_two_state_chain_is_valid():
    doc = WorkflowSpecDoc.model_validate(
        {
            "states": [_state("state1", ["state2"]), _state("state2")],
            "initial_state": "state1",
            "manager": _manager(),
        }
    )
    assert len(doc.states) == 2


def test_explicit_end_reference_is_valid_without_being_declared():
    doc = WorkflowSpecDoc.model_validate(
        {"states": [_state("state1", ["END"])], "initial_state": "state1", "manager": _manager()}
    )
    assert doc.states[0].next_states == ["END"]


def test_multi_target_branch_with_no_condition_is_valid():
    """Real branching, manager-routed at run time - no code-execution condition needed."""
    doc = WorkflowSpecDoc.model_validate(
        {
            "states": [_state("state1", ["state2", "state3"]), _state("state2"), _state("state3")],
            "initial_state": "state1",
            "manager": _manager(),
        }
    )
    assert doc.states[0].next_states == ["state2", "state3"]


def test_duplicate_state_ids_rejected():
    with pytest.raises(ValidationError, match="duplicate state id"):
        WorkflowSpecDoc.model_validate(
            {"states": [_state("state1"), _state("state1")], "initial_state": "state1", "manager": _manager()}
        )


def test_initial_state_must_match_a_declared_state():
    with pytest.raises(ValidationError, match="does not match any declared state"):
        WorkflowSpecDoc.model_validate(
            {"states": [_state("state1")], "initial_state": "nope", "manager": _manager()}
        )


def test_dangling_edge_to_undeclared_state_rejected():
    with pytest.raises(ValidationError, match="undeclared state"):
        WorkflowSpecDoc.model_validate(
            {"states": [_state("state1", ["nope"])], "initial_state": "state1", "manager": _manager()}
        )


def test_unreachable_state_rejected():
    with pytest.raises(ValidationError, match="[Uu]nreachable"):
        WorkflowSpecDoc.model_validate(
            {
                "states": [_state("state1"), _state("state2")],
                "initial_state": "state1",
                "manager": _manager(),
            }
        )


def test_cycle_rejected():
    with pytest.raises(ValidationError, match="[Cc]ycle"):
        WorkflowSpecDoc.model_validate(
            {
                "states": [_state("state1", ["state2"]), _state("state2", ["state1"])],
                "initial_state": "state1",
                "manager": _manager(),
            }
        )


def test_agent_version_id_must_be_a_uuid():
    with pytest.raises(ValidationError):
        WorkflowSpecDoc.model_validate(
            {
                "states": [_state("state1", agent_version_id="not-a-uuid")],
                "initial_state": "state1",
                "manager": _manager(),
            }
        )


def test_manager_connection_id_must_be_a_uuid():
    with pytest.raises(ValidationError):
        WorkflowSpecDoc.model_validate(
            {"states": [_state("state1")], "initial_state": "state1", "manager": _manager("not-a-uuid")}
        )


def test_requires_at_least_one_state():
    with pytest.raises(ValidationError):
        WorkflowSpecDoc.model_validate({"states": [], "initial_state": "state1", "manager": _manager()})
