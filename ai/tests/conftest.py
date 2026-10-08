import json
import uuid

import psycopg
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main_gateway import app as gateway_app
from app.main_runtime import app as runtime_app
from app.main_runtime import require_internal_auth, require_runtime_auth

from .markers import fake_internal_claims, fake_runtime_claims


@pytest.fixture
def runtime_client(request) -> TestClient:
    """`request.param`, if set (via `@pytest.mark.parametrize` or
    `pytest.fixture(params=...)`), is a company_id to bake into the fake verified
    runtime-token claims (see markers.fake_runtime_claims); otherwise company_id
    defaults to "" (no memory). Also bypasses the internal-call auth (markers.
    fake_internal_claims) so existing stub-wiring tests don't all need their own
    internal token — tests that care about enforcement use a bare TestClient instead."""
    company_id = getattr(request, "param", "")
    runtime_app.dependency_overrides[require_runtime_auth] = fake_runtime_claims(company_id)
    runtime_app.dependency_overrides[require_internal_auth] = fake_internal_claims()
    try:
        yield TestClient(runtime_app)
    finally:
        runtime_app.dependency_overrides.pop(require_runtime_auth, None)
        runtime_app.dependency_overrides.pop(require_internal_auth, None)


@pytest.fixture
def gateway_client() -> TestClient:
    return TestClient(gateway_app)


@pytest.fixture(autouse=True)
async def _fresh_engine():
    """`get_engine` is lru_cached, but each async test runs on its own event loop and
    asyncpg connections are bound to the loop that opened them - rebuild the engine per
    test and dispose it on the same loop afterwards."""
    from app.core.db import get_engine, get_sessionmaker

    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    yield
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


@pytest.fixture(autouse=True)
async def _fresh_redis():
    """Same event-loop-binding problem as `_fresh_engine`, for `app.core.redis.get_redis`
    (also `lru_cache`'d) and `app.core.redis.get_arq_pool` (its own module-global
    singleton, `create_pool` being async means it can't use `lru_cache` the same way)."""
    import app.core.redis as redis_module

    redis_module.get_redis.cache_clear()
    yield
    if redis_module.get_redis.cache_info().currsize:
        await redis_module.get_redis().aclose()
    redis_module.get_redis.cache_clear()
    if redis_module._arq_pool is not None:
        await redis_module._arq_pool.aclose()
    redis_module._arq_pool = None


@pytest.fixture
async def company_ids():
    """Hands out fresh company ids and, after the test, deletes every
    runtime.conversations / runtime.agent_memory / logs.guardrail_events / logs.runs /
    runtime.connection_secrets / runtime.company_keys / runtime.kb_documents row created
    under them - the full set of runtime.*/logs.* tables any Playground-resolution test
    (resolvable_agent_version, run_playground_agent) or KB test can write to for a given
    company.

    Only touches the DB during teardown, and only if an id was handed out,
    so tests that never use it don't need Postgres. guardrail_events is deleted before
    runs - its run_id is a foreign key into runs.
    """
    from app.core.db import get_company_session
    from app.db.tables import (
        company_keys_table,
        connection_secrets_table,
        conversations_table,
        guardrail_events_table,
        kb_documents_table,
        runs_table,
    )

    issued: list[str] = []

    def new_company_id() -> str:
        company_id = str(uuid.uuid4())
        issued.append(company_id)
        return company_id

    yield new_company_id

    for company_id in issued:
        company_uuid = uuid.UUID(company_id)
        async with get_company_session(company_id) as session:
            result = await session.execute(
                conversations_table.delete()
                .where(conversations_table.c.company_id == company_uuid)
                .returning(conversations_table.c.id)
            )
            session_ids = [str(row[0]) for row in result.fetchall()]
            if session_ids:
                await session.execute(
                    sa.text("DELETE FROM runtime.agent_memory WHERE metadata ->> 'session_id' = ANY(:ids)"),
                    {"ids": session_ids},
                )
            await session.execute(
                guardrail_events_table.delete().where(guardrail_events_table.c.company_id == company_uuid)
            )
            await session.execute(runs_table.delete().where(runs_table.c.company_id == company_uuid))
            await session.execute(
                connection_secrets_table.delete().where(connection_secrets_table.c.company_id == company_uuid)
            )
            await session.execute(company_keys_table.delete().where(company_keys_table.c.company_id == company_uuid))
            await session.execute(
                kb_documents_table.delete().where(kb_documents_table.c.company_id == company_uuid)
            )


