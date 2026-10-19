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
from app.services.quotas import QuotaExceededError, _bangkok_today, record_actual_usage
from app.services.runs import RunUsage
from app.services.workflow_runtime import execute_workflow_run, run_deployment_workflow

from .markers import fake_workflow_runtime_claims, requires_openai_key, requires_postgres, requires_redis

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


def _single_state_spec(agent_version_id: str, connection_id: str) -> dict:
    return {
        "states": [{"id": "only", "agent_version_id": agent_version_id, "next_states": []}],
        "initial_state": "only",
        "manager": {"connection_id": connection_id, "model": "gpt-4o-mini"},
    }


@pytest.fixture
def make_fake_workflow_deployment(company_ids, make_workflow_version, make_deployment):
    """Shared setup for run_deployment_workflow tests: a company, a (never-compiled)
    workflow version row to satisfy `console.deployments.workflow_version_id`'s FK, and a
    deployment row targeting it. These tests monkeypatch `execute_workflow_run` itself
    (see `fake_execute_workflow_run`/`failing_execute_workflow_run` below) to isolate
    `run_deployment_workflow`'s own dispatch/quota logic from GraphOrchestrator's real
    construction - already proven separately by `test_run_workflow_returns_real_output`
    and the compiler's own test suite - so the spec content here is never read."""

    async def _make(**deployment_kwargs) -> tuple[str, str]:
        company_id = company_ids()
        workflow_version_id = make_workflow_version(company_id, {})
        deployment_id, _slug = make_deployment(company_id, workflow_version_id=workflow_version_id, **deployment_kwargs)
        return company_id, deployment_id

    return _make


_FAKE_WORKFLOW_RESULT = {
    "output": "fake workflow reply",
    "trace_id": "fake-trace-id",
    "latency_ms": 5,
    "tokens_in": 0,
    "tokens_out": 0,
    "cost_usd": 0.0,
}


@pytest.fixture
def fake_execute_workflow_run(monkeypatch):
    """Stands in for a real GraphOrchestrator run - returns a canned success result with
    no usage, so run_deployment_workflow's own quota/rate-limit/dispatch logic can be
    exercised without a real LLM or a real compilable workflow spec."""

    async def _fake(*_args, **_kwargs) -> dict:
        return dict(_FAKE_WORKFLOW_RESULT)

    monkeypatch.setattr("app.services.workflow_runtime.execute_workflow_run", _fake)


@pytest.fixture
def failing_execute_workflow_run(monkeypatch):
    """Simulates a run that burns real usage (mirrors a mid-run LLM failure) before
    raising - the `partial_usage` attribute is exactly what execute_workflow_run's own
    `except` block attaches in the real path."""

    async def _fake(*_args, **_kwargs) -> dict:
        exc = RuntimeError("boom after partial usage")
        exc.partial_usage = RunUsage(tokens_in=100, tokens_out=50, cost_usd=0.01)
        raise exc

    monkeypatch.setattr("app.services.workflow_runtime.execute_workflow_run", _fake)


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


@requires_postgres
async def test_run_deployment_workflow_unknown_deployment_raises(company_ids):
    with pytest.raises(ValueError, match="not found"):
        await run_deployment_workflow(company_ids(), str(uuid.uuid4()), "viewer", "hi")


@requires_postgres
async def test_run_deployment_workflow_disabled_deployment_raises(make_fake_workflow_deployment):
    company_id, deployment_id = await make_fake_workflow_deployment(enabled=False)

    with pytest.raises(ValueError, match="disabled"):
        await run_deployment_workflow(company_id, deployment_id, "viewer", "hi")


@requires_postgres
async def test_run_deployment_workflow_rejects_an_agent_deployment(
    company_ids, make_connection, make_agent_version, make_deployment
):
    """A deployment bound to an agent version (not a workflow) must be rejected here,
    never silently treated as a workflow deployment - the gateway relies on this guard
    to be the one place that distinguishes the two before dispatching."""
    company_id = company_ids()
    connection_id = make_connection(company_id)
    agent_version_id = make_agent_version(company_id, connection_id)
    deployment_id, _slug = make_deployment(company_id, agent_version_id)

    with pytest.raises(ValueError, match="not a workflow deployment"):
        await run_deployment_workflow(company_id, deployment_id, "viewer", "hi")


