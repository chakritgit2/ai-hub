import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main_runtime import app as runtime_app
from app.services.compiler import compile_spec

from .markers import requires_postgres

VALID_IDENTITY = {
    "name": "vending-support",
    "display_name": "Vending Helper",
    "owner": "CS team",
    "role": "Answers customer questions about using machines and refunds.",
    "languages": ["th", "en"],
}


def _valid_spec(connection_id: str) -> dict:
    return {
        "identity": VALID_IDENTITY,
        "model": {"connection_id": connection_id, "model": "gpt-4o-mini"},
    }


async def test_missing_identity_section_fails_without_db():
    spec = {"model": {"connection_id": str(uuid.uuid4()), "model": "gpt-4o-mini"}}
    result = await compile_spec(spec, "developer", str(uuid.uuid4()))

    assert result["ok"] is False
    assert result["compiled_definition"] is None
    assert any(error["path"] == "identity" for error in result["errors"])


async def test_missing_model_section_fails_without_db():
    result = await compile_spec({"identity": VALID_IDENTITY}, "developer", str(uuid.uuid4()))

    assert result["ok"] is False
    assert any(error["path"] == "model" for error in result["errors"])


def test_compile_route_requires_internal_token():
    """Real auth, no override — matches test_health.py's test_internal_route_requires_internal_token."""
    resp = TestClient(runtime_app).post(
        "/internal/v1/agents/compile",
        json={"spec": {}, "role": "developer"},
        headers={"X-Company-Id": "company-1"},
    )
    assert resp.status_code == 401


def test_compile_route_rejects_missing_role(runtime_client):
    resp = runtime_client.post("/internal/v1/agents/compile", json={"spec": {}})
    assert resp.status_code == 422


def test_compile_route_returns_200_for_a_bad_spec(runtime_client):
    """A malformed spec is a normal 200 with ok:false (ai-internal.yaml's CompileResult
    contract) — never a 4xx/5xx, even though auth and request shape are both fine."""
    resp = runtime_client.post("/internal/v1/agents/compile", json={"spec": {}, "role": "developer"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["compiled_definition"] is None
    assert len(body["errors"]) > 0


@pytest.fixture
def make_connection():
    """Inserts a `console.connections` row as `console_app` (console-api's own write
    role — `ai_app` has no write grant on this table by design, PRD §8.1) and deletes it
    afterward. Local-dev-only credentials, matching the `changeme_local_dev_only`
    convention used everywhere else in this repo's local setup."""
    settings = get_settings()
    created: list[tuple[str, str]] = []  # (company_id, connection_id)

    def _connect() -> psycopg.Connection:
        return psycopg.connect(
            host=settings.MEMORY_DB_HOST,
            port=settings.MEMORY_DB_PORT,
            dbname=settings.MEMORY_DB_NAME,
            user="console_app",
            password="changeme_local_dev_only",
        )

    def _make(
        company_id: str,
        connection_type: str = "dynamiq.connections.OpenAI",
        name: str = "test-connection",
    ) -> str:
        connection_id = str(uuid.uuid4())
        with _connect() as conn:
            # SET LOCAL doesn't accept a bound parameter — company_id here is always our
            # own freshly generated uuid4, never external input.
            conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
            conn.execute(
                "INSERT INTO console.connections (id, company_id, name, type) VALUES (%s, %s, %s, %s)",
                (connection_id, company_id, name, connection_type),
            )
            conn.commit()
        created.append((company_id, connection_id))
        return connection_id

    yield _make

    for company_id, connection_id in created:
        with _connect() as conn:
            conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
            conn.execute("DELETE FROM console.connections WHERE id = %s", (connection_id,))
            conn.commit()


@requires_postgres
async def test_compile_happy_path_constructs_a_real_agent(make_connection):
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id)

    result = await compile_spec(_valid_spec(connection_id), "developer", company_id)

    assert result["ok"] is True, result["errors"]
    assert result["compiled_definition"]["agent"]["name"] == "vending-support"
    assert result["compiled_definition"]["agent"]["llm"]["type"] == "dynamiq.nodes.llms.OpenAI"
    assert result["compiled_definition"]["agent"]["llm"]["connection"]["connection_id"] == connection_id
    assert result["compiler_version"]
    assert result["dynamiq_version"]


@requires_postgres
async def test_compile_rejects_unknown_connection_id(make_connection):
    company_id = str(uuid.uuid4())
    make_connection(company_id)  # a real connection exists, just not this id

    result = await compile_spec(_valid_spec(str(uuid.uuid4())), "developer", company_id)

    assert result["ok"] is False
    assert result["errors"][0]["path"] == "model.connection_id"


@requires_postgres
async def test_compile_rejects_cross_company_connection(make_connection):
    company_a = str(uuid.uuid4())
    company_b = str(uuid.uuid4())
    connection_id = make_connection(company_b)

    result = await compile_spec(_valid_spec(connection_id), "developer", company_a)

    assert result["ok"] is False
    assert result["errors"][0]["path"] == "model.connection_id"


@requires_postgres
async def test_compile_rejects_disallowed_node_type_for_developer(make_connection):
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id, connection_type="dynamiq.nodes.tools.python.Python")

    result = await compile_spec(_valid_spec(connection_id), "developer", company_id)

    assert result["ok"] is False
    assert "not allowed for role" in result["errors"][0]["message"]


@requires_postgres
async def test_compile_allows_admin_past_the_allowlist_but_still_fails_to_build(make_connection):
    """Proves the allowlist gate actually fires (admin gets past it, developer doesn't,
    see the previous test) rather than just happening to reject everything — admin still
    fails here, but for an entirely different reason (no LLM builder for this type)."""
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id, connection_type="dynamiq.nodes.tools.python.Python")

    result = await compile_spec(_valid_spec(connection_id), "admin", company_id)

    assert result["ok"] is False
    assert "not allowed for role" not in result["errors"][0]["message"]
    assert "Unsupported connection type" in result["errors"][0]["message"]