@pytest.fixture
def make_connection():
    """Inserts a `console.connections` row as `console_app` (console-api's own write
    role — `ai_app` has no write grant on this table by design, PRD §8.1) and deletes it
    afterward. Local-dev-only credentials, matching the `changeme_local_dev_only`
    convention used everywhere else in this repo's local setup. Shared by
    test_compiler.py and test_connection_secret_endpoints.py."""
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
        api_base: str | None = None,
    ) -> str:
        connection_id = str(uuid.uuid4())
        with _connect() as conn:
            # SET LOCAL doesn't accept a bound parameter — company_id here is always our
            # own freshly generated uuid4, never external input.
            conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
            conn.execute(
                "INSERT INTO console.connections (id, company_id, name, type, api_base) "
                "VALUES (%s, %s, %s, %s, %s)",
                (connection_id, company_id, name, connection_type, api_base),
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


@pytest.fixture
def make_tool():
    """Inserts a `console.tools` row as `console_app` (same write-role convention as
    make_connection) and deletes it afterward."""
    settings = get_settings()
    created: list[tuple[str, str]] = []  # (company_id, tool_id)

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
        name: str = "test-tool",
        kind: str = "http",
        access_level: str = "read",
        auth_mode: str = "service",
        config: dict | None = None,
        enabled: bool = True,
    ) -> str:
        tool_id = str(uuid.uuid4())
        with _connect() as conn:
            # SET LOCAL doesn't accept a bound parameter — company_id here is always our
            # own freshly generated uuid4, never external input.
            conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
            conn.execute(
                "INSERT INTO console.tools (id, company_id, name, kind, access_level, auth_mode, config, enabled) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (tool_id, company_id, name, kind, access_level, auth_mode, json.dumps(config or {}), enabled),
            )
            conn.commit()
        created.append((company_id, tool_id))
        return tool_id

    yield _make

    for company_id, tool_id in created:
        with _connect() as conn:
            conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
            conn.execute("DELETE FROM console.tools WHERE id = %s", (tool_id,))
            conn.commit()


@pytest.fixture
def make_skill():
    """Inserts a `console.skills` + one `console.skill_versions` row as `console_app` (same
    write-role convention as make_connection/make_tool) and deletes them afterward.
    `content_hash` is a placeholder - nothing on the Python side reads it."""
    settings = get_settings()
    created: list[tuple[str, str, str]] = []  # (company_id, skill_id, version_id)

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
        name: str = "test-skill",
        description: str | None = None,
        content: str = "# Test Skill\n\nSome instructions.",
        version_no: int = 1,
        is_published: bool = True,
    ) -> str:
        skill_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        with _connect() as conn:
            # SET LOCAL doesn't accept a bound parameter — company_id here is always our
            # own freshly generated uuid4, never external input.
            conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
            conn.execute(
                "INSERT INTO console.skills (id, company_id, name, description) VALUES (%s, %s, %s, %s)",
                (skill_id, company_id, name, description),
            )
            conn.execute(
                "INSERT INTO console.skill_versions "
                "(id, company_id, skill_id, version_no, content, content_hash, is_published) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (version_id, company_id, skill_id, version_no, content, "placeholder-hash", is_published),
            )
            conn.commit()
        created.append((company_id, skill_id, version_id))
        return skill_id

    yield _make

    for company_id, skill_id, version_id in created:
        with _connect() as conn:
            conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
            conn.execute("DELETE FROM console.skill_versions WHERE id = %s", (version_id,))
            conn.execute("DELETE FROM console.skills WHERE id = %s", (skill_id,))
            conn.commit()


@pytest.fixture
def make_knowledge_base():
    """Inserts a `console.knowledge_bases` row as `console_app` (same write-role
    convention as make_connection/make_tool) and deletes it afterward."""
    settings = get_settings()
    created: list[tuple[str, str]] = []  # (company_id, kb_id)

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
        embedder_connection_id: str,
        name: str = "test-kb",
        chunk_size: int = 800,
        chunk_overlap: int = 100,
        retrieval_mode: str = "vector",
        alpha: float = 0.60,
        okf_field_map: dict | None = None,
    ) -> str:
        kb_id = str(uuid.uuid4())
        with _connect() as conn:
            # SET LOCAL doesn't accept a bound parameter — company_id here is always our
            # own freshly generated uuid4, never external input.
            conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
            conn.execute(
                "INSERT INTO console.knowledge_bases "
                "(id, company_id, name, embedder_connection_id, chunk_size, chunk_overlap, "
                " retrieval_mode, alpha, okf_field_map) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    kb_id,
                    company_id,
                    name,
                    embedder_connection_id,
                    chunk_size,
                    chunk_overlap,
                    retrieval_mode,
                    alpha,
                    json.dumps(okf_field_map or {}),
                ),
            )
            conn.commit()
        created.append((company_id, kb_id))
        return kb_id

    yield _make

    for company_id, kb_id in created:
        with _connect() as conn:
            conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
            conn.execute("DELETE FROM console.knowledge_bases WHERE id = %s", (kb_id,))
            conn.commit()


