import json
import uuid

import psycopg
import pytest

from app.core.config import get_settings
from app.services.agent_versions import recompile_all_published_agent_versions

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


def _connect() -> psycopg.Connection:
    settings = get_settings()
    return psycopg.connect(
        host=settings.MEMORY_DB_HOST,
        port=settings.MEMORY_DB_PORT,
        dbname=settings.MEMORY_DB_NAME,
        user="console_app",
        password="changeme_local_dev_only",
    )


def _publish(company_id: str, agent_version_id: str, spec: dict) -> None:
    """Publishes a draft `make_agent_version` row with a deliberately stale
    compiled_definition/compiler_version/dynamiq_version, and overwrites `spec` too - lets a
    test publish a version with a spec that will fail to recompile (e.g. a dangling
    connection_id) while still exercising the real publish-time columns."""
    with _connect() as conn:
        conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
        conn.execute(
            """UPDATE console.agent_versions
               SET is_published = true, spec = %s,
                   compiled_definition = %s, compiler_version = '0', dynamiq_version = 'stale'
               WHERE id = %s""",
            (json.dumps(spec), json.dumps({"stale": True}), agent_version_id),
        )
        conn.commit()


def _read_version(company_id: str, agent_version_id: str) -> dict:
    with _connect() as conn:
        conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
        row = conn.execute(
            "SELECT compiled_definition, compiler_version, dynamiq_version, is_published "
            "FROM console.agent_versions WHERE id = %s",
            (agent_version_id,),
        ).fetchone()
        conn.commit()
    return {
        "compiled_definition": row[0],
        "compiler_version": row[1],
        "dynamiq_version": row[2],
        "is_published": row[3],
    }


@requires_postgres
async def test_recompile_all_overwrites_a_published_version(make_connection, make_agent_version):
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id)
    agent_version_id = make_agent_version(company_id, connection_id)
    _publish(company_id, agent_version_id, _valid_spec(connection_id))

    result = await recompile_all_published_agent_versions()

    assert result.recompiled >= 1
    assert not any(f.agent_version_id == agent_version_id for f in result.failed)

    row = _read_version(company_id, agent_version_id)
    assert row["compiler_version"] != "0"
    assert row["dynamiq_version"] != "stale"
    assert row["compiled_definition"]["agent"]["name"] == "vending-support"


@requires_postgres
async def test_recompile_all_reports_a_failing_version_and_leaves_it_untouched(
    make_connection, make_agent_version
):
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id)
    agent_version_id = make_agent_version(company_id, connection_id)
    # A connection_id that doesn't exist for this company - same compile failure
    # test_compiler.py's own cross-company/dangling-connection tests use.
    _publish(company_id, agent_version_id, _valid_spec(str(uuid.uuid4())))

    result = await recompile_all_published_agent_versions()

    failure = next((f for f in result.failed if f.agent_version_id == agent_version_id), None)
    assert failure is not None, result.failed
    assert failure.error

    row = _read_version(company_id, agent_version_id)
    assert row["compiler_version"] == "0"
    assert row["dynamiq_version"] == "stale"
    assert row["compiled_definition"] == {"stale": True}


@requires_postgres
async def test_recompile_all_skips_unpublished_drafts(make_connection, make_agent_version):
    company_id = str(uuid.uuid4())
    connection_id = make_connection(company_id)
    agent_version_id = make_agent_version(company_id, connection_id)  # unpublished by default

    result = await recompile_all_published_agent_versions()

    assert not any(f.agent_version_id == agent_version_id for f in result.failed)
    row = _read_version(company_id, agent_version_id)
    assert row["is_published"] is False
    assert row["compiled_definition"] is None
