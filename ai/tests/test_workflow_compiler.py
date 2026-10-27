import json
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main_runtime import app as runtime_app
from app.services.compiler import compile_spec
from app.services.workflow_compiler import compile_workflow_spec

from .markers import requires_postgres

VALID_IDENTITY = {
    "name": "test-agent",
    "display_name": "Test Agent",
    "owner": "qa",
    "role": "answers test questions",
    "languages": ["en"],
}


def _manager(connection_id: str) -> dict:
    return {"connection_id": connection_id, "model": "gpt-4o-mini"}


def _workflow_spec(manager_connection_id: str, agent_version_id: str) -> dict:
    return {
        "states": [{"id": "state1", "agent_version_id": agent_version_id, "next_states": []}],
        "initial_state": "state1",
        "manager": _manager(manager_connection_id),
    }


def _connect() -> psycopg.Connection:
    settings = get_settings()
    return psycopg.connect(
        host=settings.MEMORY_DB_HOST,
        port=settings.MEMORY_DB_PORT,
        dbname=settings.MEMORY_DB_NAME,
        user="console_app",
        password="changeme_local_dev_only",
    )


def _publish(company_id: str, agent_version_id: str, compiled_definition: dict) -> None:
    """Directly sets is_published=true + a real compiled_definition on an
    `agent_versions` row created by the `make_agent_version` fixture - that fixture only
    creates an unpublished draft (by design, shared with Playground tests), so workflow
    compiler tests that need a *published* agent (the only kind a workflow may reference)
    publish it themselves here rather than going through the full AgentsController HTTP
    flow this test suite doesn't exercise."""
    with _connect() as conn:
        conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
        conn.execute(
            """UPDATE console.agent_versions
               SET is_published = true, compiled_definition = %s, compiler_version = '1',
                   dynamiq_version = 'test'
               WHERE id = %s""",
            (json.dumps(compiled_definition), agent_version_id),
        )
        conn.commit()


async def test_missing_states_section_fails_without_db():
    result = await compile_workflow_spec(
        {"initial_state": "x", "manager": _manager(str(uuid.uuid4()))}, "developer", str(uuid.uuid4())
    )
    assert result["ok"] is False
    assert result["compiled_definition"] is None
    assert any(error["path"] == "states" for error in result["errors"])


def test_compile_route_requires_internal_token():
    resp = TestClient(runtime_app).post(
        "/internal/v1/workflows/compile",
        json={"spec": {}, "role": "developer"},
        headers={"X-Company-Id": "company-1"},
    )
    assert resp.status_code == 401


def test_compile_route_returns_200_for_a_bad_spec(runtime_client):
    resp = runtime_client.post("/internal/v1/workflows/compile", json={"spec": {}, "role": "developer"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["compiled_definition"] is None
    assert len(body["errors"]) > 0


@requires_postgres
async def test_unknown_agent_version_id_reports_not_found(make_connection):
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id)

    spec = _workflow_spec(connection_id, str(uuid.uuid4()))
    result = await compile_workflow_spec(spec, "developer", company_id)

    assert result["ok"] is False
    assert any(
        error["path"] == "states[0].agent_version_id" and "no agent version" in error["message"]
        for error in result["errors"]
    )


@requires_postgres
async def test_unpublished_agent_version_reports_not_published(make_connection, make_agent_version):
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id)
    agent_version_id = make_agent_version(company_id, connection_id)  # unpublished by default

    spec = _workflow_spec(connection_id, agent_version_id)
    result = await compile_workflow_spec(spec, "developer", company_id)

    assert result["ok"] is False
    assert any(
        error["path"] == "states[0].agent_version_id" and "not published" in error["message"]
        for error in result["errors"]
    )


@requires_postgres
async def test_compile_happy_path_constructs_a_real_graph_orchestrator(make_connection, make_agent_version):
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id)
    agent_version_id = make_agent_version(company_id, connection_id)

    agent_compile_result = await compile_spec(
        {"identity": VALID_IDENTITY, "model": {"connection_id": connection_id, "model": "gpt-4o-mini"}},
        "developer",
        company_id,
    )
    assert agent_compile_result["ok"] is True, agent_compile_result["errors"]
    _publish(company_id, agent_version_id, agent_compile_result["compiled_definition"])

    spec = _workflow_spec(connection_id, agent_version_id)
    result = await compile_workflow_spec(spec, "developer", company_id)

    assert result["ok"] is True, result["errors"]
    assert result["compiled_definition"]["workflow"]["type"] == (
        "dynamiq.nodes.agents.orchestrators.graph.GraphOrchestrator"
    )
    assert result["compiled_definition"]["workflow"]["states"][0]["id"] == "state1"
    assert result["compiled_definition"]["workflow"]["states"][0]["next_states"] == ["END"]
    assert result["compiler_version"]
    assert result["dynamiq_version"]