@pytest.fixture
def minio_cleanup():
    """Deletes every MinIO object under the given prefixes after the test - tests that
    exercise kb_indexer/kb_search/kb_export write real objects to the real local MinIO
    (docker-compose), there is nothing to mock against."""
    from app.core.storage import delete_prefix

    prefixes: list[str] = []

    yield prefixes.append

    for prefix in prefixes:
        delete_prefix(prefix)


@pytest.fixture
def make_egress_allowlist_entry():
    """Inserts a `console.egress_allowlist` row as `console_app` (same write-role
    convention as make_connection) and deletes it afterward."""
    settings = get_settings()
    created: list[tuple[str, str]] = []  # (company_id, entry_id)

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
        host_pattern: str,
        port: int | None = None,
        allow_private_ip: bool = False,
    ) -> str:
        entry_id = str(uuid.uuid4())
        with _connect() as conn:
            # SET LOCAL doesn't accept a bound parameter — company_id here is always our
            # own freshly generated uuid4, never external input.
            conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
            conn.execute(
                "INSERT INTO console.egress_allowlist (id, company_id, host_pattern, port, allow_private_ip) "
                "VALUES (%s, %s, %s, %s, %s)",
                (entry_id, company_id, host_pattern, port, allow_private_ip),
            )
            conn.commit()
        created.append((company_id, entry_id))
        return entry_id

    yield _make

    for company_id, entry_id in created:
        with _connect() as conn:
            conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
            conn.execute("DELETE FROM console.egress_allowlist WHERE id = %s", (entry_id,))
            conn.commit()


@pytest.fixture
def make_agent_version():
    """Inserts `console.agents` + `console.agent_versions` rows as `console_app` (same
    convention as make_connection) and deletes them afterward. Used by
    test_playground_run.py / test_playground_guardrails.py to give
    run_playground_agent's mandatory agent_version_id a real, resolvable row -
    app.services.agent_versions.resolve_agent_version has no fallback for a missing one."""
    settings = get_settings()
    created: list[tuple[str, str, str]] = []  # (company_id, agent_id, version_id)

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
        connection_id: str,
        guardrails: dict | None = None,
        model: str = "gpt-4o-mini",
    ) -> str:
        agent_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        spec = {
            "identity": {
                "name": "test-agent",
                "display_name": "Test Agent",
                "owner": "qa",
                "role": "answers test questions",
                "languages": ["en"],
            },
            "model": {"connection_id": connection_id, "model": model},
        }
        if guardrails is not None:
            spec["guardrails"] = guardrails

        with _connect() as conn:
            # SET LOCAL doesn't accept a bound parameter — company_id here is always our
            # own freshly generated uuid4, never external input.
            conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
            conn.execute(
                "INSERT INTO console.agents (id, company_id, name) VALUES (%s, %s, %s)",
                (agent_id, company_id, "test-agent"),
            )
            conn.execute(
                """INSERT INTO console.agent_versions
                   (id, company_id, agent_id, version_no, spec, spec_version)
                   VALUES (%s, %s, %s, 1, %s, '1')""",
                (version_id, company_id, agent_id, json.dumps(spec)),
            )
            conn.commit()
        created.append((company_id, agent_id, version_id))
        return version_id

    yield _make

    for company_id, agent_id, version_id in created:
        with _connect() as conn:
            conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
            conn.execute("DELETE FROM console.agent_versions WHERE id = %s", (version_id,))
            conn.execute("DELETE FROM console.agents WHERE id = %s", (agent_id,))
            conn.commit()


@pytest.fixture
async def store_connection_secret():
    """Encrypts and stores a real connection secret via the same services the real
    putConnectionSecret endpoint uses (app.services.company_keys/connection_secrets) -
    called directly, no HTTP round trip needed since tests run in-process."""
    from app.core.crypto import encrypt_with_dek
    from app.services.company_keys import get_or_create_company_dek
    from app.services.connection_secrets import upsert_connection_secret

    async def _store(company_id: str, connection_id: str, secret: str) -> None:
        dek = await get_or_create_company_dek(company_id)
        ciphertext, nonce = encrypt_with_dek(secret.encode(), dek.dek)
        await upsert_connection_secret(
            company_id=company_id,
            connection_id=connection_id,
            ciphertext=ciphertext,
            nonce=nonce,
            dek_version=dek.dek_version,
        )

    return _store


