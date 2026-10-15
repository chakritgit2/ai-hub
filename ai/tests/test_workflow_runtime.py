import json
import os
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main_runtime import app as runtime_app
from app.main_runtime import require_runtime_auth
from app.services.compiler import compile_spec
from app.services.workflow_runtime import execute_workflow_run

from .markers import fake_workflow_runtime_claims, requires_openai_key, requires_postgres

VALID_IDENTITY = {
    "name": "test-agent",
    "display_name": "Test Agent",
    "owner": "qa",
    "role": "answers test questions",
    "languages": ["en"],
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


def _publish_agent(company_id: str, agent_version_id: str, compiled_definition: dict) -> None:
    """Directly publishes an `agent_versions` row created by `make_agent_version` - see
    `test_workflow_compiler.py`'s identical helper for why (that fixture only creates an
    unpublished draft, shared with Playground tests)."""
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


@pytest.fixture
def make_workflow_version():
    """Inserts `console.workflows` + `console.workflow_versions` rows directly - no
    WorkflowsController HTTP round trip needed, same `console_app`-role direct-SQL
    convention `make_agent_version`/`make_connection` already use. Always a DRAFT
    (never published) - deliberately, since this slice's whole point is proving a draft
    workflow version is runnable."""
    created: list[tuple[str, str, str]] = []  # (company_id, workflow_id, version_id)

    def _make(company_id: str, spec: dict) -> str:
        workflow_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        with _connect() as conn:
            conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
            conn.execute(
                "INSERT INTO console.workflows (id, company_id, name) VALUES (%s, %s, %s)",
                (workflow_id, company_id, "test-workflow"),
            )
            conn.execute(
                """INSERT INTO console.workflow_versions
                   (id, company_id, workflow_id, version_no, spec, spec_version)
                   VALUES (%s, %s, %s, 1, %s, '1')""",
                (version_id, company_id, workflow_id, json.dumps(spec)),
            )
            conn.commit()
        created.append((company_id, workflow_id, version_id))
        return version_id

    yield _make

    for company_id, workflow_id, version_id in created:
        with _connect() as conn:
            conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
            conn.execute("DELETE FROM console.workflow_versions WHERE id = %s", (version_id,))
            conn.execute("DELETE FROM console.workflows WHERE id = %s", (workflow_id,))
            conn.commit()


def _single_state_spec(agent_version_id: str, connection_id: str) -> dict:
    return {
        "states": [{"id": "only", "agent_version_id": agent_version_id, "next_states": []}],
        "initial_state": "only",
        "manager": {"connection_id": connection_id, "model": "gpt-4o-mini"},
    }


@requires_postgres
async def test_unknown_workflow_version_id_raises(company_ids):
    company_id = company_ids()

    with pytest.raises(ValueError, match="not found"):
        await execute_workflow_run(company_id, str(uuid.uuid4()), "developer", "hi")


@requires_postgres
async def test_cross_company_workflow_version_id_is_indistinguishable_from_not_found(
    company_ids, make_connection, make_agent_version, make_workflow_version
):
    """Same PRD §12 guarantee `test_playground_run.py`'s agent-version equivalent already
    proves - a version belonging to another company reads exactly like one that doesn't
    exist."""
    owner_company_id = company_ids()
    other_company_id = company_ids()
    connection_id = make_connection(owner_company_id)
    agent_version_id = make_agent_version(owner_company_id, connection_id)
    workflow_version_id = make_workflow_version(
        owner_company_id, _single_state_spec(agent_version_id, connection_id)
    )

    with pytest.raises(ValueError, match="not found"):
        await execute_workflow_run(other_company_id, workflow_version_id, "developer", "hi")


@requires_postgres
async def test_compile_failure_raises(company_ids, make_connection, make_workflow_version):
    """A spec referencing a nonexistent agent_version_id fails to compile - raised as a
    clear ValueError, never an unhandled exception from deep inside execution."""
    company_id = company_ids()
    connection_id = make_connection(company_id)
    workflow_version_id = make_workflow_version(
        company_id, _single_state_spec(str(uuid.uuid4()), connection_id)
    )

    with pytest.raises(ValueError, match="failed to compile"):
        await execute_workflow_run(company_id, workflow_version_id, "developer", "hi")


@requires_openai_key
@requires_postgres
async def test_run_workflow_returns_real_output(
    company_ids, make_connection, make_agent_version, make_workflow_version, store_connection_secret
):
    """End-to-end with a real LLM, mirroring `test_playground_run.py::
    test_run_playground_agent_returns_real_output` exactly. The workflow version created
    here is NEVER published (`make_workflow_version` always inserts a draft) - this is
    the concrete proof that a draft workflow version is runnable in Playground, the same
    way a draft agent version already is (only each state's *referenced agent* must be
    published, enforced separately by `compile_workflow_spec` itself)."""
    company_id = company_ids()
    connection_id = make_connection(company_id)
    await store_connection_secret(company_id, connection_id, os.environ["OPENAI_API_KEY"])
    agent_version_id = make_agent_version(company_id, connection_id)

    agent_compile_result = await compile_spec(
        {"identity": VALID_IDENTITY, "model": {"connection_id": connection_id, "model": "gpt-4o-mini"}},
        "developer",
        company_id,
    )
    assert agent_compile_result["ok"] is True, agent_compile_result["errors"]
    _publish_agent(company_id, agent_version_id, agent_compile_result["compiled_definition"])

    workflow_version_id = make_workflow_version(
        company_id, _single_state_spec(agent_version_id, connection_id)
    )

    result = await execute_workflow_run(
        company_id, workflow_version_id, "developer", "Say hi in 3 words."
    )

    assert result["output"]
    assert isinstance(result["output"], str)
    assert result["trace_id"]
    assert isinstance(result["latency_ms"], int)


def test_playground_workflow_run_requires_runtime_token() -> None:
    """Real auth, no override - matches test_playground_run.py's own agent-version test."""
    resp = TestClient(runtime_app).post("/ai/v1/playground/run-workflow", json={"input": "hi"})
    assert resp.status_code == 401


def test_playground_workflow_run_route_wired() -> None:
    """Even with a zero-UUID workflow_version_id claim that resolves to nothing, the
    route must be reachable and fail with a clear upstream error (502), never an
    import/wiring error (500)."""
    runtime_app.dependency_overrides[require_runtime_auth] = fake_workflow_runtime_claims()
    try:
        resp = TestClient(runtime_app).post("/ai/v1/playground/run-workflow", json={"input": "hi"})
        assert resp.status_code == 502
    finally:
        runtime_app.dependency_overrides.pop(require_runtime_auth, None)