@requires_postgres
async def test_compile_proves_a_state_agent_with_a_fallback_model_is_buildable(make_connection, make_agent_version):
    """Regression guard: _build_workflow's own self-proof build_agent() call must thread
    fallback_api_key through exactly like compiler.compile_spec's own self-proof call does
    - a state's agent having a fallback configured must not touch a real ambient
    OPENAI_API_KEY env var or crash with no key, during what is supposed to be a pure,
    never-a-real-call compile-time proof (found by code-review before this shipped)."""
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id)
    fallback_connection_id = make_connection(company_id, name="fallback-connection")
    agent_version_id = make_agent_version(company_id, connection_id)

    agent_compile_result = await compile_spec(
        {
            "identity": VALID_IDENTITY,
            "model": {
                "connection_id": connection_id,
                "model": "gpt-4o-mini",
                "fallback_connection_id": fallback_connection_id,
                "fallback_model": "gpt-4o",
            },
        },
        "developer",
        company_id,
    )
    assert agent_compile_result["ok"] is True, agent_compile_result["errors"]
    _publish(company_id, agent_version_id, agent_compile_result["compiled_definition"])

    spec = _workflow_spec(connection_id, agent_version_id)
    result = await compile_workflow_spec(spec, "developer", company_id)

    assert result["ok"] is True, result["errors"]


@requires_postgres
async def test_multi_target_branch_compiles(make_connection, make_agent_version):
    """Proves the manager-routed branching case (2+ next_states, no condition) is
    constructible, even though it's never executed this slice. Reuses one published agent
    version across all three states - `make_agent_version` hardcodes a single agent name
    per company (`UNIQUE (company_id, name)`), and this test only needs to prove the
    branching *graph shape* compiles, not that each state has a distinct agent."""
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id)
    agent_version_id = make_agent_version(company_id, connection_id)

    agent_compile_result = await compile_spec(
        {"identity": VALID_IDENTITY, "model": {"connection_id": connection_id, "model": "gpt-4o-mini"}},
        "developer",
        company_id,
    )
    assert agent_compile_result["ok"] is True, agent_compile_result["errors"]
    _publish(company_id, agent_version_id, agent_compile_result["compiled_definition"])

    spec = {
        "states": [
            {"id": "start", "agent_version_id": agent_version_id, "next_states": ["a", "b"]},
            {"id": "a", "agent_version_id": agent_version_id, "next_states": []},
            {"id": "b", "agent_version_id": agent_version_id, "next_states": []},
        ],
        "initial_state": "start",
        "manager": _manager(connection_id),
    }
    result = await compile_workflow_spec(spec, "developer", company_id)

    assert result["ok"] is True, result["errors"]


@requires_postgres
async def test_multi_target_branch_including_end_compiles(make_connection, make_agent_version):
    """The literal `END` sentinel is always pre-registered in `GraphOrchestrator._state_by_id`
    (dynamiq's own `__init__` adds it), so it must be a valid target even inside a
    manager-routed multi-target branch, not just as a lone `add_edge` destination - a spec
    like `next_states: ["other", "END"]` is spec-valid per `workflow_spec.py`'s own
    validator (END is always an allowed target), so the compiler must handle it too."""
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id)
    agent_version_id = make_agent_version(company_id, connection_id)

    agent_compile_result = await compile_spec(
        {"identity": VALID_IDENTITY, "model": {"connection_id": connection_id, "model": "gpt-4o-mini"}},
        "developer",
        company_id,
    )
    assert agent_compile_result["ok"] is True, agent_compile_result["errors"]
    _publish(company_id, agent_version_id, agent_compile_result["compiled_definition"])

    spec = {
        "states": [
            {"id": "start", "agent_version_id": agent_version_id, "next_states": ["other", "END"]},
            {"id": "other", "agent_version_id": agent_version_id, "next_states": []},
        ],
        "initial_state": "start",
        "manager": _manager(connection_id),
    }
    result = await compile_workflow_spec(spec, "developer", company_id)

    assert result["ok"] is True, result["errors"]