@pytest.fixture
def make_deployment():
    """Inserts a `console.deployments` row as `console_app` (same write-role convention
    as make_connection/make_agent_version — ai_app has no write grant on this table
    either) and deletes it afterward. `slug` is globally unique (deployments_slug_unique),
    so a random one per call avoids cross-test collisions."""
    settings = get_settings()
    created: list[tuple[str, str]] = []  # (company_id, deployment_id)

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
        agent_version_id: str,
        *,
        rate_limit_per_min: int = 60,
        daily_token_limit: int | None = None,
        daily_cost_limit_usd: float | None = None,
        allowed_origins: list[str] | None = None,
        guardrail_overrides: dict | None = None,
        enabled: bool = True,
    ) -> tuple[str, str]:
        deployment_id = str(uuid.uuid4())
        slug = f"test-{deployment_id}"
        with _connect() as conn:
            # SET LOCAL doesn't accept a bound parameter — company_id here is always our
            # own freshly generated uuid4, never external input.
            conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
            conn.execute(
                """INSERT INTO console.deployments
                   (id, company_id, slug, agent_version_id, rate_limit_per_min, daily_token_limit,
                    daily_cost_limit_usd, allowed_origins, guardrail_overrides, enabled)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    deployment_id,
                    company_id,
                    slug,
                    agent_version_id,
                    rate_limit_per_min,
                    daily_token_limit,
                    daily_cost_limit_usd,
                    json.dumps(allowed_origins or []),
                    json.dumps(guardrail_overrides or {}),
                    enabled,
                ),
            )
            conn.commit()
        created.append((company_id, deployment_id))
        return deployment_id, slug

    yield _make

    for company_id, deployment_id in created:
        with _connect() as conn:
            conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
            conn.execute("DELETE FROM console.deployments WHERE id = %s", (deployment_id,))
            conn.commit()


@pytest.fixture
def make_api_key():
    """Inserts a `console.api_keys` row plus one `console.api_key_scopes` row per
    `deployment_ids` entry, as `console_app`, and deletes both afterward. Returns
    `(api_key_id, full_key)` — `full_key` hashes (SHA-256, app.services.api_keys.
    hash_gateway_api_key) to the stored `key_hash`, same scheme console-api's own
    ApiKeysController uses."""
    settings = get_settings()
    created: list[tuple[str, str]] = []  # (company_id, api_key_id)

    def _connect() -> psycopg.Connection:
        return psycopg.connect(
            host=settings.MEMORY_DB_HOST,
            port=settings.MEMORY_DB_PORT,
            dbname=settings.MEMORY_DB_NAME,
            user="console_app",
            password="changeme_local_dev_only",
        )

    def _make(company_id: str, deployment_ids: list[str], allowed_ips: list[str] | None = None) -> tuple[str, str]:
        from app.services.api_keys import hash_gateway_api_key

        api_key_id = str(uuid.uuid4())
        full_key = f"ak_test_{uuid.uuid4().hex}"
        key_hash = hash_gateway_api_key(full_key)

        with _connect() as conn:
            conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
            conn.execute(
                """INSERT INTO console.api_keys (id, company_id, name, key_hash, prefix, allowed_ips)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (api_key_id, company_id, "test-key", key_hash, full_key[:9], json.dumps(allowed_ips or [])),
            )
            for deployment_id in deployment_ids:
                conn.execute(
                    """INSERT INTO console.api_key_scopes (id, company_id, api_key_id, deployment_id)
                       VALUES (%s, %s, %s, %s)""",
                    (str(uuid.uuid4()), company_id, api_key_id, deployment_id),
                )
            conn.commit()
        created.append((company_id, api_key_id))
        return api_key_id, full_key

    yield _make

    for company_id, api_key_id in created:
        with _connect() as conn:
            conn.execute(f"SET LOCAL app.company_id = '{company_id}'")
            conn.execute("DELETE FROM console.api_key_scopes WHERE api_key_id = %s", (api_key_id,))
            conn.execute("DELETE FROM console.api_keys WHERE id = %s", (api_key_id,))
            conn.commit()


@pytest.fixture
def resolvable_agent_version(make_connection, make_agent_version, store_connection_secret):
    """One-call setup for a fully resolvable Playground run: a real connection, a real
    stored secret, and a real agent version referencing both - returns
    (company_id, agent_version_id). The secret defaults to a syntactically-valid-looking
    but fake key (true negative - OpenAI deterministically rejects it, no
    @requires_openai_key needed to prove the resolution pipeline itself works)."""

    async def _make(
        company_id: str,
        secret: str = "sk-test-garbage-key-0000000000000000",
        guardrails: dict | None = None,
        model: str = "gpt-4o-mini",
    ) -> str:
        connection_id = make_connection(company_id)
        await store_connection_secret(company_id, connection_id, secret)
        return make_agent_version(company_id, connection_id, guardrails=guardrails, model=model)

    return _make