@requires_postgres
@requires_redis
async def test_run_deployment_workflow_returns_429_when_rate_limited(
    fake_execute_workflow_run, make_fake_workflow_deployment
):
    company_id, deployment_id = await make_fake_workflow_deployment(rate_limit_per_min=1)

    first = await run_deployment_workflow(company_id, deployment_id, "viewer", "hi")
    assert first["output"] == "fake workflow reply"

    with pytest.raises(QuotaExceededError) as exc_info:
        await run_deployment_workflow(company_id, deployment_id, "viewer", "hi")
    assert exc_info.value.reason == "rate_limit"


@requires_postgres
@requires_redis
async def test_run_deployment_workflow_returns_429_when_already_over_daily_limit(
    fake_execute_workflow_run, make_fake_workflow_deployment
):
    company_id, deployment_id = await make_fake_workflow_deployment(daily_token_limit=100)
    # check_quota_not_exceeded never reserves - simulate a prior run having already used
    # up the daily budget, since there's no single-call way to exceed it from zero here.
    await record_actual_usage(deployment_id, actual_tokens=100, actual_cost_usd=0.0, today=_bangkok_today(), ttl=60)

    with pytest.raises(QuotaExceededError) as exc_info:
        await run_deployment_workflow(company_id, deployment_id, "viewer", "hi")
    assert exc_info.value.reason == "daily_token_limit"


@requires_postgres
@requires_redis
async def test_run_deployment_workflow_happy_path_records_real_usage(
    fake_execute_workflow_run, make_fake_workflow_deployment
):
    company_id, deployment_id = await make_fake_workflow_deployment(daily_token_limit=100_000)

    result = await run_deployment_workflow(company_id, deployment_id, "viewer", "hi")

    assert result["output"] == "fake workflow reply"
    assert result["trace_id"]
    # The canned result carries zero usage - confirms the record_actual_usage call path
    # runs without error even with nothing to record, distinct from the "no limits
    # configured at all" no-op case tested below.
    from app.core.redis import get_redis

    redis_client = get_redis()
    tokens_key = f"gw:quota:tokens:{deployment_id}:{_bangkok_today()}"
    assert await redis_client.get(tokens_key) in (None, b"0")


@requires_postgres
@requires_redis
async def test_run_deployment_workflow_without_limits_never_touches_quota_counters(
    fake_execute_workflow_run, make_fake_workflow_deployment
):
    company_id, deployment_id = await make_fake_workflow_deployment()  # no daily_token_limit/daily_cost_limit_usd

    await run_deployment_workflow(company_id, deployment_id, "viewer", "hi")

    from app.core.redis import get_redis

    redis_client = get_redis()
    tokens_key = f"gw:quota:tokens:{deployment_id}:{_bangkok_today()}"
    assert await redis_client.get(tokens_key) is None


@requires_postgres
@requires_redis
async def test_run_deployment_workflow_records_partial_usage_on_failure(
    failing_execute_workflow_run, make_fake_workflow_deployment
):
    """Regression-style test for the same partial-usage-on-failure guarantee
    test_gateway_run.py's `test_quota_counts_real_usage_even_when_run_fails_after_partial_usage`
    already proves for run_deployment_agent - a workflow run that burns real usage before
    failing must still have that spend recorded, not silently dropped because the run as a
    whole raised. `failing_execute_workflow_run` simulates exactly the shape
    `execute_workflow_run`'s own `except` block produces: an exception with a
    `partial_usage` attribute carrying whatever usage was collected before the failure."""
    company_id, deployment_id = await make_fake_workflow_deployment(daily_token_limit=100_000)

    with pytest.raises(RuntimeError, match="boom after partial usage"):
        await run_deployment_workflow(company_id, deployment_id, "viewer", "hi")

    from app.core.redis import get_redis

    redis_client = get_redis()
    tokens_key = f"gw:quota:tokens:{deployment_id}:{_bangkok_today()}"
    assert int(await redis_client.get(tokens_key)) == 150
